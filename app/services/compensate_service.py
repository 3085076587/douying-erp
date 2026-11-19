"""账单赔付：每天把昨天的流水入库，页面只查库。"""
from __future__ import annotations

import json
import logging
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.compensate import DyCompensate
from app.models.shop import Shop
from app.services.dy_client import FXG_HOST, ShopApiError, ShopAuthError, cookie_store, cookies_for
from app.services.dy_compensate import fetch_compensate_list
from app.services.fetch_pace import take_turns
from app.services.store_service import keep_shop_jar, revive_shop

_LOG = logging.getLogger("uvicorn.error")
LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
STATE_FILE = LOG_DIR / "compensate_scheduler_state.json"
# 抖店赔付账单页写明记录从这天开始，单次查询最长约 90 天。
HISTORY_START = date(2023, 5, 13)
WINDOW_DAYS = 89


def _day_bounds(start: date, end: date) -> tuple[int, int]:
    start_at = datetime(start.year, start.month, start.day)
    end_at = datetime(end.year, end.month, end.day, 23, 59, 59)
    return int(start_at.timestamp()), int(end_at.timestamp())


def read_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_state(**fields: object) -> None:
    state = read_state()
    state.update(fields)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def needs_backfill() -> bool:
    return not read_state().get("backfill_done")


def _upsert(db: Session, shop: Shop, items: list[dict]) -> int:
    if not items:
        return 0
    ids = [item["compensate_id"] for item in items]
    existing = {
        row.compensate_id: row
        for row in db.scalars(
            select(DyCompensate).where(
                DyCompensate.store_pk == shop.id,
                DyCompensate.compensate_id.in_(ids),
            )
        )
    }
    saved = 0
    for item in items:
        record = existing.get(item["compensate_id"])
        if record is None:
            record = DyCompensate(store_pk=shop.id, shop_id=shop.shop_id or "", compensate_id=item["compensate_id"])
            db.add(record)
            existing[item["compensate_id"]] = record
        record.shop_id = shop.shop_id or ""
        record.finished_at = item["finished_at"]
        record.scene = item["scene"]
        record.recipient = item["recipient"]
        record.direction = item["direction"]
        record.amount_fen = int(item["amount_fen"] or 0)
        record.compensator = item["compensator"]
        record.account_name = item["account_name"]
        record.reason = item["reason"]
        record.shop_order_id = item["shop_order_id"]
        record.order_id = item["order_id"]
        record.aftersale_id = item["aftersale_id"]
        saved += 1
    db.commit()
    return saved


def _pull_one(store_pk: int, start_ts: int, end_ts: int) -> tuple[bool, int]:
    """拉一家店。失败不把店铺标成离线。返回（是否成功, 入库条数）。"""
    db = SessionLocal()
    cookies: dict[str, str] = {}
    try:
        shop = db.get(Shop, store_pk)
        if shop is None:
            return False, 0
        cookies = cookies_for(cookie_store(shop.cookie_data), FXG_HOST)
        if not cookies or shop.shop_cookie != 1:
            return False, 0
        try:
            items = fetch_compensate_list(cookies, start_ts, end_ts)
        except ShopAuthError:
            if not revive_shop(db, shop):
                return False, 0
            cookies = cookies_for(cookie_store(shop.cookie_data), FXG_HOST)
            items = fetch_compensate_list(cookies, start_ts, end_ts)
        saved = _upsert(db, shop, items)
        return True, saved
    except (ShopApiError, ShopAuthError) as exc:
        _LOG.info("账单赔付 %s 没拉到：%s", store_pk, exc)
        return False, 0
    except Exception:
        _LOG.exception("账单赔付 %s 入库失败", store_pk)
        return False, 0
    finally:
        if cookies:
            shop = db.get(Shop, store_pk)
            if shop is not None:
                keep_shop_jar(shop, cookies)
                db.commit()
        db.close()


def _windows(start: date, end: date):
    cursor = start
    while cursor <= end:
        stop = min(end, cursor + timedelta(days=WINDOW_DAYS - 1))
        yield cursor, stop
        cursor = stop + timedelta(days=1)


def pull_dates(start: date, end: date) -> tuple[int, int, int, str]:
    """拉取这个日期范围内的赔付流水。返回（成功店铺数, 失败店铺数, 入库条数, 说明）。"""
    if end < start:
        start, end = end, start
    start_ts, end_ts = _day_bounds(start, end)
    db = SessionLocal()
    try:
        shop_ids = [
            shop.id
            for shop in db.scalars(select(Shop).order_by(Shop.id.asc()))
            if shop.shop_cookie == 1 and shop.cookie_data
        ]
    finally:
        db.close()
    if not shop_ids:
        return 0, 0, 0, "还没有可以拉取的店铺凭证"
    saved_shops = 0
    saved_rows = 0
    failed = 0
    lock = threading.Lock()

    def one(store_pk: int) -> None:
        nonlocal saved_shops, saved_rows, failed
        ok, count = _pull_one(store_pk, start_ts, end_ts)
        with lock:
            if ok:
                saved_shops += 1
                saved_rows += count
            else:
                failed += 1

    take_turns(shop_ids, one)
    message = f"{start.isoformat()} 至 {end.isoformat()}，{saved_shops} 家入库 {saved_rows} 条"
    if failed:
        message += f"，{failed} 家没拉到"
    return saved_shops, failed, saved_rows, message


def pull_yesterday() -> str:
    day = date.today() - timedelta(days=1)
    _saved, _failed, _rows, message = pull_dates(day, day)
    write_state(last_run=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), last_message=message)
    return message


def pull_all() -> str:
    """第一次把平台有记录以来的赔付全部入库。页面打开时仍默认只看近 30 天。"""
    end = date.today()
    touched = 0
    failed = 0
    for start, stop in _windows(HISTORY_START, end):
        _saved, window_failed, rows, message = pull_dates(start, stop)
        touched += rows
        failed = max(failed, window_failed)
        _LOG.info("账单赔付 %s", message)
    summary = f"{HISTORY_START.isoformat()} 至 {end.isoformat()}，写入 {touched} 条"
    if failed:
        summary += f"，有窗口 {failed} 家没拉到"
    else:
        write_state(backfill_done=True)
    write_state(last_run=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), last_message=summary)
    return summary


def _parse_day(value: str) -> date | None:
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _range(start_date: str, end_date: str) -> tuple[datetime, datetime]:
    end = _parse_day(end_date) or date.today()
    start = _parse_day(start_date) or (end - timedelta(days=29))
    if end < start:
        start, end = end, start
    return datetime(start.year, start.month, start.day), datetime(end.year, end.month, end.day) + timedelta(days=1)


def list_shop_options(db: Session) -> list[dict]:
    shops = db.scalars(select(Shop).order_by(Shop.name.asc(), Shop.id.asc()))
    return [{"store_pk": shop.id, "name": shop.name or shop.shop_id or str(shop.id)} for shop in shops]


def list_shop_totals(db: Session, *, store_pk: int = 0, start_date: str = "", end_date: str = "") -> dict:
    start_at, end_at = _range(start_date, end_date)
    query = (
        select(
            DyCompensate.store_pk,
            func.coalesce(func.sum(DyCompensate.amount_fen), 0),
            func.count(DyCompensate.id),
        )
        .where(DyCompensate.finished_at >= start_at, DyCompensate.finished_at < end_at)
        .group_by(DyCompensate.store_pk)
    )
    if store_pk:
        query = query.where(DyCompensate.store_pk == store_pk)
    sums = {pk: (int(amount or 0), int(count or 0)) for pk, amount, count in db.execute(query)}
    if store_pk:
        shops = db.scalars(select(Shop).where(Shop.id == store_pk))
    else:
        shops = db.scalars(select(Shop).order_by(Shop.name.asc(), Shop.id.asc()))
    items = []
    total_fen = 0
    for shop in shops:
        amount, count = sums.get(shop.id, (0, 0))
        if not store_pk and count == 0:
            continue
        items.append(
            {
                "store_pk": shop.id,
                "shop_name": shop.name or shop.shop_id or str(shop.id),
                "amount_fen": amount,
                "count": count,
            }
        )
        total_fen += amount
    items.sort(key=lambda row: row["amount_fen"])
    return {"items": items, "total_fen": total_fen, "shop_count": len(items)}


def list_detail(db: Session, store_pk: int, start_date: str = "", end_date: str = "") -> dict:
    shop = db.get(Shop, store_pk)
    if shop is None:
        return {"store_pk": store_pk, "shop_name": "", "amount_fen": 0, "items": []}
    start_at, end_at = _range(start_date, end_date)
    rows = db.scalars(
        select(DyCompensate)
        .where(
            DyCompensate.store_pk == store_pk,
            DyCompensate.finished_at >= start_at,
            DyCompensate.finished_at < end_at,
        )
        .order_by(DyCompensate.finished_at.desc(), DyCompensate.id.desc())
    )
    items = []
    total = 0
    for row in rows:
        total += int(row.amount_fen or 0)
        items.append(
            {
                "compensate_id": row.compensate_id,
                "finished_at": row.finished_at.strftime("%Y-%m-%d %H:%M:%S") if row.finished_at else "",
                "scene": row.scene,
                "recipient": row.recipient,
                "direction": row.direction,
                "amount_fen": int(row.amount_fen or 0),
                "compensator": row.compensator,
                "account_name": row.account_name,
                "reason": row.reason,
                "shop_order_id": row.shop_order_id,
                "order_id": row.order_id,
                "aftersale_id": row.aftersale_id,
            }
        )
    return {
        "store_pk": shop.id,
        "shop_name": shop.name or "",
        "amount_fen": total,
        "items": items,
    }

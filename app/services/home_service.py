"""店铺首页待办：每天 0 点和 12 点写入 Redis，页面只读缓存。"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.shop import Shop
from app.services import fund_cache
from app.services.dy_client import FXG_HOST, ShopApiError, ShopAuthError, cookie_store, cookies_for
from app.services.dy_home import fetch_home_todos
from app.services.dy_violation import fetch_appeal_ticket_ids
from app.services.schedule_log import write_fail, write_success
from app.services.fetch_pace import merge_kept, take_turns
from app.services.store_service import keep_shop_jar

_LOG = logging.getLogger("uvicorn.error")
_CACHE = "home"
_META = "home_meta"
_TTL_SECONDS = 7 * 24 * 3600
_FRESH_SECONDS = 30
_JOB = threading.Lock()
_PROGRESS = threading.Lock()
_PROGRESS_SLOT: dict[str, object] = {
    "running": False,
    "done": 0,
    "total": 0,
    "failed": 0,
    "message": "",
    "fresh": False,
}
_FIELDS = (
    "pending_pay",
    "pending_ship",
    "abnormal_package",
    "pending_aftersale",
    "service_ticket",
    "risk_point",
    "pending_violation",
)


def next_home_wait(now: datetime | None = None) -> float:
    """距离下一次 0 点或 12 点的秒数。"""
    now = now or datetime.now()
    slots = []
    for hour in (0, 12):
        slot = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if slot <= now:
            slot = slot + timedelta(days=1)
        slots.append(slot)
    return max(5.0, (min(slots) - now).total_seconds())


def _blank(shop: Shop, error: str = "") -> dict:
    row = {
        "store_pk": shop.id,
        "shop_id": shop.shop_id,
        "sync_error": error[:200],
    }
    for field in _FIELDS:
        row[field] = "" if error else "0"
    return row


def _pull_one(shop_id: int) -> dict:
    db = SessionLocal()
    try:
        shop = db.get(Shop, shop_id)
        if shop is None:
            return {}
        cookies = cookies_for(cookie_store(shop.cookie_data), FXG_HOST)
        if not cookies or shop.shop_cookie != 1:
            return _blank(shop, "还没有这家店的登录凭证")
        try:
            values = fetch_home_todos(cookies)
            keep_shop_jar(shop, cookies)
            db.commit()
        except ShopAuthError as exc:
            return _blank(shop, str(exc) or "登录已失效")
        except ShopApiError as exc:
            return _blank(shop, exc.message or "首页接口失败")
        row = _blank(shop)
        row.update(values)
        try:
            row["pending_violation_ids"] = fetch_appeal_ticket_ids(cookies)
        except (ShopAuthError, ShopApiError):
            pass
        return row
    except Exception:
        _LOG.exception("拉取店铺 %s 首页待办失败", shop_id)
        return {"store_pk": shop_id, "shop_id": "", "sync_error": "暂时没拉到"}
    finally:
        db.close()


def home_progress() -> dict:
    with _PROGRESS:
        return dict(_PROGRESS_SLOT)


def _updated_age() -> float | None:
    meta = fund_cache.read_obj(_META) or {}
    text = str(meta.get("updated_at") or "")
    if not text or text == "cached":
        return None
    try:
        updated = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return (datetime.now() - updated).total_seconds()


def _refresh_home() -> dict:
    """真正去抖店拉首页待办。调用方要已经拿着 _JOB。"""
    db = SessionLocal()
    try:
        shops = list(db.scalars(select(Shop).order_by(Shop.id.asc())))
    finally:
        db.close()
    by_pk: dict[int, dict] = {}
    gate = threading.Lock()
    done_count = {"n": 0}
    with _PROGRESS:
        _PROGRESS_SLOT.update(running=True, done=0, total=len(shops), failed=0, message="", fresh=False)

    def pull(shop_id: int) -> dict:
        row = _pull_one(shop_id)
        with gate:
            if row.get("store_pk"):
                by_pk[int(row["store_pk"])] = row
            done_count["n"] += 1
            current = done_count["n"]
        with _PROGRESS:
            _PROGRESS_SLOT["done"] = current
        return row

    try:
        if shops:
            take_turns([shop.id for shop in shops], pull)
        rows = [by_pk[shop.id] for shop in shops if shop.id in by_pk]
        previous = fund_cache.read(_CACHE) or []
        rows = merge_kept(previous if isinstance(previous, list) else [], rows)
        updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        fund_cache.write(_CACHE, rows, _TTL_SECONDS)
        fund_cache.write_obj(_META, {"updated_at": updated_at}, _TTL_SECONDS)
        failed = sum(1 for row in rows if row.get("sync_error"))
        message = f"店铺首页待办 {len(rows) - failed} 家完成" + (f"，{failed} 家失败" if failed else "")
        with _PROGRESS:
            _PROGRESS_SLOT.update(done=len(shops), failed=failed, message=message or "完成")
        if failed:
            write_fail("店铺首页待办", message)
        else:
            write_success(message)
        _LOG.info(message)
        return {"total": len(rows), "failed": failed, "message": message}
    except Exception:
        _LOG.exception("店铺首页待办更新失败")
        with _PROGRESS:
            _PROGRESS_SLOT.update(failed=1, message="拉取过程出错了，请查看服务端日志")
        write_fail("店铺首页待办", "拉取过程出错了")
        return {"total": len(shops), "failed": 1, "message": "拉取过程出错了，请查看服务端日志"}


def refresh_home() -> dict:
    """定时任务用。有人正在拉时就等这一轮结束，不并行打抖店。"""
    _JOB.acquire()
    try:
        return _refresh_home()
    finally:
        with _PROGRESS:
            _PROGRESS_SLOT["running"] = False
        _JOB.release()


def start_home_refresh() -> dict:
    """按钮触发。正在拉就跟上这一轮；30 秒内刚更新过就不再打抖店。"""
    with _PROGRESS:
        if _PROGRESS_SLOT.get("running"):
            return dict(_PROGRESS_SLOT)
    age = _updated_age()
    if age is not None and age < _FRESH_SECONDS:
        return {"running": False, "done": 0, "total": 0, "failed": 0, "message": "", "fresh": True}
    if not _JOB.acquire(blocking=False):
        with _PROGRESS:
            slot = dict(_PROGRESS_SLOT)
            slot["running"] = True
            slot["fresh"] = False
            return slot
    with _PROGRESS:
        _PROGRESS_SLOT.update(running=True, done=0, total=0, failed=0, message="", fresh=False)

    def run() -> None:
        try:
            _refresh_home()
        finally:
            with _PROGRESS:
                _PROGRESS_SLOT["running"] = False
            _JOB.release()

    threading.Thread(target=run, name="center-home", daemon=True).start()
    return home_progress()


def read_home(db) -> dict:
    cached = fund_cache.read(_CACHE) or []
    meta = fund_cache.read_obj(_META) or {}
    updated_at = str(meta.get("updated_at") or "")
    if cached and not updated_at:
        updated_at = "cached"
    by_pk = {
        int(row["store_pk"]): row
        for row in cached
        if isinstance(row, dict) and str(row.get("store_pk") or "").isdigit()
    }
    items = []
    for shop in db.scalars(select(Shop).order_by(Shop.id.asc())):
        saved = by_pk.get(shop.id) or {}
        item = {
            "store_pk": shop.id,
            "shop_id": shop.shop_id,
            "name": shop.name,
            "login_status": shop.login_status,
            "sync_error": str(saved.get("sync_error") or ""),
        }
        for field in _FIELDS:
            item[field] = str(saved.get(field) or "")
        raw_ids = saved.get("pending_violation_ids")
        item["pending_violation_ids"] = [str(value) for value in raw_ids if str(value).strip()] if isinstance(raw_ids, list) else []
        items.append(item)
    return {"items": items, "updated_at": updated_at}

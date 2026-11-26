"""把每家店的成交日写入 dy_deal_day。页面打开时不调用这里。"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.db.session import SessionLocal, engine
from app.models.deal import DyDealDay
from app.models.shop import Shop
from app.services.center_service import _cell_number
from app.services.dy_client import FXG_HOST, ShopApiError, ShopAuthError, cookie_store, cookies_for
from app.services.dy_compass import fetch_operating_cards
from app.services.store_service import keep_shop_jar

class DealSourceError(RuntimeError):
    """首页指标里没有成交金额，不能把空数写进表。"""


DAY_GAP = 0.3
SHOP_GAP = 0.4
_FIELDS = (
    "pay_gmv",
    "user_pay_gmv",
    "pay_order_cnt",
    "pay_uv",
    "refund_pay_gmv",
    "refund_pay_ratio",
    "refund_pay_cnt",
)
# 罗盘指标代码 → 成交日表字段。金额在接口里已经是分。
# 退款率存成「百分数 × 100」，12.34% 写成 1234，页面再除回去。
_INDEX_MAP = {
    "pay_amt": "pay_gmv",
    "income_amt": "user_pay_gmv",
    "pay_cnt": "pay_order_cnt",
    "pay_ucnt": "pay_uv",
    "pay_refund_amt": "refund_pay_gmv",
    "refund_amt_rate": "refund_pay_ratio",
    "pay_refund_order_cnt": "refund_pay_cnt",
}
_RATE_FIELDS = frozenset({"refund_pay_ratio"})
_EXTRA_COLUMNS = (
    ("user_pay_gmv", "INT NOT NULL DEFAULT 0"),
    ("refund_pay_gmv", "INT NOT NULL DEFAULT 0"),
    ("refund_pay_ratio", "INT NOT NULL DEFAULT 0"),
    ("refund_pay_cnt", "INT NOT NULL DEFAULT 0"),
)


def ensure_deal_table() -> None:
    with engine.begin() as conn:
        conn.execute(text(
            """
            CREATE TABLE IF NOT EXISTS dy_deal_day (
                id INT NOT NULL AUTO_INCREMENT,
                store_pk INT NOT NULL,
                stat_date DATE NOT NULL,
                pay_gmv INT NOT NULL DEFAULT 0,
                pay_order_cnt INT NOT NULL DEFAULT 0,
                pay_uv INT NOT NULL DEFAULT 0,
                create_gmv INT NOT NULL DEFAULT 0,
                create_cnt INT NOT NULL DEFAULT 0,
                create_uv INT NOT NULL DEFAULT 0,
                pay_refund_gmv INT NOT NULL DEFAULT 0,
                refund_gmv INT NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                PRIMARY KEY (id),
                UNIQUE KEY uk_dy_deal_day (store_pk, stat_date),
                KEY ix_dy_deal_day_date (stat_date)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        ))
        names = {
            row[0]
            for row in conn.execute(text(
                "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dy_deal_day'"
            ))
        }
        for name, ddl in _EXTRA_COLUMNS:
            if name not in names:
                conn.execute(text(f"ALTER TABLE dy_deal_day ADD COLUMN {name} {ddl}"))


def recent_through_yesterday(days: int = 30, today: date | None = None) -> tuple[date, date]:
    """昨天往前共 days 天，不含今天。"""
    today = today or date.today()
    end = today - timedelta(days=1)
    start = end - timedelta(days=max(days, 1) - 1)
    return start, end


def _usable(values: dict) -> bool:
    number, unit = _cell_number(values.get("value"))
    return number is not None and unit != "6"


def _keep(found: dict[str, dict], code: str, values: object) -> None:
    """同一指标先碰到单位 6 的空占位时，后面有数字的节点盖掉它。"""
    if not code or not isinstance(values, dict) or "value" not in values:
        return
    current = found.get(code)
    if current is None or (_usable(values) and not _usable(current)):
        found[code] = values


def _keep_wrapped(found: dict[str, dict], code: str, wrapped: object) -> None:
    if isinstance(wrapped, dict) and isinstance(wrapped.get("index_values"), dict):
        _keep(found, code, wrapped["index_values"])


def index_values(payload: dict) -> dict[str, dict]:
    """罗盘首页指标里的 index_code → 数值节点。"""
    found: dict[str, dict] = {}

    def consider(info: dict, code: str) -> None:
        _keep_wrapped(found, code, info.get("index_value"))
        for key, bucket in info.items():
            if not isinstance(bucket, dict):
                continue
            name = str(key)
            _keep_wrapped(found, name, bucket.get(name + "_index_values"))
            _keep_wrapped(found, name, bucket.get("index_value"))
            if code:
                _keep_wrapped(found, code, bucket.get(code + "_index_values"))

    def walk(node: object) -> None:
        if isinstance(node, dict):
            extra = node.get("extra") if isinstance(node.get("extra"), dict) else {}
            code = str(extra.get("index_code") or node.get("index_code") or "").strip()
            info = node.get("cell_info") if isinstance(node.get("cell_info"), dict) else None
            if info is not None:
                consider(info, code)
            for child in node.values():
                if isinstance(child, (dict, list)):
                    walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(payload)
    return found


def _stored(field: str, number: float) -> int:
    if field in _RATE_FIELDS:
        percent = number * 100 if abs(number) <= 2 else number
        return int(round(percent * 100))
    return int(round(number))


def numbers_from_cards(payload: dict) -> tuple[dict[str, int], list[str]]:
    cells = index_values(payload)
    numbers = {field: 0 for field in _FIELDS}
    for code, field in _INDEX_MAP.items():
        values = cells.get(code)
        if not isinstance(values, dict):
            continue
        number, unit = _cell_number(values.get("value"))
        if number is None or unit == "6":
            continue
        numbers[field] = _stored(field, number)
    return numbers, sorted(cells)


def sync_yesterday() -> str:
    """从每家店最后一条成交日的下一天补到昨天。没拉到的日期下次凌晨继续补。"""
    ensure_deal_table()
    yesterday = date.today() - timedelta(days=1)
    with SessionLocal() as db:
        shops = list(db.scalars(select(Shop).order_by(Shop.id.asc())))
    ok = fail = skip = 0
    announced = False
    for index, shop in enumerate(shops, 1):
        days = _due_days(shop.id, yesterday)
        if not days:
            print(f"{index}/{len(shops)} {shop.name} 已有 {yesterday.isoformat()}", flush=True)
            ok += 1
            continue
        print(
            f"{index}/{len(shops)} 开始 {shop.name} {'、'.join(day.isoformat() for day in days)}",
            flush=True,
        )
        try:
            line, announced = _sync_shop(shop.id, shop.name, days, announced)
        except DealSourceError:
            raise
        except Exception as exc:
            line = f"[失败] {shop.name} {exc}"
        print(f"{index}/{len(shops)} {line}", flush=True)
        if line.startswith("[完成]"):
            ok += 1
        elif line.startswith("[跳过]"):
            skip += 1
        else:
            fail += 1
        if index < len(shops):
            time.sleep(SHOP_GAP)
    return f"补到 {yesterday.isoformat()} 成功 {ok} 家，失败 {fail} 家，跳过 {skip} 家"


def _due_days(store_pk: int, yesterday: date) -> list[date]:
    """这家店最后一条成交日的下一天，一直补到昨天。"""
    with SessionLocal() as db:
        last = db.execute(
            text("SELECT MAX(stat_date) FROM dy_deal_day WHERE store_pk = :pk"),
            {"pk": store_pk},
        ).scalar()
    if isinstance(last, datetime):
        last = last.date()
    if last is None:
        return [yesterday]
    if last >= yesterday:
        return []
    return _dates(last + timedelta(days=1), yesterday)


def backfill_recent(days: int = 30) -> None:
    """昨天往前共 days 天。同一天再跑会覆盖。一家店拉完再到下一家。"""
    ensure_deal_table()
    start, end = recent_through_yesterday(days)
    print(f"成交日 {start.isoformat()} 到 {end.isoformat()}", flush=True)
    ok, fail, skip = _sync_range(start, end)
    with SessionLocal() as db:
        total = db.execute(text("SELECT COUNT(*) FROM dy_deal_day")).scalar()
        span = db.execute(text("SELECT MIN(stat_date), MAX(stat_date) FROM dy_deal_day")).one()
    print(
        f"结束：成功 {ok} 家，失败 {fail} 家，跳过 {skip} 家。"
        f"表内 {int(total or 0)} 行，日期 {span[0]} 至 {span[1]}",
        flush=True,
    )


def _dates(start: date, end: date) -> list[date]:
    days = []
    cursor = start
    while cursor <= end:
        days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _sync_range(start: date, end: date) -> tuple[int, int, int]:
    with SessionLocal() as db:
        shops = list(db.scalars(select(Shop).order_by(Shop.id.asc())))
    ok = fail = skip = 0
    days = _dates(start, end)
    announced = False
    for index, shop in enumerate(shops, 1):
        print(f"{index}/{len(shops)} 开始 {shop.name}", flush=True)
        try:
            line, announced = _sync_shop(shop.id, shop.name, days, announced)
        except DealSourceError:
            raise
        except Exception as exc:
            line = f"[失败] {shop.name} {exc}"
        print(f"{index}/{len(shops)} {line}", flush=True)
        if line.startswith("[完成]"):
            ok += 1
        elif line.startswith("[跳过]"):
            skip += 1
        else:
            fail += 1
        if index < len(shops):
            time.sleep(SHOP_GAP)
    return ok, fail, skip


def _sync_shop(shop_id: int, shop_name: str, days: list[date], announced: bool) -> tuple[str, bool]:
    db = SessionLocal()
    try:
        shop = db.get(Shop, shop_id)
        if shop is None:
            return f"[跳过] {shop_name}", announced
        cookies = cookies_for(cookie_store(shop.cookie_data), FXG_HOST)
        if not cookies or shop.shop_cookie != 1:
            return f"[跳过] {shop_name} 没有登录凭证", announced
        written = 0
        try:
            for day in days:
                payload = fetch_operating_cards(
                    cookies,
                    shop_id=str(shop.shop_id or ""),
                    date_type=2,
                    begin_date=day.strftime("%Y/%m/%d 00:00:00"),
                    end_date=day.strftime("%Y/%m/%d 00:00:00"),
                )
                numbers, codes = numbers_from_cards(payload)
                if not announced:
                    missing = sorted({field for code, field in _INDEX_MAP.items() if code not in codes})
                    print("指标代码 " + "、".join(codes), flush=True)
                    if "pay_amt" not in codes:
                        keep_shop_jar(shop, cookies)
                        db.commit()
                        raise DealSourceError("这一天没有成交金额，先停下来。指标代码已打印在上面")
                    if missing:
                        print("还没对上的字段 " + "、".join(sorted(set(missing))), flush=True)
                    _print_day(shop_name, day, numbers)
                    announced = True
                _upsert(db, shop.id, day, numbers)
                written += 1
                time.sleep(DAY_GAP)
        except ShopAuthError as exc:
            keep_shop_jar(shop, cookies)
            db.commit()
            return f"[掉线] {shop_name} {exc}，已写入 {written} 天", announced
        except ShopApiError as exc:
            keep_shop_jar(shop, cookies)
            db.commit()
            return f"[失败] {shop_name} {exc}，已写入 {written} 天", announced
        keep_shop_jar(shop, cookies)
        db.commit()
        return f"[完成] {shop_name}: 写入 {written} 天", announced
    finally:
        db.close()


def _print_day(shop_name: str, day: date, numbers: dict[str, int]) -> None:
    print(
        f"{shop_name} {day.isoformat()} 成交金额 {numbers['pay_gmv'] / 100:.2f}"
        f" 用户支付 {numbers['user_pay_gmv'] / 100:.2f}"
        f" 成交订单 {numbers['pay_order_cnt']} 成交人数 {numbers['pay_uv']}"
        f" 退款金额 {numbers['refund_pay_gmv'] / 100:.2f}"
        f" 退款率 {numbers['refund_pay_ratio'] / 100:.2f}%"
        f" 退款订单 {numbers['refund_pay_cnt']}",
        flush=True,
    )


def _upsert(db: Session, store_pk: int, day: date, numbers: dict[str, int]) -> None:
    now = datetime.now().replace(microsecond=0)
    values = {"store_pk": store_pk, "stat_date": day, "created_at": now, "updated_at": now, **numbers}
    stmt = mysql_insert(DyDealDay).values(**values)
    stmt = stmt.on_duplicate_key_update(
        **{key: stmt.inserted[key] for key in _FIELDS},
        updated_at=now,
    )
    db.execute(stmt)
    db.commit()

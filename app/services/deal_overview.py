"""数据总览：只读成交日表。一家店一天一行，缺的日期补 0。不向抖音现拉。"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.deal import DyDealDay
from app.models.shop import Shop
from app.services.deal_sync import ensure_deal_table

MAX_DAYS = 93
_FIELDS = (
    "pay_gmv",
    "user_pay_gmv",
    "pay_order_cnt",
    "pay_uv",
    "refund_pay_gmv",
    "refund_pay_ratio",
    "refund_pay_cnt",
)


class DealRangeError(ValueError):
    pass


def _parse_day(value: str) -> date | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise DealRangeError("日期格式要用 YYYY-MM-DD") from exc


def resolve_range(start: str, end: str, today: date | None = None) -> tuple[date, date, list[date]]:
    """默认昨天往前共 7 天，不含今天。结束日不能是今天，一次最多 93 天。"""
    today = today or date.today()
    yesterday = today - timedelta(days=1)
    end_day = _parse_day(end) or yesterday
    start_day = _parse_day(start) or (end_day - timedelta(days=6))
    if end_day >= today:
        raise DealRangeError("结束日期不能选今天")
    if start_day > end_day:
        raise DealRangeError("开始日期不能晚于结束日期")
    count = (end_day - start_day).days + 1
    if count > MAX_DAYS:
        raise DealRangeError("一次最多选择 93 天")
    days = [start_day + timedelta(days=offset) for offset in range(count)]
    return start_day, end_day, days


def account_names(owner_account: str) -> list[str]:
    """一个店绑了多个抖音号时，用中文逗号拆开。"""
    names: list[str] = []
    for part in (owner_account or "").split("，"):
        name = part.strip()
        if name and name not in names:
            names.append(name)
    return names


def assemble(shops: list, rows: list, days: list[date]) -> list[dict]:
    """按抖音号分组。中文逗号拆开的店，整店数字分别算进每一个号。缺的日期是 0。"""
    day_index = {day: index for index, day in enumerate(days)}
    by_shop: dict[int, dict[date, object]] = {}
    for row in rows:
        by_shop.setdefault(row.store_pk, {})[row.stat_date] = row

    grouped: dict[str, list[dict]] = {}
    for shop in shops:
        names = account_names(shop.owner_account)
        if not names:
            continue
        saved = by_shop.get(shop.id, {})
        series = {field: [0] * len(days) for field in _FIELDS}
        for day, index in day_index.items():
            record = saved.get(day)
            if record is None:
                continue
            for field in _FIELDS:
                series[field][index] = int(getattr(record, field) or 0)
        item = {
            "store_pk": shop.id,
            "name": shop.name or shop.shop_id or str(shop.id),
            "charge_name": "",
            **series,
        }
        for name in names:
            grouped.setdefault(name, []).append(dict(item))

    groups = []
    for account in sorted(grouped):
        shops_in_group = sorted(grouped[account], key=lambda item: (item["name"], item["store_pk"]))
        groups.append({"account": account, "shops": shops_in_group})
    return groups


def list_deals(db: Session, start: str = "", end: str = "") -> dict:
    ensure_deal_table()
    start_day, end_day, days = resolve_range(start, end)
    shops = list(db.scalars(select(Shop).order_by(Shop.name.asc(), Shop.id.asc())))
    rows = list(
        db.scalars(
            select(DyDealDay).where(DyDealDay.stat_date >= start_day, DyDealDay.stat_date <= end_day)
        )
    )
    groups = assemble(shops, rows, days)
    from app.services.shop_owner import names_by_store

    from app.services.violation_service import shop_states

    charges = names_by_store(db)
    states = shop_states(db)
    for group in groups:
        for shop in group["shops"]:
            store_pk = int(shop["store_pk"])
            label, due = states.get(store_pk, ("正常", ""))
            shop["charge_name"] = charges.get(store_pk, "")
            shop["shop_state"] = label
            shop["shop_state_due"] = due
    return {
        "start": start_day.isoformat(),
        "end": end_day.isoformat(),
        "days": [day.isoformat() for day in days],
        "groups": groups,
    }

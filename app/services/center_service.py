"""数据总览：每家店各自的罗盘首页收支概况。"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.shop import Shop
from app.services import fund_cache
from app.services.dy_client import FXG_HOST, ShopApiError, ShopAuthError, cookie_store, cookies_for
from app.services.dy_compass import (
    ProductPartial,
    fetch_financial_summary,
    fetch_operating_cards,
    fetch_product_list,
    fetch_promotion_window,
)
from app.services.fetch_pace import limited_shop_ids, merge_kept, prefer_limited, take_turns
from app.services.schedule_log import write_fail, write_success
from app.services.store_service import keep_shop_jar

_LOG = logging.getLogger("uvicorn.error")
_JOB = threading.Lock()
_PROGRESS = threading.Lock()
_PROGRESS_SLOT: dict[str, object] = {"running": False, "done": 0, "total": 0, "failed": 0, "message": ""}
_PENDING: dict[str, str] | None = None
_OVERVIEW_CACHE: dict[str, dict] = {}
_COMPASS_TURN = threading.Lock()
_DAILY_LOCK = threading.Lock()
_DAILY: dict[str, str] = {"last": ""}
_ORDER = (
    "结算金额",
    "退款率",
    "投放消耗",
    "综合费比",
    "成交金额",
    "用户支付金额",
    "智能优惠券",
    "平台补贴",
    "其他金额",
    "支出金额",
    "达人佣金",
    "平台佣金",
)
_LABEL_KEYS = ("index_display", "display_name", "index_show_name", "show_name", "title", "name")
_RATIO_KEYS = ("out_period_ratio", "last_period_ratio", "same_period_ratio", "last_period_change", "change_ratio", "ratio")
_BENCH_TITLES = ("同行基准", "同行标杆", "同行顶尖", "同行中间值")


def _replace_row(rows: list[dict], row: dict) -> None:
    pk = int(row.get("store_pk") or 0)
    for index, item in enumerate(rows):
        if int(item.get("store_pk") or 0) == pk:
            rows[index] = row
            return
    rows.append(row)


def progress() -> dict:
    with _PROGRESS:
        return dict(_PROGRESS_SLOT)


_PRODUCT_PRESETS = ("realtime", "1", "7", "30")


def _parse_day(value: str, label: str) -> date:
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ShopApiError(label) from exc


def _month_end(day: date) -> date:
    if day.month == 12:
        return date(day.year + 1, 1, 1) - timedelta(days=1)
    return date(day.year, day.month + 1, 1) - timedelta(days=1)


def _cap_closed_end(start_day: date, end_day: date) -> date:
    """自然周、自然月没过完时，结束日停在昨天。罗盘不接受还没到的日期。"""
    latest = date.today() - timedelta(days=1)
    if start_day > latest:
        latest = start_day
    if end_day > latest:
        return latest
    return end_day


def resolve_span(preset: str, start: str = "", end: str = "") -> tuple[int, date, date]:
    """页面时间 → 罗盘 date_type 和起止日期。近 1/7/30 天不含今天，截止到昨天。"""
    today = date.today()
    preset = (preset or "realtime").strip()
    if preset == "realtime":
        return 1, today, today
    if preset == "1":
        day = today - timedelta(days=1)
        return 20, day, day
    if preset == "7":
        end_day = today - timedelta(days=1)
        return 21, end_day - timedelta(days=6), end_day
    if preset == "30":
        end_day = today - timedelta(days=1)
        return 23, end_day - timedelta(days=29), end_day
    if preset == "day":
        anchor = _parse_day(start, "请选择自然日") if start.strip() else today - timedelta(days=1)
        if anchor > today or anchor < today - timedelta(days=180):
            raise ShopApiError("自然日只能选今天往前 180 天")
        return 2, anchor, anchor
    if preset == "week":
        anchor = _parse_day(start, "请选择自然周") if start.strip() else today - timedelta(days=1)
        if anchor > today or anchor < today - timedelta(days=180):
            raise ShopApiError("自然周只能选今天往前 180 天")
        monday = anchor - timedelta(days=anchor.weekday())
        return 3, monday, _cap_closed_end(monday, monday + timedelta(days=6))
    if preset == "month":
        anchor = _parse_day(start, "请选择自然月") if start.strip() else today - timedelta(days=1)
        if anchor > today or anchor < today - timedelta(days=180):
            raise ShopApiError("自然月只能选今天往前 180 天")
        first = anchor.replace(day=1)
        return 4, first, _cap_closed_end(first, _month_end(first))
    if preset == "promotion":
        return 100, today, today
    if preset == "custom":
        try:
            start_day = datetime.strptime(start.strip(), "%Y-%m-%d").date()
            end_day = datetime.strptime(end.strip(), "%Y-%m-%d").date()
        except ValueError as exc:
            raise ShopApiError("请选择开始和结束日期") from exc
        if start_day > end_day:
            raise ShopApiError("开始日期不能晚于结束日期")
        if end_day > today or start_day < today - timedelta(days=90):
            raise ShopApiError("自定义只能选今天和前 90 天")
        return 999, start_day, end_day
    raise ShopApiError("不支持的时间范围")


def _span_key(preset: str, start_day: date, end_day: date) -> str:
    return f"{preset}:{start_day.isoformat()}:{end_day.isoformat()}"


def _compass_time(day: date) -> str:
    return day.strftime("%Y/%m/%d 00:00:00")


def _number(node: Any) -> float | None:
    if isinstance(node, bool) or node is None:
        return None
    if isinstance(node, (int, float)):
        return float(node)
    if isinstance(node, str):
        text = node.replace(",", "").replace("%", "").replace("¥", "").strip()
        if not text or text in {"-", "—"}:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    if isinstance(node, dict) and "value" in node:
        return _number(node.get("value"))
    return None


def _label(node: dict) -> str:
    for key in _LABEL_KEYS:
        value = node.get(key)
        if isinstance(value, str) and value.strip() and not value.startswith("http"):
            return value.strip()[:40]
    return ""


def _unit(node: dict, key: str = "value") -> str:
    value = node.get(key)
    if isinstance(value, dict):
        return str(value.get("unit") or "")
    return str(node.get("unit") or "")


def _source(node: dict) -> dict:
    """指标名在外层，数值经常包在 index_values 里。"""
    inner = node.get("index_values")
    if not isinstance(inner, dict):
        return node
    if not any(key in inner for key in ("value", "out_period_ratio", "last_value", "benchmark")):
        return node
    merged = dict(inner)
    for key in _LABEL_KEYS:
        if key not in merged and key in node:
            merged[key] = node[key]
    return merged


def _is_metric(node: dict) -> bool:
    source = _source(node)
    if not _label(source):
        return False
    for key in ("value", "index_value", "current_value"):
        raw = source.get(key)
        if _number(raw) is not None:
            return True
        if isinstance(raw, dict) and (raw.get("value_str") or _number(raw.get("value")) is not None):
            return True
    return False


def _walk(node: Any, found: list[dict]) -> None:
    if isinstance(node, dict):
        if _is_metric(node):
            found.append(_source(node))
        for value in node.values():
            _walk(value, found)
    elif isinstance(node, list):
        for item in node:
            _walk(item, found)


def _money(number: float) -> str:
    sign = "-" if number < 0 else ""
    whole, dot, frac = f"{abs(number):.2f}".partition(".")
    whole = f"{int(whole):,}"
    return f"{sign}¥{whole}.{frac}"


def _percent(number: float) -> str:
    percent = number * 100 if abs(number) <= 2 else number
    if abs(percent - round(percent)) < 0.05:
        return f"{percent:+.0f}%"
    return f"{percent:+.2f}%"


def _value_text(label: str, number: float, unit: str) -> str:
    unit_name = unit.lower()
    if "ratio" in unit_name or "rate" in unit_name or "率" in label or "费比" in label:
        return _percent(number).replace("+", "")
    if unit_name in {"cent", "fen"}:
        return _money(number / 100)
    return _money(number)


def _bench_title(node: dict) -> str:
    extra = node.get("extra_value")
    if not isinstance(extra, dict):
        return _BENCH_TITLES[0]
    kind = _number(extra.get("benchmark_type"))
    if kind is None:
        return _BENCH_TITLES[0]
    index = int(kind)
    if 0 <= index < len(_BENCH_TITLES):
        return _BENCH_TITLES[index]
    return _BENCH_TITLES[0]


def _metric(node: dict, date_type: int) -> dict:
    label = _label(node)
    number = None
    unit = ""
    for key in ("value", "index_value", "current_value"):
        if key in node:
            number = _number(node.get(key))
            unit = _unit(node, key)
            break
    ratio = None
    for key in _RATIO_KEYS:
        if key in node:
            ratio = _number(node.get(key))
            if ratio is not None:
                break
    parts: list[str] = []
    if date_type == 1:
        previous = _number(node.get("last_value"))
        if previous is not None:
            parts.append("昨日 " + _value_text(label, previous, _unit(node, "last_value") or unit))
    benchmark = _number(node.get("benchmark"))
    if benchmark is None:
        benchmark = _number(node.get("benchmark_value"))
    if benchmark is not None:
        parts.append(_bench_title(node) + " " + _value_text(label, benchmark, _unit(node, "benchmark") or unit))
    return {
        "label": label,
        "value_text": "—" if number is None else _value_text(label, number, unit),
        "ratio_text": "" if ratio is None else _percent(ratio),
        "extra_text": " · ".join(parts),
    }


def _rank(label: str) -> int:
    for index, key in enumerate(_ORDER):
        if key in label:
            return index
    return len(_ORDER)


_CARD_LABELS = (
    ("income_amt", "结算金额"),
    ("refund_amt_rate", "退款率(支付时间)"),
    ("refund_amt_pay_time", "退款金额(支付时间)"),
    ("ad_cost", "投放消耗"),
    ("ad_expense_ratio_with_refund", "综合费比"),
    ("pay_amt", "成交金额"),
    ("pay_qc_plat_coupon_amt", "智能优惠券金额"),
    ("pay_plat_cost_amt", "平台补贴金额"),
    ("cost_amt", "支出金额"),
    ("real_commission", "达人佣金(预估)"),
    ("shop_serv_amt", "平台佣金(预估)"),
    ("saling_refund_amt_rate", "发货前退款率"),
    ("saled_and_return_refund_amt_rate", "退货退款率"),
)


def _cell_number(node: Any) -> tuple[float | None, str]:
    if not isinstance(node, dict):
        return None, ""
    unit = str(node.get("unit") or "")
    return _number(node.get("value")), unit


def _cell_text(label: str, number: float | None, unit: str) -> str:
    if number is None:
        return "—"
    if unit == "3":
        return _money(number / 100)
    if unit == "4" or "率" in label or "费比" in label:
        percent = number * 100 if abs(number) <= 2 else number
        if abs(percent - round(percent)) < 0.05:
            return f"{percent:.0f}%"
        return f"{percent:.2f}%"
    return _money(number)


def _card_metrics(data: dict, date_type: int) -> list[dict] | None:
    module = data.get("module_data") if isinstance(data, dict) else None
    summary = module.get("financial_summary") if isinstance(module, dict) else None
    card = summary.get("compass_general_multi_index_card_value") if isinstance(summary, dict) else None
    rows = card.get("data") if isinstance(card, dict) else None
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        return None
    source = rows[0]
    metrics = []
    for key, label in _CARD_LABELS:
        item = source.get(key)
        index = item.get("index_value") if isinstance(item, dict) else {}
        if not isinstance(index, dict):
            index = {}
        number, unit = _cell_number(index.get("value"))
        ratio, _ratio_unit = _cell_number(index.get("out_period_ratio"))
        parts = []
        if date_type == 1:
            previous, previous_unit = _cell_number(index.get("last_value"))
            if previous is not None:
                parts.append("昨日 " + _cell_text(label, previous, previous_unit or unit))
        benchmark, benchmark_unit = _cell_number(index.get("benchmark"))
        if benchmark is not None:
            parts.append("同行基准 " + _cell_text(label, benchmark, benchmark_unit or unit))
        metrics.append(
            {
                "label": label,
                "value_text": _cell_text(label, number, unit),
                "ratio_text": "" if ratio is None else _percent(ratio),
                "extra_text": " · ".join(parts),
            }
        )
    return metrics


def shape_metrics(data: dict, date_type: int = 1) -> list[dict]:
    card = _card_metrics(data, date_type)
    if card:
        return card
    found: list[dict] = []
    _walk(data, found)
    metrics: list[dict] = []
    seen: set[str] = set()
    for node in found:
        item = _metric(node, date_type)
        if not item["label"] or item["label"] in seen:
            continue
        seen.add(item["label"])
        metrics.append(item)
    metrics.sort(key=lambda item: _rank(item["label"]))
    return metrics


def _row(shop: Shop, metrics: list[dict] | None = None, error: str = "") -> dict:
    return {
        "store_pk": shop.id,
        "shop_id": shop.shop_id,
        "name": shop.name,
        "login_status": shop.login_status,
        "sync_error": error[:200],
        "metrics": metrics or [],
    }


def _pull_one(shop_id: int, date_type: int, start_day: date, end_day: date) -> dict:
    db = SessionLocal()
    shop: Shop | None = None
    try:
        shop = db.get(Shop, shop_id)
        if shop is None:
            return {}
        cookies = cookies_for(cookie_store(shop.cookie_data), FXG_HOST)
        if not cookies or shop.shop_cookie != 1:
            return _row(shop, error="还没有这家店的登录凭证")
        try:
            data = fetch_financial_summary(
                cookies,
                shop_id=str(shop.shop_id or ""),
                date_type=date_type,
                begin_date=_compass_time(start_day),
                end_date=_compass_time(end_day),
            )
            keep_shop_jar(shop, cookies)
            db.commit()
        except ShopAuthError as exc:
            return _row(shop, error=str(exc) or "登录已失效")
        except ShopApiError as exc:
            return _row(shop, error=exc.message or "罗盘接口失败")
        metrics = shape_metrics(data, date_type)
        if not metrics:
            _LOG.info("收支概况没有解析出指标，店铺 %s，字段 %s", shop.shop_id, list(data)[:12])
            return _row(shop, error="罗盘没有返回收支概况")
        return _row(shop, metrics)
    except Exception:
        _LOG.exception("拉取店铺 %s 收支概况失败", shop_id)
        if shop is not None:
            return _row(shop, error="暂时没拉到")
        return {"store_pk": shop_id, "name": "", "login_status": "", "sync_error": "暂时没拉到", "metrics": []}
    finally:
        db.close()


def _shops(db: Session) -> list[Shop]:
    return list(db.scalars(select(Shop).order_by(Shop.id.asc())))


def _overview_kind(key: str) -> str:
    return "center_overview:" + key


def _remember_overview(key: str, preset: str, date_type: int, start_day: date, end_day: date, items: list[dict]) -> None:
    payload = {
        "preset": preset,
        "date_type": date_type,
        "start": start_day.isoformat(),
        "end": end_day.isoformat(),
        "items": items,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _OVERVIEW_CACHE[key] = payload
    fund_cache.write_obj(_overview_kind(key), payload, _CACHE_TTL_SECONDS)


def _load_overview(key: str) -> dict | None:
    cached = _OVERVIEW_CACHE.get(key)
    if cached:
        return cached
    stored = fund_cache.read_obj(_overview_kind(key))
    if not isinstance(stored, dict):
        return None
    _OVERVIEW_CACHE[key] = stored
    return stored


def read_overview(db: Session, preset: str, start: str = "", end: str = "") -> dict:
    date_type, start_day, end_day = resolve_span(preset, start, end)
    key = _span_key(preset, start_day, end_day)
    payload = _load_overview(key) or {}
    return {
        "preset": preset,
        "date_type": date_type,
        "start": start_day.isoformat(),
        "end": end_day.isoformat(),
        "updated_at": str(payload.get("updated_at") or ""),
        "items": list(payload.get("items") or []),
    }


def _refresh(preset: str, start: str, end: str) -> None:
    date_type, start_day, end_day = resolve_span(preset, start, end)
    db = SessionLocal()
    try:
        shops = _shops(db)
    finally:
        db.close()
    with _PROGRESS:
        _PROGRESS_SLOT.update(running=True, done=0, total=len(shops), failed=0, message="")
    rows: list[dict] = []
    cache_key = _span_key(preset, start_day, end_day)
    previous = list((_load_overview(cache_key) or {}).get("items") or [])
    shop_ids = prefer_limited([shop.id for shop in shops], limited_shop_ids(previous))

    def pull(shop_id: int) -> dict:
        row = _pull_one(shop_id, date_type, start_day, end_day)
        with _PROGRESS:
            if row.get("store_pk"):
                _replace_row(rows, row)
            _PROGRESS_SLOT["done"] = min(int(_PROGRESS_SLOT["total"]), len(rows))
        return row

    try:
        if shops:
            take_turns(shop_ids, pull)
        rows = merge_kept(previous, rows)
        rows.sort(key=lambda item: int(item.get("store_pk") or 0))
        failed = sum(1 for row in rows if row.get("sync_error"))
        message = f"{len(shops) - failed} 家完成" + (f"，{failed} 家失败" if failed else "")
        _remember_overview(cache_key, preset, date_type, start_day, end_day, rows)
        with _PROGRESS:
            _PROGRESS_SLOT.update(done=len(shops), failed=failed, message=message or "完成")
    except Exception:
        _LOG.exception("数据总览更新失败")
        with _PROGRESS:
            _PROGRESS_SLOT.update(message="拉取过程出错了，请查看服务端日志")


def start_refresh(preset: str, start: str = "", end: str = "") -> dict:
    global _PENDING
    resolve_span(preset, start, end)

    def run(current: dict) -> None:
        global _PENDING
        try:
            while current:
                try:
                    _refresh(current["preset"], current["start"], current["end"])
                except Exception:
                    _LOG.exception("数据总览更新失败")
                    with _PROGRESS:
                        _PROGRESS_SLOT.update(message="拉取过程出错了，请查看服务端日志")
                with _PROGRESS:
                    current = _PENDING
                    _PENDING = None
                    if current:
                        _PROGRESS_SLOT.update(running=True, done=0, total=0, failed=0, message="")
                        continue
                    _PROGRESS_SLOT["running"] = False
                    _JOB.release()
                    return
        except Exception:
            _LOG.exception("数据总览更新失败")
            with _PROGRESS:
                _PROGRESS_SLOT.update(running=False, message="拉取过程出错了，请查看服务端日志")
            _JOB.release()

    if not _JOB.acquire(blocking=False):
        with _PROGRESS:
            _PENDING = {"preset": preset, "start": start, "end": end}
            _PROGRESS_SLOT["running"] = True
        if not _JOB.acquire(blocking=False):
            return progress()
        with _PROGRESS:
            queued = _PENDING
            _PENDING = None
        if not queued:
            _JOB.release()
            return progress()
        threading.Thread(target=run, args=(queued,), name="center-overview", daemon=True).start()
        return progress()
    with _PROGRESS:
        _PROGRESS_SLOT.update(running=True, done=0, total=0, failed=0, message="")
    threading.Thread(
        target=run,
        args=({"preset": preset, "start": start, "end": end},),
        name="center-overview",
        daemon=True,
    ).start()
    return progress()


_SHOP_KEYS = (
    ("income_amt", "pay_amount", "money"),
    ("pay_cnt", "order_count", "count"),
    ("refund_amt_rate", "refund_rate", "rate"),
    ("income_refund_amt", "refund_amount", "money"),
)
_SHOP_JOB = threading.Lock()
_SHOP_PROGRESS = threading.Lock()
_SHOP_PROGRESS_SLOT: dict[str, object] = {"running": False, "done": 0, "total": 0, "failed": 0, "message": ""}
_SHOP_PENDING: dict[str, str] | None = None
_SHOP_CACHE: dict[str, dict] = {}
_INTERVAL_MINUTES = 10
_CACHE_TTL_SECONDS = 7 * 24 * 3600
_SCHEDULE_LOCK = threading.Lock()
_SCHEDULE: dict[str, object] = {"interval_minutes": _INTERVAL_MINUTES, "last_update": "", "next_update": ""}


def shop_progress() -> dict:
    with _SHOP_PROGRESS:
        return dict(_SHOP_PROGRESS_SLOT)


def _operating_cells(data: dict) -> dict[str, dict]:
    found: dict[str, dict] = {}
    wanted = {key for key, _field, _kind in _SHOP_KEYS}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            extra = node.get("extra")
            code = extra.get("index_code") if isinstance(extra, dict) else ""
            info = node.get("cell_info")
            if code in wanted and isinstance(info, dict):
                values = info.get("index_value")
                inner = values.get("index_values") if isinstance(values, dict) else None
                if isinstance(inner, dict):
                    found[str(code)] = inner
                return
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(data)
    return found


def _operating_text(kind: str, node: Any, *, empty: str) -> str:
    number, unit = _cell_number(node)
    if number is None or unit == "6":
        return empty
    if kind == "money":
        return _money(number / 100)
    if kind == "rate":
        percent = number * 100 if abs(number) <= 2 else number
        if abs(percent - round(percent)) < 0.05:
            return f"{percent:.0f}%"
        return f"{percent:.2f}%"
    if number == int(number):
        return str(int(number))
    return f"{number:.2f}"


def shape_operating(data: dict, date_type: int) -> dict[str, str] | None:
    cells = _operating_cells(data)
    if not cells:
        return None
    shaped: dict[str, str] = {}
    for code, field, kind in _SHOP_KEYS:
        values = cells.get(code) or {}
        current = values.get("value")
        last = values.get("last_value")
        empty = "0" if kind == "count" else "—"
        shaped[field] = _operating_text(kind, current, empty=empty)
        if date_type == 1:
            shaped[field + "_extra"] = "昨日 " + _operating_text(kind, last, empty="—")
        else:
            shaped[field + "_extra"] = ""
    return shaped


def _shop_row(shop: Shop, metrics: dict[str, str] | None = None, error: str = "") -> dict:
    row = {
        "store_pk": shop.id,
        "shop_id": shop.shop_id,
        "name": shop.name,
        "login_status": shop.login_status,
        "sync_error": error[:200],
        "pay_amount": "",
        "pay_amount_extra": "",
        "order_count": "",
        "order_count_extra": "",
        "refund_rate": "",
        "refund_rate_extra": "",
        "refund_amount": "",
        "refund_amount_extra": "",
    }
    if metrics:
        row.update(metrics)
    return row


def _pull_shop(shop_id: int, date_type: int, start_day: date, end_day: date) -> dict:
    db = SessionLocal()
    shop: Shop | None = None
    try:
        shop = db.get(Shop, shop_id)
        if shop is None:
            return {}
        cookies = cookies_for(cookie_store(shop.cookie_data), FXG_HOST)
        if not cookies or shop.shop_cookie != 1:
            return _shop_row(shop, error="还没有这家店的登录凭证")
        try:
            data = fetch_operating_cards(
                cookies,
                shop_id=str(shop.shop_id or ""),
                date_type=date_type,
                begin_date=_compass_time(start_day),
                end_date=_compass_time(end_day),
            )
            keep_shop_jar(shop, cookies)
            db.commit()
        except ShopAuthError as exc:
            return _shop_row(shop, error=str(exc) or "登录已失效")
        except ShopApiError as exc:
            return _shop_row(shop, error=exc.message or "罗盘接口失败")
        metrics = shape_operating(data, date_type)
        if not metrics:
            return _shop_row(shop, error="罗盘没有返回经营概况")
        return _shop_row(shop, metrics)
    except Exception:
        _LOG.exception("拉取店铺 %s 经营概况失败", shop_id)
        if shop is not None:
            return _shop_row(shop, error="暂时没拉到")
        return {"store_pk": shop_id, "name": "", "login_status": "", "sync_error": "暂时没拉到"}
    finally:
        db.close()


def _shop_cache_kind(key: str) -> str:
    return "center_shop:" + key


def _remember_shops(key: str, preset: str, date_type: int, start_day: date, end_day: date, items: list[dict]) -> None:
    payload = {
        "preset": preset,
        "date_type": date_type,
        "start": start_day.isoformat(),
        "end": end_day.isoformat(),
        "items": items,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _SHOP_CACHE[key] = payload
    fund_cache.write_obj(_shop_cache_kind(key), payload, _CACHE_TTL_SECONDS)


def _load_shops(key: str) -> dict | None:
    cached = _SHOP_CACHE.get(key)
    if cached:
        return cached
    stored = fund_cache.read_obj(_shop_cache_kind(key))
    if not isinstance(stored, dict):
        return None
    _SHOP_CACHE[key] = stored
    return stored


def read_shops(db: Session, preset: str, start: str = "", end: str = "") -> dict:
    date_type, start_day, end_day = resolve_span(preset, start, end)
    key = _span_key(preset, start_day, end_day)
    payload = _load_shops(key) or {}
    items = list(payload.get("items") or [])
    data = {
        "preset": preset,
        "date_type": date_type,
        "start": start_day.isoformat(),
        "end": end_day.isoformat(),
        "items": items,
    }
    data.update(center_schedule())
    return data


def _refresh_shops(preset: str, start: str, end: str) -> None:
    date_type, start_day, end_day = resolve_span(preset, start, end)
    db = SessionLocal()
    try:
        shops = _shops(db)
    finally:
        db.close()
    with _SHOP_PROGRESS:
        _SHOP_PROGRESS_SLOT.update(running=True, done=0, total=len(shops), failed=0, message="")
    rows: list[dict] = []
    cache_key = _span_key(preset, start_day, end_day)
    previous = list((_load_shops(cache_key) or {}).get("items") or [])
    shop_ids = prefer_limited([shop.id for shop in shops], limited_shop_ids(previous))

    def pull(shop_id: int) -> dict:
        row = _pull_shop(shop_id, date_type, start_day, end_day)
        with _SHOP_PROGRESS:
            if row.get("store_pk"):
                _replace_row(rows, row)
            _SHOP_PROGRESS_SLOT["done"] = min(int(_SHOP_PROGRESS_SLOT["total"]), len(rows))
        return row

    try:
        if shops:
            take_turns(shop_ids, pull)
        rows = merge_kept(previous, rows)
        rows.sort(key=lambda item: int(item.get("store_pk") or 0))
        failed = sum(1 for row in rows if row.get("sync_error"))
        message = f"{len(shops) - failed} 家完成" + (f"，{failed} 家失败" if failed else "")
        _remember_shops(cache_key, preset, date_type, start_day, end_day, rows)
        with _SHOP_PROGRESS:
            _SHOP_PROGRESS_SLOT.update(done=len(shops), failed=failed, message=message or "完成")
    except Exception:
        _LOG.exception("店铺明细更新失败")
        with _SHOP_PROGRESS:
            _SHOP_PROGRESS_SLOT.update(message="拉取过程出错了，请查看服务端日志")


def start_shop_refresh(preset: str, start: str = "", end: str = "") -> dict:
    global _SHOP_PENDING
    resolve_span(preset, start, end)
    def run(current: dict) -> None:
        global _SHOP_PENDING
        try:
            while current:
                try:
                    _refresh_shops(current["preset"], current["start"], current["end"])
                except Exception:
                    _LOG.exception("店铺明细更新失败")
                    with _SHOP_PROGRESS:
                        _SHOP_PROGRESS_SLOT.update(message="拉取过程出错了，请查看服务端日志")
                with _SHOP_PROGRESS:
                    current = _SHOP_PENDING
                    _SHOP_PENDING = None
                    if current:
                        _SHOP_PROGRESS_SLOT.update(running=True, done=0, total=0, failed=0, message="")
                        continue
                    _SHOP_PROGRESS_SLOT["running"] = False
                    _SHOP_JOB.release()
                    return
        except Exception:
            _LOG.exception("店铺明细更新失败")
            with _SHOP_PROGRESS:
                _SHOP_PROGRESS_SLOT.update(running=False, message="拉取过程出错了，请查看服务端日志")
            _SHOP_JOB.release()

    if not _SHOP_JOB.acquire(blocking=False):
        with _SHOP_PROGRESS:
            _SHOP_PENDING = {"preset": preset, "start": start, "end": end}
            _SHOP_PROGRESS_SLOT["running"] = True
        if not _SHOP_JOB.acquire(blocking=False):
            return shop_progress()
        with _SHOP_PROGRESS:
            queued = _SHOP_PENDING
            _SHOP_PENDING = None
        if not queued:
            _SHOP_JOB.release()
            return shop_progress()
        threading.Thread(target=run, args=(queued,), name="center-shops", daemon=True).start()
        return shop_progress()
    with _SHOP_PROGRESS:
        _SHOP_PROGRESS_SLOT.update(running=True, done=0, total=0, failed=0, message="")

    threading.Thread(
        target=run,
        args=({"preset": preset, "start": start, "end": end},),
        name="center-shops",
        daemon=True,
    ).start()
    return shop_progress()


_PRODUCT_KEYS = (
    ("ad_receive_amt", "pay_amount", "money"),
    ("pay_cnt", "order_count", "count"),
    ("pay_refund_ratio", "refund_rate", "rate"),
    ("pay_refund_success_amt", "refund_amount", "money"),
    ("comment_good_eval_ratio", "good_rate", "rate"),
)
_PRODUCT_JOB = threading.Lock()
_PRODUCT_PROGRESS = threading.Lock()
_PRODUCT_PROGRESS_SLOT: dict[str, object] = {"running": False, "done": 0, "total": 0, "failed": 0, "message": ""}
_PRODUCT_PENDING: dict | None = None
_PRODUCT_CACHE: dict[str, dict] = {}


def product_progress() -> dict:
    with _PRODUCT_PROGRESS:
        return dict(_PRODUCT_PROGRESS_SLOT)


def _text_cell(node: Any) -> str:
    if not isinstance(node, dict):
        return ""
    value = node.get("value")
    if isinstance(value, dict):
        return str(value.get("value_str") or "")
    return ""


def _product_value(info: dict, code: str) -> dict:
    bucket = info.get(code)
    if not isinstance(bucket, dict):
        return {}
    wrapped = bucket.get(code + "_index_values")
    if not isinstance(wrapped, dict):
        return {}
    values = wrapped.get("index_values")
    return values if isinstance(values, dict) else {}


def _product_row(shop: Shop, info: dict | None = None, error: str = "") -> dict:
    product = info.get("product_info") if isinstance(info, dict) else None
    if not isinstance(product, dict):
        product = {}
    row = {
        "store_pk": shop.id,
        "shop_id": shop.shop_id,
        "name": shop.name,
        "login_status": shop.login_status,
        "sync_error": error[:200],
        "product_id": _text_cell(product.get("product_id_value")),
        "product_name": _text_cell(product.get("product_name_value")),
        "pay_amount": "",
        "order_count": "",
        "refund_rate": "",
        "refund_amount": "",
        "good_rate": "",
    }
    if error or not isinstance(info, dict):
        if not error and not row["product_name"]:
            row["product_name"] = "没有商品"
        return row
    for code, field, kind in _PRODUCT_KEYS:
        values = _product_value(info, code)
        if kind == "count":
            empty = "0"
        elif kind == "rate":
            empty = "0%"
        else:
            empty = "—"
        row[field] = _operating_text(kind, values.get("value"), empty=empty)
    return row


def _shape_product_rows(shop: Shop, raw_rows: list[dict]) -> list[dict]:
    shaped = []
    for raw in raw_rows:
        info = raw.get("cell_info") if isinstance(raw, dict) else None
        if isinstance(info, dict):
            shaped.append(_product_row(shop, info))
    if not shaped:
        return [_product_row(shop)]
    return shaped


def _kept_products(span_key: str, store_pk: int) -> tuple[int, list[dict]]:
    """上一轮停在某一页时，返回那一页和已经入库的商品。完整结果从第 1 页重拉。"""
    items = list((_load_products(span_key) or {}).get("items") or [])
    kept: list[dict] = []
    resume = 1
    for row in items:
        if not isinstance(row, dict):
            continue
        try:
            pk = int(row.get("store_pk") or 0)
        except (TypeError, ValueError):
            continue
        if pk != store_pk:
            continue
        try:
            page = int(row.get("resume_page") or 0)
        except (TypeError, ValueError):
            page = 0
        if page > 1:
            resume = page
            continue
        if row.get("sync_error") or not row.get("product_id"):
            continue
        kept.append(row)
    if resume <= 1:
        return 1, []
    return resume, kept


def _join_products(shop: Shop, kept: list[dict], raw_rows: list[dict]) -> list[dict]:
    fresh = [row for row in _shape_product_rows(shop, raw_rows) if row.get("product_id")] if raw_rows else []
    if kept:
        return kept + fresh
    if fresh:
        return fresh
    return _shape_product_rows(shop, [])


def _partial_rows(shop: Shop, kept: list[dict], exc: ProductPartial) -> list[dict]:
    fresh = [row for row in _shape_product_rows(shop, exc.rows) if row.get("product_id")] if exc.rows else []
    body = list(kept) + fresh
    marker = _product_row(shop, error=exc.message or "罗盘接口失败")
    if body and exc.page_no > 1:
        marker["resume_page"] = exc.page_no
        return body + [marker]
    return [marker]


def _pull_product_spans(shop_id: int, spans: list[dict]) -> dict[str, list[dict]]:
    """一家店一次进罗盘，把这一轮要的时间都拉完。登录失效后不再重复打。"""
    db = SessionLocal()
    shop: Shop | None = None
    empty = {span["key"]: [] for span in spans}
    try:
        shop = db.get(Shop, shop_id)
        if shop is None:
            return empty
        cookies = cookies_for(cookie_store(shop.cookie_data), FXG_HOST)
        if not cookies or shop.shop_cookie != 1:
            row = [_product_row(shop, error="还没有这家店的登录凭证")]
            return {span["key"]: row for span in spans}
        out: dict[str, list[dict]] = {}
        auth_error = ""
        touched = False
        for span in spans:
            if auth_error:
                out[span["key"]] = [_product_row(shop, error=auth_error)]
                continue
            start_page, kept = _kept_products(span["key"], shop.id)
            try:
                raw_rows = fetch_product_list(
                    cookies,
                    shop_id=str(shop.shop_id or ""),
                    date_type=int(span["date_type"]),
                    begin_date=_compass_time(span["start_day"]),
                    end_date=_compass_time(span["end_day"]),
                    activity_id=str(span.get("activity_id") or ""),
                    start_page=start_page,
                    already=len(kept),
                )
                touched = True
            except ShopAuthError as exc:
                auth_error = str(exc) or "登录已失效"
                out[span["key"]] = [_product_row(shop, error=auth_error)]
                continue
            except ProductPartial as exc:
                if exc.rows:
                    touched = True
                out[span["key"]] = _partial_rows(shop, kept if start_page > 1 else [], exc)
                continue
            except ShopApiError as exc:
                out[span["key"]] = [_product_row(shop, error=exc.message or "罗盘接口失败")]
                continue
            out[span["key"]] = _join_products(shop, kept if start_page > 1 else [], raw_rows)
        if touched:
            keep_shop_jar(shop, cookies)
            db.commit()
        return out
    except Exception:
        _LOG.exception("拉取店铺 %s 商品明细失败", shop_id)
        if shop is None:
            row = [{"store_pk": shop_id, "name": "", "login_status": "", "sync_error": "暂时没拉到"}]
        else:
            row = [_product_row(shop, error="暂时没拉到")]
        return {span["key"]: row for span in spans}
    finally:
        db.close()


def _promotion_window(shop_ids: list[int]) -> tuple[str, date, date] | None:
    """用第一家能读罗盘的店看当前大促。读不到就不拿别的时间冒充。"""
    if not shop_ids:
        return None
    db = SessionLocal()
    attempts = 0
    try:
        for shop_id in shop_ids:
            shop = db.get(Shop, shop_id)
            if shop is None:
                continue
            cookies = cookies_for(cookie_store(shop.cookie_data), FXG_HOST)
            if not cookies or shop.shop_cookie != 1:
                continue
            attempts += 1
            try:
                found = fetch_promotion_window(cookies, str(shop.shop_id or ""))
            except (ShopAuthError, ShopApiError):
                if attempts >= 3:
                    return None
                continue
            keep_shop_jar(shop, cookies)
            db.commit()
            return found
    finally:
        db.close()
    return None


def _product_cache_kind(key: str) -> str:
    return "center_product:" + key


def _remember_products(
    key: str,
    preset: str,
    date_type: int,
    start_day: date | None,
    end_day: date | None,
    items: list[dict],
    note: str = "",
) -> None:
    payload = {
        "preset": preset,
        "date_type": date_type,
        "start": start_day.isoformat() if start_day else "",
        "end": end_day.isoformat() if end_day else "",
        "items": items,
        "note": note[:80],
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _PRODUCT_CACHE[key] = payload
    fund_cache.write_obj(_product_cache_kind(key), payload, _CACHE_TTL_SECONDS)


def _load_products(key: str) -> dict | None:
    cached = _PRODUCT_CACHE.get(key)
    if cached:
        return cached
    stored = fund_cache.read_obj(_product_cache_kind(key))
    if not isinstance(stored, dict):
        return None
    _PRODUCT_CACHE[key] = stored
    return stored


def _product_store_key(preset: str, start_day: date, end_day: date) -> str:
    if preset == "promotion":
        return "promotion:current"
    return _span_key(preset, start_day, end_day)


def read_products(db: Session, preset: str, start: str = "", end: str = "") -> dict:
    preset = (preset or "7").strip()
    if preset == "promotion":
        payload = _load_products("promotion:current") or {}
        date_type = int(payload.get("date_type") or 100)
        start_text = str(payload.get("start") or "")
        end_text = str(payload.get("end") or "")
    else:
        date_type, start_day, end_day = resolve_span(preset, start, end)
        payload = _load_products(_span_key(preset, start_day, end_day)) or {}
        start_text = start_day.isoformat()
        end_text = end_day.isoformat()
    data = {
        "preset": preset,
        "date_type": date_type,
        "start": start_text,
        "end": end_text,
        "updated_at": str(payload.get("updated_at") or ""),
        "note": str(payload.get("note") or ""),
        "items": list(payload.get("items") or []),
    }
    data.update(center_schedule())
    return data


def _product_spans(jobs: list[dict], shop_ids: list[int]) -> list[dict]:
    ready: list[dict] = []
    for job in jobs:
        preset = str(job.get("preset") or "").strip()
        start = str(job.get("start") or "")
        end = str(job.get("end") or "")
        if preset == "promotion":
            try:
                window = _promotion_window(shop_ids)
            except Exception:
                _LOG.exception("读取大促活动失败")
                window = None
            if window is None:
                _remember_products("promotion:current", "promotion", 100, None, None, [], note="当前没有大促")
                continue
            activity_id, start_day, end_day = window
            ready.append({
                "key": "promotion:current",
                "preset": "promotion",
                "date_type": 100,
                "start_day": start_day,
                "end_day": end_day,
                "activity_id": activity_id,
            })
            continue
        date_type, start_day, end_day = resolve_span(preset, start, end)
        ready.append({
            "key": _product_store_key(preset, start_day, end_day),
            "preset": preset,
            "date_type": date_type,
            "start_day": start_day,
            "end_day": end_day,
            "activity_id": "",
        })
    return ready


def _refresh_product_job(jobs: list[dict]) -> None:
    db = SessionLocal()
    try:
        shops = _shops(db)
    finally:
        db.close()
    spans = _product_spans(jobs, [shop.id for shop in shops])
    with _PRODUCT_PROGRESS:
        _PRODUCT_PROGRESS_SLOT.update(running=True, done=0, total=len(shops), failed=0, message="")
    by_key: dict[str, dict[int, list[dict]]] = {}
    seen: set[int] = set()
    limited_ids: set[int] = set()
    for span in spans:
        limited_ids |= limited_shop_ids(list((_load_products(span["key"]) or {}).get("items") or []))
    shop_ids = prefer_limited([shop.id for shop in shops], limited_ids)

    gate = threading.Lock()

    def pull(shop_id: int) -> dict:
        grouped = _pull_product_spans(shop_id, spans) if spans else {}
        with gate:
            for key, rows in grouped.items():
                by_key.setdefault(key, {})[shop_id] = rows
            seen.add(shop_id)
            with _PRODUCT_PROGRESS:
                _PRODUCT_PROGRESS_SLOT["done"] = min(int(_PRODUCT_PROGRESS_SLOT["total"]), len(seen))
        return grouped

    try:
        if shops and spans:
            take_turns(shop_ids, pull)
        for span in spans:
            items: list[dict] = []
            shop_rows = by_key.get(span["key"]) or {}
            for shop in shops:
                items.extend(shop_rows.get(shop.id) or [])
            previous = list((_load_products(span["key"]) or {}).get("items") or [])
            _remember_products(
                span["key"],
                span["preset"],
                int(span["date_type"]),
                span["start_day"],
                span["end_day"],
                merge_kept(previous, items),
            )
        failed = 0
        for shop in shops:
            shop_failed = False
            for span in spans:
                rows = (by_key.get(span["key"]) or {}).get(shop.id) or []
                if any(row.get("sync_error") for row in rows):
                    shop_failed = True
                    break
            if shop_failed:
                failed += 1
        message = f"{len(shops) - failed} 家完成" + (f"，{failed} 家失败" if failed else "")
        with _PRODUCT_PROGRESS:
            _PRODUCT_PROGRESS_SLOT.update(done=len(shops), failed=failed, message=message or "完成")
    except Exception:
        _LOG.exception("商品明细更新失败")
        with _PRODUCT_PROGRESS:
            _PRODUCT_PROGRESS_SLOT.update(message="拉取过程出错了，请查看服务端日志")


def _enqueue_product_job(jobs: list[dict]) -> dict:
    global _PRODUCT_PENDING
    for job in jobs:
        resolve_span(str(job.get("preset") or ""), str(job.get("start") or ""), str(job.get("end") or ""))

    def run(current: dict) -> None:
        global _PRODUCT_PENDING
        try:
            while current:
                try:
                    _refresh_product_job(list(current.get("spans") or []))
                except Exception:
                    _LOG.exception("商品明细更新失败")
                    with _PRODUCT_PROGRESS:
                        _PRODUCT_PROGRESS_SLOT.update(message="拉取过程出错了，请查看服务端日志")
                with _PRODUCT_PROGRESS:
                    current = _PRODUCT_PENDING
                    _PRODUCT_PENDING = None
                    if current:
                        _PRODUCT_PROGRESS_SLOT.update(running=True, done=0, total=0, failed=0, message="")
                        continue
                    _PRODUCT_PROGRESS_SLOT["running"] = False
                    _PRODUCT_JOB.release()
                    return
        except Exception:
            _LOG.exception("商品明细更新失败")
            with _PRODUCT_PROGRESS:
                _PRODUCT_PROGRESS_SLOT.update(running=False, message="拉取过程出错了，请查看服务端日志")
            _PRODUCT_JOB.release()

    payload = {"spans": jobs}
    if not _PRODUCT_JOB.acquire(blocking=False):
        with _PRODUCT_PROGRESS:
            _PRODUCT_PENDING = payload
            _PRODUCT_PROGRESS_SLOT["running"] = True
        if not _PRODUCT_JOB.acquire(blocking=False):
            return product_progress()
        with _PRODUCT_PROGRESS:
            queued = _PRODUCT_PENDING
            _PRODUCT_PENDING = None
        if not queued:
            _PRODUCT_JOB.release()
            return product_progress()
        threading.Thread(target=run, args=(queued,), name="center-products", daemon=True).start()
        return product_progress()
    with _PRODUCT_PROGRESS:
        _PRODUCT_PROGRESS_SLOT.update(running=True, done=0, total=0, failed=0, message="")
    threading.Thread(target=run, args=(payload,), name="center-products", daemon=True).start()
    return product_progress()


def start_product_refresh(preset: str, start: str = "", end: str = "") -> dict:
    return _enqueue_product_job([{"preset": preset, "start": start, "end": end}])


def start_product_defaults() -> dict:
    """10 分钟任务刷新商品明细的实时、近 1 天、近 7 天、近 30 天。一家店只进一次罗盘。"""
    return _enqueue_product_job([{"preset": preset, "start": "", "end": ""} for preset in _PRODUCT_PRESETS])


def touch_daily() -> str:
    """数据总览、店铺首页刚写完一轮时记一下，页面据此重新读缓存，不展示这个时间。"""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _DAILY_LOCK:
        _DAILY["last"] = stamp
    fund_cache.write_obj("center_daily", {"last": stamp}, _CACHE_TTL_SECONDS)
    return stamp


def center_schedule() -> dict:
    with _SCHEDULE_LOCK:
        data = dict(_SCHEDULE)
    data["interval_minutes"] = _INTERVAL_MINUTES
    data["last_update"] = str(data.get("last_update") or "")
    data["next_update"] = str(data.get("next_update") or "")
    data["shops_running"] = bool(shop_progress().get("running"))
    data["products_running"] = bool(product_progress().get("running"))
    data["overview_running"] = bool(progress().get("running"))
    with _DAILY_LOCK:
        data["daily_stamp"] = str(_DAILY.get("last") or "")
    return data


def overview_cache_fresh(seconds: int = 600) -> bool:
    """数据总览四个时间窗口的缓存是否都在指定秒数内。"""
    for preset in ("realtime", "1", "7", "30"):
        try:
            _date_type, start_day, end_day = resolve_span(preset)
        except Exception:
            return False
        payload = fund_cache.read_obj(_overview_kind(_span_key(preset, start_day, end_day))) or {}
        if not fund_cache.stamp_fresh(payload.get("updated_at"), seconds):
            return False
    return True


def center_cache_fresh(seconds: int = 600) -> bool:
    """店铺明细和商品明细这一轮是否在指定秒数内写完。"""
    stored = fund_cache.read_obj("center_schedule") or {}
    return fund_cache.stamp_fresh(stored.get("last_update"), seconds)


def load_center_cache() -> None:
    """进程刚起来时先把 Redis 里的数据总览、店铺明细、商品明细读回来，页面不用等这一轮拉完。"""
    for preset in ("realtime", "1", "7", "30"):
        try:
            _date_type, start_day, end_day = resolve_span(preset)
            _load_overview(_span_key(preset, start_day, end_day))
            _load_shops(_span_key(preset, start_day, end_day))
        except Exception:
            _LOG.exception("读取数据总览或店铺明细缓存失败 %s", preset)
    for preset in _PRODUCT_PRESETS:
        try:
            _date_type, start_day, end_day = resolve_span(preset)
            _load_products(_span_key(preset, start_day, end_day))
        except Exception:
            _LOG.exception("读取商品明细缓存失败 %s", preset)
    stored = fund_cache.read_obj("center_schedule")
    if isinstance(stored, dict):
        with _SCHEDULE_LOCK:
            _SCHEDULE.update(stored)
    daily = fund_cache.read_obj("center_daily")
    if isinstance(daily, dict):
        with _DAILY_LOCK:
            _DAILY["last"] = str(daily.get("last") or "")


def _wait_idle(reader) -> None:
    while reader().get("running"):
        time.sleep(0.5)


def run_center_cycle() -> None:
    """先更新店铺明细的四个固定时间，再更新商品明细的四个固定时间，然后把下次时间写进 Redis。"""
    _COMPASS_TURN.acquire()
    try:
        for preset in ("realtime", "1", "7", "30"):
            start_shop_refresh(preset)
            _wait_idle(shop_progress)
        start_product_defaults()
        _wait_idle(product_progress)
        finished = datetime.now()
        nxt = finished + timedelta(minutes=_INTERVAL_MINUTES)
        payload = {
            "interval_minutes": _INTERVAL_MINUTES,
            "last_update": finished.strftime("%Y-%m-%d %H:%M:%S"),
            "next_update": nxt.strftime("%Y-%m-%d %H:%M"),
        }
        with _SCHEDULE_LOCK:
            _SCHEDULE.update(payload)
        fund_cache.write_obj("center_schedule", payload, _CACHE_TTL_SECONDS)
        _LOG.info("店铺明细和商品明细四个时间下次更新 %s", payload["next_update"])
    finally:
        _COMPASS_TURN.release()


def run_overview_cycle() -> dict:
    """数据总览的实时、近 1 天、近 7 天、近 30 天写入 Redis。和店铺明细错开，不同时打罗盘。"""
    _COMPASS_TURN.acquire()
    failed = 0
    try:
        for preset in ("realtime", "1", "7", "30"):
            start_refresh(preset)
            _wait_idle(progress)
            failed += int(progress().get("failed") or 0)
            touch_daily()
    finally:
        _COMPASS_TURN.release()
    message = "数据总览已写入 Redis" + (f"，{failed} 家次没有拉到" if failed else "")
    if failed:
        write_fail("数据总览", message)
    else:
        write_success(message)
    _LOG.info(message)
    return {"failed": failed, "message": message}

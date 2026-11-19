"""账单赔付调用的抖店接口。

赔付账单页（/ffa/maftersale/fund/pay）用 POST /aftersale/compensate/list。
动账金额 amount_num 是分，出账为负数。
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import httpx

from app.services.dy_client import (
    ShopApiError,
    ShopAuthError,
    _CLIENT,
    _HOST,
    _UA,
    _cookies,
    absorb_cookies,
)
from app.services.fetch_pace import call_paced

COMPENSATE_LIST_PATH = "/aftersale/compensate/list"
_REFERER = "https://fxg.jinritemai.com/ffa/maftersale/fund/pay"
_PAGE_SIZE = 50
_PAGE_CAP = 40


def _amount_fen(value: Any) -> int:
    """整数按分；带小数点的按元换成分。"""
    if value in (None, ""):
        return 0
    if isinstance(value, str):
        raw = value.replace(",", "").replace("¥", "").strip()
        if not raw:
            return 0
        if "." in raw:
            return int(round(float(raw) * 100))
        return int(raw)
    if isinstance(value, float) and not value.is_integer():
        return int(round(value * 100))
    return int(value)


def _unix_at(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    if number > 10**12:
        number //= 1000
    if number < 10**9:
        return None
    return datetime.fromtimestamp(number)


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _post_list(cookies: dict[str, str], body: dict) -> tuple[list[dict], int]:
    def once() -> tuple[list[dict], int]:
        try:
            response = _CLIENT.post(
                _HOST + COMPENSATE_LIST_PATH,
                json=body,
                cookies=_cookies(cookies),
                headers={
                    "User-Agent": _UA,
                    "Referer": _REFERER,
                    "Accept": "application/json, text/plain, */*",
                },
            )
        except httpx.HTTPError as exc:
            raise ShopApiError("抖店暂时连不上") from exc
        if response.status_code in {401, 403}:
            raise ShopAuthError("登录已失效")
        fresh = absorb_cookies(cookies, response)
        cookies.clear()
        cookies.update(fresh)
        try:
            payload = response.json()
        except ValueError as exc:
            raise ShopApiError("抖店返回了无法识别的内容") from exc
        if not isinstance(payload, dict):
            raise ShopApiError("抖店返回了无法识别的内容")
        try:
            code = int(payload.get("code") or 0)
        except (TypeError, ValueError):
            code = -1
        message = str(payload.get("msg") or payload.get("description") or "")
        if code in {401, 10008} or (code not in {0} and "登录" in message):
            raise ShopAuthError(message or "登录已失效")
        if code not in {0}:
            raise ShopApiError((message or "抖店接口失败")[:200])
        data = payload.get("data")
        rows: list = data if isinstance(data, list) else []
        total_raw = payload.get("total")
        if isinstance(data, dict):
            for key in ("list", "records", "items", "data"):
                if isinstance(data.get(key), list):
                    rows = data[key]
                    break
            if total_raw in (None, ""):
                total_raw = data.get("total")
        try:
            total = int(total_raw or 0)
        except (TypeError, ValueError):
            total = 0
        clean = [row for row in rows if isinstance(row, dict)]
        return clean, total or len(clean)

    return call_paced(once)


def _shape(item: dict) -> dict | None:
    compensate_id = _text(item.get("compensate_id"), 64)
    if not compensate_id:
        return None
    return {
        "compensate_id": compensate_id,
        "finished_at": _unix_at(item.get("finish_time")),
        "scene": _text(item.get("compensate_scene"), 100),
        "recipient": _text(item.get("recipient_type"), 50),
        "direction": _text(item.get("account_trend"), 20),
        "amount_fen": _amount_fen(item.get("amount_num")),
        "compensator": _text(item.get("compensator_name"), 50),
        "account_name": _text(item.get("account_type_name"), 50),
        "reason": _text(item.get("biz_type_name"), 1000),
        "shop_order_id": _text(item.get("shop_order_id"), 64),
        "order_id": _text(item.get("order_id"), 64),
        "aftersale_id": _text(item.get("aftersale_id"), 64),
    }


def fetch_compensate_list(cookies: dict[str, str], start_ts: int, end_ts: int) -> list[dict]:
    """按动账时间拉一家店的赔付流水。"""
    rows: list[dict] = []
    seen: set[str] = set()
    page = 1
    total = 0
    while page <= _PAGE_CAP:
        batch, total = _post_list(
            cookies,
            {
                "account_start_time": start_ts,
                "account_end_time": end_ts,
                "page": page,
                "pageSize": _PAGE_SIZE,
            },
        )
        if not batch:
            break
        for item in batch:
            shaped = _shape(item)
            if shaped is None or shaped["compensate_id"] in seen:
                continue
            seen.add(shaped["compensate_id"])
            rows.append(shaped)
        if len(batch) < _PAGE_SIZE or (total and len(rows) >= total):
            break
        page += 1
        time.sleep(0.4)
    return rows

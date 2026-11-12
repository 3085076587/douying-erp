"""抖店发票数量。

我给平台开票：待开票账单、处理中、历史处理记录。数量按开票主体查。
给消费者开票：待开票。
"""
from __future__ import annotations

from urllib.parse import quote

import httpx

from app.services.dy_client import ShopApiError, ShopAuthError, _CLIENT, _HOST, _UA, _cookies
from app.services.fetch_pace import call_paced

_PLATFORM_REFERER = f"{_HOST}/ffa/m-invoice/merchant-invoice"
_CONSUMER_REFERER = f"{_HOST}/ffa/morder/receipt/list"


def _count(value: object) -> int:
    if value in (None, ""):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _get(cookies: dict[str, str], path: str, referer: str) -> dict:
    def once() -> dict:
        try:
            response = _CLIENT.get(
                _HOST + path,
                cookies=_cookies(cookies),
                headers={
                    "User-Agent": _UA,
                    "Referer": referer,
                    "Accept": "application/json, text/plain, */*",
                },
            )
        except httpx.HTTPError as exc:
            raise ShopApiError("抖店暂时连不上") from exc
        if response.status_code in {401, 403}:
            raise ShopAuthError("登录已失效")
        fresh = dict(cookies)
        for key, value in response.cookies.items():
            if key and value:
                fresh[key] = value
        cookies.clear()
        cookies.update(fresh)
        try:
            body = response.json()
        except ValueError as exc:
            raise ShopApiError("抖店返回了无法识别的内容") from exc
        if not isinstance(body, dict):
            raise ShopApiError("抖店返回了无法识别的内容")
        return body

    return call_paced(once)


def _subject_codes(cookies: dict[str, str]) -> list[str]:
    """开票主体。不带主体时，待开票账单会是 0。"""
    body = _get(cookies, "/api/ecomfinance/subject/list", _PLATFORM_REFERER)
    code = str(body.get("ret_code") or body.get("RetCode") or "")
    if code not in {"0000", "0"}:
        raise ShopApiError(str(body.get("ret_message") or body.get("RetMessage") or "开票主体没拉到")[:200])
    data = body.get("ret_data") if isinstance(body.get("ret_data"), dict) else body.get("RetData")
    items = data.get("subject_list") or data.get("SubjectList") or [] if isinstance(data, dict) else []
    codes: list[str] = []
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            credit = str(item.get("CreditCode") or item.get("credit_code") or "").strip()
            if credit and credit not in codes:
                codes.append(credit)
    return codes


def _platform_counts(cookies: dict[str, str], credit_code: str) -> dict[str, int]:
    path = "/api/ecomfinance/invoicerecord/statuscount"
    if credit_code:
        path += "?CreditCode=" + quote(credit_code, safe="")
    body = _get(cookies, path, _PLATFORM_REFERER)
    if str(body.get("RetCode") or "") not in {"0000", "0"}:
        raise ShopApiError(str(body.get("RetMessage") or "平台发票数量没拉到")[:200])
    data = body.get("RetData") if isinstance(body.get("RetData"), dict) else {}
    return {
        "platform_pending": _count(data.get("UninvoicedCount")),
        "platform_processing": _count(data.get("ProcessingCount")),
        "platform_finished": _count(data.get("FinishedCount")),
    }


def fetch_platform_invoice_counts(cookies: dict[str, str]) -> dict[str, int]:
    """我给平台开票。待开票账单、处理中、历史处理记录，按开票主体相加。"""
    codes = _subject_codes(cookies) or [""]
    pending = processing = finished = 0
    for credit_code in codes:
        counts = _platform_counts(cookies, credit_code)
        pending += counts["platform_pending"]
        processing += counts["platform_processing"]
        finished += counts["platform_finished"]
    return {
        "platform_pending": pending,
        "platform_processing": processing,
        "platform_finished": finished,
    }


def fetch_consumer_invoice_counts(cookies: dict[str, str]) -> dict[str, int]:
    """给消费者开票。待开票用列表页角标上的 total_wait_count。"""
    body = _get(cookies, "/order/invoice/get_counts", _CONSUMER_REFERER)
    code = body.get("code", body.get("st", body.get("errno", 0)))
    try:
        code_no = int(code or 0)
    except (TypeError, ValueError):
        code_no = -1
    if code_no not in {0}:
        raise ShopApiError(str(body.get("msg") or body.get("message") or "消费者发票数量没拉到")[:200])
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    if "total_wait_count" in data:
        pending = _count(data.get("total_wait_count"))
    else:
        pending = _count(data.get("wait_blue")) + _count(data.get("wait_red"))
    return {"consumer_pending": pending}

"""罗盘首页「收支概况」。金额按罗盘原样返回，这里不改单位。"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any

import httpx

from app.services.dy_client import ShopApiError, ShopAuthError, _cookies, enter_listed_shop
from app.services.fetch_pace import call_paced

_HOST = "https://compass.jinritemai.com"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _session_shop_id(cookies: dict[str, str]) -> str:
    for name in ("ecom_gray_shop_id", "SHOP_ID", "shop_id"):
        value = str(cookies.get(name) or "").strip()
        if value.isdigit():
            return value
    return ""


def _already_in_shop(cookies: dict[str, str], shop_id: str) -> bool:
    wanted = str(shop_id or "").strip()
    return bool(wanted) and _session_shop_id(cookies) == wanted


def _enter_compass(cookies: dict[str, str], shop_id: str) -> None:
    """插件交来的凭证已经停在这家店，直接用。选店会话才按名单进店。"""
    if _already_in_shop(cookies, shop_id):
        return
    enter_listed_shop(cookies, shop_id, host="compass.jinritemai.com", login_source="compass")


def _unwrap(payload: Any) -> dict:
    if not isinstance(payload, dict):
        raise ShopApiError("罗盘返回了无法识别的内容")
    code = payload.get("st", payload.get("code", payload.get("error_code", 0)))
    try:
        code_no = int(code or 0)
    except (TypeError, ValueError):
        code_no = -1
    message = str(payload.get("msg") or payload.get("message") or payload.get("description") or "")
    if code_no not in {0}:
        if code_no in {401, 10008} or "登录" in message:
            raise ShopAuthError(message or "登录已失效")
        raise ShopApiError((message or "罗盘接口失败")[:200])
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


def _read_card(
    cookies: dict[str, str],
    *,
    path: str,
    date_type: int,
    begin_date: str,
    end_date: str,
) -> dict:
    def once() -> dict:
        try:
            response = httpx.get(
                f"{_HOST}{path}",
                params={
                    "date_type": str(date_type),
                    "begin_date": begin_date,
                    "end_date": end_date,
                    "activity_id": "",
                    "is_activity": "false",
                },
                cookies=_cookies(cookies),
                headers={
                    "User-Agent": _UA,
                    "Referer": f"{_HOST}/shop?dateType={date_type}",
                    "Accept": "application/json, text/plain, */*",
                },
                timeout=30,
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            raise ShopApiError("罗盘暂时连不上") from exc
        if response.status_code in {401, 403}:
            raise ShopAuthError("登录已失效")
        fresh = dict(cookies)
        for key, value in response.cookies.items():
            if key and value:
                fresh[key] = value
        cookies.clear()
        cookies.update(fresh)
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise ShopApiError("罗盘返回了无法识别的内容") from exc
        return _unwrap(payload)

    return call_paced(once)


def _get(
    cookies: dict[str, str],
    *,
    shop_id: str,
    path: str,
    date_type: int,
    begin_date: str,
    end_date: str,
) -> dict:
    """已经在这家店里就直接读。读的时候登录失效，再尝试按名单进一次。"""
    entered = not _already_in_shop(cookies, shop_id)
    if entered:
        enter_listed_shop(cookies, shop_id, host="compass.jinritemai.com", login_source="compass")
    try:
        return _read_card(
            cookies,
            path=path,
            date_type=date_type,
            begin_date=begin_date,
            end_date=end_date,
        )
    except ShopAuthError as first:
        if entered:
            raise
        try:
            enter_listed_shop(cookies, shop_id, host="compass.jinritemai.com", login_source="compass")
        except ShopApiError as exc:
            if "登录名单里没有这家店" in (exc.message or ""):
                raise first from exc
            raise
        return _read_card(
            cookies,
            path=path,
            date_type=date_type,
            begin_date=begin_date,
            end_date=end_date,
        )


def fetch_financial_summary(
    cookies: dict[str, str],
    *,
    shop_id: str,
    date_type: int,
    begin_date: str,
    end_date: str,
) -> dict:
    """罗盘首页收支概况。"""
    return _get(
        cookies,
        shop_id=shop_id,
        path="/compass_api/shop/common/homepage/financial_summary",
        date_type=date_type,
        begin_date=begin_date,
        end_date=end_date,
    )


def fetch_operating_cards(
    cookies: dict[str, str],
    *,
    shop_id: str,
    date_type: int,
    begin_date: str,
    end_date: str,
) -> dict:
    """罗盘首页经营概况的指标目录，含成交金额、成交订单数、退款率、成交退款金额。"""
    return _get(
        cookies,
        shop_id=shop_id,
        path="/compass_api/shop/common/ai_homepage/shop_data/index_card_list",
        date_type=date_type,
        begin_date=begin_date,
        end_date=end_date,
    )


_PRODUCT_INDEXES = ",".join(
    (
        "ad_receive_amt",
        "pay_cnt",
        "pay_refund_ratio",
        "pay_refund_success_amt",
        "comment_good_eval_ratio",
    )
)
_PRODUCT_PAGE_SIZE = 10
_PRODUCT_PAGE_CAP = 40


def _checked(payload: Any) -> dict:
    if not isinstance(payload, dict):
        raise ShopApiError("罗盘返回了无法识别的内容")
    code = payload.get("st", payload.get("code", payload.get("error_code", 0)))
    try:
        code_no = int(code or 0)
    except (TypeError, ValueError):
        code_no = -1
    message = str(payload.get("msg") or payload.get("message") or payload.get("description") or "")
    if code_no not in {0}:
        if code_no in {401, 10008} or "登录" in message:
            raise ShopAuthError(message or "登录已失效")
        raise ShopApiError((message or "罗盘接口失败")[:200])
    return payload


_PROMOTION_PATHS = (
    "/compass_api/config_center/activity/activity_list",
    "/compass_api/shop/common/activity_list",
    "/compass_api/config_center/shop/activity_list",
)


def _as_day(value: Any) -> date | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 10_000_000_000:
            number = number / 1000
        if number < 1_000_000_000:
            return None
        try:
            return datetime.fromtimestamp(number).date()
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip().replace("/", "-")[:10]
        try:
            return datetime.strptime(text, "%Y-%m-%d").date()
        except ValueError:
            return None
    if isinstance(value, dict) and "value" in value:
        return _as_day(value.get("value"))
    return None


def _collect_promotions(node: Any, found: list[tuple[str, date, date]]) -> None:
    if isinstance(node, dict):
        activity_id = ""
        for key in ("activity_id", "project_id"):
            raw = node.get(key)
            text = str(raw or "").strip()
            if text.isdigit() and text != "0":
                activity_id = text
                break
        start_day = None
        end_day = None
        for key in ("begin_date", "start_date", "start_time", "begin_time"):
            start_day = _as_day(node.get(key))
            if start_day:
                break
        for key in ("end_date", "end_time"):
            end_day = _as_day(node.get(key))
            if end_day:
                break
        if activity_id and start_day and end_day and start_day <= end_day:
            found.append((activity_id, start_day, end_day))
        for value in node.values():
            _collect_promotions(value, found)
        return
    if isinstance(node, list):
        for item in node:
            _collect_promotions(item, found)


def _choose_promotion(found: list[tuple[str, date, date]]) -> tuple[str, date, date] | None:
    if not found:
        return None
    today = date.today()
    current = [item for item in found if item[1] <= today <= item[2]]
    if current:
        current.sort(key=lambda item: item[1], reverse=True)
        return current[0]
    recent = [item for item in found if item[2] >= today - timedelta(days=14)]
    if not recent:
        return None
    recent.sort(key=lambda item: item[2], reverse=True)
    return recent[0]


def _read_promotion(cookies: dict[str, str], path: str) -> tuple[str, date, date] | None:
    def once() -> tuple[str, date, date] | None:
        try:
            response = httpx.get(
                f"{_HOST}{path}",
                cookies=_cookies(cookies),
                headers={
                    "User-Agent": _UA,
                    "Referer": f"{_HOST}/shop/commodity/product-list",
                    "Accept": "application/json, text/plain, */*",
                },
                timeout=20,
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            raise ShopApiError("罗盘暂时连不上") from exc
        if response.status_code in {401, 403}:
            raise ShopAuthError("登录已失效")
        fresh = dict(cookies)
        for key, value in response.cookies.items():
            if key and value:
                fresh[key] = value
        cookies.clear()
        cookies.update(fresh)
        if response.status_code != 200:
            return None
        try:
            body = response.json()
        except json.JSONDecodeError:
            return None
        if not isinstance(body, dict):
            return None
        code = body.get("st", body.get("code", body.get("error_code", 0)))
        try:
            code_no = int(code or 0)
        except (TypeError, ValueError):
            return None
        message = str(body.get("msg") or body.get("message") or "")
        if code_no in {401, 10008} or "登录" in message:
            raise ShopAuthError(message or "登录已失效")
        if code_no not in {0}:
            if "频繁" in message:
                raise ShopApiError(message)
            return None
        found: list[tuple[str, date, date]] = []
        _collect_promotions(body.get("data", body), found)
        return _choose_promotion(found)

    try:
        return call_paced(once)
    except ShopApiError:
        return None


def fetch_promotion_window(cookies: dict[str, str], shop_id: str) -> tuple[str, date, date] | None:
    """当前或刚结束的大促。接口没有活动时返回空，不拿别的时间顶上。"""
    _enter_compass(cookies, shop_id)
    for path in _PROMOTION_PATHS:
        window = _read_promotion(cookies, path)
        if window:
            return window
    return None


def _product_page(
    cookies: dict[str, str],
    *,
    date_type: int,
    begin_date: str,
    end_date: str,
    page_no: int,
    activity_id: str = "",
) -> dict:
    """拉一页商品。太频繁时由全局限速再试，不把半截页写进结果。"""

    def once() -> dict:
        try:
            response = httpx.get(
                f"{_HOST}/compass_api/shop/product/product/product_list",
                params={
                    "date_type": str(date_type),
                    "begin_date": begin_date,
                    "end_date": end_date,
                    "activity_id": activity_id or "",
                    "is_activity": "true" if activity_id else "false",
                    "page_no": str(page_no),
                    "page_size": str(_PRODUCT_PAGE_SIZE),
                    "sort_field": "ad_receive_amt",
                    "is_asc": "false",
                    "index_selected": _PRODUCT_INDEXES,
                    "sale_type": "1",
                    "content_type": "1",
                    "product_tab": "0",
                    "only_abnormal": "false",
                    "new_version": "true",
                },
                cookies=_cookies(cookies),
                headers={
                    "User-Agent": _UA,
                    "Referer": f"{_HOST}/shop/commodity/product-list",
                    "Accept": "application/json, text/plain, */*",
                },
                timeout=30,
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            raise ShopApiError("罗盘暂时连不上") from exc
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
        except json.JSONDecodeError as exc:
            raise ShopApiError("罗盘返回了无法识别的内容") from exc
        return _checked(body)

    return call_paced(once)


class ProductPartial(ShopApiError):
    """翻页中途失败。rows 是这一轮已经拿到的页，page_no 是没拿到的那一页。"""

    def __init__(self, message: str, rows: list[dict], page_no: int):
        super().__init__(message)
        self.rows = rows
        self.page_no = page_no


def fetch_product_list(
    cookies: dict[str, str],
    *,
    shop_id: str,
    date_type: int,
    begin_date: str,
    end_date: str,
    activity_id: str = "",
    start_page: int = 1,
    already: int = 0,
) -> list[dict]:
    """商品列表明细。只取成交金额、成交订单数、退款率、成交退款金额、评价好评率。"""
    _enter_compass(cookies, shop_id)
    try:
        return _collect_products(
            cookies,
            date_type=date_type,
            begin_date=begin_date,
            end_date=end_date,
            activity_id=activity_id,
            start_page=start_page,
            already=already,
        )
    except ShopAuthError as first:
        if not _already_in_shop(cookies, shop_id):
            raise
        try:
            enter_listed_shop(cookies, shop_id, host="compass.jinritemai.com", login_source="compass")
        except ShopApiError as exc:
            if "登录名单里没有这家店" in (exc.message or ""):
                raise first from exc
            raise
        return _collect_products(
            cookies,
            date_type=date_type,
            begin_date=begin_date,
            end_date=end_date,
            activity_id=activity_id,
            start_page=start_page,
            already=already,
        )


def _collect_products(
    cookies: dict[str, str],
    *,
    date_type: int,
    begin_date: str,
    end_date: str,
    activity_id: str = "",
    start_page: int = 1,
    already: int = 0,
) -> list[dict]:
    rows: list[dict] = []
    page_no = max(1, int(start_page or 1))
    while page_no <= _PRODUCT_PAGE_CAP:
        try:
            payload = _product_page(
                cookies,
                date_type=date_type,
                begin_date=begin_date,
                end_date=end_date,
                page_no=page_no,
                activity_id=activity_id,
            )
        except ShopAuthError:
            raise
        except ShopApiError as exc:
            raise ProductPartial(exc.message or "罗盘接口失败", rows, page_no) from exc
        data = payload.get("data")
        if data is None:
            data = []
        if not isinstance(data, list):
            raise ProductPartial("罗盘没有返回商品列表", rows, page_no)
        rows.extend(item for item in data if isinstance(item, dict))
        page = payload.get("page_result") if isinstance(payload.get("page_result"), dict) else {}
        try:
            total = int(page.get("total") or 0)
        except (TypeError, ValueError):
            total = 0
        got = max(0, int(already or 0)) + len(rows)
        if len(data) < _PRODUCT_PAGE_SIZE or (total and got >= total):
            break
        page_no += 1
    return rows

"""罗盘首页「收支概况」。金额按罗盘原样返回，这里不改单位。"""
from __future__ import annotations

import json
import time
from typing import Any

import httpx

from app.services.dy_client import ShopApiError, ShopAuthError, _cookies, enter_listed_shop

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


def _product_page(
    cookies: dict[str, str],
    *,
    date_type: int,
    begin_date: str,
    end_date: str,
    page_no: int,
) -> dict:
    """拉一页商品。罗盘说太频繁时隔一会儿再试，最多三次。"""
    delay = 0.0
    for attempt in range(3):
        if delay:
            time.sleep(delay)
        try:
            response = httpx.get(
                f"{_HOST}/compass_api/shop/product/product/product_list",
                params={
                    "date_type": str(date_type),
                    "begin_date": begin_date,
                    "end_date": end_date,
                    "activity_id": "",
                    "is_activity": "false",
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
        try:
            return _checked(body)
        except ShopApiError as exc:
            if "频繁" not in (exc.message or "") or attempt == 2:
                raise
            delay = 1.5 * (attempt + 1)
    raise ShopApiError("罗盘暂时连不上")


def fetch_product_list(
    cookies: dict[str, str],
    *,
    shop_id: str,
    date_type: int,
    begin_date: str,
    end_date: str,
) -> list[dict]:
    """商品列表明细。只取成交金额、成交订单数、退款率、成交退款金额、评价好评率。"""
    _enter_compass(cookies, shop_id)
    try:
        return _collect_products(cookies, date_type=date_type, begin_date=begin_date, end_date=end_date)
    except ShopAuthError as first:
        if not _already_in_shop(cookies, shop_id):
            raise
        try:
            enter_listed_shop(cookies, shop_id, host="compass.jinritemai.com", login_source="compass")
        except ShopApiError as exc:
            if "登录名单里没有这家店" in (exc.message or ""):
                raise first from exc
            raise
        return _collect_products(cookies, date_type=date_type, begin_date=begin_date, end_date=end_date)


def _collect_products(
    cookies: dict[str, str],
    *,
    date_type: int,
    begin_date: str,
    end_date: str,
) -> list[dict]:
    rows: list[dict] = []
    page_no = 1
    while page_no <= _PRODUCT_PAGE_CAP:
        payload = _product_page(
            cookies,
            date_type=date_type,
            begin_date=begin_date,
            end_date=end_date,
            page_no=page_no,
        )
        data = payload.get("data")
        if data is None:
            data = []
        if not isinstance(data, list):
            raise ShopApiError("罗盘没有返回商品列表")
        rows.extend(item for item in data if isinstance(item, dict))
        page = payload.get("page_result") if isinstance(payload.get("page_result"), dict) else {}
        try:
            total = int(page.get("total") or 0)
        except (TypeError, ValueError):
            total = 0
        if len(data) < _PRODUCT_PAGE_SIZE or (total and len(rows) >= total):
            break
        page_no += 1
    return rows

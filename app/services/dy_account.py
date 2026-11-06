"""账户中心调用的抖店接口。金额单位是分。"""
from __future__ import annotations

from typing import Any

from app.services.dy_client import ShopApiError, ShopAuthError, request_shop

_ACCOUNT_QUERY = {"preload": "Y", "_s": "fe", "req_source": "dou_dian_pc", "ac_redesign": "doudian"}
_COMMON_QUERY = {"req_source": "dou_dian_pc", "ac_redesign": "doudian"}


def fetch_account_bundle(cookies: dict[str, str]) -> dict[str, Any]:
    """货款账户、保证金、千川可投、结算周期、管理人。"""
    account = request_shop(cookies, "GET", "/account/center/getAccountList", params=_ACCOUNT_QUERY)
    bundle: dict[str, Any] = {"account": account}
    for key, path in (
        ("margin", "/account/center/marginExpose"),
        ("settle", "/account/center/getShopSettleDayConfig"),
        ("freeze", "/settlement/account/queryfreezeinfo"),
    ):
        try:
            bundle[key] = request_shop(cookies, "GET", path, params=_COMMON_QUERY)
        except (ShopAuthError, ShopApiError):
            bundle[key] = {}
    try:
        bundle["qianchuan"] = request_shop(
            cookies, "GET", "/settlement/account/queryQCShopInfo", params={"req_source": "dou_dian_pc"}
        )
    except (ShopAuthError, ShopApiError):
        bundle["qianchuan"] = {}
    try:
        bundle["admin"] = request_shop(cookies, "GET", "/byteshop/shopuser/getAdminV2")
    except (ShopAuthError, ShopApiError):
        bundle["admin"] = {}
    try:
        info = request_shop(cookies, "GET", "/center/qualification/shop/info", params={"version": "0"})
        bundle["profile"] = info
    except (ShopAuthError, ShopApiError):
        bundle["profile"] = {}
    try:
        bundle["base_margin"] = request_shop(
            cookies,
            "GET",
            "/api/base_margin/get_base_margin_info",
            params={"base_margin_platform": "fxg_pc"},
        )
    except (ShopAuthError, ShopApiError):
        bundle["base_margin"] = {}
    try:
        bundle["experience_margin"] = request_shop(
            cookies, "GET", "/governance/shop/tpledgecash/payable/info_v2"
        )
    except (ShopAuthError, ShopApiError):
        bundle["experience_margin"] = {}
    return bundle


def send_withdraw_code(cookies: dict[str, str]) -> None:
    request_shop(
        cookies,
        "POST",
        "/settlement/index/getmsgverificationcode",
        data={},
    )


def base_refund_detail(cookies: dict[str, str]) -> dict:
    return request_shop(
        cookies,
        "GET",
        "/api/base_margin/get_refund_detail",
        params={"base_margin_platform": "fxg_pc"},
    )


def submit_base_refund(cookies: dict[str, str], body: dict) -> dict:
    payload = {"base_margin_platform": "fxg_pc"}
    payload.update(body)
    return request_shop(cookies, "POST", "/api/base_margin/submit_refund", json_body=payload)


def experience_refund_check(cookies: dict[str, str]) -> dict:
    return request_shop(cookies, "GET", "/governance/shop/tpledgecash/refund/check")


def experience_refund_info(cookies: dict[str, str]) -> dict:
    return request_shop(cookies, "GET", "/governance/shop/tpledgecash/refund/info")


def submit_experience_refund(cookies: dict[str, str], body: dict) -> dict:
    return request_shop(cookies, "POST", "/governance/shop/tpledgecash/refund/submit", json_body=body)


def submit_partial_withdraw(
    cookies: dict[str, str],
    *,
    amount_fen: int,
    code: str,
    remark: str,
    channel_type: str,
) -> dict:
    """部分提现。验证码由页面填写，这里只转交抖店。"""
    return request_shop(
        cookies,
        "POST",
        "/settlement/index/verifymsgcode",
        data={
            "amount": str(amount_fen),
            "code": code,
            "remark": remark,
            "channel_type": channel_type,
            "withdraw_type": "part",
            "req_source": "dou_dian_pc",
        },
    )

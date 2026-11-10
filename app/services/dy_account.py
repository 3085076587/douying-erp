"""账户中心调用的抖店接口。金额单位是分。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.services.dy_client import ShopApiError, ShopAuthError, request_shop

_ACCOUNT_QUERY = {"preload": "Y", "_s": "fe", "req_source": "dou_dian_pc", "ac_redesign": "doudian"}
_COMMON_QUERY = {"req_source": "dou_dian_pc", "ac_redesign": "doudian"}


def fetch_account_bundle(cookies: dict[str, str]) -> dict[str, Any]:
    """货款账户、保证金、千川可投、结算周期、管理人。货款先取，其余一起取。"""
    account = request_shop(cookies, "GET", "/account/center/getAccountList", params=_ACCOUNT_QUERY)
    bundle: dict[str, Any] = {"account": account}
    jobs = (
        ("margin", "/account/center/marginExpose", _COMMON_QUERY),
        ("settle", "/account/center/getShopSettleDayConfig", _COMMON_QUERY),
        ("freeze", "/settlement/account/queryfreezeinfo", _COMMON_QUERY),
        ("qianchuan", "/settlement/account/queryQCShopInfo", {"req_source": "dou_dian_pc"}),
        ("admin", "/byteshop/shopuser/getAdminV2", None),
        ("profile", "/center/qualification/shop/info", {"version": "0"}),
        ("base_margin", "/api/base_margin/get_base_margin_info", {"base_margin_platform": "fxg_pc"}),
        ("experience_margin", "/governance/shop/tpledgecash/payable/info_v2", None),
    )

    def pull(job: tuple[str, str, dict | None]) -> tuple[str, dict, dict[str, str]]:
        key, path, params = job
        local = dict(cookies)
        try:
            data = request_shop(local, "GET", path, params=params)
        except (ShopAuthError, ShopApiError):
            data = {}
        return key, data if isinstance(data, dict) else {}, local

    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        for key, data, local in pool.map(pull, jobs):
            bundle[key] = data
            cookies.update({name: value for name, value in local.items() if name and value})
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

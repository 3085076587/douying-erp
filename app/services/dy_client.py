"""抖店后台接口。金额单位是分。登录凭证由调用方传入，这里不落日志。"""
from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

_HOST = "https://fxg.jinritemai.com"
_SSO = "https://doudian-sso.jinritemai.com"
FXG_HOST = "fxg.jinritemai.com"
SSO_HOST = "doudian-sso.jinritemai.com"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_ACCOUNT_QUERY = {"preload": "Y", "_s": "fe", "req_source": "dou_dian_pc", "ac_redesign": "doudian"}
_COMMON_QUERY = {"req_source": "dou_dian_pc", "ac_redesign": "doudian"}


class ShopAuthError(Exception):
    pass


class ShopApiError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def mix_encode(value: str) -> str:
    """抖店通行证 mix_mode=1：每个字符与 5 异或后写成十六进制。"""
    return "".join(f"{ord(ch) ^ 5:02x}" for ch in value)


def mix_decode(value: str) -> str:
    """mix_encode 的还原。不是这种编码时返回空字符串。"""
    raw = value.strip()
    if not raw or len(raw) % 2:
        return ""
    chars: list[str] = []
    for index in range(0, len(raw), 2):
        try:
            number = int(raw[index : index + 2], 16)
        except ValueError:
            return ""
        chars.append(chr(number ^ 5))
    return "".join(chars)


def _cookies(jar: dict[str, str]) -> dict[str, str]:
    return {str(key): str(value) for key, value in jar.items() if key and value}


def _raise_for_body(payload: Any) -> dict:
    if not isinstance(payload, dict):
        raise ShopApiError("抖店返回了无法识别的内容")
    code = payload.get("code", payload.get("error_code", 0))
    try:
        code_no = int(code or 0)
    except (TypeError, ValueError):
        code_no = -1
    if code_no in {401, 10008} or "登录" in str(payload.get("msg") or payload.get("description") or ""):
        if code_no not in {0}:
            message = str(payload.get("msg") or payload.get("description") or "登录已失效")
            if "登录" in message or code_no in {401, 10008}:
                raise ShopAuthError(message)
    if code_no not in {0}:
        raise ShopApiError(str(payload.get("msg") or payload.get("description") or "抖店接口失败")[:200])
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


def request_shop(
    cookies: dict[str, str],
    method: str,
    path: str,
    *,
    params: dict | None = None,
    data: dict | None = None,
    json_body: dict | None = None,
    host: str = _HOST,
) -> dict:
    headers = {
        "User-Agent": _UA,
        "Referer": f"{_HOST}/ffa/fund-control/account-center",
        "Accept": "application/json, text/plain, */*",
    }
    url = host + path if path.startswith("/") else path
    try:
        response = httpx.request(
            method,
            url,
            params=params,
            data=data,
            json=json_body,
            cookies=_cookies(cookies),
            headers=headers,
            timeout=30,
            follow_redirects=False,
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
    except json.JSONDecodeError as exc:
        raise ShopApiError("抖店返回了无法识别的内容") from exc
    return _raise_for_body(payload)


def absorb_cookies(cookies: dict[str, str], response: httpx.Response) -> dict[str, str]:
    merged = dict(cookies)
    for key, value in response.cookies.items():
        if key and value:
            merged[key] = value
    return merged


def list_subjects(cookies: dict[str, str]) -> list[dict]:
    data = request_shop(
        cookies,
        "GET",
        "/ecomauth/loginv1/get_login_subject",
        params={
            "bus_type": "1",
            "login_source": "doudian_pc_web",
            "entry_source": "0",
            "bus_child_type": "0",
        },
    )
    rows = data.get("login_subject_list")
    return rows if isinstance(rows, list) else []


def touch_session(cookies: dict[str, str]) -> None:
    """访问会话检查，并把抖店回写的凭证并进这个字典。调用方负责落库。"""
    request_shop(
        cookies,
        "GET",
        "/ecomauth/loginv1/session_check",
        params={"login_source": "doudian_pc_web", "bus_type": "1"},
    )


def session_alive(cookies: dict[str, str]) -> bool:
    try:
        request_shop(cookies, "GET", "/ecomauth/loginv1/session_check", params={"login_source": "doudian_pc_web", "bus_type": "1"})
        return True
    except (ShopAuthError, ShopApiError):
        return False


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


def _subject_failure(payload: dict) -> str:
    """通行证把错误码放在 data 里。外层没有 error_code 时不能当成切换成功。"""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    for source in (payload, data):
        if "error_code" not in source:
            continue
        try:
            code_no = int(source.get("error_code") or 0)
        except (TypeError, ValueError):
            code_no = -1
        if code_no not in {0}:
            text = source.get("description") or data.get("description") or payload.get("message") or "切换店铺没有成功"
            return str(text)[:200]
    return ""


def _follow(store: dict[str, dict[str, str]], url: str) -> None:
    current = url
    for _ in range(6):
        host = urlparse(current).netloc
        if not host:
            return
        response = httpx.get(
            current,
            cookies=_cookies(cookies_for(store, host)),
            headers={"User-Agent": _UA, "Referer": f"{_HOST}/login/common"},
            timeout=30,
            follow_redirects=False,
        )
        absorb_host(store, host, response)
        if response.status_code not in {301, 302, 303, 307, 308}:
            return
        location = response.headers.get("location") or ""
        if not location:
            return
        current = urljoin(current, location)


def switch_subject(store: dict[str, dict[str, str]], subject_id: str, member_id: str) -> dict[str, dict[str, str]]:
    """用通行证凭证进入一家工作台。主站和通行证的同名 Cookie 分开保存。"""
    hosts = {host: dict(jar) for host, jar in store.items()}
    sso = dict(hosts.get(SSO_HOST) or hosts.get(FXG_HOST) or {})
    fp = sso.get("s_v_web_id") or (hosts.get(FXG_HOST) or {}).get("s_v_web_id", "")
    csrf = sso.get("passport_csrf_token") or sso.get("passport_csrf_token_default") or ""
    body = {
        "service": f"{_HOST}/login_404_page",
        "fp": fp,
        "subject_aid": "4966",
        "login_subject_uid": mix_encode(subject_id),
        "user_identity_id": mix_encode(member_id),
        "mix_mode": "1",
    }
    response = httpx.post(
        f"{_SSO}/passport/aff/web/subject/login/",
        params={
            "subject_aid": "4966",
            "fp": fp,
            "aid": "4272",
            "language": "zh",
            "account_sdk_source": "web",
        },
        data=body,
        cookies=_cookies(sso),
        headers={
            "User-Agent": _UA,
            "Referer": f"{_HOST}/login/common",
            "Origin": _HOST,
            "X-Requested-With": "XMLHttpRequest",
            "x-tt-passport-csrf-token": csrf,
        },
        timeout=30,
        follow_redirects=False,
    )
    absorb_host(hosts, SSO_HOST, response)
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise ShopApiError("切换店铺没有成功") from exc
    if not isinstance(payload, dict):
        raise ShopApiError("切换店铺没有成功")
    failure = _subject_failure(payload)
    if failure:
        raise ShopApiError(failure)
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    redirect = str(data.get("redirect_url") or "") if isinstance(data, dict) else ""
    if redirect:
        _follow(hosts, f"{redirect}&aid=4272&subject_aid=4966&login_member=1")
    _follow(hosts, f"{_HOST}/ecomauth/loginv1/callback?login_source=doudian_pc_web&subject_aid=4966")
    return hosts


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


def dump_cookies(cookies: dict[str, str]) -> str:
    kept = {key: value for key, value in cookies.items() if key and value}
    return json.dumps(kept, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _clean_jar(jar: dict) -> dict[str, str]:
    return {str(key): str(value) for key, value in jar.items() if key and value is not None and not isinstance(value, dict)}


def cookie_store(raw: str | None) -> dict[str, dict[str, str]]:
    """按域名分开的登录凭证。旧数据只有一套，当作主站凭证。"""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    hosts = data.get("hosts") if isinstance(data.get("hosts"), dict) else None
    if hosts is None and data and all(isinstance(value, dict) for value in data.values()):
        hosts = data
    if hosts is None:
        flat = _clean_jar(data)
        return {FXG_HOST: flat} if flat else {}
    cleaned: dict[str, dict[str, str]] = {}
    for host, jar in hosts.items():
        if isinstance(jar, dict):
            kept = _clean_jar(jar)
            if kept:
                cleaned[str(host)] = kept
    return cleaned


def as_store(cookies: dict) -> dict[str, dict[str, str]]:
    if not cookies:
        return {}
    if isinstance(cookies.get("hosts"), dict):
        return cookie_store(json.dumps(cookies))
    if all(isinstance(value, dict) for value in cookies.values()):
        return cookie_store(json.dumps(cookies))
    flat = _clean_jar(cookies)
    return {FXG_HOST: flat} if flat else {}


def dump_cookie_store(store: dict[str, dict[str, str]]) -> str:
    return json.dumps({"hosts": store}, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def cookies_for(store: dict[str, dict[str, str]], host: str) -> dict[str, str]:
    """通行证请求只用自己的那份。其他域名在通行证凭证上覆盖本域名后写的同名项。"""
    if host == SSO_HOST:
        return dict(store.get(SSO_HOST) or {})
    merged = dict(store.get(SSO_HOST) or {})
    merged.update(store.get(host) or {})
    return merged


def load_cookie_blob(raw: str | None) -> dict[str, str]:
    return cookies_for(cookie_store(raw), FXG_HOST)


def absorb_host(store: dict[str, dict[str, str]], host: str, response: httpx.Response) -> None:
    jar = store.setdefault(host, {})
    for key, value in response.cookies.items():
        if key and value:
            jar[key] = value


def parse_cookie_header(header: str) -> dict[str, str]:
    jar: dict[str, str] = {}
    for item in header.split(";"):
        item = item.strip()
        if not item or "=" not in item:
            continue
        name, value = item.split("=", 1)
        name = name.strip()
        if name and value and name not in {"dyp_gate", "dyp_sid"}:
            jar[name] = value
    return jar

# ---------------------------------------------------------------------------
# 违规与预警
#
# 抖店后台「违规管理」页（/ffa/govern-guarantee/regulation/violation）用的接口。
# 路径和参数是从该页面的前端代码里确认过的，不需要再抓包。
# ---------------------------------------------------------------------------

PENALTY_LIST_PATH = "/governance/shop/penalty/get_penalty_list"
PENALTY_ENUM_PATH = "/governance/shop/penalty/enum_config"
_VIOLATION_REFERER = f"{_HOST}/ffa/govern-guarantee/regulation/violation"


def _first(item: dict, *keys: str) -> Any:
    for key in keys:
        value = item.get(key)
        if value not in (None, "", [], {}):
            return value
    return ""


def _enum_label(enums: dict[str, dict[str, str]], group: str, value: Any) -> str:
    mapping = enums.get(group) or {}
    if value in (None, ""):
        return ""
    return mapping.get(str(value), "")


def fetch_penalty_enums(cookies: dict[str, str]) -> dict[str, dict[str, str]]:
    """处罚方式、状态等数字值对应的中文。拿不到就返回空，不阻断列表拉取。"""
    try:
        data = request_shop(cookies, "POST", PENALTY_ENUM_PATH, json_body={})
    except (ShopApiError, ShopAuthError):
        return {}
    enums: dict[str, dict[str, str]] = {}
    if not isinstance(data, dict):
        return enums
    for group, items in data.items():
        if not isinstance(items, list):
            continue
        enums[group] = {
            str(item.get("value")): str(item.get("label"))
            for item in items
            if isinstance(item, dict) and item.get("value") not in (None, "")
        }
    return enums


def _score_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)[:50]
    if number == 0:
        return "0分"
    return f"{number:+.0f}分"


def _penalty_text(enums: dict[str, dict[str, str]], value: Any) -> str:
    if isinstance(value, list):
        texts = [_enum_label(enums, "penalize_type", item) or str(item) for item in value]
        return "、".join(text for text in texts if text)[:50]
    label = _enum_label(enums, "penalize_type", value)
    return (label or str(value or ""))[:50]


def _type_text(enums: dict[str, dict[str, str]], item: dict) -> str:
    """违规类型：抖店主要给 node_name（如「商家超时未履约开票义务」）。"""
    text = _first(item, "node_name", "violation_type_desc", "zhili_tag_code_cn", "scene_type_text")
    if text:
        return str(text)[:50]
    severity = _first(item, "severity_level_desc", "severity_level")
    label = _enum_label(enums, "severity_level", severity)
    return (label or str(severity or ""))[:50]


def _execute_penalty(item: dict, enums: dict[str, dict[str, str]]) -> str:
    """处罚方式：优先用 penalty_types_text，其次看处置计划里的名字。"""
    text = str(item.get("penalty_types_text") or "").strip()
    if not text:
        names = []
        for plan in item.get("execute_plans") or []:
            if not isinstance(plan, dict):
                continue
            name = str(plan.get("exec_type_name") or plan.get("biz_show_desc") or "").strip()
            if name and name not in names:
                names.append(name)
        text = "、".join(names)
    if not text:
        text = _penalty_text(enums, _first(item, "penalize_type", "penalty_type", "punish_type"))
    return text[:50]


def _shape_violation(item: dict, enums: dict[str, dict[str, str]]) -> dict:
    """把抖店的一条违规单统一成本模块的字段名。

    抖店返回的真实结构（2026-11 实测）：
      ticket_id / violation_time / node_name / show_violation_reason /
      violation_detail / status_text_detail.main_status_text / execute_plans[].exec_type_name
    """
    detail = item.get("status_text_detail") if isinstance(item.get("status_text_detail"), dict) else {}
    status_text = str(detail.get("main_status_text") or "").strip()
    if not status_text:
        status_text = _enum_label(enums, "penalty_status", _first(item, "penalty_status", "status"))
    description = str(item.get("violation_detail") or "").strip() or str(
        _first(item, "show_violation_reason", "violation_reason", "reason", "remark")
    ).strip()
    return {
        "violation_id": str(_first(item, "ticket_id", "ticket_no", "penalty_ticket_id", "id"))[:64],
        "time": str(_first(item, "violation_time", "penalty_time", "create_time"))[:32],
        "type": _type_text(enums, item),
        "penalty": _execute_penalty(item, enums),
        "credit": _score_text(_first(item, "violation_score", "score_change", "credit_change", "score"))[:50],
        "description": description[:1000],
        "status": (status_text or str(_first(item, "ticket_status") or ""))[:20],
    }


def fetch_violation_list(
    cookies: dict[str, str],
    start_ts: int,
    end_ts: int,
    *,
    page_size: int = 50,
    max_pages: int = 10,
) -> tuple[list[dict], int]:
    """按时间范围拉一家店的违规记录，返回（记录列表, 平台显示的总条数）。

    分页要点（实测确认）：
    - 抖店这个接口的 page 从 0 开始，传 1 会漏掉第一页（最新的记录）；
    - page_size 最大一次能给到 50 条，正常店铺一页就能拿全；
    - sort 要带上 sort_type / sort_params，否则返回条数不稳定；
    - 同一店铺短时间内连续请求会被限流，返回空列表（total 也是 0），
      所以翻页之间要留间隔，翻到空页时如果总数还没拿够，等一会再重试。
    """
    rows: list[dict] = []
    seen: set[str] = set()
    total = 0
    page = 0
    empty_retries = 0
    body = {
        "condition": {"create_time_start": start_ts, "create_time_end": end_ts},
        "sort": {"sort_type": 1, "sort_params": "create_time"},
        "request_env": {},
        "bucket_id": "",
        "page_tab": "",
        "page": 0,
        "page_size": page_size,
    }
    while page < max_pages:
        body["page"] = page
        data = request_shop(cookies, "POST", PENALTY_LIST_PATH, json_body=body)
        try:
            total = int(data.get("total") or 0)
        except (TypeError, ValueError):
            total = 0
        tickets = data.get("tickets") if isinstance(data.get("tickets"), list) else []
        if not tickets:
            # 总数还没拿够却返回空，通常是限流，等 60 秒再试同一页
            if rows and len(rows) < total and empty_retries < 2:
                empty_retries += 1
                time.sleep(60)
                continue
            break
        empty_retries = 0
        added = 0
        for item in tickets:
            if not isinstance(item, dict):
                continue
            ticket_id = str(item.get("ticket_id") or "")
            if ticket_id and ticket_id in seen:
                continue
            if ticket_id:
                seen.add(ticket_id)
            rows.append(_shape_violation(item, {}))
            added += 1
        if not added or len(tickets) < page_size or (total and len(rows) >= total):
            break
        page += 1
        time.sleep(3)
    return rows, total

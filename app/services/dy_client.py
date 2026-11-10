"""抖店登录和店铺会话。金额和违规接口在各自的页面模块里。登录凭证由调用方传入，这里不落日志。"""
from __future__ import annotations

import json
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
_CLIENT = httpx.Client(
    limits=httpx.Limits(max_connections=40, max_keepalive_connections=20),
    timeout=30,
    follow_redirects=False,
)


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
        response = _CLIENT.request(
            method,
            url,
            params=params,
            data=data,
            json=json_body,
            cookies=_cookies(cookies),
            headers=headers,
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


def enter_listed_shop(
    cookies: dict[str, str],
    shop_id: str,
    *,
    host: str,
    login_source: str,
) -> dict[str, str]:
    """选店会话还在、但还没进到这家店时，用名单里的成员编号进入。"""
    headers = {
        "User-Agent": _UA,
        "Referer": f"https://{host}/",
        "Accept": "application/json, text/plain, */*",
    }
    jar = dict(cookies)
    try:
        listed = httpx.get(
            f"https://{host}/ecomauth/loginv1/get_login_subject",
            params={"bus_type": "1", "login_source": login_source, "entry_source": "0", "bus_child_type": "0"},
            cookies=_cookies(jar),
            headers=headers,
            timeout=30,
            follow_redirects=False,
        )
    except httpx.HTTPError as exc:
        raise ShopApiError("暂时连不上店铺名单") from exc
    jar = absorb_cookies(jar, listed)
    try:
        payload = listed.json()
    except json.JSONDecodeError as exc:
        raise ShopApiError("店铺名单无法识别") from exc
    data = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else {}
    rows = data.get("login_subject_list") if isinstance(data.get("login_subject_list"), list) else []
    target = next(
        (row for row in rows if isinstance(row, dict) and str(row.get("account_id") or "") == str(shop_id)),
        None,
    )
    if not isinstance(target, dict):
        raise ShopApiError("登录名单里没有这家店")
    if target.get("can_login") is False:
        raise ShopApiError("这家店现在不能进入")
    encode_member_id = str(target.get("encode_member_id") or "")
    if not encode_member_id:
        raise ShopApiError("这家店缺少进店凭证")
    try:
        callback = httpx.get(
            f"https://{host}/ecomauth/loginv1/callback",
            params={
                "subject_aid": "4966",
                "login_source": login_source,
                "action_type": "1",
                "encode_member_id": encode_member_id,
            },
            cookies=_cookies(jar),
            headers=headers,
            timeout=30,
            follow_redirects=False,
        )
    except httpx.HTTPError as exc:
        raise ShopApiError("进入店铺没有成功") from exc
    jar = absorb_cookies(jar, callback)
    try:
        body = callback.json()
    except json.JSONDecodeError as exc:
        raise ShopApiError("进入店铺没有成功") from exc
    code = body.get("code", body.get("st", 0)) if isinstance(body, dict) else -1
    try:
        code_no = int(code or 0)
    except (TypeError, ValueError):
        code_no = -1
    message = str(body.get("msg") or body.get("message") or "") if isinstance(body, dict) else ""
    if code_no not in {0}:
        text = (message or "进入店铺没有成功")[:200]
        if code_no in {401, 10001, 10008} or "登录" in text:
            raise ShopAuthError(text)
        raise ShopApiError(text)
    cookies.clear()
    cookies.update(jar)
    return cookies


def session_alive(cookies: dict[str, str]) -> bool:
    try:
        request_shop(cookies, "GET", "/ecomauth/loginv1/session_check", params={"login_source": "doudian_pc_web", "bus_type": "1"})
        return True
    except (ShopAuthError, ShopApiError):
        return False


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

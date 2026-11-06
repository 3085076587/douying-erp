"""把抖店登录页转到本站，登录 Cookie 才能留在嵌套框里并写入数据库。"""
from __future__ import annotations

import json
import logging
import re
import secrets
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from app.db.session import SessionLocal
from app.services.dy_client import parse_cookie_header
from app.services.store_service import note_embed_login, save_subjects

_ROOTS = (
    "jinritemai.com",
    "zijieapi.com",
    "bytefae.com",
    "bytetcc.com",
    "ecombdimg.com",
    "byteimg.com",
    "bytegoofy.com",
    "ibytedapm.com",
    "snssdk.com",
    "bytedance.com",
    "ecombdstatic.com",
    "bytetos.com",
    "huoshanstatic.com",
    "bytednsdoc.com",
    "yhgfb-cn-static.com",
    "bytescm.com",
    "volccdn.com",
    "ibytedtos.com",
    "pstatp.com",
)
_TEXT_TYPES = ("text/html", "text/css", "javascript", "json", "text/plain", "xml")
_JAR_LOCK = threading.Lock()
_JARS: dict[str, dict[str, dict[str, str]]] = {}
_LOGIN_HOLD: dict[str, dict[str, str]] = {}
_LOGIN_ACCOUNT: dict[str, str] = {}
_LOG = logging.getLogger("uvicorn.error")
_MAX_BYTES = 20 * 1024 * 1024
_MOUNT = "/dyp"
_FLAT_HOST = "fxg.jinritemai.com"
_URL_RE = re.compile(r"https?://([A-Za-z0-9.-]+)([^\s\"'<>]*)", re.IGNORECASE)
_PROTO_REL_RE = re.compile(
    r"(?<!:)//([A-Za-z0-9.-]+\.[A-Za-z]{2,})([^\s\"'<>]*)",
    re.IGNORECASE,
)


def browser_origin(host_header: str, scheme: str, referer: str = "", origin_header: str = "") -> str:
    """浏览器实际打开的地址。宝塔反代的 Host 不带端口时，从 Referer 把端口补回来。"""
    raw_host = (host_header or "").strip()
    if raw_host.startswith("0.0.0.0"):
        raw_host = "127.0.0.1" + raw_host.removeprefix("0.0.0.0")
    scheme = (scheme or "http").split(",")[0].strip() or "http"
    if raw_host.startswith("["):
        name = raw_host[1:].split("]", 1)[0]
        has_port = "]:" in raw_host
    else:
        name = raw_host.split(":", 1)[0]
        has_port = ":" in raw_host
    if raw_host and not has_port:
        for raw in (referer, origin_header):
            parsed = urlparse(raw)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                continue
            if parsed.hostname.lower() != name.lower() or not parsed.port:
                continue
            return f"{parsed.scheme}://{parsed.netloc}"
    if not raw_host:
        return ""
    return f"{scheme}://{raw_host}"


_PAGE_BOOT = (
    "<script>(function(){"
    "var roots=['jinritemai.com','zijieapi.com','bytedance.com','bytegoofy.com','ecombdstatic.com',"
    "'bytetos.com','huoshanstatic.com','bytednsdoc.com','yhgfb-cn-static.com','bytescm.com',"
    "'volccdn.com','ibytedtos.com','bytetcc.com','ibytedapm.com','pstatp.com'];"
    "function allowed(host){host=(host||'').split(':')[0].toLowerCase();"
    "for(var i=0;i<roots.length;i++){if(host===roots[i]||host.slice(-(roots[i].length+1))==='.'+roots[i])return true;}"
    "return false;}"
    "function mapUrl(url){if(typeof url!=='string'||url.indexOf('http')!==0)return url;"
    "var match=url.match(/^(https?:)\\/\\/([^\\/?#]+)([^?#]*)(.*)$/);if(!match||!allowed(match[2]))return url;"
    "var host=match[2].split(':')[0];var path=match[3]||'/';var tail=match[4]||'';"
    "var lower=host.toLowerCase();"
    "if(lower==='localhost'||lower.indexOf('localhost.')===0)return url;"
    "if(lower==='fxg.jinritemai.com')return location.origin+path+tail;"
    "return location.origin+'/dyp/'+host+path+tail;}"
    "if(window.fetch){var origFetch=window.fetch;window.fetch=function(input,init){"
    "if(typeof input==='string')input=mapUrl(input);"
    "else if(input&&input.url){var next=mapUrl(input.url);if(next!==input.url)input=new Request(next,input);}"
    "return origFetch.call(this,input,init);};}"
    "var open=XMLHttpRequest.prototype.open;XMLHttpRequest.prototype.open=function(method,url){"
    "if(typeof url==='string')arguments[1]=mapUrl(url);return open.apply(this,arguments);};"
    "try{var hrefDesc=Object.getOwnPropertyDescriptor(Location.prototype,'href');"
    "if(hrefDesc&&hrefDesc.set&&hrefDesc.configurable){Object.defineProperty(Location.prototype,'href',{"
    "configurable:true,enumerable:hrefDesc.enumerable,get:hrefDesc.get,"
    "set:function(v){return hrefDesc.set.call(this,mapUrl(String(v)));}});}}catch(e){}"
    "['assign','replace'].forEach(function(name){var raw=Location.prototype[name];"
    "if(!raw)return;Location.prototype[name]=function(url){return raw.call(this,mapUrl(String(url)));};});"
    "})();</script>"
)
_EMAIL_BOOT = (
    "<script>(function(){"
    "if(location.search.indexOf('n=')>=0)return;"
    "var tries=0;var timer=setInterval(function(){"
    "if((document.body&&document.body.innerText||'').indexOf('请选择店铺')>=0){clearInterval(timer);return;}"
    "var nodes=document.querySelectorAll('div,span,button,a');"
    "for(var i=0;i<nodes.length;i++){if((nodes[i].textContent||'').trim()==='邮箱登录'){nodes[i].click();clearInterval(timer);return;}}"
    "if(++tries>40)clearInterval(timer);"
    "},300);})();</script>"
)
_ENTER_BOOT = (
    "<script>(function(){"
    "if(location.pathname.indexOf('/login/common')!==0)return;"
    "var key='dy_enter_clicked',nameKey='dy_enter_names';"
    "if(location.search.indexOf('fresh=1')>=0){sessionStorage.removeItem(key);sessionStorage.removeItem(nameKey);}"
    "window.addEventListener('message',function(e){"
    "if(!e.data)return;"
    "if(e.data.type==='dy-reset'){sessionStorage.removeItem(key);armed=false;misses=0;return;}"
    "if(e.data.type!=='dy-names'||!e.data.names)return;"
    "sessionStorage.setItem(nameKey,JSON.stringify(e.data.names));});"
    "function read(k){try{return JSON.parse(sessionStorage.getItem(k)||'[]');}catch(err){return[];}}"
    "function isShop(text){return /严选$/.test(text)&&text.length>=3&&text.length<=20;}"
    "function clean(text){return (text||'').replace(/\\s+/g,'');}"
    "function findNode(name){var want=clean(name),nodes=document.querySelectorAll('div,span,p,td,a,li,button'),hit=null,area=1e15,loose=null;"
    "for(var i=0;i<nodes.length;i++){var el=nodes[i];if(clean(el.textContent)!==want)continue;if(!loose||!el.children.length)loose=el;"
    "var rect=el.getBoundingClientRect();if(rect.width<2||rect.height<2)continue;var size=rect.width*rect.height;"
    "if(size<area){hit=el;area=size;}}return hit||loose;}"
    "function press(el){if(!el)return;var view=window;try{el.scrollIntoView({block:'center',inline:'nearest'});}catch(err){}"
    "var card=el;for(var i=0;i<6&&card;i++){card.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true,view:view,button:0}));"
    "card.dispatchEvent(new MouseEvent('mouseup',{bubbles:true,cancelable:true,view:view,button:0}));card.click();"
    "if((card.innerText||'').length>80)break;card=card.parentElement;}}"
    "function scrollLists(){var nodes=document.querySelectorAll('div,ul');"
    "for(var i=0;i<nodes.length;i++){if(nodes[i].scrollHeight>nodes[i].clientHeight+20)nodes[i].scrollTop+=320;}}"
    "function tell(type,extra){var msg={type:type};if(extra){for(var k in extra)msg[k]=extra[k];}"
    "try{parent.postMessage(msg,location.origin);}catch(err){}}"
    "var captchaSent=false,armed=false,armedName='',misses=0,toldPicker=false,timer=0;"
    "function captchaOn(){var nodes=document.querySelectorAll('div,span');"
    "for(var i=0;i<nodes.length;i++){var el=nodes[i],rect=el.getBoundingClientRect();"
    "if(rect.width<20||rect.height<20)continue;var text=(el.innerText||'').trim();if(text.length>40)continue;"
    "if(text.indexOf('滑块')>=0||text.indexOf('向右滑动')>=0||text.indexOf('拖动滑块')>=0||text.indexOf('按住左边按钮')>=0)return true;}"
    "return false;}"
    "function undo(giveUp){var queued=read(key),at=queued.lastIndexOf(armedName);"
    "if(at>=0)queued.splice(at,1);if(giveUp&&armedName)queued.push(armedName);"
    "sessionStorage.setItem(key,JSON.stringify(queued));armed=false;}"
    "timer=setInterval(function(){"
    "if(!document.body)return;"
    "if(captchaOn()){if(armed)undo(false);if(!captchaSent){captchaSent=true;tell('dy-captcha');}return;}"
    "if(captchaSent){captchaSent=false;tell('dy-captcha-done');}"
    "var bodyText=document.body.innerText||'';"
    "if(bodyText.indexOf('请选择店铺')<0&&bodyText.indexOf('选择店铺')<0)return;"
    "var order=read(nameKey);"
    "if(!order.length){var seen={},nodes=document.querySelectorAll('div,span,p,td');"
    "for(var i=0;i<nodes.length;i++){var label=clean(nodes[i].innerText||'');"
    "if(!isShop(label)||nodes[i].children.length||seen[label])continue;seen[label]=1;order.push(label);}}"
    "if(!order.length)return;"
    "if(!toldPicker){toldPicker=true;tell('dy-picker',{total:order.length});}"
    "var done=read(key),next='';"
    "for(var n=0;n<order.length;n++){if(done.indexOf(order[n])<0){next=order[n];break;}}"
    "if(!next){clearInterval(timer);tell('dy-enter-done',{total:order.length});return;}"
    "if(armed)return;"
    "var el=findNode(next);"
    "if(!el){scrollLists();return;}"
    "done.push(next);sessionStorage.setItem(key,JSON.stringify(done));armed=true;armedName=next;misses++;"
    "tell('dy-status',{name:next});press(el);var waiting=next;"
    "setTimeout(function(){if(captchaOn()||location.pathname.indexOf('/login/common')!==0||armedName!==waiting)return;"
    "undo(misses>=3);if(misses>=3)misses=0;},6000);"
    "},400);})();</script>"
)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

    def http_error_301(self, req, fp, code, msg, headers):
        return fp

    http_error_302 = http_error_303 = http_error_307 = http_error_308 = http_error_301


def _local_probe(host: str) -> bool:
    """抖店页面会探测本机调试端口，例如 localhost.bytedance.com:63000。这里没有这个服务。"""
    name = host.lower().split(":")[0].rstrip(".")
    return name == "localhost" or name.startswith("localhost.")


def host_allowed(host: str) -> bool:
    if _local_probe(host):
        return False
    host = host.lower().split(":")[0].rstrip(".")
    return any(host == root or host.endswith("." + root) for root in _ROOTS)


def rewrite_text(text: str, origin: str) -> str:
    """抖店主站保持原路径，别的域名放到 /dyp 下面。页面只认 /login/common 这种路径。"""

    def mapped(host: str, rest: str) -> str:
        if host.lower() == _FLAT_HOST:
            return f"{origin}{rest or '/'}"
        return f"{origin}{_MOUNT}/{host}{rest}"

    def repl(match: re.Match[str]) -> str:
        host, rest = match.group(1), match.group(2)
        if not host_allowed(host):
            return match.group(0)
        return mapped(host, rest)

    text = _URL_RE.sub(repl, text)
    return _PROTO_REL_RE.sub(repl, text)


def rewrite_set_cookie(raw: str) -> str:
    parts = [part.strip() for part in raw.split(";") if part.strip()]
    if not parts or "=" not in parts[0]:
        return ""
    name, value = parts[0].split("=", 1)
    name = name.strip()
    if name.startswith("__Host-") or name.startswith("__Secure-"):
        name = name.split("-", 1)[1]
    if not name:
        return ""
    httponly = any(part.lower() == "httponly" for part in parts[1:])
    cookie = f"{name}={value}; Path=/; SameSite=Lax"
    if httponly:
        cookie += "; HttpOnly"
    return cookie


def _rewrite_back(proxy_url: str) -> str:
    matched = re.search(rf"{_MOUNT}/([^/?#]+)(/[^?#]*)?", proxy_url)
    if matched is None:
        return "https://fxg.jinritemai.com/login/common"
    return f"https://{matched.group(1)}{matched.group(2) or '/'}"


def _fetch(method: str, url: str, headers: dict[str, str], body: bytes | None):
    request = Request(url, data=body if method not in {"GET", "HEAD"} else None, headers=headers, method=method)
    opener = build_opener(_NoRedirect)
    try:
        response = opener.open(request, timeout=40)
    except HTTPError as exc:
        response = exc
    except URLError as exc:
        raise RuntimeError("抖店页面暂时打不开") from exc
    status = getattr(response, "status", None) or response.getcode()
    header_items = []
    raw_headers = response.headers
    for key in raw_headers.keys():
        values = raw_headers.get_all(key) if hasattr(raw_headers, "get_all") else [raw_headers.get(key)]
        for value in values:
            if value is not None:
                header_items.append((key, value))
    payload = b"" if method == "HEAD" else response.read(_MAX_BYTES + 1)
    if len(payload) > _MAX_BYTES:
        raise RuntimeError("抖店页面过大，已中止")
    return status, header_items, payload


def _browser_sid(cookie_header: str) -> tuple[str, str]:
    sid = ""
    kept: list[str] = []
    for item in cookie_header.split(";"):
        item = item.strip()
        if not item or "=" not in item:
            continue
        name, value = item.split("=", 1)
        if name.strip() == "dyp_sid":
            sid = value.strip()
            continue
        kept.append(item)
    return sid, "; ".join(kept)


def _keep_upstream_cookie(sid: str, host: str, raw_set_cookie: str) -> None:
    parts = [part.strip() for part in raw_set_cookie.split(";") if part.strip()]
    if not parts or "=" not in parts[0]:
        return
    name, value = parts[0].split("=", 1)
    name = name.strip()
    if name.startswith("__Host-") or name.startswith("__Secure-"):
        name = name.split("-", 1)[1]
    value = value.strip()
    if not sid or not name or not value:
        return
    with _JAR_LOCK:
        _JARS.setdefault(sid, {}).setdefault(host, {})[name] = value


def _copy_jars(sid: str) -> dict[str, dict[str, str]]:
    with _JAR_LOCK:
        found = _JARS.get(sid) or {}
        return {host: dict(jar) for host, jar in found.items()}


def _save_shop_cookies(cookies: dict[str, str], shop_id: str) -> None:
    from sqlalchemy import select

    from app.models.shop import Shop
    from app.services.dy_client import FXG_HOST, dump_cookie_store

    if not cookies or not shop_id:
        return
    db = SessionLocal()
    try:
        shop = db.scalar(select(Shop).where(Shop.shop_id == shop_id))
        if shop is None:
            return
        shop.cookie_data = dump_cookie_store({FXG_HOST: cookies})
        shop.login_status = "online"
        shop.shop_cookie = 1
        db.commit()
    finally:
        db.close()


def _capture_account_list(cookie_header: str, text: str) -> None:
    """浏览器拉到货款账户时，把当前店铺凭证单独存下来。服务端切换没有页面签名，存不住。"""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return
    data = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else {}
    shop_id = str(data.get("shop_id") or "").strip()
    _save_shop_cookies(parse_cookie_header(cookie_header), shop_id)


def _merged_cookies(cookie_header: str, set_cookies: list[str]) -> dict[str, str]:
    cookies = parse_cookie_header(cookie_header)
    for raw in set_cookies:
        parts = [part.strip() for part in raw.split(";") if part.strip()]
        if not parts or "=" not in parts[0]:
            continue
        name, value = parts[0].split("=", 1)
        name = name.strip()
        if name.startswith("__Host-") or name.startswith("__Secure-"):
            name = name.split("-", 1)[1]
        if name and value.strip() and name not in {"dyp_sid", "dyp_gate"}:
            cookies[name] = value.strip()
    return cookies


def _query_value(query: str, name: str) -> str:
    from urllib.parse import parse_qs

    found = parse_qs(query or "", keep_blank_values=False).get(name) or []
    return found[0].strip() if found else ""


def _picker_page() -> bytes:
    """进店成功或工作台要跳首页时，回到选店页，不清已点过的店，也不清浏览器登录。"""
    page = (
        "<!doctype html><meta charset=\"utf-8\">"
        "<script>location.replace('/login/common?channel=zhaoshang&n='+Date.now());</script>"
    )
    return page.encode("utf-8")


def _shop_home(path: str) -> bool:
    lowered = path.lower()
    return "ffa/" in lowered or "ffa-micro-config" in lowered or "mshop/homepage" in lowered


def _capture_entered_shop(query: str, cookie_header: str, set_cookies: list[str]) -> None:
    """浏览器进店回调里带有成员编号。凭证按这家店单独保存，不在这里拉货款。"""
    from app.services.store_service import save_entered_cookies

    save_entered_cookies(_query_value(query, "member_id"), _merged_cookies(cookie_header, set_cookies))


def _account_from_login(body: bytes | None) -> str:
    """从通行证登录请求里取出账号。密码不读、不记。"""
    from urllib.parse import parse_qs

    from app.services.dy_client import mix_decode

    if not body:
        return ""
    text = body.decode("utf-8", errors="ignore")
    found = ""
    if text[:1] in "{[":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            found = str(payload.get("account") or payload.get("email") or payload.get("mobile") or "")
    else:
        query = parse_qs(text, keep_blank_values=False)
        for key in ("account", "email", "mobile"):
            values = query.get(key) or []
            if values and values[0].strip():
                found = values[0].strip()
                break
    found = found.strip()
    if not found:
        return ""
    decoded = mix_decode(found)
    account = decoded if decoded else found
    if "@" not in account and not account.isdigit():
        return ""
    return account[:100]


def _remember_login_account(sid: str, body: bytes | None) -> None:
    if not sid:
        return
    account = _account_from_login(body)
    if not account:
        return
    with _JAR_LOCK:
        _LOGIN_ACCOUNT[sid] = account


def _remember_subjects(cookie_header: str, text: str, jars: dict[str, dict[str, str]], request_host: str, sid: str) -> None:
    if "login_subject_list" not in text:
        return
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return
    data = payload.get("data") if isinstance(payload, dict) else None
    subjects = data.get("login_subject_list") if isinstance(data, dict) else None
    if not isinstance(subjects, list) or not subjects:
        return
    cookies = parse_cookie_header(cookie_header)
    hosts = {host: dict(jar) for host, jar in jars.items()}
    slot = hosts.setdefault(request_host, {})
    for name, value in cookies.items():
        slot.setdefault(name, value)
    if not any(hosts.values()):
        return
    db = SessionLocal()
    try:
        with _JAR_LOCK:
            owner = _LOGIN_ACCOUNT.get(sid, "")
        saved = save_subjects(
            db, {"hosts": hosts}, subjects, "embed", replace_cookies=False, owner_account=owner
        )
        note_embed_login(saved)
    finally:
        db.close()


def _hold_login(sid: str, cookie_header: str) -> None:
    """记下选店前的登录凭证。进完一家店后写回浏览器，名单页才不会被顶掉。"""
    if not sid:
        return
    held = parse_cookie_header(cookie_header)
    if not held:
        return
    with _JAR_LOCK:
        _LOGIN_HOLD.setdefault(sid, held)


def _restore_login_headers(sid: str, raw_sets: list[str], cookie_header: str = "") -> list[tuple[str, str]]:
    """把进店前的邮箱登录写回浏览器，并清掉这一家店多出来的会话。没有记下登录时不动。"""
    with _JAR_LOCK:
        hold = dict(_LOGIN_HOLD.get(sid) or {})
    if not hold:
        return []
    headers: list[tuple[str, str]] = []
    for name, value in hold.items():
        headers.append(("set-cookie", f"{name}={value}; Path=/; SameSite=Lax"))
    seen = set(hold)
    blobs = list(raw_sets)
    blobs.extend(part.strip() for part in cookie_header.split(";") if part.strip())
    for raw in blobs:
        piece = raw.split(";", 1)[0].strip()
        if "=" not in piece:
            continue
        name = piece.split("=", 1)[0].strip()
        if name.startswith("__Host-") or name.startswith("__Secure-"):
            name = name.split("-", 1)[1]
        if not name or name in seen or name in {"dyp_sid", "dyp_gate"}:
            continue
        seen.add(name)
        headers.append(("set-cookie", f"{name}=; Path=/; Max-Age=0; SameSite=Lax"))
    return headers


def _logout_blocked(path: str) -> bool:
    """工作台发现地址不是抖店官网就会退出登录，把会话作废。这里直接拦住。"""
    return "logout" in path.lower()


def _expire_browser_cookies(cookie_header: str) -> list[tuple[str, str]]:
    """重新登录时清掉浏览器里上一轮进店留下的会话，库里的凭证不动。"""
    keep = {"dyp_sid", "dyp_gate"}
    headers: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in cookie_header.split(";"):
        name = item.split("=", 1)[0].strip()
        if not name or name in keep or name in seen:
            continue
        seen.add(name)
        headers.append(("set-cookie", f"{name}=; Path=/; Max-Age=0; SameSite=Lax"))
    return headers


def build_proxy_response(
    *,
    method: str,
    host: str,
    upstream_path: str,
    query: str,
    request_headers: dict[str, str],
    body: bytes | None,
    origin: str,
) -> tuple[int, bytes, str, list[tuple[str, str]]]:
    if _local_probe(host):
        return 204, b"", "text/plain", []
    if not host_allowed(host):
        raise PermissionError("这个地址不能转发")
    fresh_login = method == "GET" and "fresh=1" in query and "login/common" in upstream_path
    cookie = request_headers.get("cookie", "")
    sid, forwarded_cookie = _browser_sid(cookie)
    back_headers = _restore_login_headers(sid, [], cookie)
    if _logout_blocked(upstream_path):
        if request_headers.get("sec-fetch-dest") == "document":
            return 200, _picker_page(), "text/html; charset=utf-8", back_headers
        return 200, b'{"code":0,"message":"success"}', "application/json", []
    if method == "GET" and request_headers.get("sec-fetch-dest") == "document" and _shop_home(upstream_path):
        return 200, _picker_page(), "text/html; charset=utf-8", back_headers
    if method == "GET" and host.lower().split(":")[0] == "buyin.jinritemai.com" and not upstream_path.strip("/"):
        return 200, _picker_page(), "text/html; charset=utf-8", back_headers
    url = f"https://{host}/{upstream_path.lstrip('/')}"
    if query:
        url = f"{url}?{query}"
    dropped = {"host", "content-length", "connection", "accept-encoding", "cookie"}
    forwarded = {key: value for key, value in request_headers.items() if key not in dropped and value}
    forwarded["Accept-Encoding"] = "identity"
    forwarded.setdefault("User-Agent", "Mozilla/5.0")
    returning = (
        method == "GET"
        and "login/common" in upstream_path
        and "n=" in query
        and "fresh=1" not in query
    )
    if returning:
        with _JAR_LOCK:
            hold = dict(_LOGIN_HOLD.get(sid) or {})
        if hold:
            forwarded_cookie = "; ".join(f"{name}={value}" for name, value in hold.items())
    if fresh_login:
        forwarded_cookie = ""
    minted = False
    if not sid:
        sid = secrets.token_hex(16)
        minted = True
    if method == "POST" and "account_login" in upstream_path.lower():
        _remember_login_account(sid, body)
    if forwarded_cookie:
        forwarded["Cookie"] = forwarded_cookie
    referer = request_headers.get("referer", "")
    if referer:
        forwarded["Referer"] = _rewrite_back(referer)
    forwarded["Origin"] = f"https://{host}"

    status, header_items, payload = _fetch(method, url, forwarded, body)
    content_type = "application/octet-stream"
    response_headers: list[tuple[str, str]] = []
    if fresh_login:
        response_headers.extend(_expire_browser_cookies(cookie))
    raw_sets: list[str] = []
    for key, value in header_items:
        lowered = key.lower()
        if lowered == "content-type":
            content_type = value
        elif lowered == "location":
            response_headers.append(("location", rewrite_text(value, origin)))
        elif lowered == "set-cookie":
            raw_sets.append(value)
            _keep_upstream_cookie(sid, host, value)
            rewritten = rewrite_set_cookie(value)
            if rewritten:
                response_headers.append(("set-cookie", rewritten))
    if "subject/login" in upstream_path and method == "POST":
        _LOG.info("浏览器正在进入一家店铺")
        _hold_login(sid, cookie)
    navigating = request_headers.get("sec-fetch-dest") == "document" or request_headers.get("sec-fetch-mode") == "navigate"
    back_to_picker = status == 200 and "ecomauth/loginv1/callback" in upstream_path and navigating
    if status == 200 and "ecomauth/loginv1/callback" in upstream_path:
        _capture_entered_shop(query, cookie, raw_sets)
    if back_to_picker:
        if minted:
            response_headers.append(("set-cookie", f"dyp_sid={sid}; Path=/; HttpOnly; SameSite=Lax"))
        response_headers.extend(_restore_login_headers(sid, raw_sets, cookie))
        page = (
            "<!doctype html><meta charset=\"utf-8\">"
            "<script>location.replace('/login/common?channel=zhaoshang');</script>"
        )
        return 200, page.encode("utf-8"), "text/html; charset=utf-8", response_headers
    if minted:
        response_headers.append(("set-cookie", f"dyp_sid={sid}; Path=/; HttpOnly; SameSite=Lax"))
    if returning:
        response_headers.extend(_restore_login_headers(sid, raw_sets, cookie))
    if method != "HEAD" and payload and any(token in content_type.lower() for token in _TEXT_TYPES):
        text = payload.decode("utf-8", errors="replace")
        if "login_subject_list" in text:
            _remember_subjects(cookie, text, _copy_jars(sid), host, sid)
        if status == 200 and "account/center/getAccountList" in upstream_path:
            _capture_account_list(cookie, text)
        is_json = "json" in content_type.lower()
        if "text/html" in content_type.lower():
            nonce = ""
            found = re.search(r'nonce="([^"]+)"', text)
            if found:
                nonce = f' nonce="{found.group(1)}"'
            boot = _PAGE_BOOT.replace("<script>", f"<script{nonce}>", 1)
            if "login" in upstream_path:
                boot += _EMAIL_BOOT.replace("<script>", f"<script{nonce}>", 1)
                boot += _ENTER_BOOT.replace("<script>", f"<script{nonce}>", 1)
            text = re.sub(
                r"(<head[^>]*>)",
                lambda match: match.group(1) + boot,
                text,
                count=1,
                flags=re.IGNORECASE,
            )
        # 通行证回跳地址改成本地会被页面丢掉。进店后的跳转地址要留在本站，否则嵌套框会跨域。
        keep_official = "passport" in upstream_path.lower()
        if not is_json or not keep_official:
            text = rewrite_text(text, origin)
        payload = text.encode("utf-8")
    return status, payload, content_type, response_headers

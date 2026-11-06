"""店铺列表：登录后入库、进店凭证、登录态续期。"""
from __future__ import annotations

import logging
import threading

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.shop import Shop
from app.services.dy_client import (
    FXG_HOST,
    ShopApiError,
    ShopAuthError,
    as_store,
    cookie_store,
    cookies_for,
    dump_cookie_store,
    touch_session,
)
from app.services.schedule_log import write_fail, write_success

_LOG = logging.getLogger("uvicorn.error")
_LOGIN_SYNC = threading.Lock()
_LOGIN_MARK = {"mark": 0, "total": 0}


def _labels(item: dict) -> tuple[str, str, str]:
    texts = []
    for label in item.get("bottom_label") or []:
        if isinstance(label, dict) and label.get("text"):
            texts.append(str(label["text"]))
    identity = str(item.get("identity_type_desc") or "")
    shop_type = ""
    status = ""
    for text in texts:
        if text in {"子账号", "主账号"}:
            identity = identity or text
        elif "营业" in text or text in {"停业整顿", "待营业"}:
            status = text
        elif not shop_type:
            shop_type = text
    return identity, shop_type, status


def save_subjects(
    db: Session,
    cookies: dict,
    subjects: list[dict],
    session_key: str,
    *,
    replace_cookies: bool = True,
    owner_account: str = "",
) -> int:
    """把一次登录拉到的全部工作台写入数据库。凭证按域名分开存。"""
    blob = dump_cookie_store(as_store(cookies))
    saved = 0
    for item in subjects:
        if not isinstance(item, dict) or not item.get("can_login", True):
            continue
        shop_id = str(item.get("account_id") or "").strip()
        if not shop_id:
            continue
        identity, shop_type, status = _labels(item)
        shop = db.scalar(select(Shop).where(Shop.shop_id == shop_id))
        if shop is None:
            shop = Shop(shop_id=shop_id, name=str(item.get("account_name") or shop_id)[:100])
            db.add(shop)
        shop.name = str(item.get("account_name") or shop.name)[:100]
        shop.subject_id = str(item.get("subject_id") or "")[:32]
        shop.member_id = str(item.get("member_id") or item.get("bus_member_id") or "")[:32]
        shop.identity_desc = identity[:20]
        shop.shop_type_text = shop_type[:20]
        shop.operate_status = status[:20]
        if replace_cookies or not shop.cookie_data:
            shop.cookie_data = blob
        shop.login_status = "online"
        shop.session_key = session_key[:64]
        if owner_account:
            shop.owner_account = owner_account[:100]
        saved += 1
    db.commit()
    return saved


def note_embed_login(saved: int) -> None:
    """嵌入登录刚写入店铺。页面靠这个标记自动关掉登录窗。"""
    if saved < 1:
        return
    with _LOGIN_SYNC:
        _LOGIN_MARK["mark"] += 1
        _LOGIN_MARK["total"] = saved


def login_sync_state() -> dict[str, int]:
    with _LOGIN_SYNC:
        return {"mark": _LOGIN_MARK["mark"], "total": _LOGIN_MARK["total"]}


def ensure_owner_account_column() -> None:
    """老库补上归属账号。只在字段刚加上时，给还是空的店写上这次登录用的邮箱。"""
    from sqlalchemy import inspect, text

    from app.db.session import engine

    try:
        names = {col["name"] for col in inspect(engine).get_columns("dy_shop")}
    except Exception:
        return
    with engine.begin() as conn:
        if "owner_account" in names:
            return
        conn.execute(text("ALTER TABLE dy_shop ADD COLUMN owner_account VARCHAR(100) NOT NULL DEFAULT ''"))
        conn.execute(
            text("UPDATE dy_shop SET owner_account = :account WHERE owner_account = ''"),
            {"account": "dy18596760512@163.com"},
        )


def ensure_shop_cookie_column() -> None:
    """老库没有 shop_cookie 时补上。这个字段表示这家店已经有自己的登录凭证。"""
    from sqlalchemy import inspect, text

    from app.db.session import engine

    try:
        names = {col["name"] for col in inspect(engine).get_columns("dy_shop")}
    except Exception:
        return
    with engine.begin() as conn:
        if "shop_cookie" not in names:
            conn.execute(text("ALTER TABLE dy_shop ADD COLUMN shop_cookie SMALLINT NOT NULL DEFAULT 0"))
        conn.execute(
            text(
                "UPDATE dy_shop AS s INNER JOIN dy_shop_account AS a ON a.store_pk = s.id "
                "SET s.shop_cookie = 1 WHERE a.balance IS NOT NULL AND s.shop_cookie = 0"
            )
        )


def enter_progress(db: Session) -> dict[str, int]:
    """已经单独存下登录凭证的店 / 全部店。添加店铺时进度条读这个。"""
    total = db.scalar(select(func.count()).select_from(Shop)) or 0
    done = db.scalar(select(func.count()).select_from(Shop).where(Shop.shop_cookie == 1)) or 0
    return {"done": int(done), "total": int(total)}


def save_entered_cookies(member_id: str, cookies: dict[str, str]) -> None:
    """进店回调带有这家店的成员编号。凭证按这家店单独保存，不再等货款接口。"""
    member_id = (member_id or "").strip()[:32]
    if not member_id or not cookies:
        return
    db = SessionLocal()
    try:
        shops = list(db.scalars(select(Shop).where(Shop.member_id == member_id)))
        if not shops:
            _LOG.info("进店回调没有对上店铺")
            return
        blob = dump_cookie_store({FXG_HOST: cookies})
        for shop in shops:
            shop.cookie_data = blob
            shop.login_status = "online"
            shop.shop_cookie = 1
        db.commit()
        _LOG.info("已保存店铺登录凭证 %s 家", len(shops))
    finally:
        db.close()


def list_shop_rows(db: Session) -> list[dict]:
    shops = list(db.scalars(select(Shop).order_by(Shop.id.asc())))
    return [
        {
            "id": shop.id,
            "shop_id": shop.shop_id,
            "name": shop.name,
            "identity_desc": shop.identity_desc,
            "shop_type_text": shop.shop_type_text,
            "operate_status": shop.operate_status,
            "legal_person": shop.legal_person,
            "login_status": shop.login_status,
            "owner_account": shop.owner_account or "",
            "shop_cookie": int(shop.shop_cookie or 0),
            "opened_on": shop.opened_on,
        }
        for shop in shops
    ]


def keep_shop_jar(shop: Shop, cookies: dict[str, str]) -> None:
    """把这次请求回写的凭证存回这家店。没有新值时不改原来的。"""
    if not cookies:
        return
    store = cookie_store(shop.cookie_data)
    slot = store.setdefault(FXG_HOST, {})
    slot.update({key: value for key, value in cookies.items() if key and value})
    shop.cookie_data = dump_cookie_store(store)


def renew_cookies() -> int:
    """每家已有自己凭证的店单独续一次。只写回这家店，不重新登录，也不连坐其他店。"""
    db = SessionLocal()
    try:
        shops = [
            shop
            for shop in db.scalars(select(Shop).order_by(Shop.id.asc()))
            if shop.shop_cookie == 1 and shop.cookie_data
        ]
        updated = 0
        failed = 0
        for shop in shops:
            cookies = cookies_for(cookie_store(shop.cookie_data), FXG_HOST)
            if not cookies:
                failed += 1
                shop.login_status = "offline"
                write_fail(f"登录凭证维护 {shop.name}", "还没有这家店的登录凭证")
                continue
            try:
                touch_session(cookies)
            except ShopAuthError as exc:
                failed += 1
                shop.login_status = "offline"
                write_fail(f"登录凭证维护 {shop.name}", str(exc) or "会话已失效")
                continue
            except ShopApiError as exc:
                failed += 1
                write_fail(f"登录凭证维护 {shop.name}", exc.message or "会话检查失败")
                continue
            keep_shop_jar(shop, cookies)
            shop.login_status = "online"
            updated += 1
        db.commit()
        if not failed:
            _LOG.info("登录凭证维护成功，写回 %s 家", updated)
            write_success(f"登录凭证维护，写回 {updated} 家")
        else:
            _LOG.error("登录凭证维护结束，写回 %s 家，失败 %s 家", updated, failed)
        return updated
    finally:
        db.close()

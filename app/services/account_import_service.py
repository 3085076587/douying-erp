"""把插件交来的登录写进店铺。已有店铺覆盖凭证，没有的店铺新建。"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account_import import AccountImport
from app.models.shop import Shop
from app.schemas.account_import import AccountImportIn
from app.services.dy_client import FXG_HOST, SSO_HOST, cookie_store, dump_cookie_store

_LOG = logging.getLogger("uvicorn.error")
_FILE = Path(__file__).resolve().parent.parent.parent / "logs" / "account_import.log"
_SHOP_ID_COOKIES = {"ecom_gray_shop_id", "SHOP_ID", "shop_id"}


def _compact(value: str) -> str:
    return "".join(str(value or "").split())


def _shop_name(body: AccountImportIn) -> str:
    for item in body.shops:
        name = _compact(str(item.get("name") or ""))
        if name:
            return name[:100]
    return ""


def _shop_id(body: AccountImportIn) -> str:
    for item in body.shops:
        shop_id = str(item.get("shop_id") or "").strip()
        if shop_id.isdigit():
            return shop_id[:32]
    for item in body.cookies:
        if str(item.get("name") or "") in _SHOP_ID_COOKIES and str(item.get("value") or "").isdigit():
            return str(item["value"])[:32]
    return ""


def _cookie_store(body: AccountImportIn) -> dict[str, dict[str, str]]:
    store: dict[str, dict[str, str]] = {}
    for item in body.cookies:
        name = str(item.get("name") or "")
        value = str(item.get("value") or "")
        if not name or not value:
            continue
        domain = str(item.get("domain") or "").lstrip(".").lower()
        if "doudian-sso.jinritemai.com" in domain:
            hosts = (SSO_HOST,)
        elif domain == "jinritemai.com":
            hosts = (FXG_HOST, SSO_HOST)
        else:
            hosts = (FXG_HOST,)
        for host in hosts:
            store.setdefault(host, {})[name] = value
    return store


def _find_shop(db: Session, shop_id: str, name: str) -> tuple[Shop | None, str]:
    if shop_id:
        found = db.scalar(select(Shop).where(Shop.shop_id == shop_id))
        if found is not None:
            return found, "update"
    if not name:
        return None, "create" if shop_id else "skip"
    matches = [shop for shop in db.scalars(select(Shop)) if _compact(shop.name) == name]
    if len(matches) == 1:
        return matches[0], "update"
    if len(matches) > 1:
        return None, "skip"
    return None, "create" if shop_id else "skip"


def _find_import(db: Session, name: str, shop_id: str) -> AccountImport | None:
    rows = db.scalars(select(AccountImport).order_by(AccountImport.id.desc())).all()
    for row in rows:
        try:
            shops = json.loads(row.shops_json or "[]")
        except json.JSONDecodeError:
            continue
        if not isinstance(shops, list):
            continue
        for item in shops:
            if not isinstance(item, dict):
                continue
            if shop_id and str(item.get("shop_id") or "") == shop_id:
                return row
            if name and _compact(str(item.get("name") or "")) == name:
                return row
    return None


def _apply_shop(shop: Shop, body: AccountImportIn, store: dict[str, dict[str, str]], name: str) -> None:
    current = cookie_store(shop.cookie_data)
    if SSO_HOST not in store and current.get(SSO_HOST):
        store[SSO_HOST] = current[SSO_HOST]
    shop.cookie_data = dump_cookie_store(store)
    shop.login_status = "online"
    shop.shop_cookie = 1
    if name and not _compact(shop.name):
        shop.name = name
    if body.account:
        shop.owner_account = body.account[:100]


def save_import(db: Session, body: AccountImportIn) -> tuple[AccountImport, str, str]:
    names = sorted({str(item.get("name")) for item in body.cookies if item.get("name")})
    name = _shop_name(body)
    shop_id = _shop_id(body)
    store = _cookie_store(body)
    shops_json = json.dumps(
        [{"name": name, "shop_id": shop_id}] if name or shop_id else body.shops,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    shop, action = _find_shop(db, shop_id, name)
    if action == "create":
        shop = Shop(shop_id=shop_id, name=name or shop_id)
        db.add(shop)
    if shop is not None and action in {"update", "create"}:
        _apply_shop(shop, body, store, name)
    row = _find_import(db, name, shop_id)
    if row is None:
        row = AccountImport(account="", shops_json="[]", cookies_json="[]")
        db.add(row)
    row.account = (body.account or "")[:100]
    row.shops_json = shops_json
    row.cookies_json = json.dumps(body.cookies, ensure_ascii=False, separators=(",", ":"))
    row.local_storage_json = json.dumps(body.local_storage, ensure_ascii=False, separators=(",", ":"))
    row.ua = (body.ua or "")[:512]
    db.commit()
    db.refresh(row)
    when = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = (
        f"{when} import id={row.id} action={action} shop={name or '-'} shop_id={shop_id or '-'} "
        f"cookies={len(names)} names={','.join(names)}\n"
    )
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    with _FILE.open("a", encoding="utf-8") as handle:
        handle.write(line)
    _LOG.info("账号导入 action=%s shop=%s shop_id=%s cookies=%s", action, name, shop_id, ",".join(names))
    return row, action, name

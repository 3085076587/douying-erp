"""发票数量：按店拉取后放进 Redis，页面只读缓存。"""
from __future__ import annotations

import logging
import threading
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.shop import Shop
from app.services import fund_cache
from app.services.dy_client import FXG_HOST, ShopApiError, ShopAuthError, cookie_store, cookies_for
from app.services.dy_compass import _already_in_shop
from app.services.dy_invoice import fetch_consumer_invoice_counts, fetch_platform_invoice_counts
from app.services.fetch_pace import take_turns
from app.services.store_service import keep_shop_jar

_LOG = logging.getLogger("uvicorn.error")
_CACHE = "invoice"
_META = "invoice_meta"
_TTL_SECONDS = 7 * 24 * 3600
_JOB = threading.Lock()
_PROGRESS = threading.Lock()
_PROGRESS_SLOT: dict[str, object] = {"running": False, "done": 0, "total": 0, "failed": 0, "message": ""}


def progress() -> dict:
    with _PROGRESS:
        return dict(_PROGRESS_SLOT)


def _blank(shop: Shop, error: str = "") -> dict:
    return {
        "store_pk": shop.id,
        "shop_id": shop.shop_id,
        "name": shop.name,
        "owner_account": shop.owner_account or "",
        "login_status": shop.login_status,
        "platform_pending": None,
        "platform_processing": None,
        "platform_finished": None,
        "consumer_pending": None,
        "sync_error": error[:200],
    }


def _pull_one(shop_id: int) -> dict:
    db = SessionLocal()
    try:
        shop = db.get(Shop, shop_id)
        if shop is None:
            return {"store_pk": shop_id, "name": "", "sync_error": "店铺不存在"}
        cookies = cookies_for(cookie_store(shop.cookie_data), FXG_HOST)
        if not cookies or shop.shop_cookie != 1:
            return _blank(shop, "还没有这家店的登录凭证")
        if not _already_in_shop(cookies, shop.shop_id):
            return _blank(shop, "登录凭证对不上这家店")
        row = _blank(shop)
        errors: list[str] = []
        try:
            row.update(fetch_platform_invoice_counts(cookies))
        except ShopAuthError as exc:
            errors.append(str(exc) or "登录已失效")
        except ShopApiError as exc:
            errors.append(exc.message or "平台发票数量没拉到")
        try:
            row.update(fetch_consumer_invoice_counts(cookies))
        except ShopAuthError as exc:
            message = str(exc) or "登录已失效"
            if message not in errors:
                errors.append(message)
        except ShopApiError as exc:
            errors.append(exc.message or "消费者发票数量没拉到")
        if errors:
            row["sync_error"] = "；".join(errors)[:200]
        else:
            keep_shop_jar(shop, cookies)
            db.commit()
        return row
    except Exception:
        _LOG.exception("拉取发票数量失败")
        return {"store_pk": shop_id, "name": "", "sync_error": "暂时没拉到"}
    finally:
        db.close()


def _totals(items: list[dict]) -> dict[str, int]:
    totals = {
        "platform_pending": 0,
        "platform_processing": 0,
        "platform_finished": 0,
        "consumer_pending": 0,
    }
    for row in items:
        for key in totals:
            value = row.get(key)
            if isinstance(value, int):
                totals[key] += value
    return totals


def list_invoices(db: Session) -> dict:
    cached = fund_cache.read(_CACHE) or []
    by_pk: dict[int, dict] = {}
    if isinstance(cached, list):
        for row in cached:
            if isinstance(row, dict) and row.get("store_pk"):
                by_pk[int(row["store_pk"])] = row
    items: list[dict] = []
    for shop in db.scalars(select(Shop).order_by(Shop.id.asc())):
        saved = by_pk.get(shop.id)
        if saved:
            saved = dict(saved)
            saved["name"] = shop.name
            saved["shop_id"] = shop.shop_id
            saved["owner_account"] = shop.owner_account or ""
            saved["login_status"] = shop.login_status
            items.append(saved)
        else:
            items.append(_blank(shop))
    meta = fund_cache.read_obj(_META) or {}
    payload = {"items": items, "updated_at": str(meta.get("updated_at") or "")}
    payload.update(_totals(items))
    return payload


def refresh_invoices() -> dict:
    db = SessionLocal()
    try:
        shops = list(db.scalars(select(Shop).order_by(Shop.id.asc())))
    finally:
        db.close()
    by_pk: dict[int, dict] = {}
    gate = threading.Lock()
    with _PROGRESS:
        _PROGRESS_SLOT.update(running=True, done=0, total=len(shops), failed=0, message="")

    def pull(shop_id: int) -> None:
        row = _pull_one(shop_id)
        with gate:
            if row.get("store_pk"):
                by_pk[int(row["store_pk"])] = row
            with _PROGRESS:
                _PROGRESS_SLOT["done"] = min(int(_PROGRESS_SLOT["total"]), len(by_pk))

    try:
        take_turns([shop.id for shop in shops], pull)
        rows = [by_pk[shop.id] for shop in shops if shop.id in by_pk]
        updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        fund_cache.write(_CACHE, rows, _TTL_SECONDS)
        fund_cache.write_obj(_META, {"updated_at": updated_at}, _TTL_SECONDS)
        failed = sum(1 for row in rows if row.get("sync_error"))
        message = f"发票 {len(rows) - failed} 家完成" + (f"，{failed} 家失败" if failed else "")
        with _PROGRESS:
            _PROGRESS_SLOT.update(done=len(shops), failed=failed, message=message or "完成")
        return {"total": len(rows), "failed": failed, "message": message}
    except Exception:
        _LOG.exception("发票数量更新失败")
        with _PROGRESS:
            _PROGRESS_SLOT.update(message="拉取过程出错了，请查看服务端日志")
        return {"total": 0, "failed": 0, "message": "拉取过程出错了"}


def start_refresh() -> dict:
    if not _JOB.acquire(blocking=False):
        return progress()

    def run() -> None:
        try:
            refresh_invoices()
        finally:
            with _PROGRESS:
                _PROGRESS_SLOT["running"] = False
            _JOB.release()

    with _PROGRESS:
        _PROGRESS_SLOT.update(running=True, done=0, total=0, failed=0, message="")
    threading.Thread(target=run, name="invoice-refresh", daemon=True).start()
    return progress()

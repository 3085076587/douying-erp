"""账户中心：余额、保证金、拉取和提现。定时更新一家一家拉，限流的店等一会儿再取。"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.account import ShopAccount
from app.models.shop import Shop
from app.services import fund_cache
from app.services.fetch_pace import take_turns
from app.services.dy_account import (
    base_refund_detail,
    experience_refund_check,
    experience_refund_info,
    fetch_account_bundle,
    send_withdraw_code,
    submit_base_refund,
    submit_experience_refund,
    submit_partial_withdraw,
)
from app.services.dy_client import (
    FXG_HOST,
    ShopApiError,
    ShopAuthError,
    cookie_store,
    cookies_for,
    dump_cookie_store,
    enter_listed_shop,
    load_cookie_blob,
    session_alive,
    switch_subject,
)
from app.services.schedule_log import write_fail, write_success
from app.services.store_service import keep_shop_jar, revive_shop

_LOG = logging.getLogger("uvicorn.error")
_JOB = threading.Lock()
_PROGRESS = threading.Lock()
_PROGRESS_SLOT = {"running": False, "done": 0, "total": 0}
_LIVE = threading.Lock()
_LIVE_KEYS = (
    "balance",
    "withdraw_balance",
    "reserve_balance",
    "frozen_balance",
    "pending_settle",
    "settle_day",
    "qianchuan_balance",
    "deposit_base",
    "deposit_experience",
    "deposit_base_withdraw",
    "deposit_experience_withdraw",
    "synced_at",
    "sync_error",
)


def cache_ttl_seconds() -> int:
    hours = max(1, min(int(get_settings().data_refresh_hours or 1), 48))
    return hours * 3600 + 3600


def _fen(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bank(account: dict) -> dict:
    cards = account.get("account_list") if isinstance(account.get("account_list"), list) else []
    picked = None
    for card in cards:
        if isinstance(card, dict) and card.get("channel_type") == "PA":
            picked = card
            break
    if picked is None and cards and isinstance(cards[0], dict):
        picked = cards[0]
    info = picked.get("bank_info") if isinstance(picked, dict) and isinstance(picked.get("bank_info"), dict) else {}
    number = "".join(ch for ch in str(info.get("account_number") or "") if ch.isdigit())
    channels = []
    for card in cards:
        if not isinstance(card, dict):
            continue
        channels.append(
            {
                "channel_type": str(card.get("channel_type") or ""),
                "account_desc": str(card.get("account_desc") or ""),
                "withdraw_balance": _fen(card.get("withdraw_balance")) or 0,
            }
        )
    return {
        "bank_name": str(info.get("bank_name") or "")[:50],
        "bank_tail": number[-4:] if number else "",
        "channels": channels,
        "channel_type": str(picked.get("channel_type") or "PA") if isinstance(picked, dict) else "PA",
    }


def _shape_account(shop: Shop, bundle: dict) -> dict:
    account = bundle.get("account") if isinstance(bundle.get("account"), dict) else {}
    margin = bundle.get("margin") if isinstance(bundle.get("margin"), dict) else {}
    settle = bundle.get("settle") if isinstance(bundle.get("settle"), dict) else {}
    qianchuan = bundle.get("qianchuan") if isinstance(bundle.get("qianchuan"), dict) else {}
    admin = bundle.get("admin") if isinstance(bundle.get("admin"), dict) else {}
    profile = bundle.get("profile") if isinstance(bundle.get("profile"), dict) else {}
    bank = _bank(account)
    base = margin.get("basePledgeCashData") if isinstance(margin.get("basePledgeCashData"), dict) else {}
    experience = margin.get("experiencePledgeCashData") if isinstance(margin.get("experiencePledgeCashData"), dict) else {}
    base_page = bundle.get("base_margin") if isinstance(bundle.get("base_margin"), dict) else {}
    base_account = base_page.get("base_margin_account_info") if isinstance(base_page.get("base_margin_account_info"), dict) else {}
    exp_page = bundle.get("experience_margin") if isinstance(bundle.get("experience_margin"), dict) else {}
    exp_withdraw = exp_page.get("withdraw_detail") if isinstance(exp_page.get("withdraw_detail"), dict) else {}
    deposit_base = _fen(base_account.get("account_amount"))
    if deposit_base is None:
        deposit_base = _fen(base.get("balance"))
    deposit_experience = _fen(exp_page.get("balance"))
    if deposit_experience is None:
        deposit_experience = _fen(experience.get("balance"))
    days = settle.get("DelayDayList") if isinstance(settle.get("DelayDayList"), list) and settle.get("DelayDayList") else None
    settle_day = str(days[0]) if days else ""
    if not settle_day and isinstance(profile.get("shop_settle_day"), dict):
        settle_day = str(profile["shop_settle_day"].get("delay_day_display") or "").replace("天", "")
    shop.legal_person = str(admin.get("shop_charge_name") or shop.legal_person or "")[:32]
    shop.charge_mobile = str(admin.get("shop_charge_mobile") or "")[:20]
    shop.login_mobile = str(admin.get("shop_login_mobile") or "")[:20]
    shop.operate_status = str(profile.get("operate_status_str") or shop.operate_status or "")[:20]
    shop.opened_on = str(profile.get("shop_open_time_str") or shop.opened_on or "")[:20]
    shop.login_status = "online"
    return {
        "store_pk": shop.id,
        "shop_id": shop.shop_id,
        "name": shop.name,
        "login_status": "online",
        "legal_person": shop.legal_person,
        "charge_mobile": shop.charge_mobile,
        "login_mobile": shop.login_mobile,
        "opened_on": shop.opened_on,
        "operate_status": shop.operate_status,
        "shop_type_text": shop.shop_type_text,
        "balance": _fen(account.get("balance")),
        "withdraw_balance": _fen(account.get("withdraw_balance")),
        "reserve_balance": _fen(account.get("has_refund_reserve_balance")),
        "frozen_balance": _fen(account.get("has_freeze_balance")),
        "pending_settle": _fen(account.get("valid_pending_settle_amount")),
        "settle_day": settle_day,
        "qianchuan_balance": _fen(qianchuan.get("avail_balance")),
        "deposit_base": deposit_base,
        "deposit_experience": deposit_experience,
        "deposit_base_withdraw": _fen(base_account.get("can_refund_amount")),
        "deposit_experience_withdraw": _fen(exp_withdraw.get("withdrawable_amount")),
        "bank_name": bank["bank_name"],
        "bank_tail": bank["bank_tail"],
        "channel_type": bank["channel_type"],
        "channels": bank["channels"],
        "synced_at": datetime.now().isoformat(timespec="seconds"),
        "sync_error": "",
    }


def _write_snapshot(db: Session, shop: Shop, row: dict) -> None:
    """法人在店铺表上。银行卡入库。金额不落库，调用方另行写入 Redis。"""
    account = db.scalar(select(ShopAccount).where(ShopAccount.store_pk == shop.id))
    if account is None:
        account = ShopAccount(store_pk=shop.id)
        db.add(account)
    account.bank_name = str(row.get("bank_name") or "")[:50]
    account.bank_tail = str(row.get("bank_tail") or "")[:8]
    account.channel_type = str(row.get("channel_type") or "")[:20]
    account.channels_json = json.dumps(row.get("channels") or [], ensure_ascii=False)
    account.synced_at = datetime.now()
    account.sync_error = ""


def store_entered_bundle(shop_id: str, bundle: dict) -> None:
    """进店后的货款数据落库。页面读的是这里，不是临时缓存。"""
    if not shop_id or not isinstance(bundle, dict):
        return
    db = SessionLocal()
    try:
        shop = db.scalar(select(Shop).where(Shop.shop_id == shop_id))
        if shop is None:
            return
        row = _shape_account(shop, bundle)
        _write_snapshot(db, shop, row)
        db.commit()
    finally:
        db.close()


def _saved_row(shop: Shop, account: ShopAccount | None) -> dict:
    channels: list = []
    if account is not None and account.channels_json:
        try:
            loaded = json.loads(account.channels_json)
        except json.JSONDecodeError:
            loaded = []
        channels = loaded if isinstance(loaded, list) else []
    return {
        "store_pk": shop.id,
        "shop_id": shop.shop_id,
        "name": shop.name,
        "login_status": shop.login_status,
        "legal_person": shop.legal_person,
        "charge_mobile": shop.charge_mobile,
        "login_mobile": shop.login_mobile,
        "opened_on": shop.opened_on,
        "operate_status": shop.operate_status,
        "shop_type_text": shop.shop_type_text,
        "balance": None,
        "withdraw_balance": None,
        "reserve_balance": None,
        "frozen_balance": None,
        "pending_settle": None,
        "settle_day": "",
        "qianchuan_balance": None,
        "deposit_base": None,
        "deposit_experience": None,
        "deposit_base_withdraw": None,
        "deposit_experience_withdraw": None,
        "bank_name": account.bank_name if account is not None else "",
        "bank_tail": account.bank_tail if account is not None else "",
        "channel_type": account.channel_type if account is not None else "",
        "channels": channels,
        "synced_at": None,
        "sync_error": "",
    }


def _store_live(store_pk: int, row: dict) -> None:
    with _LIVE:
        current = fund_cache.read_obj("live") or {}
        previous = current.get(str(store_pk)) if isinstance(current.get(str(store_pk)), dict) else {}
        payload = {}
        for key in _LIVE_KEYS:
            value = row.get(key) if key in row else previous.get(key)
            if key in {"settle_day", "sync_error"} and value is None:
                value = ""
            payload[key] = value
        current[str(store_pk)] = payload
        fund_cache.write_obj("live", current, cache_ttl_seconds())


def list_saved_accounts(db: Session) -> list[dict]:
    """店铺名、法人、银行卡读库。金额读 Redis 里最近一次实时拉取。"""
    shops = list(db.scalars(select(Shop).order_by(Shop.id.asc())))
    saved = {item.store_pk: item for item in db.scalars(select(ShopAccount))}
    live = fund_cache.read_obj("live") or {}
    rows = []
    for shop in shops:
        row = _saved_row(shop, saved.get(shop.id))
        extra = live.get(str(shop.id))
        if isinstance(extra, dict):
            for key in _LIVE_KEYS:
                if key not in extra or extra[key] is None:
                    continue
                row[key] = extra[key]
        elif not shop.shop_cookie:
            row["sync_error"] = "还没有这家店的登录凭证"
        rows.append(row)
    return rows


def _enter(shop: Shop) -> dict:
    """只用这家店已经保存的凭证。服务端切换没有页面签名，不会再去登录。"""
    cookies = cookies_for(cookie_store(shop.cookie_data), FXG_HOST)
    if not cookies:
        raise ShopAuthError("还没有登录凭证")
    try:
        bundle = fetch_account_bundle(cookies)
    except ShopAuthError:
        enter_listed_shop(cookies, shop.shop_id, host=FXG_HOST, login_source="doudian_pc_web")
        bundle = fetch_account_bundle(cookies)
    keep_shop_jar(shop, cookies)
    account = bundle.get("account") if isinstance(bundle, dict) else None
    current = str(account.get("shop_id") or "") if isinstance(account, dict) else ""
    if current != shop.shop_id:
        raise ShopAuthError("还没进入这家店铺")
    return bundle


def _keep_login(db: Session, shop: Shop) -> None:
    """会话还在就保持在线。主站会话没了，再用通行证重新进店。"""
    cookies = load_cookie_blob(shop.cookie_data)
    if cookies and session_alive(cookies):
        shop.login_status = "online"
        return
    if revive_shop(db, shop):
        return
    shop.login_status = "offline"


def _pull_one(shop_id: int) -> dict:
    db = SessionLocal()
    try:
        shop = db.get(Shop, shop_id)
        if shop is None:
            return {}
        try:
            try:
                bundle = _enter(shop)
            except ShopAuthError as first:
                if "还没进入" not in str(first) or not revive_shop(db, shop):
                    raise first
                bundle = _enter(shop)
            row = _shape_account(shop, bundle)
            _write_snapshot(db, shop, row)
            db.commit()
            _store_live(shop.id, row)
            return row
        except ShopAuthError as exc:
            _keep_login(db, shop)
            db.commit()
            row = _error_row(shop, str(exc) or "登录已失效")
            _store_live(shop.id, row)
            return row
        except ShopApiError as exc:
            _keep_login(db, shop)
            db.commit()
            row = _error_row(shop, exc.message)
            _store_live(shop.id, row)
            return row
        except Exception:
            _LOG.exception("拉取店铺 %s 失败", shop.shop_id)
            db.rollback()
            row = _error_row(shop, "暂时没拉到")
            _store_live(shop.id, row)
            return row
    finally:
        db.close()


def _error_row(shop: Shop, message: str) -> dict:
    return {
        "store_pk": shop.id,
        "shop_id": shop.shop_id,
        "name": shop.name,
        "login_status": shop.login_status,
        "legal_person": shop.legal_person,
        "sync_error": message[:100],
        "channels": [],
    }


def progress() -> dict:
    with _PROGRESS:
        return dict(_PROGRESS_SLOT)


def refresh_accounts(db: Session, force: bool = False) -> list[dict]:
    """只用已经单独存下登录凭证的店去向抖店要金额。金额写入 Redis。"""
    shops = [
        shop
        for shop in db.scalars(select(Shop).order_by(Shop.id.asc()))
        if shop.shop_cookie == 1 and shop.cookie_data
    ]
    if not shops:
        with _PROGRESS:
            _PROGRESS_SLOT.update(running=False, done=0, total=0)
        return list_saved_accounts(db)
    seen: set[int] = set()
    with _PROGRESS:
        _PROGRESS_SLOT.update(running=True, done=0, total=len(shops))

    gate = threading.Lock()

    def pull(shop_id: int) -> dict:
        row = _pull_one(shop_id) or {}
        with gate:
            seen.add(shop_id)
            with _PROGRESS:
                _PROGRESS_SLOT["done"] = min(int(_PROGRESS_SLOT["total"]), len(seen))
        return row

    try:
        take_turns([shop.id for shop in shops], pull)
    finally:
        with _PROGRESS:
            _PROGRESS_SLOT["running"] = False
            _PROGRESS_SLOT["done"] = _PROGRESS_SLOT["total"]
    db.expire_all()
    return list_saved_accounts(db)


def start_refresh() -> dict:
    """页面点拉取最新时立刻返回，进度在后台走。"""
    if not _JOB.acquire(blocking=False):
        return progress()

    def run() -> None:
        try:
            db = SessionLocal()
            try:
                refresh_accounts(db, True)
                _LOG.info("账户中心更新完成")
            finally:
                db.close()
        except Exception:
            _LOG.exception("账户中心更新失败")
            with _PROGRESS:
                _PROGRESS_SLOT["running"] = False
        finally:
            _JOB.release()

    with _PROGRESS:
        _PROGRESS_SLOT.update(running=True, done=0, total=_PROGRESS_SLOT.get("total") or 0)
    threading.Thread(target=run, name="fund-refresh", daemon=True).start()
    return progress()


def refresh_saved_accounts() -> int:
    if not _JOB.acquire(blocking=False):
        _LOG.info("账户中心更新跳过，上一次还没结束")
        write_fail("账户中心", "上一次还没结束")
        return 0
    try:
        db = SessionLocal()
        try:
            targets = [
                (shop.id, shop.name)
                for shop in db.scalars(select(Shop).order_by(Shop.id.asc()))
                if shop.shop_cookie == 1 and shop.cookie_data
            ]
            rows = refresh_accounts(db, True)
            by_pk = {row.get("store_pk"): row for row in rows}
            failed = 0
            for shop_id, name in targets:
                message = str((by_pk.get(shop_id) or {}).get("sync_error") or "")
                if not message:
                    continue
                failed += 1
                write_fail(f"账户中心 {name}", message)
            done = len(targets) - failed
            if not failed:
                _LOG.info("账户中心更新成功，%s 家", done)
                write_success(f"账户中心 {done} 家")
            else:
                _LOG.error("账户中心更新结束，成功 %s 家，失败 %s 家", done, failed)
            return done
        finally:
            db.close()
    finally:
        _JOB.release()



def shop_cookies(db: Session, store_pk: int) -> tuple[Shop, dict[str, str]]:
    shop = db.get(Shop, store_pk)
    if shop is None:
        raise ShopApiError("店铺不存在")
    cookies = load_cookie_blob(shop.cookie_data)
    if not cookies or shop.login_status != "online":
        raise ShopAuthError("登录已失效，请重新登录")
    return shop, cookies


def request_withdraw_code(db: Session, store_pk: int) -> None:
    shop, _cookies = shop_cookies(db, store_pk)
    store = cookie_store(shop.cookie_data)
    fxg = cookies_for(store, FXG_HOST)
    if shop.subject_id and shop.member_id:
        try:
            bundle = fetch_account_bundle(fxg)
            current = str((bundle.get("account") or {}).get("shop_id") or "")
        except (ShopAuthError, ShopApiError):
            current = ""
        if current != shop.shop_id:
            store = switch_subject(store, shop.subject_id, shop.member_id)
            shop.cookie_data = dump_cookie_store(store)
            fxg = cookies_for(store, FXG_HOST)
            db.commit()
    send_withdraw_code(fxg)


def request_partial_withdraw(db: Session, store_pk: int, amount_fen: int, code: str, remark: str) -> dict:
    shop, cookies = shop_cookies(db, store_pk)
    bundle = _enter(shop)
    db.commit()
    account = bundle.get("account") if isinstance(bundle.get("account"), dict) else {}
    bank = _bank(account)
    cap = _fen(account.get("withdraw_balance")) or 0
    if amount_fen <= 0:
        raise ShopApiError("提现金额要大于 0")
    if amount_fen > cap:
        raise ShopApiError("超过可提现金额")
    cookies = load_cookie_blob(shop.cookie_data)
    submit_partial_withdraw(
        cookies,
        amount_fen=amount_fen,
        code=code,
        remark=remark,
        channel_type=bank["channel_type"] or "PA",
    )
    fund_cache.drop("live")
    return {"store_pk": shop.id, "amount_fen": amount_fen}


_REFUND_BANK_KEYS = ("account_prop", "bank_name", "bank_branch", "province", "city", "s_bank_code", "bank_code")


def _card_number(raw: str) -> str:
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) < 8 or len(digits) > 30:
        raise ShopApiError("请填写收款银行卡号")
    return digits


def _refund_body(detail: dict, amount_fen: int, account_number: str) -> dict:
    body: dict[str, Any] = {"amount": amount_fen, "account_number": account_number}
    for key in _REFUND_BANK_KEYS:
        value = detail.get(key)
        if value not in (None, ""):
            body[key] = value
    return body


def _within_cap(amount_fen: int, *caps: int | None) -> None:
    known = [cap for cap in caps if cap is not None]
    if not known:
        raise ShopApiError("没有查到可提现金额")
    if amount_fen > min(known):
        raise ShopApiError("超过可提现金额")


def _raise_if_risk(result: dict) -> None:
    if isinstance(result, dict) and result.get("risk_uuid"):
        raise ShopApiError("抖店要求安全验证，请到保证金页面完成这次提现")


def request_deposit_withdraw(db: Session, store_pk: int, source: str, amount_fen: int, account_number: str) -> dict:
    """基础保证金、体验保证金的可提现部分退回。金额单位是分。"""
    if amount_fen <= 0:
        raise ShopApiError("提现金额要大于 0")
    number = _card_number(account_number)
    shop, _cookies = shop_cookies(db, store_pk)
    bundle = _enter(shop)
    db.commit()
    cookies = load_cookie_blob(shop.cookie_data)
    if source == "base":
        page = bundle.get("base_margin") if isinstance(bundle.get("base_margin"), dict) else {}
        account = page.get("base_margin_account_info") if isinstance(page.get("base_margin_account_info"), dict) else {}
        detail = base_refund_detail(cookies)
        forbid = str(detail.get("forbid_title") or "").strip()
        if forbid:
            raise ShopApiError(forbid[:200])
        _within_cap(amount_fen, _fen(account.get("can_refund_amount")), _fen(detail.get("refund_pledge_cash")))
        result = submit_base_refund(cookies, _refund_body(detail, amount_fen, number))
    elif source == "experience":
        page = bundle.get("experience_margin") if isinstance(bundle.get("experience_margin"), dict) else {}
        withdraw = page.get("withdraw_detail") if isinstance(page.get("withdraw_detail"), dict) else {}
        check = experience_refund_check(cookies)
        if _fen(check.get("check_result")) == 0:
            reason = str(check.get("reason") or "").strip() or "当前暂不支持部分退"
            raise ShopApiError(reason[:200])
        detail = experience_refund_info(cookies)
        _within_cap(amount_fen, _fen(withdraw.get("withdrawable_amount")), _fen(detail.get("refund_pledge_cash")))
        result = submit_experience_refund(cookies, _refund_body(detail, amount_fen, number))
    else:
        raise ShopApiError("不支持的提现账户")
    _raise_if_risk(result)
    fund_cache.drop("live")
    return {"store_pk": shop.id, "amount_fen": amount_fen}

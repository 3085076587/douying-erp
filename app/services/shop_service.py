"""店铺入库、账户拉取和登录凭证续期。多家店并行，进度互不影响。"""
from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.account import ShopAccount
from app.models.shop import Shop
from app.services import fund_cache
from app.services.schedule_log import write_fail, write_success
from app.services.dy_client import (
    FXG_HOST,
    ShopApiError,
    ShopAuthError,
    as_store,
    cookie_store,
    cookies_for,
    dump_cookie_store,
    fetch_account_bundle,
    load_cookie_blob,
    send_withdraw_code,
    session_alive,
    touch_session,
    submit_partial_withdraw,
    switch_subject,
)

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
    "synced_at",
    "sync_error",
)
_LOGIN_SYNC = threading.Lock()
_LOGIN_MARK = {"mark": 0, "total": 0}


def cache_ttl_seconds() -> int:
    hours = max(1, min(int(get_settings().data_refresh_hours or 1), 48))
    return hours * 3600 + 3600


def worker_count(job_count: int) -> int:
    if job_count < 1:
        return 0
    configured = max(1, min(int(get_settings().fetch_workers or 3), 32))
    return min(configured, job_count)


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
        "deposit_base": _fen(base.get("balance")),
        "deposit_experience": _fen(experience.get("balance")),
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
        "bank_name": account.bank_name if account is not None else "",
        "bank_tail": account.bank_tail if account is not None else "",
        "channel_type": account.channel_type if account is not None else "",
        "channels": channels,
        "synced_at": None,
        "sync_error": "",
    }


def _store_live(store_pk: int, row: dict) -> None:
    payload = {key: row.get(key) for key in _LIVE_KEYS}
    with _LIVE:
        current = fund_cache.read_obj("live") or {}
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
                if key in extra:
                    row[key] = extra[key]
        elif not shop.shop_cookie:
            row["sync_error"] = "还没有这家店的登录凭证"
        rows.append(row)
    return rows


def _keep_jar(shop: Shop, cookies: dict[str, str]) -> None:
    """把这次请求回写的凭证存回这家店。没有新值时不改原来的。"""
    if not cookies:
        return
    store = cookie_store(shop.cookie_data)
    slot = store.setdefault(FXG_HOST, {})
    slot.update({key: value for key, value in cookies.items() if key and value})
    shop.cookie_data = dump_cookie_store(store)


def _enter(shop: Shop) -> dict:
    """只用这家店已经保存的凭证。服务端切换没有页面签名，不会再去登录。"""
    cookies = cookies_for(cookie_store(shop.cookie_data), FXG_HOST)
    if not cookies:
        raise ShopAuthError("还没有登录凭证")
    bundle = fetch_account_bundle(cookies)
    _keep_jar(shop, cookies)
    account = bundle.get("account") if isinstance(bundle, dict) else None
    current = str(account.get("shop_id") or "") if isinstance(account, dict) else ""
    if current != shop.shop_id:
        raise ShopAuthError("还没进入这家店铺")
    return bundle


def _pull_one(shop_id: int) -> dict:
    db = SessionLocal()
    try:
        shop = db.get(Shop, shop_id)
        if shop is None:
            return {}
        try:
            bundle = _enter(shop)
            row = _shape_account(shop, bundle)
            _write_snapshot(db, shop, row)
            db.commit()
            _store_live(shop.id, row)
            return row
        except ShopAuthError as exc:
            cookies = load_cookie_blob(shop.cookie_data)
            shop.login_status = "online" if cookies and session_alive(cookies) else "offline"
            db.commit()
            message = str(exc) or "登录已失效"
            if shop.login_status == "online" and "还没进入" not in message and "还没有登录凭证" not in message:
                message = "还没进入这家店铺"
            row = _error_row(shop, message)
            _store_live(shop.id, row)
            return row
        except ShopApiError as exc:
            cookies = load_cookie_blob(shop.cookie_data)
            shop.login_status = "online" if cookies and session_alive(cookies) else "offline"
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


def _pull_group(shop_ids: list[int]) -> list[dict]:
    """同一套登录凭证里的店要串行切换，不同凭证可以并行。"""
    rows = []
    for shop_id in shop_ids:
        row = _pull_one(shop_id)
        if row:
            rows.append(row)
        with _PROGRESS:
            _PROGRESS_SLOT["done"] = min(_PROGRESS_SLOT["total"], _PROGRESS_SLOT["done"] + 1)
    return rows


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
    groups = {str(shop.id): [shop.id] for shop in shops}
    workers = worker_count(len(groups))
    with _PROGRESS:
        _PROGRESS_SLOT.update(running=True, done=0, total=len(shops))
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(_pull_group, list(groups.values())))
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
            _keep_jar(shop, cookies)
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

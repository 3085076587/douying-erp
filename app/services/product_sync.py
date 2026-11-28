"""商品日：除实时外，每个自然日一行商品。页面打开时不调用这里。"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta

from sqlalchemy import delete, select, text

from app.db.session import SessionLocal, engine
from app.models.product_day import DyProductDay
from app.models.shop import Shop
from app.services.center_service import _cell_number, _product_value, _text_cell
from app.services.dy_client import FXG_HOST, ShopApiError, ShopAuthError, cookie_store, cookies_for
from app.services.dy_compass import ProductPartial, fetch_product_list
from app.services.store_service import keep_shop_jar

HISTORY_START = date(2026, 10, 1)
DAY_GAP = 0.3
SHOP_GAP = 0.4


def ensure_product_table() -> None:
    with engine.begin() as conn:
        conn.execute(text(
            """
            CREATE TABLE IF NOT EXISTS dy_product_day (
                id INT NOT NULL AUTO_INCREMENT,
                store_pk INT NOT NULL,
                shop_id VARCHAR(32) NOT NULL DEFAULT '',
                shop_name VARCHAR(100) NOT NULL DEFAULT '',
                product_id VARCHAR(40) NOT NULL DEFAULT '',
                product_name VARCHAR(255) NOT NULL DEFAULT '',
                stat_date DATE NOT NULL,
                pay_amount INT NOT NULL DEFAULT 0,
                user_pay_amount INT NOT NULL DEFAULT 0,
                order_count INT NOT NULL DEFAULT 0,
                refund_amount INT NOT NULL DEFAULT 0,
                refund_rate INT NOT NULL DEFAULT -1,
                good_rate INT NOT NULL DEFAULT -1,
                good_count INT NOT NULL DEFAULT 0,
                eval_count INT NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                PRIMARY KEY (id),
                UNIQUE KEY uk_dy_product_day (store_pk, product_id, stat_date),
                KEY ix_dy_product_day_date (stat_date)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        ))
        names = {
            row[0]
            for row in conn.execute(text(
                "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dy_product_day'"
            ))
        }
        for name, ddl in (
            ("user_pay_amount", "INT NOT NULL DEFAULT 0"),
            ("good_count", "INT NOT NULL DEFAULT 0"),
            ("eval_count", "INT NOT NULL DEFAULT 0"),
        ):
            if name not in names:
                conn.execute(text(f"ALTER TABLE dy_product_day ADD COLUMN {name} {ddl}"))
        conn.execute(text(
            """
            CREATE TABLE IF NOT EXISTS dy_product_day_mark (
                store_pk INT NOT NULL,
                stat_date DATE NOT NULL,
                PRIMARY KEY (store_pk, stat_date)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        ))


def sync_yesterday() -> str:
    """只补还没有昨天商品日的店。昨天已经入库的店不再请求罗盘。"""
    ensure_product_table()
    yesterday = date.today() - timedelta(days=1)
    missing = _missing_shop_ids(yesterday)
    if not missing:
        message = f"{yesterday.isoformat()} 已有数据，跳过"
        print(message, flush=True)
        return message
    only_ids: set[int] | None = set(missing)
    failed_ids: list[int] = []
    offline_ids: list[int] = []
    for attempt in range(1, 4):
        _ok, _fail, _skip, failed_ids, found_offline = _sync_due(yesterday, only_ids)
        for shop_id in found_offline:
            if shop_id not in offline_ids:
                offline_ids.append(shop_id)
        if not failed_ids:
            break
        only_ids = set(failed_ids)
        if attempt < 3:
            print(f"还有 {len(failed_ids)} 家没拿到，第 {attempt + 1} 次再试", flush=True)
    if failed_ids or offline_ids:
        print(
            f"{len(failed_ids)} 家没拉完，{len(offline_ids)} 家登录失效。"
            "这些日期不记成已完成，下次从最后入库日继续补",
            flush=True,
        )
    from app.services.product_refund_rate import rebuild_refund_rates

    rate = rebuild_refund_rates()
    return f"{yesterday.isoformat()} 未补齐 {len(failed_ids) + len(offline_ids)} 家。{rate}"


def backfill_since_october() -> None:
    """2026-10-01 到昨天，每个自然日各拉一遍。同一天再跑会覆盖。"""
    ensure_product_table()
    end = date.today() - timedelta(days=1)
    if end < HISTORY_START:
        print(f"昨天 {end.isoformat()} 早于 {HISTORY_START.isoformat()}，没有可入库的日期", flush=True)
        return
    print(f"商品日 {HISTORY_START.isoformat()} 到 {end.isoformat()}", flush=True)
    ok, fail, skip = _sync_range(HISTORY_START, end)
    with SessionLocal() as db:
        total = db.execute(text("SELECT COUNT(*) FROM dy_product_day")).scalar()
        span = db.execute(text("SELECT MIN(stat_date), MAX(stat_date) FROM dy_product_day")).one()
    print(
        f"结束：成功 {ok} 家，失败 {fail} 家，跳过 {skip} 家。"
        f"表内 {int(total or 0)} 行，日期 {span[0]} 至 {span[1]}",
        flush=True,
    )


def _dates(start: date, end: date) -> list[date]:
    days = []
    cursor = start
    while cursor <= end:
        days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _missing_shop_ids(yesterday: date) -> list[int]:
    """昨天还没有商品行的店。已经有昨天数据的店不在这里。"""
    with SessionLocal() as db:
        rows = db.execute(
            text(
                """
                SELECT s.id
                FROM dy_shop s
                LEFT JOIN (
                    SELECT store_pk, MAX(stat_date) AS last_day
                    FROM dy_product_day
                    GROUP BY store_pk
                ) d ON d.store_pk = s.id
                WHERE d.last_day IS NULL OR d.last_day < :yesterday
                ORDER BY s.id
                """
            ),
            {"yesterday": yesterday},
        ).all()
    return [int(row[0]) for row in rows]


def _due_days(store_pk: int, yesterday: date) -> list[date]:
    """这家店最后一条商品日的下一天，一直补到昨天。标记表不算入库。"""
    with SessionLocal() as db:
        last = db.execute(
            text("SELECT MAX(stat_date) FROM dy_product_day WHERE store_pk = :pk"),
            {"pk": store_pk},
        ).scalar()
    if isinstance(last, datetime):
        last = last.date()
    if last is None:
        return [yesterday]
    if last >= yesterday:
        return []
    return _dates(last + timedelta(days=1), yesterday)


def _sync_due(
    yesterday: date, only_ids: set[int] | None = None
) -> tuple[int, int, int, list[int], list[int]]:
    with SessionLocal() as db:
        shops = list(db.scalars(select(Shop).order_by(Shop.id.asc())))
    if only_ids is not None:
        shops = [shop for shop in shops if shop.id in only_ids]
    ok = fail = skip = 0
    failed_ids: list[int] = []
    offline_ids: list[int] = []
    announced = False
    for index, shop in enumerate(shops, 1):
        days = _due_days(shop.id, yesterday)
        if not days:
            print(f"{index}/{len(shops)} {shop.name} 已有 {yesterday.isoformat()}", flush=True)
            ok += 1
            continue
        print(f"{index}/{len(shops)} 开始 {shop.name} {'、'.join(day.isoformat() for day in days)}", flush=True)
        try:
            line, announced = _sync_shop(shop.id, shop.name, days, announced)
        except Exception as exc:
            line = f"[失败] {shop.name} {exc}"
        print(f"{index}/{len(shops)} {line}", flush=True)
        if line.startswith("[完成]"):
            ok += 1
        elif line.startswith("[跳过]"):
            skip += 1
        elif line.startswith("[掉线]"):
            fail += 1
            offline_ids.append(shop.id)
        else:
            fail += 1
            failed_ids.append(shop.id)
        if index < len(shops):
            time.sleep(SHOP_GAP)
    return ok, fail, skip, failed_ids, offline_ids


def _sync_range(start: date, end: date) -> tuple[int, int, int]:
    with SessionLocal() as db:
        shops = list(db.scalars(select(Shop).order_by(Shop.id.asc())))
    ok = fail = skip = 0
    days = _dates(start, end)
    announced = False
    for index, shop in enumerate(shops, 1):
        print(f"{index}/{len(shops)} 开始 {shop.name}", flush=True)
        try:
            line, announced = _sync_shop(shop.id, shop.name, days, announced)
        except Exception as exc:
            line = f"[失败] {shop.name} {exc}"
        print(f"{index}/{len(shops)} {line}", flush=True)
        if line.startswith("[完成]"):
            ok += 1
        elif line.startswith("[跳过]"):
            skip += 1
        else:
            fail += 1
        if index < len(shops):
            time.sleep(SHOP_GAP)
    return ok, fail, skip


def _sync_shop(shop_id: int, shop_name: str, days: list[date], announced: bool) -> tuple[str, bool]:
    view = _shop_view(shop_id)
    if view is None:
        return f"[跳过] {shop_name}", announced
    cookies = view["cookies"]
    if not cookies or view["shop_cookie"] != 1:
        return f"[跳过] {shop_name} 没有登录凭证", announced
    written = 0
    try:
        for day in days:
            rows = fetch_product_list(
                cookies,
                shop_id=view["shop_id"],
                date_type=2,
                begin_date=day.strftime("%Y/%m/%d 00:00:00"),
                end_date=day.strftime("%Y/%m/%d 00:00:00"),
            )
            records = _records(view, rows, day)
            if rows and not records:
                _save_cookies(view["id"], cookies)
                return f"[失败] {shop_name} {day.isoformat()} 没有商品编号，已写入 {written} 天", announced
            if not announced and records:
                _print_sample(shop_name, day, next(iter(records.values())))
                announced = True
            _replace_day(view["id"], day, list(records.values()), cookies)
            written += 1
            print(f"  {shop_name} {day.isoformat()} 商品 {len(records)}", flush=True)
            time.sleep(DAY_GAP)
    except ProductPartial as exc:
        _save_cookies(view["id"], cookies)
        reason = exc.message or "罗盘接口失败"
        return f"[失败] {shop_name} 第 {exc.page_no} 页没拉完：{reason}，已写入 {written} 天", announced
    except ShopAuthError as exc:
        _save_cookies(view["id"], cookies)
        return f"[掉线] {shop_name} {exc}，已写入 {written} 天", announced
    except ShopApiError as exc:
        _save_cookies(view["id"], cookies)
        return f"[失败] {shop_name} {exc}，已写入 {written} 天", announced
    _save_cookies(view["id"], cookies)
    return f"[完成] {shop_name}: 写入 {written} 天", announced


def _shop_view(shop_id: int) -> dict | None:
    db = SessionLocal()
    try:
        shop = db.get(Shop, shop_id)
        if shop is None:
            return None
        return {
            "id": shop.id,
            "shop_id": str(shop.shop_id or ""),
            "name": shop.name or "",
            "shop_cookie": shop.shop_cookie,
            "cookies": cookies_for(cookie_store(shop.cookie_data), FXG_HOST),
        }
    finally:
        db.close()


def _save_cookies(store_pk: int, cookies: dict[str, str]) -> None:
    db = SessionLocal()
    try:
        shop = db.get(Shop, store_pk)
        if shop is None:
            return
        keep_shop_jar(shop, cookies)
        db.commit()
    finally:
        db.close()


def _stored_rate(node: dict) -> int:
    number, unit = _cell_number(node.get("value") if isinstance(node, dict) else None)
    if number is None or unit == "6":
        return -1
    percent = number * 100 if abs(number) <= 2 else number
    return int(round(percent * 100))


def _review_counts(cell: dict) -> tuple[int, int]:
    """好评条数来自原站。评价条数用好评条数除以当天好评率还原，0% 的那天不计入。"""
    good_number, good_unit = _cell_number(_product_value(cell, "good_eval_cnt").get("value"))
    rate_number, rate_unit = _cell_number(_product_value(cell, "comment_good_eval_ratio").get("value"))
    good = 0 if good_number is None or good_unit == "6" else max(0, int(round(good_number)))
    if rate_number is None or rate_unit == "6":
        return good, 0
    rate = rate_number if abs(rate_number) <= 2 else rate_number / 100
    if rate <= 0 or good <= 0:
        return good, 0
    total = int(round(good / rate))
    return good, total if total >= good else good


def _stored_count(node: dict) -> int:
    number, unit = _cell_number(node.get("value") if isinstance(node, dict) else None)
    if number is None or unit == "6":
        return 0
    return int(round(number))


def _records(view: dict, rows: list[dict], day: date) -> dict[str, dict]:
    now = datetime.now().replace(microsecond=0)
    found: dict[str, dict] = {}
    for info in rows:
        if not isinstance(info, dict):
            continue
        cell = info.get("cell_info") if isinstance(info.get("cell_info"), dict) else info
        product = cell.get("product_info") if isinstance(cell.get("product_info"), dict) else {}
        product_id = _text_cell(product.get("product_id_value")).strip()[:40]
        if not product_id:
            continue
        good_count, eval_count = _review_counts(cell)
        found[product_id] = {
            "store_pk": view["id"],
            "shop_id": view["shop_id"][:32],
            "shop_name": (view["name"] or "")[:100],
            "product_id": product_id,
            "product_name": _text_cell(product.get("product_name_value")).strip()[:255],
            "stat_date": day,
            "pay_amount": _stored_count(_product_value(cell, "ad_receive_amt")),
            "user_pay_amount": _stored_count(_product_value(cell, "pay_amt")),
            "order_count": _stored_count(_product_value(cell, "pay_cnt")),
            "refund_amount": _stored_count(_product_value(cell, "pay_refund_success_amt")),
            "refund_rate": _stored_rate(_product_value(cell, "pay_refund_ratio")),
            "good_rate": _stored_rate(_product_value(cell, "comment_good_eval_ratio")),
            "good_count": good_count,
            "eval_count": eval_count,
            "created_at": now,
            "updated_at": now,
        }
    return found


def _print_sample(shop_name: str, day: date, row: dict) -> None:
    good = "—" if row["good_rate"] < 0 else f"{row['good_rate'] / 100:.2f}%"
    refund = "—" if row["refund_rate"] < 0 else f"{row['refund_rate'] / 100:.2f}%"
    print(
        f"样例 {shop_name} {day.isoformat()} {row['product_name']}"
        f" 成交 {row['pay_amount'] / 100:.2f} 订单 {row['order_count']}"
        f" 退款 {row['refund_amount'] / 100:.2f} 退款率 {refund} 好评率 {good}",
        flush=True,
    )


def _replace_day(store_pk: int, day: date, records: list[dict], cookies: dict[str, str]) -> None:
    """这一天整页都拉到之后才换掉旧行。中途失败不进这里。"""
    db = SessionLocal()
    try:
        shop = db.get(Shop, store_pk)
        db.execute(delete(DyProductDay).where(
            DyProductDay.store_pk == store_pk,
            DyProductDay.stat_date == day,
        ))
        for record in records:
            db.add(DyProductDay(**record))
        db.execute(text(
            """
            INSERT INTO dy_product_day_mark (store_pk, stat_date)
            VALUES (:pk, :day)
            ON DUPLICATE KEY UPDATE stat_date = VALUES(stat_date)
            """
        ), {"pk": store_pk, "day": day})
        if shop is not None:
            keep_shop_jar(shop, cookies)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

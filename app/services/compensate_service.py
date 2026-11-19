"""账单赔付：每天把昨天的流水入库，页面只查库。"""
from __future__ import annotations

import io
import json
import logging
import re
import threading
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.compensate import DyCompensate
from app.models.shop import Shop
from app.services.dy_client import FXG_HOST, ShopApiError, ShopAuthError, cookie_store, cookies_for
from app.services.dy_compensate import fetch_compensate_list
from app.services.fetch_pace import take_turns
from app.services.store_service import keep_shop_jar, revive_shop

_LOG = logging.getLogger("uvicorn.error")
LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
STATE_FILE = LOG_DIR / "compensate_scheduler_state.json"
# 抖店赔付账单页写明记录从这天开始，单次查询最长约 90 天。
HISTORY_START = date(2023, 5, 13)
WINDOW_DAYS = 89


def _day_bounds(start: date, end: date) -> tuple[int, int]:
    start_at = datetime(start.year, start.month, start.day)
    end_at = datetime(end.year, end.month, end.day, 23, 59, 59)
    return int(start_at.timestamp()), int(end_at.timestamp())


def read_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_state(**fields: object) -> None:
    state = read_state()
    state.update(fields)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def needs_backfill() -> bool:
    return not read_state().get("backfill_done")


def _upsert(db: Session, shop: Shop, items: list[dict]) -> int:
    if not items:
        return 0
    ids = [item["compensate_id"] for item in items]
    existing = {
        row.compensate_id: row
        for row in db.scalars(
            select(DyCompensate).where(
                DyCompensate.store_pk == shop.id,
                DyCompensate.compensate_id.in_(ids),
            )
        )
    }
    saved = 0
    for item in items:
        record = existing.get(item["compensate_id"])
        if record is None:
            record = DyCompensate(store_pk=shop.id, shop_id=shop.shop_id or "", compensate_id=item["compensate_id"])
            db.add(record)
            existing[item["compensate_id"]] = record
        record.shop_id = shop.shop_id or ""
        record.finished_at = item["finished_at"]
        record.scene = item["scene"]
        record.recipient = item["recipient"]
        record.direction = item["direction"]
        record.amount_fen = int(item["amount_fen"] or 0)
        record.compensator = item["compensator"]
        record.account_name = item["account_name"]
        record.reason = item["reason"]
        record.shop_order_id = item["shop_order_id"]
        record.order_id = item["order_id"]
        record.aftersale_id = item["aftersale_id"]
        saved += 1
    db.commit()
    return saved


def _pull_one(store_pk: int, start_ts: int, end_ts: int) -> tuple[bool, int]:
    """拉一家店。失败不把店铺标成离线。返回（是否成功, 入库条数）。"""
    db = SessionLocal()
    cookies: dict[str, str] = {}
    try:
        shop = db.get(Shop, store_pk)
        if shop is None:
            return False, 0
        cookies = cookies_for(cookie_store(shop.cookie_data), FXG_HOST)
        if not cookies or shop.shop_cookie != 1:
            return False, 0
        try:
            items = fetch_compensate_list(cookies, start_ts, end_ts)
        except ShopAuthError:
            if not revive_shop(db, shop):
                return False, 0
            cookies = cookies_for(cookie_store(shop.cookie_data), FXG_HOST)
            items = fetch_compensate_list(cookies, start_ts, end_ts)
        saved = _upsert(db, shop, items)
        return True, saved
    except (ShopApiError, ShopAuthError) as exc:
        _LOG.info("账单赔付 %s 没拉到：%s", store_pk, exc)
        return False, 0
    except Exception:
        _LOG.exception("账单赔付 %s 入库失败", store_pk)
        return False, 0
    finally:
        if cookies:
            shop = db.get(Shop, store_pk)
            if shop is not None:
                keep_shop_jar(shop, cookies)
                db.commit()
        db.close()


def _windows(start: date, end: date):
    cursor = start
    while cursor <= end:
        stop = min(end, cursor + timedelta(days=WINDOW_DAYS - 1))
        yield cursor, stop
        cursor = stop + timedelta(days=1)


def pull_dates(start: date, end: date) -> tuple[int, int, int, str]:
    """拉取这个日期范围内的赔付流水。返回（成功店铺数, 失败店铺数, 入库条数, 说明）。"""
    if end < start:
        start, end = end, start
    start_ts, end_ts = _day_bounds(start, end)
    db = SessionLocal()
    try:
        shop_ids = [
            shop.id
            for shop in db.scalars(select(Shop).order_by(Shop.id.asc()))
            if shop.shop_cookie == 1 and shop.cookie_data
        ]
    finally:
        db.close()
    if not shop_ids:
        return 0, 0, 0, "还没有可以拉取的店铺凭证"
    saved_shops = 0
    saved_rows = 0
    failed = 0
    lock = threading.Lock()

    def one(store_pk: int) -> None:
        nonlocal saved_shops, saved_rows, failed
        ok, count = _pull_one(store_pk, start_ts, end_ts)
        with lock:
            if ok:
                saved_shops += 1
                saved_rows += count
            else:
                failed += 1

    take_turns(shop_ids, one)
    message = f"{start.isoformat()} 至 {end.isoformat()}，{saved_shops} 家入库 {saved_rows} 条"
    if failed:
        message += f"，{failed} 家没拉到"
    return saved_shops, failed, saved_rows, message


def pull_yesterday() -> str:
    day = date.today() - timedelta(days=1)
    _saved, _failed, _rows, message = pull_dates(day, day)
    write_state(last_run=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), last_message=message)
    return message


def pull_all() -> str:
    """第一次把平台有记录以来的赔付全部入库。页面打开时仍默认只看近 30 天。"""
    end = date.today()
    touched = 0
    failed = 0
    for start, stop in _windows(HISTORY_START, end):
        _saved, window_failed, rows, message = pull_dates(start, stop)
        touched += rows
        failed = max(failed, window_failed)
        _LOG.info("账单赔付 %s", message)
    summary = f"{HISTORY_START.isoformat()} 至 {end.isoformat()}，写入 {touched} 条"
    if failed:
        summary += f"，有窗口 {failed} 家没拉到"
    else:
        write_state(backfill_done=True)
    write_state(last_run=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), last_message=summary)
    return summary


def _parse_day(value: str) -> date | None:
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _range(start_date: str, end_date: str) -> tuple[datetime, datetime]:
    end = _parse_day(end_date) or date.today()
    start = _parse_day(start_date) or (end - timedelta(days=29))
    if end < start:
        start, end = end, start
    return datetime(start.year, start.month, start.day), datetime(end.year, end.month, end.day) + timedelta(days=1)


def list_shop_options(db: Session) -> list[dict]:
    shops = db.scalars(select(Shop).order_by(Shop.name.asc(), Shop.id.asc()))
    return [{"store_pk": shop.id, "name": shop.name or shop.shop_id or str(shop.id)} for shop in shops]


def list_shop_totals(db: Session, *, store_pk: int = 0, start_date: str = "", end_date: str = "") -> dict:
    start_at, end_at = _range(start_date, end_date)
    query = (
        select(
            DyCompensate.store_pk,
            func.coalesce(func.sum(DyCompensate.amount_fen), 0),
            func.count(DyCompensate.id),
        )
        .where(DyCompensate.finished_at >= start_at, DyCompensate.finished_at < end_at)
        .group_by(DyCompensate.store_pk)
    )
    if store_pk:
        query = query.where(DyCompensate.store_pk == store_pk)
    sums = {pk: (int(amount or 0), int(count or 0)) for pk, amount, count in db.execute(query)}
    if store_pk:
        shops = db.scalars(select(Shop).where(Shop.id == store_pk))
    else:
        shops = db.scalars(select(Shop).order_by(Shop.name.asc(), Shop.id.asc()))
    items = []
    total_fen = 0
    for shop in shops:
        amount, count = sums.get(shop.id, (0, 0))
        if not store_pk and count == 0:
            continue
        items.append(
            {
                "store_pk": shop.id,
                "shop_name": shop.name or shop.shop_id or str(shop.id),
                "amount_fen": amount,
                "count": count,
            }
        )
        total_fen += amount
    items.sort(key=lambda row: row["amount_fen"])
    return {"items": items, "total_fen": total_fen, "shop_count": len(items)}


def list_detail(db: Session, store_pk: int, start_date: str = "", end_date: str = "") -> dict:
    shop = db.get(Shop, store_pk)
    if shop is None:
        return {"store_pk": store_pk, "shop_name": "", "amount_fen": 0, "items": []}
    start_at, end_at = _range(start_date, end_date)
    rows = db.scalars(
        select(DyCompensate)
        .where(
            DyCompensate.store_pk == store_pk,
            DyCompensate.finished_at >= start_at,
            DyCompensate.finished_at < end_at,
        )
        .order_by(DyCompensate.finished_at.desc(), DyCompensate.id.desc())
    )
    items = []
    total = 0
    for row in rows:
        total += int(row.amount_fen or 0)
        items.append(
            {
                "compensate_id": row.compensate_id,
                "finished_at": row.finished_at.strftime("%Y-%m-%d %H:%M:%S") if row.finished_at else "",
                "scene": row.scene,
                "recipient": row.recipient,
                "direction": row.direction,
                "amount_fen": int(row.amount_fen or 0),
                "compensator": row.compensator,
                "account_name": row.account_name,
                "reason": row.reason,
                "shop_order_id": row.shop_order_id,
                "order_id": row.order_id,
                "aftersale_id": row.aftersale_id,
            }
        )
    return {
        "store_pk": shop.id,
        "shop_name": shop.name or "",
        "amount_fen": total,
        "items": items,
    }


def _yuan(fen: int) -> str:
    """分转成元的小数字符串，出账保持负数，避免二进制小数。"""
    number = int(fen or 0)
    sign = "-" if number < 0 else ""
    number = abs(number)
    return f"{sign}{number // 100}.{number % 100:02d}"


_XML_BAD = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")


def _xml(value: object) -> str:
    text = _XML_BAD.sub("", str(value or ""))
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _column(index: int) -> str:
    name = ""
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def _sheet(rows: list[list[tuple[str, str]]]) -> str:
    """一行是若干 (类型, 值)。类型 s 是文本，n 是数字。"""
    body = []
    for row_no, row in enumerate(rows, start=1):
        cells = []
        for col_no, (kind, value) in enumerate(row, start=1):
            ref = f"{_column(col_no)}{row_no}"
            if kind == "n":
                cells.append(f'<c r="{ref}" s="1"><v>{value}</v></c>')
            elif kind == "i":
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{_xml(value)}</t></is></c>')
        body.append(f'<row r="{row_no}">{"".join(cells)}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(body)}</sheetData></worksheet>'
    )


def _workbook(sheets: list[tuple[str, str]]) -> bytes:
    """sheets 是 (表名, 工作表 XML)。用标准库打成 xlsx，线上不用额外装包。"""
    styles = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<numFmts count="1"><numFmt numFmtId="164" formatCode="0.00"/></numFmts>'
        '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="2"><fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="2">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
        "</cellXfs>"
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        "</styleSheet>"
    )
    sheet_tags = []
    rels = [
        '<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    ]
    overrides = [
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
    ]
    files: dict[str, str] = {"xl/styles.xml": styles}
    for index, (name, xml) in enumerate(sheets, start=1):
        sheet_tags.append(f'<sheet name="{_xml(name)}" sheetId="{index}" r:id="rId{index}"/>')
        rels.append(
            f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>'
        )
        overrides.append(
            f'<Override PartName="/xl/worksheets/sheet{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        )
        files[f"xl/worksheets/sheet{index}.xml"] = xml
    files["xl/workbook.xml"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets>{"".join(sheet_tags)}</sheets></workbook>'
    )
    files["xl/_rels/workbook.xml.rels"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'{"".join(rels)}</Relationships>'
    )
    files["[Content_Types].xml"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        f'{"".join(overrides)}</Types>'
    )
    files["_rels/.rels"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as book:
        for path, xml in files.items():
            book.writestr(path, xml.encode("utf-8"))
    return buffer.getvalue()


def export_file(db: Session, *, store_pk: int = 0, start_date: str = "", end_date: str = "") -> tuple[bytes, str]:
    """按页面当前的店铺和时间，导出汇总、明细两个表。金额是元，出账为负。"""
    start_at, end_at = _range(start_date, end_date)
    summary = list_shop_totals(db, store_pk=store_pk, start_date=start_date, end_date=end_date)
    detail_query = (
        select(DyCompensate, Shop.name)
        .join(Shop, Shop.id == DyCompensate.store_pk)
        .where(DyCompensate.finished_at >= start_at, DyCompensate.finished_at < end_at)
        .order_by(Shop.name.asc(), DyCompensate.finished_at.desc(), DyCompensate.id.desc())
    )
    if store_pk:
        detail_query = detail_query.where(DyCompensate.store_pk == store_pk)
    summary_rows: list[list[tuple[str, str]]] = [[("s", "店铺"), ("s", "赔付金额(元)"), ("s", "条数")]]
    total_fen = 0
    total_count = 0
    for item in summary["items"]:
        total_fen += int(item["amount_fen"] or 0)
        total_count += int(item["count"] or 0)
        summary_rows.append(
            [("s", item["shop_name"]), ("n", _yuan(item["amount_fen"])), ("i", str(int(item["count"] or 0)))]
        )
    summary_rows.append([("s", "合计"), ("n", _yuan(total_fen)), ("i", str(total_count))])
    detail_rows: list[list[tuple[str, str]]] = [[
        ("s", "店铺"), ("s", "动账时间"), ("s", "赔付单号"), ("s", "赔付场景"), ("s", "赔付对象"),
        ("s", "动账方向"), ("s", "动账金额(元)"), ("s", "出资方"), ("s", "动账账户"), ("s", "赔付原因"),
        ("s", "订单编号"), ("s", "子订单编号"), ("s", "售后单号"),
    ]]
    for row, shop_name in db.execute(detail_query):
        detail_rows.append([
            ("s", shop_name or ""),
            ("s", row.finished_at.strftime("%Y-%m-%d %H:%M:%S") if row.finished_at else ""),
            ("s", row.compensate_id or ""),
            ("s", row.scene or ""),
            ("s", row.recipient or ""),
            ("s", row.direction or ""),
            ("n", _yuan(row.amount_fen)),
            ("s", row.compensator or ""),
            ("s", row.account_name or ""),
            ("s", row.reason or ""),
            ("s", row.shop_order_id or ""),
            ("s", row.order_id or ""),
            ("s", row.aftersale_id or ""),
        ])
    start_label = start_at.date().isoformat()
    end_label = (end_at - timedelta(days=1)).date().isoformat()
    filename = f"账单赔付-{start_label}至{end_label}.xlsx"
    return _workbook([("汇总", _sheet(summary_rows)), ("明细", _sheet(detail_rows))]), filename

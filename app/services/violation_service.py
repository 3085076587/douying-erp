"""抖店违规与预警：拉取、缓存和查询。

数据不落库：定时任务每 10 分钟拉一次近 30 天，按店铺写进 Redis；
页面查询近 30 天内的任意范围（今天/近7天/近30天/自定义）都直接读 Redis，
状态最多滞后一个刷新周期，不会出现"入库时的旧状态"。
查询范围超出近 30 天时实时向抖店拉，结果只放在进程内存里（几分钟过期），
不写 Redis 也不入库，保证看到的永远是拉取那一刻的最新状态。
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.shop import Shop
from app.services import fund_cache
from app.services.fetch_pace import take_turns
from app.services.dy_client import (
    FXG_HOST,
    ShopApiError,
    ShopAuthError,
    cookie_store,
    cookies_for,
    session_alive,
)
from app.services.dy_violation import fetch_violation_list

_LOG = logging.getLogger("uvicorn.error")

LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
STATE_FILE = LOG_DIR / "violation_scheduler_state.json"

CACHE_TTL_SECONDS = 1200  # Redis 里放 20 分钟，比刷新间隔长一倍，扛住一次失败的周期
_LIVE_TTL_SECONDS = 900   # 超出 30 天的实时拉取结果在内存里留 15 分钟
_LIVE_MAX = 8             # 内存里最多留几个时间范围的结果
_JOB = threading.Lock()
_PROGRESS = threading.Lock()
_PROGRESS_SLOT: dict[str, object] = {
    "running": False, "done": 0, "total": 0, "failed": 0, "message": "", "trigger": ""
}

# ---------------------------------------------------------------------------
# 超出 30 天的实时拉取结果：只放进程内存，不进 Redis 也不进数据库。
# 键是 (开始日期, 结束日期)，值是（过期时间戳, 展示行）。
# ---------------------------------------------------------------------------
_LIVE_LOCK = threading.Lock()
_LIVE: dict[tuple[str, str], tuple[float, list[dict]]] = {}
# 记录每个范围最近一次自动触发拉取的时间，拉完没拿到结果时先不自动重拉，避免循环打接口
_LIVE_ATTEMPTS: dict[tuple[str, str], float] = {}


def _mark_attempt(key: tuple[str, str]) -> None:
    with _LIVE_LOCK:
        _LIVE_ATTEMPTS[key] = time.time()


def _attempt_recent(key: tuple[str, str], seconds: int = 120) -> bool:
    with _LIVE_LOCK:
        at = _LIVE_ATTEMPTS.get(key)
        return at is not None and time.time() - at < seconds


def _live_store(key: tuple[str, str], rows: list[dict]) -> None:
    with _LIVE_LOCK:
        now = time.time()
        _LIVE[key] = (now + _LIVE_TTL_SECONDS, rows)
        for stale in [k for k, (expire, _rows) in _LIVE.items() if expire < now]:
            _LIVE.pop(stale, None)
        while len(_LIVE) > _LIVE_MAX:
            oldest = min(_LIVE, key=lambda k: _LIVE[k][0])
            _LIVE.pop(oldest, None)


def _live_read(key: tuple[str, str]) -> list[dict] | None:
    with _LIVE_LOCK:
        slot = _LIVE.get(key)
        if slot is None:
            return None
        if slot[0] < time.time():
            _LIVE.pop(key, None)
            return None
        return slot[1]


def schedule_minutes() -> int:
    """定时更新的间隔，默认 10 分钟，可用环境变量 VIOLATION_REFRESH_MINUTES 改。"""
    return max(1, int(get_settings().violation_refresh_minutes or 10))


def next_update_text(last_update: str = "") -> str:
    """下次更新时间。和定时任务一样，从上次完成时间再加一个间隔。"""
    raw = (last_update or "").strip()
    if not raw:
        return ""
    try:
        last_at = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return ""
    return (last_at + timedelta(minutes=schedule_minutes())).strftime("%Y-%m-%d %H:%M")


def cache_fresh(seconds: int = 600) -> bool:
    """上一轮违规拉取是否还在指定秒数内。"""
    return fund_cache.stamp_fresh(read_state().get("last_update"), seconds)


def read_state() -> dict:
    """上一次更新的时间、结果。写在 logs 下的 json，重启也还在。"""
    try:
        return json.loads(STATE_FILE.read_text("utf-8"))
    except Exception:
        return {}


def write_state(**fields) -> None:
    state = read_state()
    state.update(fields)
    state["interval_minutes"] = schedule_minutes()
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        temp = STATE_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")
        temp.replace(STATE_FILE)
    except Exception:
        _LOG.warning("违规更新状态没写进去", exc_info=True)

# ---------------------------------------------------------------------------
# 处罚方式归类
# 抖店给的是原始长文本（如「应冻结指定金额货款26.01元365天」「店铺每日限单20单,30个自然日」），
# 里面的金额、天数、单量每家不同，没法直接当筛选项用。这里统一压成「类型」，
# 页面下拉只显示类型，查询时按类型匹配；列表里显示的仍是带天数的原文。
# ---------------------------------------------------------------------------
_PENALTY_RULES: list[tuple[str, str]] = [
    # 从细到粗排：先匹配更具体的写法，避免被粗的规则吃掉
    ("每日限单0单", "店铺限制出单"),
    ("限单0单", "店铺限制出单"),
    ("已结算订单货款", "冻结已结算订单货款"),
    ("冻结体验保证金", "冻结体验保证金账户"),
    ("冻结指定金额货款", "冻结指定金额货款"),
    ("冻结保证金", "冻结保证金账户"),
    ("暂停结算", "违规商品订单暂停结算"),
    ("扣除违约金", "扣除违约金"),
    ("店铺每日限单", "店铺每日限单"),
    ("货款账户限制提现", "货款账户限制提现"),
    ("货款停止结算", "货款停止结算"),
    ("店铺搜索降权", "店铺搜索降权"),
    ("商品封禁", "商品封禁"),
    ("商品下架", "商品下架"),
    ("店铺清退", "店铺清退"),
    ("停业整顿", "停业整顿"),
    ("关闭预售权限", "关闭预售权限"),
    ("限制发品", "限制发品"),
    ("限制提报活动", "限制提报活动"),
    ("警告", "警告"),
]

# 下拉里的展示顺序，从轻到重（和抖店后台的处置说法一致）
_PENALTY_ORDER = [
    "警告",
    "商品下架",
    "商品封禁",
    "关闭预售权限",
    "限制发品",
    "限制提报活动",
    "店铺每日限单",
    "店铺限制出单",
    "店铺搜索降权",
    "货款停止结算",
    "违规商品订单暂停结算",
    "货款账户限制提现",
    "冻结指定金额货款",
    "冻结已结算订单货款",
    "冻结体验保证金账户",
    "冻结保证金账户",
    "扣除违约金",
    "停业整顿",
    "店铺清退",
]

_PENALTY_SPLIT = re.compile(r"[;；、,，]")
_CUT_AT_NUMBER = re.compile(r"[-－—]?\d.*$")


def _one_penalty(part: str) -> str:
    """把一条（可能带金额/天数的）处罚文本压成类型名。"""
    text = (part or "").strip()
    if not text:
        return ""
    for keyword, category in _PENALTY_RULES:
        if keyword in text:
            return category
    # 没匹配到规则的，去掉数字及后面的内容（如「限制账号登录30天」→「限制账号登录」）
    text = _CUT_AT_NUMBER.sub("", text).strip(" -－—()（）")
    return text[:30]


def penalty_categories(text: str) -> list[str]:
    """一条记录的处罚方式 → 类型列表（一条记录可能同时有好几种处罚）。"""
    result: list[str] = []
    for part in _PENALTY_SPLIT.split(text or ""):
        category = _one_penalty(part)
        if category and category not in result:
            result.append(category)
    return result


# ---------------------------------------------------------------------------
# 违规类型：抖店给的原文常把处置动作也拼在后面，例如
#   「劣质商品的同款/同类商品（商品封禁）」
#   「商品品质差（信任放行）-限单100 7天+停止结算+冻结货款」
# 页面上「违规类型」应该只写违规原因，所以把尾部这些处置说明去掉；
# 「情节一般/严重」这类属于违规严重程度，要保留。
# ---------------------------------------------------------------------------
_TYPE_DISPOSAL = re.compile(
    r"封禁|下架|限单|降权|停止结算|暂停结算|冻结|扣违约金|扣除违约金"
    r"|清退|停业|限制发品|限制提现|限制货款提现|警告"
)
_TYPE_TAIL_PAREN = re.compile(r"[（(][^）)]*[）)]\s*$")
_TYPE_TAIL_DASH = re.compile(r"[-—–+＋].*$")


def clean_violation_type(text: str) -> str:
    """违规原因原文 → 只留原因（去掉尾巴上的处置动作）。"""
    out = str(text or "").strip()
    if not out:
        return ""
    for _ in range(3):
        match = _TYPE_TAIL_PAREN.search(out)
        if not match:
            break
        tail = match.group(0)
        if "情节" in tail or not _TYPE_DISPOSAL.search(tail):
            break
        out = out[: match.start()].rstrip()
    match = _TYPE_TAIL_DASH.search(out)
    if match:
        tail = match.group(0)
        if "情节" not in tail and _TYPE_DISPOSAL.search(tail):
            out = out[: match.start()].rstrip()
    return out.rstrip("。.． ,，、")


# ---------------------------------------------------------------------------
# 违规说明压缩
# 抖店原文写得很长：带上订单号、申诉链接、解冻规则、操作指引。
# 页面只需要「为什么被罚」，所以这里去掉订单号、链接、括号备注和后续指引，
# 只留关键原因，最长 80 字。
# ---------------------------------------------------------------------------
_DESC_JSON = re.compile(r"<<.*?>>", re.S)
_DESC_URL = re.compile(r"https?://\S+")
_DESC_ORDER_NO = re.compile(r"订单[0-9]{8,}")
_DESC_LONG_NO = re.compile(r"[0-9]{10,}")  # 商品 ID 这类长串数字
_DESC_PAREN = re.compile(
    r"[（(][^）)]{0,60}(?:店铺id|店铺ID|可在|详见|点击|查看)[^）)]{0,60}[）)]", re.I
)
_DESC_CLAUSE = re.compile(r"[^，。；、·\n\r]+")
# 操作指引类子句：整句丢掉
_DESC_DROP = re.compile(
    r"^(请|建议|注意|为了|当风险|解封|无效举证|重新编辑|做好|下架|具体为|您可|如您|若)"
    r"|^或(?=[0-9０-９%＜<≥])"
)
# 处置结果 / 列举明细：从这里往后都不用再看了
_DESC_BREAK = re.compile(
    r"^(现冻结|现对|平台已|平台将|将面临|若在最晚|如未|逾期|包括|例如)"
)
DESC_LIMIT = 80


def summarize_description(text: str, limit: int = DESC_LIMIT) -> str:
    """违规说明 → 一句关键原因。订单号、链接、申诉和解冻说明都去掉。"""
    if not text:
        return ""
    body = _DESC_JSON.sub("", str(text))
    body = _DESC_URL.sub("", body)
    body = _DESC_PAREN.sub("", body)
    body = body.replace("\\n", "，").replace("\r", "，").replace("\n", "，")
    body = _DESC_ORDER_NO.sub("该订单", body)          # 先替换订单号，避免被下面的长数字规则一起吃掉
    body = _DESC_LONG_NO.sub("", body)
    body = re.sub(r"的等", "的", body)

    kept: list[str] = []
    used = 0
    trimmed = False
    for raw in _DESC_CLAUSE.findall(body):
        clause = raw.strip(" -—·:：，。\t")
        if not clause:
            continue
        if _DESC_BREAK.match(clause):
            trimmed = True
            break
        if _DESC_DROP.match(clause):
            trimmed = True
            continue
        kept.append(clause)
        used += len(clause) + 1
        if used >= limit:
            trimmed = True
            break

    out = "，".join(kept).strip("，、 ")
    out = re.sub(r"(?:[，、\s]*(?:或|等))+$", "", out).strip("，、 ")
    if len(out) > limit:
        cut = out[:limit]
        pos = max(cut.rfind("，"), cut.rfind("、"))
        out = (cut[:pos] if pos > 12 else cut).rstrip("，、") + "…"
    elif trimmed and out and not out.endswith(("。", "…")):
        out += "…"
    return out


def cache_key(store_pk: int) -> str:
    return f"dy:violation:{store_pk}"


# ---------------------------------------------------------------------------
# 处罚到期日：处罚原文里的「N天/N个自然日」取最大天数，违规时间 + 天数。
# 后端算好随行返回，前端只负责显示和标色；筛选（未到期/已到期）也在后端做。
# ---------------------------------------------------------------------------
_DUE_DAYS_RE = re.compile(r"(\d+)\s*(?:个\s*自\s*然\s*日|天)")


def _penalty_max_days(text: str) -> int:
    days = 0
    for match in _DUE_DAYS_RE.finditer(text or ""):
        days = max(days, int(match.group(1)))
    return days


def _due_date(penalty_text: str, violated_at: str) -> str:
    """处罚到期日「YYYY-MM-DD」。没有天数信息或时间解析失败返回空。"""
    if not violated_at:
        return ""
    days = _penalty_max_days(penalty_text)
    if not days:
        return ""
    try:
        day = datetime.strptime(violated_at[:10], "%Y-%m-%d").date()
    except ValueError:
        return ""
    return (day + timedelta(days=days)).isoformat()


def progress() -> dict:
    with _PROGRESS:
        return dict(_PROGRESS_SLOT)


def begin_warmup() -> None:
    """启动预热一开始就记下进度，页面打开就能看到进度条。"""
    with _PROGRESS:
        if _PROGRESS_SLOT.get("running"):
            return
        _PROGRESS_SLOT.update(
            running=True, done=0, total=0, failed=0, message="正在预热", trigger="schedule"
        )


def default_range() -> tuple[date, date]:
    """默认近 30 天。"""
    today = date.today()
    return today - timedelta(days=30), today


def _parse_date(value: str) -> date:
    return datetime.strptime(value.strip(), "%Y-%m-%d").date()


def _parse_time(value: str) -> datetime | None:
    """抖店返回的时间可能是秒级时间戳或字符串，都试着认一下。"""
    value = (value or "").strip()
    if not value:
        return None
    if value.isdigit():
        seconds = int(value)
        if seconds > 10**12:  # 毫秒级时间戳
            seconds //= 1000
        return datetime.fromtimestamp(seconds)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def resolve_range(start_date: str, end_date: str) -> tuple[date, date]:
    """日期字符串 →（开始日, 结束日）。空值或格式不对就用默认近 30 天。"""
    try:
        start = _parse_date(start_date) if start_date.strip() else default_range()[0]
        end = _parse_date(end_date) if end_date.strip() else default_range()[1]
    except ValueError:
        start, end = default_range()
    if start > end:
        start, end = end, start
    return start, end


def _ts_range(start_date: str, end_date: str) -> tuple[int, int]:
    """日期字符串 → 秒级时间戳区间（结束日当天整包含）。"""
    start, end = resolve_range(start_date, end_date)
    start_ts = int(datetime.combine(start, datetime.min.time()).timestamp())
    end_ts = int(datetime.combine(end + timedelta(days=1), datetime.min.time()).timestamp())
    return start_ts, end_ts


def _within_30d(start: date) -> bool:
    """查询范围的起点是否落在近 30 天内（近 30 天内读 Redis 缓存）。"""
    return start >= date.today() - timedelta(days=30)


def _range_key(start: date, end: date) -> tuple[str, str]:
    return (start.isoformat(), end.isoformat())


def list_violation_shops(db: Session) -> list[dict]:
    shops = list(db.scalars(select(Shop).order_by(Shop.id.asc())))
    return [
        {
            "store_pk": shop.id,
            "shop_id": shop.shop_id,
            "name": shop.name,
            "login_status": shop.login_status,
            "owner_account": shop.owner_account or "",
        }
        for shop in shops
    ]


def _target_shops(
    db: Session,
    *,
    store_pk: int = 0,
    store_pks: list[int] | None = None,
    account: str = "",
) -> list[Shop]:
    """按店铺多选和抖音账号筛出目标店铺。"""
    shops = list(db.scalars(select(Shop).order_by(Shop.id.asc())))
    if store_pks:
        wanted = set(store_pks)
        shops = [shop for shop in shops if shop.id in wanted]
    elif store_pk:
        shops = [shop for shop in shops if shop.id == store_pk]
    if account:
        shops = [shop for shop in shops if (shop.owner_account or "") == account]
    return shops


def _display_rows(items: list[dict], shop: Shop | None) -> list[dict]:
    """把 Redis/实时拉取里的一条条 shaped 记录转成页面展示行。"""
    rows: list[dict] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        penalty_text = str(item.get("penalty") or "")
        at = _parse_time(str(item.get("time") or ""))
        violated_at = at.strftime("%Y-%m-%d %H:%M:%S") if at else ""
        description = str(item.get("description") or "")
        violation_type_raw = str(item.get("type") or "")
        rows.append(
            {
                "store_pk": shop.id if shop else 0,
                "shop_id": shop.shop_id if shop else "",
                "shop_name": (shop.name or "") if shop else "",
                "violation_id": str(item.get("violation_id") or ""),
                "violated_at": violated_at,
                "due_date": _due_date(penalty_text, violated_at),
                "violation_type": clean_violation_type(violation_type_raw),
                "violation_type_raw": violation_type_raw,
                "penalty": penalty_text,
                "penalty_category": "、".join(penalty_categories(penalty_text)),
                "description": summarize_description(description),
                "description_full": description,
                "status": str(item.get("status") or ""),
            }
        )
    return rows


def _row_in_range(row: dict, start: date, end: date) -> bool:
    text = str(row.get("violated_at") or "")[:10]
    if not text:
        return False
    try:
        day = _parse_date(text)
    except ValueError:
        return False
    return start <= day <= end


def _apply_filters(
    rows: list[dict],
    *,
    violation_type: str = "",
    penalty: str = "",
    status: str = "",
    due: str = "",
) -> list[dict]:
    """due：处罚到期筛选，"active"=未到期，"passed"=已到期，其他=不限。

    没有到期日的记录（处罚里没有天数，如警告）不算进任何一边。
    """
    today = date.today()
    out: list[dict] = []
    for row in rows:
        if violation_type and row.get("violation_type") != violation_type:
            continue
        if penalty and penalty not in penalty_categories(row.get("penalty", "")):
            continue
        if status and row.get("status") != status:
            continue
        if due:
            due_text = str(row.get("due_date") or "")
            if not due_text:
                continue
            try:
                passed = _parse_date(due_text) < today
            except ValueError:
                continue
            if due == "passed" and not passed:
                continue
            if due == "active" and passed:
                continue
        out.append(row)
    return out


def query_violations(
    db: Session,
    *,
    store_pk: int = 0,
    store_pks: list[int] | None = None,
    account: str = "",
    violation_type: str = "",
    penalty: str = "",
    status: str = "",
    due: str = "",
    start_date: str = "",
    end_date: str = "",
    violation_ids: list[str] | None = None,
) -> dict:
    """查询违规记录，返回 {"rows": [...], "pulling": bool}。

    - 按编号查、或范围在近 30 天内：读 Redis 定时缓存（10 分钟一轮，状态不会旧）；
    - 范围超出近 30 天：读内存里的实时拉取结果；还没有就触发后台拉取并返回
      pulling=True，页面跟着进度走，拉完自动再查。结果不进 Redis 不入库。
    """
    shops = {shop.id: shop for shop in _target_shops(db, store_pk=store_pk, store_pks=store_pks, account=account)}
    start, end = resolve_range(start_date, end_date)
    ids_mode = violation_ids is not None
    rows: list[dict] = []
    pulling = False

    if ids_mode or _within_30d(start):
        wanted = set(violation_ids or []) if ids_mode else None
        for pk, shop in shops.items():
            items = fund_cache.read(cache_key(pk))
            if not items:
                continue
            shop_rows = _display_rows(items, shop)
            if wanted is not None:
                shop_rows = [row for row in shop_rows if row["violation_id"] in wanted]
            else:
                shop_rows = [row for row in shop_rows if _row_in_range(row, start, end)]
            rows.extend(shop_rows)
    else:
        cached_rows = _live_read(_range_key(start, end))
        if cached_rows is None:
            with _PROGRESS:
                job_running = bool(_PROGRESS_SLOT.get("running"))
            if not job_running and _attempt_recent(_range_key(start, end)):
                # 上一次拉取已经跑完但没拿到结果（多半是失败），先不自动重拉，
                # 页面显示为空，用户可点「拉取最新数据」手动重试
                pass
            else:
                _mark_attempt(_range_key(start, end))
                # 这个时间范围还没拉过（或结果过期了），后台去拉，页面等进度
                start_refresh(start_date, end_date)
                pulling = True
        else:
            rows = list(cached_rows)

    rows = _apply_filters(
        rows, violation_type=violation_type, penalty=penalty, status=status, due=due
    )
    rows.sort(key=lambda row: row.get("violated_at") or "", reverse=True)
    return {"rows": rows, "pulling": pulling}


def options(
    db: Session,
    *,
    store_pk: int = 0,
    store_pks: list[int] | None = None,
    account: str = "",
    violation_type: str = "",
    penalty: str = "",
    status: str = "",
    start_date: str = "",
    end_date: str = "",
) -> dict:
    """筛选框的候选项，和查询同一数据源。

    违规类型、处罚方式给当前范围数据里出现过的值，每一项带 count；
    状态下拉由前端按当前查询结果生成（页面有什么就有什么），这里不再提供。
    """
    shops = {shop.id: shop for shop in _target_shops(db, store_pk=store_pk, store_pks=store_pks, account=account)}
    start, end = resolve_range(start_date, end_date)

    base: list[dict] = []
    if _within_30d(start):
        for pk, shop in shops.items():
            items = fund_cache.read(cache_key(pk))
            if not items:
                continue
            base.extend(row for row in _display_rows(items, shop) if _row_in_range(row, start, end))
    else:
        cached_rows = _live_read(_range_key(start, end))
        if cached_rows:
            base = list(cached_rows)

    type_counts: dict[str, int] = {}
    penalty_counts: dict[str, int] = {}
    for row in base:
        categories = penalty_categories(row.get("penalty", ""))
        key = row.get("violation_type", "")
        type_ok = not violation_type or key == violation_type
        penalty_ok = not penalty or penalty in categories
        status_ok = not status or row.get("status") == status
        if penalty_ok and status_ok and key:
            type_counts[key] = type_counts.get(key, 0) + 1
        if type_ok and status_ok:
            for category in categories:
                penalty_counts[category] = penalty_counts.get(category, 0) + 1

    # 条数多的排前面；处罚方式按从轻到重的固定顺序
    types = [{"value": key, "count": num} for key, num in type_counts.items()]
    types.sort(key=lambda row: (-row["count"], row["value"]))
    penalty_all = [c for c in _PENALTY_ORDER if c in penalty_counts] + sorted(
        c for c in penalty_counts if c not in _PENALTY_ORDER
    )
    penalties = [{"value": name, "count": penalty_counts.get(name, 0)} for name in penalty_all]

    # 抖音账号：抖店的登录账号（owner_account），后续加了新账号会自动出现在下拉里
    accounts = [a for a in db.scalars(select(Shop.owner_account).distinct().order_by(Shop.owner_account)) if a]
    return {"accounts": accounts, "types": types, "penalties": penalties}


def _pull_one(
    store_pk: int, start_ts: int, end_ts: int, force: bool, cacheable: bool
) -> tuple[bool, str, int, list[dict]]:
    """拉一家店。返回（是否成功, 失败原因, 平台显示的总条数, 记录列表）。

    cacheable=True（范围在近 30 天内）结果写 Redis 给定时缓存用；
    cacheable=False（更早的范围）只返回给调用方放内存，不写 Redis 不入库。
    """
    db = SessionLocal()
    try:
        shop = db.get(Shop, store_pk)
        if shop is None:
            return False, "店铺不存在", 0, []
        if not force:
            cached = fund_cache.read(cache_key(shop.id))
            if cached is not None:
                return True, "", -1, []
        cookies = cookies_for(cookie_store(shop.cookie_data), FXG_HOST)
        if not cookies:
            return False, "还没有这家店的登录凭证", 0, []
        try:
            items, platform_total = fetch_violation_list(cookies, start_ts, end_ts)
        except ShopAuthError as exc:
            alive = bool(cookies) and session_alive(cookies)
            if not alive:
                from app.services.store_service import revive_shop

                alive = revive_shop(db, shop)
            if not alive:
                shop.login_status = "offline"
            db.commit()
            return False, str(exc) or "登录已失效", 0, []
        except ShopApiError as exc:
            return False, exc.message or "接口失败", 0, []
        if cacheable:
            fund_cache.write(cache_key(shop.id), items, CACHE_TTL_SECONDS)
        if platform_total > len(items):
            _LOG.warning("违规记录还没取全，应有 %s 条，实际 %s 条", platform_total, len(items))
            return False, "请求过于频繁，请稍后再试", len(items), items
        return True, "", len(items), items
    except Exception:
        _LOG.exception("拉取抖店 %s 违规失败", store_pk)
        return False, "暂时没拉到", 0, []
    finally:
        db.close()


def _refresh_all(
    start_ts: int,
    end_ts: int,
    force: bool,
    trigger: str = "manual",
    start_date: str = "",
    end_date: str = "",
) -> None:
    db = SessionLocal()
    try:
        shop_rows = list(db.scalars(select(Shop).order_by(Shop.id.asc())))
        shops = {shop.id: shop for shop in shop_rows}
        targets = [shop.id for shop in shop_rows if shop.shop_cookie == 1 and shop.cookie_data]
    finally:
        db.close()
    start_day, end_day = resolve_range(start_date, end_date)
    cacheable = _within_30d(start_day)
    range_key = _range_key(start_day, end_day)
    if not targets:
        with _PROGRESS:
            _PROGRESS_SLOT.update(running=False, done=0, total=0, failed=0, message="还没有可以拉取的店铺凭证")
        write_state(
            last_update=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            last_message="还没有可以拉取的店铺凭证",
            last_failed=0,
            last_ok=False,
            last_trigger=trigger,
        )
        return
    results: dict[int, tuple[bool, str, int, list[dict]]] = {}
    seen: set[int] = set()
    with _PROGRESS:
        _PROGRESS_SLOT.update(
            running=True, done=0, total=len(targets), failed=0, message="", trigger=trigger
        )

    gate = threading.Lock()

    def pull(store_pk: int) -> tuple[bool, str, int, list[dict]]:
        result = _pull_one(store_pk, start_ts, end_ts, force, cacheable)
        with gate:
            results[store_pk] = result
            seen.add(store_pk)
            with _PROGRESS:
                _PROGRESS_SLOT["done"] = min(int(_PROGRESS_SLOT["total"]), len(seen))
        return result

    try:
        take_turns(targets, pull)
    finally:
        # 超出 30 天的拉取结果放进程内存给当前查询用；近 30 天的已在 _pull_one 里写进 Redis
        if not cacheable:
            all_rows: list[dict] = []
            for pk, (_ok, _reason, _total, items) in results.items():
                if items:
                    all_rows.extend(_display_rows(items, shops.get(pk)))
            _live_store(range_key, all_rows)
        stats = {"with_violation": 0, "rows": 0, "clear": 0}
        reasons: list[str] = []
        failed = 0
        for _pk, (ok, reason, total, _items) in results.items():
            if not ok:
                failed += 1
                if reason and reason not in reasons and len(reasons) < 3:
                    reasons.append(reason)
            elif total > 0:
                stats["with_violation"] += 1
                stats["rows"] += total
            elif total == 0:
                stats["clear"] += 1
        ok_count = max(0, len(targets) - failed)
        parts = []
        if stats["with_violation"]:
            parts.append(f"{stats['with_violation']} 家有违规（共 {stats['rows']} 条）")
        if stats["clear"]:
            parts.append(f"{stats['clear']} 家没有查到违规记录")
        if failed:
            parts.append(f"{failed} 家拉取失败")
        detail = "，".join(parts) if parts else "没有可统计的店铺"
        message = f"{ok_count} 家完成：{detail}"
        if failed and reasons:
            message += f"（{'；'.join(reasons)}）"
        with _PROGRESS:
            _PROGRESS_SLOT.update(
                running=False, done=int(_PROGRESS_SLOT["total"]), failed=failed, message=message
            )
        write_state(
            last_update=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            last_message=message,
            last_failed=failed,
            last_ok=failed == 0,
            last_trigger=trigger,
        )
    _LOG.info("违规与预警更新结束，%s 家：有违规 %s 家 %s 条，无违规 %s 家，失败 %s 家",
              len(targets), stats["with_violation"], stats["rows"], stats["clear"], failed)


def _do_refresh(start_date: str, end_date: str, trigger: str) -> None:
    """真正干活：拉一遍、写进度、写状态。调用前需要先拿到 _JOB 锁。"""
    start_ts, end_ts = _ts_range(start_date, end_date)
    _refresh_all(start_ts, end_ts, force=True, trigger=trigger, start_date=start_date, end_date=end_date)


def run_refresh_sync(start_date: str = "", end_date: str = "", trigger: str = "schedule") -> dict:
    """同步跑一次（定时任务用）。已经有任务在跑就不重复，直接返回当前进度。"""
    if not _JOB.acquire(blocking=False):
        return progress()
    try:
        _do_refresh(start_date, end_date, trigger)
    except Exception:
        _LOG.exception("违规与预警更新失败")
        with _PROGRESS:
            _PROGRESS_SLOT["running"] = False
            _PROGRESS_SLOT["message"] = "拉取过程出错了，请查看服务端日志"
        write_state(
            last_update=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            last_message="拉取过程出错了",
            last_ok=False,
            last_trigger=trigger,
        )
    finally:
        _JOB.release()
    return progress()


def start_refresh(start_date: str = "", end_date: str = "") -> dict:
    """页面点拉取最新时立刻返回，进度在后台走。预热已经在跑就跟着那一次，不再另开。"""
    with _PROGRESS:
        if _PROGRESS_SLOT.get("running"):
            return dict(_PROGRESS_SLOT)
    if not _JOB.acquire(blocking=False):
        return progress()

    def run() -> None:
        try:
            _do_refresh(start_date, end_date, "manual")
        except Exception:
            _LOG.exception("违规与预警更新失败")
            with _PROGRESS:
                _PROGRESS_SLOT["running"] = False
                _PROGRESS_SLOT["message"] = "拉取过程出错了，请查看服务端日志"
            write_state(
                last_update=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                last_message="拉取过程出错了",
                last_ok=False,
                last_trigger="manual",
            )
        finally:
            _JOB.release()

    with _PROGRESS:
        _PROGRESS_SLOT.update(
            running=True, done=0, total=int(_PROGRESS_SLOT.get("total") or 0), failed=0, message="", trigger="manual"
        )
    threading.Thread(target=run, name="violation-refresh", daemon=True).start()
    return progress()

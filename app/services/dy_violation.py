"""违规与预警调用的抖店接口。"""
from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any

from app.services.dy_client import ShopApiError, ShopAuthError, request_shop

# ---------------------------------------------------------------------------
# 违规与预警
#
# 抖店后台「违规管理」页（/ffa/govern-guarantee/regulation/violation）用的接口。
# 路径和参数是从该页面的前端代码里确认过的，不需要再抓包。
# ---------------------------------------------------------------------------

PENALTY_LIST_PATH = "/governance/shop/penalty/get_penalty_list"
PENALTY_DETAIL_PATH = "/governance/shop/penalty/v3/get_ticket_detail"
PENALTY_ENUM_PATH = "/governance/shop/penalty/enum_config"
_VIOLATION_REFERER = "https://fxg.jinritemai.com/ffa/govern-guarantee/regulation/violation"


def _first(item: dict, *keys: str) -> Any:
    for key in keys:
        value = item.get(key)
        if value not in (None, "", [], {}):
            return value
    return ""


def _enum_label(enums: dict[str, dict[str, str]], group: str, value: Any) -> str:
    mapping = enums.get(group) or {}
    if value in (None, ""):
        return ""
    return mapping.get(str(value), "")


def fetch_penalty_enums(cookies: dict[str, str]) -> dict[str, dict[str, str]]:
    """处罚方式、状态等数字值对应的中文。拿不到就返回空，不阻断列表拉取。"""
    try:
        data = request_shop(cookies, "POST", PENALTY_ENUM_PATH, json_body={})
    except (ShopApiError, ShopAuthError):
        return {}
    enums: dict[str, dict[str, str]] = {}
    if not isinstance(data, dict):
        return enums
    for group, items in data.items():
        if not isinstance(items, list):
            continue
        enums[group] = {
            str(item.get("value")): str(item.get("label"))
            for item in items
            if isinstance(item, dict) and item.get("value") not in (None, "")
        }
    return enums


def _score_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)[:50]
    if number == 0:
        return "0分"
    return f"{number:+.0f}分"


def _penalty_text(enums: dict[str, dict[str, str]], value: Any) -> str:
    if isinstance(value, list):
        texts = [_enum_label(enums, "penalize_type", item) or str(item) for item in value]
        return "、".join(text for text in texts if text)[:50]
    label = _enum_label(enums, "penalize_type", value)
    return (label or str(value or ""))[:50]


def _type_text(enums: dict[str, dict[str, str]], item: dict) -> str:
    """违规类型：抖店主要给 node_name（如「商家超时未履约开票义务」）。"""
    text = _first(item, "node_name", "violation_type_desc", "zhili_tag_code_cn", "scene_type_text")
    if text:
        return str(text)[:50]
    severity = _first(item, "severity_level_desc", "severity_level")
    label = _enum_label(enums, "severity_level", severity)
    return (label or str(severity or ""))[:50]


def _execute_penalty(item: dict, enums: dict[str, dict[str, str]]) -> str:
    """处罚方式：优先用 penalty_types_text，其次看处置计划里的名字。"""
    text = str(item.get("penalty_types_text") or "").strip()
    if not text:
        names = []
        for plan in item.get("execute_plans") or []:
            if not isinstance(plan, dict):
                continue
            name = str(plan.get("exec_type_name") or plan.get("biz_show_desc") or "").strip()
            if name and name not in names:
                names.append(name)
        text = "、".join(names)
    if not text:
        text = _penalty_text(enums, _first(item, "penalize_type", "penalty_type", "punish_type"))
    return text[:50]


def _shape_violation(item: dict, enums: dict[str, dict[str, str]]) -> dict:
    """把抖店的一条违规单统一成本模块的字段名。

    抖店返回的真实结构（2026-11 实测）：
      ticket_id / violation_time / node_name / show_violation_reason /
      violation_detail / status_text_detail.main_status_text / execute_plans[].exec_type_name
    """
    detail = item.get("status_text_detail") if isinstance(item.get("status_text_detail"), dict) else {}
    status_text = str(detail.get("main_status_text") or "").strip()
    if not status_text:
        status_text = _enum_label(enums, "penalty_status", _first(item, "penalty_status", "status"))
    description = str(item.get("violation_detail") or "").strip() or str(
        _first(item, "show_violation_reason", "violation_reason", "reason", "remark")
    ).strip()
    return {
        "violation_id": str(_first(item, "ticket_id", "ticket_no", "penalty_ticket_id", "id"))[:64],
        "time": str(_first(item, "violation_time", "penalty_time", "create_time"))[:32],
        "type": _type_text(enums, item),
        "penalty": _execute_penalty(item, enums),
        "credit": _score_text(_first(item, "violation_score", "score_change", "credit_change", "score"))[:50],
        "description": description[:1000],
        "status": (status_text or str(_first(item, "ticket_status") or ""))[:20],
    }


def fetch_violation_list(
    cookies: dict[str, str],
    start_ts: int,
    end_ts: int,
    *,
    page_size: int = 50,
    max_pages: int = 10,
) -> tuple[list[dict], int]:
    """按时间范围拉一家店的违规记录，返回（记录列表, 平台显示的总条数）。

    分页要点（实测确认）：
    - 抖店这个接口的 page 从 0 开始，传 1 会漏掉第一页（最新的记录）；
    - page_size 最大一次能给到 50 条，正常店铺一页就能拿全；
    - sort 要带上 sort_type / sort_params，否则返回条数不稳定；
    - 同一店铺短时间内连续请求会被限流，返回空列表（total 也是 0），
      所以翻页之间要留间隔，翻到空页时如果总数还没拿够，等一会再重试。
    """
    rows: list[dict] = []
    seen: set[str] = set()
    total = 0
    page = 0
    empty_retries = 0
    body = {
        "condition": {"create_time_start": start_ts, "create_time_end": end_ts},
        "sort": {"sort_type": 1, "sort_params": "create_time"},
        "request_env": {},
        "bucket_id": "",
        "page_tab": "",
        "page": 0,
        "page_size": page_size,
    }
    while page < max_pages:
        body["page"] = page
        data = request_shop(cookies, "POST", PENALTY_LIST_PATH, json_body=body)
        try:
            total = int(data.get("total") or 0)
        except (TypeError, ValueError):
            total = 0
        tickets = data.get("tickets") if isinstance(data.get("tickets"), list) else []
        if not tickets:
            # 总数还没拿够却返回空，通常是限流，等一会儿再试同一页
            if rows and len(rows) < total and empty_retries < 6:
                empty_retries += 1
                time.sleep(min(8 * empty_retries, 30))
                continue
            break
        empty_retries = 0
        added = 0
        for item in tickets:
            if not isinstance(item, dict):
                continue
            ticket_id = str(item.get("ticket_id") or "")
            if ticket_id and ticket_id in seen:
                continue
            if ticket_id:
                seen.add(ticket_id)
            rows.append(_shape_violation(item, {}))
            added += 1
        if not added or len(tickets) < page_size or (total and len(rows) >= total):
            break
        page += 1
        time.sleep(3)
    return rows, total


def fetch_appeal_ticket_ids(cookies: dict[str, str]) -> list[str]:
    """首页「待处理违规」对应还能申诉的违规单编号。"""
    body = {
        "condition": {"quick_condition": "can_appeal"},
        "sort": {"sort_type": 1, "sort_params": "create_time"},
        "request_env": {},
        "bucket_id": "",
        "page_tab": "",
        "page": 0,
        "page_size": 50,
    }
    ids: list[str] = []
    seen: set[str] = set()
    for page in range(3):
        body["page"] = page
        data = request_shop(cookies, "POST", PENALTY_LIST_PATH, json_body=body)
        tickets = data.get("tickets") if isinstance(data.get("tickets"), list) else []
        for item in tickets:
            if not isinstance(item, dict):
                continue
            ticket_id = str(item.get("ticket_id") or "").strip()
            if ticket_id and ticket_id not in seen:
                seen.add(ticket_id)
                ids.append(ticket_id)
        try:
            total = int(data.get("total") or 0)
        except (TypeError, ValueError):
            total = 0
        if not tickets or len(tickets) < 50 or len(ids) >= total:
            break
    return ids


_OBJECT_LABELS = {
    1: ("订单", "订单ID"),
    2: ("商品", "商品ID"),
    3: ("店铺", "店铺ID"),
    4: ("达人", "达人ID"),
    5: ("视频", "视频ID"),
    6: ("直播间", "直播ID"),
    25: ("渠道品", "渠道ID"),
    32: ("售后单", "售后单ID"),
}
_LEVELS = {1: "轻微", 2: "一般", 3: "严重", 4: "特别严重"}


def _plain(value: Any, *, keep_lines: bool = False) -> str:
    text = str(value or "")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    if keep_lines:
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
        return "\n".join(line for line in lines if line)[:2000]
    return re.sub(r"\s+", " ", text).strip()[:2000]


def _unix_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return str(value)[:32]
    if number > 10**12:
        number //= 1000
    if number < 10**9:
        return ""
    return datetime.fromtimestamp(number).strftime("%Y-%m-%d %H:%M:%S")


_TIME_TAIL = re.compile(
    r"^(处置生效时间：.+?\d{1,2}:\d{2}(?:-\d{4}.+?\d{1,2}:\d{2})?)\s*[,，]\s*(.+)$"
)


def _plan_lines(description: Any) -> list[str]:
    """处置说明。生效时间单独一行，后面的补充说明再另起一行。"""
    text = _plain(description, keep_lines=True)
    lines: list[str] = []
    for line in text.splitlines():
        matched = _TIME_TAIL.match(line)
        if matched:
            lines.append(matched.group(1).strip()[:200])
            extra = matched.group(2).strip()
            if extra:
                lines.append(extra[:500])
        elif line:
            lines.append(line[:500])
        if len(lines) >= 8:
            break
    return lines


def _plans(data: dict, info: dict) -> list[dict]:
    """处置结果与解除指引。每条处置计划一张卡片。"""
    raw = data.get("execute_plans")
    if not isinstance(raw, list):
        raw = info.get("execute_plans") if isinstance(info.get("execute_plans"), list) else []
    plans: list[dict] = []
    for plan in raw:
        if not isinstance(plan, dict):
            continue
        title = plan.get("title") if isinstance(plan.get("title"), dict) else {}
        name = str(title.get("name") or plan.get("biz_show_desc") or "").strip()
        tags = title.get("tags") if isinstance(title.get("tags"), list) else []
        tag = tags[0] if tags and isinstance(tags[0], dict) else {}
        status = str(tag.get("label") or "").strip()
        extra = str(tag.get("subLabel") or tag.get("sub_label") or "").strip()
        tip = str(title.get("tip") or "").strip()
        lines = _plan_lines(plan.get("description"))
        if not name and not status and not lines:
            continue
        plans.append(
            {
                "name": name[:200],
                "status": status[:40],
                "status_extra": extra[:40],
                "tip": tip[:300],
                "lines": lines,
            }
        )
        if len(plans) >= 12:
            break
    return plans


def _plan_names(data: dict, info: dict) -> list[str]:
    names: list[str] = []
    pools = [data.get("execute_plans"), info.get("execute_plans")]
    for plans in pools:
        if not isinstance(plans, list):
            continue
        for plan in plans:
            if not isinstance(plan, dict):
                continue
            title = plan.get("title") if isinstance(plan.get("title"), dict) else {}
            name = str(title.get("name") or plan.get("biz_show_desc") or plan.get("exec_type_name") or "").strip()
            if name and name not in names:
                names.append(name)
    return names


def _object(info: dict) -> dict:
    obj = info.get("object") if isinstance(info.get("object"), dict) else {}
    if not obj:
        listed = info.get("object_list")
        if isinstance(listed, list) and listed and isinstance(listed[0], dict):
            obj = listed[0]
    try:
        kind = int(obj.get("object_type") or 0)
    except (TypeError, ValueError):
        kind = 0
    label, id_label = _OBJECT_LABELS.get(kind, ("", "对象ID"))
    image = ""
    imgs = obj.get("object_imgs") if isinstance(obj.get("object_imgs"), list) else []
    for item in imgs:
        url = str(item or "")
        if url.startswith("https://") and " " not in url:
            image = url
            break
    object_id = str(obj.get("object_id") or "")[:64]
    return {
        "object_type": label,
        "object_name": str(obj.get("object_name") or "")[:200],
        "object_id": object_id,
        "object_id_label": id_label if object_id else "",
        "image": image[:500],
    }


def _rules(data: dict, info: dict) -> list[dict]:
    raw = data.get("rule_instruction_list")
    if not isinstance(raw, list):
        raw = info.get("rule_instruction_list") if isinstance(info.get("rule_instruction_list"), list) else []
    rules: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        url = str(item.get("rule_instruction_url") or "")
        host_ok = url.startswith("https://") and "jinritemai.com" in url and " " not in url
        name = str(item.get("rule_instruction_name") or "").strip()
        explain = _plain(item.get("llm_explain"))
        if not name and not explain:
            continue
        rules.append({"name": name[:200], "url": url[:500] if host_ok else "", "explain": explain[:1000]})
        if len(rules) >= 3:
            break
    return rules


def _points(info: dict) -> list[str]:
    evidence = info.get("violation_evidence") if isinstance(info.get("violation_evidence"), dict) else {}
    raw = evidence.get("shop_violation_point_cn_list")
    if not isinstance(raw, list):
        return []
    points: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            text = _plain(item.get("name") or item.get("text") or "")
        else:
            text = _plain(item)
        if text and text not in points:
            points.append(text[:200])
        if len(points) >= 20:
            break
    return points


def _status_text(data: dict, info: dict) -> str:
    for source in (info, data):
        detail = source.get("status_text_detail")
        if isinstance(detail, dict):
            text = str(detail.get("main_status_text") or "").strip()
            if text:
                return text[:20]
        text = source.get("status_text")
        if isinstance(text, str) and text.strip():
            return text.strip()[:20]
    return ""


def _shape_detail(data: dict, ticket_id: str) -> dict:
    """处罚详情页用的字段。抖店详情在 info 里，处置计划有的挂在外层。"""
    if not isinstance(data.get("info"), dict) and isinstance(data.get("ticket"), dict):
        data = data["ticket"]
    info = data.get("info") if isinstance(data.get("info"), dict) else {}
    reason = str(
        info.get("show_violation_reason") or data.get("violation_reason") or data.get("show_violation_reason") or ""
    ).strip()
    if reason in {"--", "-"}:
        reason = ""
    try:
        level = int(info.get("circumstances_level") or data.get("circumstances_level") or 0)
    except (TypeError, ValueError):
        level = 0
    shaped = {
        "violation_id": str(data.get("ticket_id") or ticket_id)[:64],
        "violated_at": _unix_text(info.get("violation_time") or data.get("violation_time")),
        "penalized_at": _unix_text(data.get("penalty_time") or info.get("penalty_time") or data.get("execute_time")),
        "reason": _plain(reason)[:500],
        "level": _LEVELS.get(level, ""),
        "penalty": "、".join(_plan_names(data, info))[:200],
        "description": _plain(info.get("violation_detail") or data.get("violation_detail"), keep_lines=True),
        "points": _points(info),
        "rules": _rules(data, info),
        "plans": _plans(data, info),
        "status": _status_text(data, info),
    }
    shaped.update(_object(info or data))
    return shaped


def fetch_violation_detail(cookies: dict[str, str], ticket_id: str) -> dict:
    """点开一条违规时现拉处罚详情。先按处罚单查，没有再按预警单查。"""
    last = ShopApiError("没有查到这条处罚详情")
    for ticket_type in ("penalty", "risk"):
        body = {
            "ticket_id": ticket_id,
            "standard_ticket_type": ticket_type,
            "request_env": {"request_source": 1},
            "need_unfreeze_progress": False,
        }
        try:
            data = request_shop(cookies, "POST", PENALTY_DETAIL_PATH, json_body=body)
        except ShopAuthError:
            raise
        except ShopApiError as exc:
            last = exc
            continue
        if not data:
            continue
        got = str(data.get("ticket_id") or "")
        if got and got != str(ticket_id):
            nested = data.get("ticket") if isinstance(data.get("ticket"), dict) else {}
            got = str(nested.get("ticket_id") or got)
        if got and got != str(ticket_id):
            raise ShopApiError("处罚详情和这条违规对不上")
        return _shape_detail(data, ticket_id)
    raise last

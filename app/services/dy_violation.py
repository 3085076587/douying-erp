"""违规与预警调用的抖店接口。"""
from __future__ import annotations

import time
from typing import Any

from app.services.dy_client import ShopApiError, ShopAuthError, request_shop

# ---------------------------------------------------------------------------
# 违规与预警
#
# 抖店后台「违规管理」页（/ffa/govern-guarantee/regulation/violation）用的接口。
# 路径和参数是从该页面的前端代码里确认过的，不需要再抓包。
# ---------------------------------------------------------------------------

PENALTY_LIST_PATH = "/governance/shop/penalty/get_penalty_list"
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
            # 总数还没拿够却返回空，通常是限流，等 60 秒再试同一页
            if rows and len(rows) < total and empty_retries < 2:
                empty_retries += 1
                time.sleep(60)
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

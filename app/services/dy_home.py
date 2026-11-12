"""抖店首页红框里的待办条。"""
from __future__ import annotations

from typing import Any

from app.services.dy_client import request_shop

_HOME_PATH = "/pc/api/home/homepage"
# 店铺首页蓝条用的是这一版。不带版本时接口给的是旧待办，异常包裹没有，待整改风险点会落到「可整改」。
_HOME_VERSION = "202606240"
_FIELDS = (
    ("pending_pay", ("unpaid",), ""),
    ("pending_ship", ("unsend",), ""),
    ("abnormal_package", ("abnormal_package", "exception_package"), "异常包裹"),
    ("pending_aftersale", ("unprocess",), ""),
    ("service_ticket", ("service_order",), ""),
    ("risk_point", ("to_berectified_risk",), "待整改风险点"),
    ("pending_violation", ("violation_pending",), ""),
)


def _todo_rows(node: Any, found: list[dict]) -> None:
    if isinstance(node, dict):
        if "title" in node and "val" in node and (node.get("key") or node.get("agg_name")):
            found.append(node)
        for value in node.values():
            _todo_rows(value, found)
    elif isinstance(node, list):
        for value in node:
            _todo_rows(value, found)


def parse_home_todos(data: dict) -> dict[str, str]:
    """首页待办。对不上的项按 0，不把经营数据混进来。"""
    rows: list[dict] = []
    _todo_rows(data, rows)
    by_key: dict[str, str] = {}
    for row in rows:
        key = str(row.get("key") or "").strip()
        if key and key not in by_key:
            by_key[key] = str(row.get("val") if row.get("val") not in (None, "") else "0")
    result: dict[str, str] = {}
    for field, keys, title_has in _FIELDS:
        value = ""
        for key in keys:
            if key in by_key:
                value = by_key[key]
                break
        if not value and title_has:
            for row in rows:
                if title_has in str(row.get("title") or ""):
                    value = str(row.get("val") if row.get("val") not in (None, "") else "0")
                    break
        result[field] = value or "0"
    return result


def fetch_home_todos(cookies: dict[str, str]) -> dict[str, str]:
    data = request_shop(cookies, "GET", _HOME_PATH, params={"homepage_version": _HOME_VERSION})
    return parse_home_todos(data if isinstance(data, dict) else {})

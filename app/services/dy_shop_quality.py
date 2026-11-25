"""店铺明细里的体验分和近 30 天评价率。分数按页面原样，失败时留空，不把店铺判成掉线。"""
from __future__ import annotations

from typing import Any

from app.services.dy_client import ShopApiError, ShopAuthError, request_shop

QUALITY_FIELDS = (
    "experience_score",
    "goods_score",
    "goods_score_change",
    "logistics_score",
    "logistics_score_change",
    "service_score",
    "service_score_change",
    "bonus_score",
    "bonus_score_change",
    "deduct_score",
    "deduct_score_change",
    "good_rate_30d",
    "mid_rate_30d",
    "bad_rate_30d",
    "quality_return_rate",
)
_RETURN_NODE = 103
_RETURN_DETAIL_NODE = 10301
_RETURN_PAGE = 20
_RETURN_PAGES = 15
_EMPTY_SCORE = -9999


def _score_text(node: Any) -> str:
    if not isinstance(node, dict):
        return ""
    try:
        number = float(node.get("score"))
    except (TypeError, ValueError):
        return ""
    if number <= _EMPTY_SCORE or number < 0:
        return ""
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _change_text(node: Any) -> str:
    """体验分页分数下面的「较前日」。升是 ↑，降是 ↓，没有变化是持平。"""
    if not _score_text(node):
        return ""
    try:
        number = float(node.get("rise_than_yesterday"))
    except (TypeError, ValueError):
        return ""
    if number <= _EMPTY_SCORE:
        return ""
    if number == 0:
        return "较前日 持平"
    shown = str(int(number)) if float(number).is_integer() else f"{number:.2f}".rstrip("0").rstrip(".")
    if number > 0:
        return "较前日 ↑" + shown
    shown = shown[1:] if shown.startswith("-") else shown
    return "较前日 ↓" + shown


def _ratio_percent(number: Any) -> str:
    """接口给的是小数，页面按百分数显示。0.00737 显示成 0.737%。"""
    try:
        figure = float(number)
    except (TypeError, ValueError):
        return ""
    text = f"{figure * 100:.4f}".rstrip("0").rstrip(".")
    return (text or "0") + "%"


def _rate_text(node: Any) -> str:
    if not isinstance(node, dict):
        return ""
    text = str(node.get("last_30days_value") or "").strip()
    if not text or text in {"-", "—"}:
        return ""
    return text


def _read(cookies: dict[str, str], path: str, params: dict | None = None) -> dict:
    try:
        data = request_shop(cookies, "GET", path, params=params)
    except (ShopAuthError, ShopApiError):
        return {}
    return data if isinstance(data, dict) else {}


def _return_rate(overview: dict) -> str:
    info = overview.get("formal_score_metric_info")
    metrics = info.get("metrics") if isinstance(info, dict) else None
    if not isinstance(metrics, list):
        return ""
    for item in metrics:
        if not isinstance(item, dict):
            continue
        if item.get("node_id") == _RETURN_NODE or item.get("title") == "商品品质退货率":
            value = item.get("value")
            figure = value.get("value_figure") if isinstance(value, dict) else None
            return _ratio_percent(figure)
    return ""


def _detail_row(row: dict) -> dict:
    return {
        "product_id": str(row.get("prod_id") or ""),
        "product_name": str(row.get("prod_name") or ""),
        "category": str(row.get("leaf_name_list") or ""),
        "order_count": "" if row.get("prod_order_cnt") is None else str(row.get("prod_order_cnt")),
        "contribute_rate": _ratio_percent(row.get("metric_contribute_rate")),
        "reasons": str(row.get("reason_list_str") or ""),
    }


def fetch_quality_return_details(cookies: dict[str, str]) -> tuple[list[dict], str]:
    """近 30 天商品品质退货率的商品明细。一页 20 条，翻完为止。"""
    items: list[dict] = []
    offset = 0
    for _ in range(_RETURN_PAGES):
        data = request_shop(
            cookies,
            "POST",
            "/governance/shop/experiencescore/getDetailList",
            json_body={
                "exp_version": "v10",
                "experience_node": _RETURN_DETAIL_NODE,
                "time": "30",
                "offset": offset,
                "count": _RETURN_PAGE,
                "filters": [],
                "sorts": [],
            },
        )
        rows = data.get("goods_items") if isinstance(data, dict) else None
        if isinstance(rows, list):
            items.extend(_detail_row(row) for row in rows if isinstance(row, dict))
        try:
            total = int(data.get("total") or 0) if isinstance(data, dict) else 0
        except (TypeError, ValueError):
            total = 0
        offset += _RETURN_PAGE
        if not isinstance(data, dict) or not data.get("hasMore") or offset >= total or not rows:
            break
    return items, ""


def fetch_shop_quality(cookies: dict[str, str]) -> dict[str, str]:
    """总分、商品体验、物流体验、服务体验、商品品质退货率，以及近 30 天好评、中评、差评率。"""
    result = {field: "" for field in QUALITY_FIELDS}
    overview = _read(
        cookies,
        "/governance/shop/experiencescore/getOverviewV10",
        {"exp_version": "v10"},
    )
    info = overview.get("formal_score_info")
    if isinstance(info, dict):
        result["experience_score"] = _score_text(info.get("experience_score"))
        result["goods_score"] = _score_text(info.get("goods_score"))
        result["goods_score_change"] = _change_text(info.get("goods_score"))
        result["logistics_score"] = _score_text(info.get("logistics_score"))
        result["logistics_score_change"] = _change_text(info.get("logistics_score"))
        result["service_score"] = _score_text(info.get("service_score"))
        result["service_score_change"] = _change_text(info.get("service_score"))
        result["bonus_score"] = _score_text(info.get("good_behavior_add_score"))
        result["bonus_score_change"] = _change_text(info.get("good_behavior_add_score"))
        result["deduct_score"] = _score_text(info.get("bad_behavior_deduct_score"))
        result["deduct_score_change"] = _change_text(info.get("bad_behavior_deduct_score"))
    score = _read(
        cookies,
        "/governance/shop/experiencescore/getSubScoreMetricsV10",
        {"exp_version": "v10"},
    )
    result["quality_return_rate"] = _return_rate(score)
    review = _read(cookies, "/product/tcomment/statistics")
    result["good_rate_30d"] = _rate_text(review.get("positive_comment_rate"))
    result["mid_rate_30d"] = _rate_text(review.get("neutral_comment_rate"))
    result["bad_rate_30d"] = _rate_text(review.get("negative_comment_rate"))
    return result

"""只看一家店昨天的罗盘指标名单。不写成交日表，不打印登录凭证。"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.shop import Shop  # noqa: E402
from app.services.center_service import _cell_number  # noqa: E402
from app.services.dy_client import FXG_HOST, ShopApiError, ShopAuthError, cookie_store, cookies_for  # noqa: E402
from app.services.dy_compass import fetch_operating_cards  # noqa: E402
from app.services.store_service import keep_shop_jar  # noqa: E402

WANTED = (
    "成交金额",
    "用户支付金额",
    "成交订单数",
    "成交人数",
    "退款金额（支付时间）",
    "退款率（支付时间）",
    "退款订单数（支付时间）",
)
_NAME_KEYS = ("index_name", "index_display", "display_name", "name", "title", "index_display_name")


def _names_near(node: dict) -> str:
    extra = node.get("extra") if isinstance(node.get("extra"), dict) else {}
    found = []
    for source in (extra, node):
        for key in _NAME_KEYS:
            text = str(source.get(key) or "").strip()
            if text and text not in found and len(text) <= 40:
                found.append(text)
    return " / ".join(found)


def _rows(payload: dict) -> list[tuple[str, str, str, str]]:
    rows: list[tuple[str, str, str, str]] = []
    seen: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            extra = node.get("extra") if isinstance(node.get("extra"), dict) else {}
            code = str(extra.get("index_code") or node.get("index_code") or "").strip()
            info = node.get("cell_info") if isinstance(node.get("cell_info"), dict) else None
            values = None
            if info is not None:
                wrapped = info.get("index_value")
                if isinstance(wrapped, dict) and isinstance(wrapped.get("index_values"), dict):
                    values = wrapped["index_values"]
            if code and isinstance(values, dict) and "value" in values and code not in seen:
                number, unit = _cell_number(values.get("value"))
                seen.add(code)
                rows.append((code, _names_near(node), "" if number is None else str(number), unit))
            for child in node.values():
                if isinstance(child, (dict, list)):
                    walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(payload)
    return rows


def main() -> None:
    day = date.today() - timedelta(days=1)
    stamp = day.strftime("%Y/%m/%d 00:00:00")
    print("要找的指标：" + "、".join(WANTED), flush=True)
    print(f"只读昨天 {day.isoformat()}，不写入成交日表", flush=True)
    db = SessionLocal()
    try:
        shops = list(db.scalars(select(Shop).where(Shop.shop_cookie == 1).order_by(Shop.id.asc())))
        if not shops:
            print("没有带登录凭证的店铺", flush=True)
            return
        shop = shops[0]
        cookies = cookies_for(cookie_store(shop.cookie_data), FXG_HOST)
        if not cookies:
            print(f"[跳过] {shop.name} 没有登录凭证", flush=True)
            return
        try:
            payload = fetch_operating_cards(
                cookies,
                shop_id=str(shop.shop_id or ""),
                date_type=2,
                begin_date=stamp,
                end_date=stamp,
            )
        except ShopAuthError as exc:
            keep_shop_jar(shop, cookies)
            db.commit()
            print(f"[掉线] {shop.name} {exc}", flush=True)
            return
        except ShopApiError as exc:
            keep_shop_jar(shop, cookies)
            db.commit()
            print(f"[失败] {shop.name} {exc}", flush=True)
            return
        keep_shop_jar(shop, cookies)
        db.commit()
        print(f"店铺 {shop.name}", flush=True)
        rows = _rows(payload)
        if not rows:
            print("这一天没有读到指标代码", flush=True)
            return
        for code, name, number, unit in rows:
            label = name or "（没有名称）"
            print(f"{code}\t{label}\t值 {number}\t单位 {unit or '-'}", flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()

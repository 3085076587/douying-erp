"""按 dy_product_day 里昨天及以前的日期，算出每个商品的退款率。不访问抖音，不含今天。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.session import engine
from app.models.product_refund_rate import DyProductRefundRate

_UPSERT = """
INSERT INTO dy_product_refund_rate (
    store_pk, shop_id, shop_name, product_id, product_name,
    start_date, end_date, day_count,
    pay_amount, order_count, refund_amount, refund_rate,
    created_at, updated_at
)
SELECT
    d.store_pk, d.shop_id, d.shop_name, d.product_id, d.product_name,
    t.start_date, t.end_date, t.day_count,
    t.pay_amount, t.order_count, t.refund_amount,
    CASE
        WHEN t.user_pay_amount > 0 THEN CAST(ROUND(t.refund_amount * 10000 / t.user_pay_amount) AS SIGNED)
        ELSE -1
    END,
    :now, :now
FROM (
    SELECT
        store_pk,
        product_id,
        SUM(pay_amount) AS pay_amount,
        SUM(user_pay_amount) AS user_pay_amount,
        SUM(order_count) AS order_count,
        SUM(refund_amount) AS refund_amount,
        MIN(stat_date) AS start_date,
        MAX(stat_date) AS end_date,
        COUNT(*) AS day_count
    FROM dy_product_day
    WHERE product_id <> ''
      AND stat_date < CURDATE()
    GROUP BY store_pk, product_id
) t
JOIN dy_product_day d
  ON d.store_pk = t.store_pk
 AND d.product_id = t.product_id
 AND d.stat_date = t.end_date
ON DUPLICATE KEY UPDATE
    shop_id = VALUES(shop_id),
    shop_name = VALUES(shop_name),
    product_name = VALUES(product_name),
    start_date = VALUES(start_date),
    end_date = VALUES(end_date),
    day_count = VALUES(day_count),
    pay_amount = VALUES(pay_amount),
    order_count = VALUES(order_count),
    refund_amount = VALUES(refund_amount),
    refund_rate = VALUES(refund_rate),
    updated_at = VALUES(updated_at)
"""

_DROP_GONE = """
DELETE r FROM dy_product_refund_rate r
LEFT JOIN (
    SELECT store_pk, product_id
    FROM dy_product_day
    WHERE product_id <> ''
      AND stat_date < CURDATE()
    GROUP BY store_pk, product_id
) t
  ON r.store_pk = t.store_pk AND r.product_id = t.product_id
WHERE t.product_id IS NULL
"""


def ensure_refund_rate_table() -> None:
    with engine.begin() as conn:
        conn.execute(text(
            """
            CREATE TABLE IF NOT EXISTS dy_product_refund_rate (
                id INT NOT NULL AUTO_INCREMENT,
                store_pk INT NOT NULL,
                shop_id VARCHAR(32) NOT NULL DEFAULT '',
                shop_name VARCHAR(100) NOT NULL DEFAULT '',
                product_id VARCHAR(40) NOT NULL DEFAULT '',
                product_name VARCHAR(255) NOT NULL DEFAULT '',
                start_date DATE NOT NULL,
                end_date DATE NOT NULL,
                day_count INT NOT NULL DEFAULT 0,
                pay_amount BIGINT NOT NULL DEFAULT 0,
                order_count BIGINT NOT NULL DEFAULT 0,
                refund_amount BIGINT NOT NULL DEFAULT 0,
                refund_rate INT NOT NULL DEFAULT -1,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                PRIMARY KEY (id),
                UNIQUE KEY uk_dy_product_refund_rate (store_pk, product_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        ))


def rebuild_refund_rates() -> str:
    """用昨天及以前的商品日重算。退款率是退款金额除以用户支付金额。今天不进这张表。"""
    from app.services.product_sync import ensure_product_table

    ensure_product_table()
    ensure_refund_rate_table()
    now = datetime.now().replace(microsecond=0)
    with engine.begin() as conn:
        conn.execute(text(_UPSERT), {"now": now})
        removed = conn.execute(text(_DROP_GONE)).rowcount
        total = conn.execute(text("SELECT COUNT(*) FROM dy_product_refund_rate")).scalar()
        span = conn.execute(text(
            "SELECT MIN(start_date), MAX(end_date) FROM dy_product_refund_rate"
        )).one()
    gone = int(removed or 0)
    if gone < 0:
        gone = 0
    return (
        f"退款率表 {int(total or 0)} 个商品，日期 {span[0]} 至 {span[1]}，"
        f"去掉 {gone} 个已无日数据的商品"
    )


def list_refund_rates(db: Session, product_id: str = "") -> list[dict]:
    stmt = select(DyProductRefundRate).order_by(DyProductRefundRate.product_id.asc())
    product_id = (product_id or "").strip()
    if product_id:
        stmt = stmt.where(DyProductRefundRate.product_id == product_id)
    items = []
    for row in db.scalars(stmt):
        rate = None if int(row.refund_rate) < 0 else round(int(row.refund_rate) / 100, 2)
        items.append({
            "updated_at": row.updated_at.strftime("%Y-%m-%d %H:%M:%S") if row.updated_at else "",
            "refund_rate": rate,
            "product_id": row.product_id,
        })
    return items

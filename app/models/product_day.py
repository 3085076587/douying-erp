"""商品日表：一家店一个商品一天一行。金额是分，比率是百分数乘 100。"""
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DyProductDay(Base):
    """除实时以外的商品明细。页面按日期把这些天加总，不再向抖音现拉。"""

    __tablename__ = "dy_product_day"
    __table_args__ = (
        Index("uk_dy_product_day", "store_pk", "product_id", "stat_date", unique=True),
        Index("ix_dy_product_day_date", "stat_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    store_pk: Mapped[int] = mapped_column(nullable=False, default=0)
    shop_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    shop_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    product_id: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    product_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    stat_date: Mapped[date] = mapped_column(Date, nullable=False)
    pay_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    user_pay_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    order_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    refund_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    refund_rate: Mapped[int] = mapped_column(Integer, nullable=False, default=-1, server_default="-1")
    good_rate: Mapped[int] = mapped_column(Integer, nullable=False, default=-1, server_default="-1")
    good_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    eval_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

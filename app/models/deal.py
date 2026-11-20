"""成交日表：一家店一天一行。金额是分，页面再换成元。"""
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Index, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DyDealDay(Base):
    """一天的成交。只给数据总览读取，不在打开页面时向抖音现拉。"""

    __tablename__ = "dy_deal_day"
    __table_args__ = (
        Index("uk_dy_deal_day", "store_pk", "stat_date", unique=True),
        Index("ix_dy_deal_day_date", "stat_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    store_pk: Mapped[int] = mapped_column(nullable=False, default=0)
    stat_date: Mapped[date] = mapped_column(Date, nullable=False)
    pay_gmv: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    pay_order_cnt: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    pay_uv: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    create_gmv: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    create_cnt: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    create_uv: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    pay_refund_gmv: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    refund_gmv: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    user_pay_gmv: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    refund_pay_gmv: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    refund_pay_ratio: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    refund_pay_cnt: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

"""一个商品在已入库的全部日期上的退款率。金额是分，退款率是百分数乘 100。"""
from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DyProductRefundRate(Base):
    """由 dy_product_day 加总而来。一家店一个商品一行，覆盖目前库里的所有自然日。"""

    __tablename__ = "dy_product_refund_rate"
    __table_args__ = (
        Index("uk_dy_product_refund_rate", "store_pk", "product_id", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    store_pk: Mapped[int] = mapped_column(nullable=False, default=0)
    shop_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    shop_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    product_id: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    product_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    day_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    pay_amount: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    order_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    refund_amount: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    refund_rate: Mapped[int] = mapped_column(Integer, nullable=False, default=-1, server_default="-1")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

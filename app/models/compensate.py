"""账单赔付：一家店的一条赔付流水。店铺用现有的 dy_shop。"""
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class DyCompensate(Base, TimestampMixin):
    """一条赔付流水。店铺 + 赔付单号唯一，同一天重复入库时更新。"""

    __tablename__ = "dy_compensate"
    __table_args__ = (
        Index("uk_dy_compensate_no", "store_pk", "compensate_id", unique=True),
        Index("ix_dy_compensate_time", "finished_at"),
        Index("ix_dy_compensate_shop", "store_pk"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    store_pk: Mapped[int] = mapped_column(nullable=False, default=0)
    shop_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    compensate_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    scene: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    recipient: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    direction: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    amount_fen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    compensator: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    account_name: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    shop_order_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    order_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    aftersale_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")

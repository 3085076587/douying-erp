"""违规与预警模块的表：抖店的违规记录。店铺直接用现有的 dy_shop。"""
from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class DyViolation(Base, TimestampMixin):
    """一条违规记录。店铺 + 违规编号唯一，重复拉取时更新而不是再插一条。"""

    __tablename__ = "dy_violation"
    __table_args__ = (
        Index("uk_dy_violation_no", "store_pk", "violation_id", unique=True),
        Index("ix_dy_violation_time", "violated_at"),
        Index("ix_dy_violation_shop", "store_pk"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    store_pk: Mapped[int] = mapped_column(nullable=False, default=0)
    shop_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    violation_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    violated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    violation_type: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    penalty: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    credit_change: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="")

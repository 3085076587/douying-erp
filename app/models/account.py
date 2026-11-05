"""账户中心快照。一家店一行，金额单位是分。"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class ShopAccount(Base, TimestampMixin):
    __tablename__ = "dy_shop_account"
    __table_args__ = (Index("uk_dy_shop_account_store", "store_pk", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    store_pk: Mapped[int] = mapped_column(ForeignKey("dy_shop.id"), nullable=False)
    balance: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    withdraw_balance: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reserve_balance: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    frozen_balance: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    pending_settle: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    settle_day: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    qianchuan_balance: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    deposit_base: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    deposit_experience: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    bank_name: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    bank_tail: Mapped[str] = mapped_column(String(8), nullable=False, default="")
    channel_type: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    channels_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sync_error: Mapped[str] = mapped_column(String(100), nullable=False, default="")

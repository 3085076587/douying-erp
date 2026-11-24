"""一家店当前的负责人。一家店一行，改负责人就是换这一行。"""
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ShopOwner(Base):
    """store_pk 是 dy_shop.id，charge_id 是 dy_charge_person.id。"""

    __tablename__ = "dy_shop_owner"
    __table_args__ = (
        Index("ix_dy_shop_owner_charge", "charge_id"),
    )

    store_pk: Mapped[int] = mapped_column(Integer, primary_key=True)
    charge_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

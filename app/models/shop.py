from datetime import datetime

from sqlalchemy import DateTime, Index, SmallInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Shop(Base, TimestampMixin):
    """一家抖店工作台。登录凭证只放在 cookie_data，接口不把它返回给页面。"""

    __tablename__ = "dy_shop"
    __table_args__ = (
        Index("uk_dy_shop_id", "shop_id", unique=True),
        Index("ix_dy_shop_login", "login_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    shop_id: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    subject_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    member_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    identity_desc: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    shop_type_text: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    operate_status: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    legal_person: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    charge_mobile: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    login_mobile: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    opened_on: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    cookie_data: Mapped[str | None] = mapped_column(Text, nullable=True)
    shop_cookie: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    login_status: Mapped[str] = mapped_column(String(20), nullable=False, default="offline")
    credential_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    owner_account: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    session_key: Mapped[str] = mapped_column(String(64), nullable=False, default="")

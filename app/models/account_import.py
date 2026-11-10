"""插件交回来的一次抖店登录。不参与现有店铺采集。"""
from sqlalchemy import String, Text
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class AccountImport(Base, TimestampMixin):
    __tablename__ = "dy_account_import"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    account: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    shops_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    cookies_json: Mapped[str] = mapped_column(MEDIUMTEXT, nullable=False)
    local_storage_json: Mapped[str] = mapped_column(MEDIUMTEXT, nullable=False, default="{}")
    ua: Mapped[str] = mapped_column(String(512), nullable=False, default="")

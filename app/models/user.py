from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class User(Base, TimestampMixin):
    """后台登录账号。和抖店邮箱不是同一个东西。"""

    __tablename__ = "sys_user"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    section_keys: Mapped[str | None] = mapped_column(String(64), nullable=True)

    @property
    def sections(self) -> list[str]:
        from app.services.access import sections_of

        return sections_of(self)

    @property
    def can_manage_members(self) -> bool:
        return self.username == "admin"

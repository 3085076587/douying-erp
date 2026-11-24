"""后台账号的板块授权。"""
from pydantic import BaseModel, Field


class ShopBrief(BaseModel):
    id: int
    name: str


class AccountAccess(BaseModel):
    id: int
    username: str
    display_name: str = ""
    sections: list[str]
    shops: list[ShopBrief] = Field(default_factory=list)


class ShopChoice(BaseModel):
    id: int
    name: str
    user_id: int | None = None
    display_name: str = ""


class AccountAccessList(BaseModel):
    items: list[AccountAccess]
    shops: list[ShopChoice] = Field(default_factory=list)


class ShopAssign(BaseModel):
    shop_ids: list[int] = Field(default_factory=list)


class SectionUpdate(BaseModel):
    sections: list[str] = Field(default_factory=list)


class AccountCreate(BaseModel):
    username: str = Field(min_length=2, max_length=50)
    display_name: str = Field(min_length=1, max_length=50)

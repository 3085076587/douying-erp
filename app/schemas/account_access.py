"""后台账号的板块授权。"""
from pydantic import BaseModel, Field


class AccountAccess(BaseModel):
    id: int
    username: str
    display_name: str = ""
    sections: list[str]


class AccountAccessList(BaseModel):
    items: list[AccountAccess]


class SectionUpdate(BaseModel):
    sections: list[str] = Field(default_factory=list)


class AccountCreate(BaseModel):
    username: str = Field(min_length=2, max_length=50)
    display_name: str = Field(min_length=1, max_length=50)

"""店铺负责人。姓名和登录账号分开。"""
from pydantic import BaseModel, Field


class ChargeShop(BaseModel):
    id: int
    name: str


class ChargePersonItem(BaseModel):
    id: int
    name: str
    shops: list[ChargeShop] = Field(default_factory=list)


class ChargeShopChoice(BaseModel):
    id: int
    name: str
    charge_id: int | None = None
    charge_name: str = ""


class ChargePersonList(BaseModel):
    items: list[ChargePersonItem]
    shops: list[ChargeShopChoice] = Field(default_factory=list)


class ChargeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=50)


class ChargeAssign(BaseModel):
    shop_ids: list[int] = Field(default_factory=list)

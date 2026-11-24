"""数据总览：成交日表读出来的结构。金额单位是分。"""
from pydantic import BaseModel, Field


class DealShopSeries(BaseModel):
    store_pk: int
    name: str = ""
    charge_name: str = ""
    pay_gmv: list[int] = Field(default_factory=list)
    user_pay_gmv: list[int] = Field(default_factory=list)
    pay_order_cnt: list[int] = Field(default_factory=list)
    pay_uv: list[int] = Field(default_factory=list)
    refund_pay_gmv: list[int] = Field(default_factory=list)
    refund_pay_ratio: list[int] = Field(default_factory=list)
    refund_pay_cnt: list[int] = Field(default_factory=list)


class DealGroup(BaseModel):
    account: str = ""
    shops: list[DealShopSeries] = Field(default_factory=list)


class DealOverview(BaseModel):
    start: str = ""
    end: str = ""
    days: list[str] = Field(default_factory=list)
    groups: list[DealGroup] = Field(default_factory=list)

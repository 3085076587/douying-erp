"""账单赔付的返回结构。金额单位是分，出账为负。"""
from pydantic import BaseModel


class CompensateShopOption(BaseModel):
    store_pk: int
    name: str = ""


class CompensateShopOptions(BaseModel):
    items: list[CompensateShopOption]


class CompensateShopRow(BaseModel):
    store_pk: int
    shop_name: str = ""
    amount_fen: int = 0
    count: int = 0


class CompensateShopList(BaseModel):
    items: list[CompensateShopRow]
    total_fen: int = 0
    shop_count: int = 0


class CompensateItem(BaseModel):
    compensate_id: str = ""
    finished_at: str = ""
    scene: str = ""
    recipient: str = ""
    direction: str = ""
    amount_fen: int = 0
    compensator: str = ""
    account_name: str = ""
    reason: str = ""
    shop_order_id: str = ""
    order_id: str = ""
    aftersale_id: str = ""


class CompensateDetail(BaseModel):
    store_pk: int
    shop_name: str = ""
    amount_fen: int = 0
    items: list[CompensateItem]

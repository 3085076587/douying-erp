"""发票数量。数量是张数或账单数，不是金额。"""
from pydantic import BaseModel


class InvoiceRow(BaseModel):
    store_pk: int
    shop_id: str = ""
    name: str = ""
    owner_account: str = ""
    login_status: str = ""
    platform_pending: int | None = None
    consumer_pending: int | None = None
    sync_error: str = ""


class InvoiceList(BaseModel):
    items: list[InvoiceRow]
    updated_at: str = ""
    platform_pending: int = 0
    consumer_pending: int = 0


class InvoiceProgress(BaseModel):
    running: bool = False
    done: int = 0
    total: int = 0
    failed: int = 0
    message: str = ""

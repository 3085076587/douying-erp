"""数据总览：每家店的罗盘收支概况。"""
from pydantic import BaseModel, Field


class CenterMetric(BaseModel):
    label: str = ""
    value_text: str = ""
    ratio_text: str = ""
    extra_text: str = ""


class CenterOverviewRow(BaseModel):
    store_pk: int
    shop_id: str = ""
    name: str = ""
    login_status: str = ""
    sync_error: str = ""
    metrics: list[CenterMetric] = []


class CenterOverviewList(BaseModel):
    preset: str = "realtime"
    date_type: int = 1
    start: str = ""
    end: str = ""
    updated_at: str = ""
    items: list[CenterOverviewRow]


class CenterProgress(BaseModel):
    running: bool = False
    done: int = 0
    total: int = 0
    failed: int = 0
    message: str = ""
    fresh: bool = False


class CenterHomeRow(BaseModel):
    store_pk: int
    shop_id: str = ""
    name: str = ""
    login_status: str = ""
    sync_error: str = ""
    pending_pay: str = ""
    pending_ship: str = ""
    abnormal_package: str = ""
    pending_aftersale: str = ""
    service_ticket: str = ""
    risk_point: str = ""
    pending_violation: str = ""
    pending_violation_ids: list[str] = []


class CenterHomeList(BaseModel):
    updated_at: str = ""
    items: list[CenterHomeRow]


class CenterShopRow(BaseModel):
    store_pk: int
    shop_id: str = ""
    name: str = ""
    login_status: str = ""
    sync_error: str = ""
    pay_amount: str = ""
    pay_amount_extra: str = ""
    order_count: str = ""
    order_count_extra: str = ""
    refund_rate: str = ""
    refund_rate_extra: str = ""
    refund_amount: str = ""
    refund_amount_extra: str = ""


class CenterShopList(BaseModel):
    preset: str = "realtime"
    date_type: int = 1
    start: str = ""
    end: str = ""
    interval_minutes: int = 10
    last_update: str = ""
    next_update: str = ""
    items: list[CenterShopRow]


class CenterProductRow(BaseModel):
    store_pk: int
    shop_id: str = ""
    name: str = ""
    login_status: str = ""
    sync_error: str = ""
    product_id: str = ""
    product_name: str = ""
    pay_amount: str = ""
    order_count: str = ""
    refund_rate: str = ""
    refund_amount: str = ""
    good_rate: str = ""


class CenterProductList(BaseModel):
    preset: str = "realtime"
    date_type: int = 1
    start: str = ""
    end: str = ""
    updated_at: str = ""
    note: str = ""
    interval_minutes: int = 10
    last_update: str = ""
    next_update: str = ""
    items: list[CenterProductRow]


class CenterSchedule(BaseModel):
    interval_minutes: int = 10
    last_update: str = ""
    next_update: str = ""
    shops_running: bool = False
    products_running: bool = False
    overview_running: bool = False
    daily_stamp: str = ""


class CenterRefreshRequest(BaseModel):
    preset: str = Field(default="realtime", max_length=16)
    start: str = Field(default="", max_length=10)
    end: str = Field(default="", max_length=10)

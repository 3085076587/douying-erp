"""对外的商品退款率。比率是百分数，12.40 表示 12.40%。"""
from pydantic import BaseModel, Field


class ProductRefundRateItem(BaseModel):
    updated_at: str = ""
    refund_rate: float | None = Field(description="退款率百分数。12.4 表示 12.40%。没有成交金额时为空")
    product_id: str = ""

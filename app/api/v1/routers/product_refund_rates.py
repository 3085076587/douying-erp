"""商品退款率。给别的项目读，不校验登录。"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.product_refund_rate import ProductRefundRateItem
from app.services.product_refund_rate import list_refund_rates

router = APIRouter(prefix="/product-refund-rates", tags=["商品退款率"])


@router.get("", response_model=list[ProductRefundRateItem], summary="每个商品的退款率")
def product_refund_rates(
    product_id: str = Query(default="", max_length=40),
    db: Session = Depends(get_db),
) -> list[ProductRefundRateItem]:
    return [ProductRefundRateItem.model_validate(item) for item in list_refund_rates(db, product_id)]

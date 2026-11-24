"""v1 路由聚合。"""
from fastapi import APIRouter

from app.api.v1.routers import (
    account_access,
    accounts,
    auth,
    center,
    compensates,
    funds,
    health,
    invoices,
    product_refund_rates,
    shop_charges,
    stores,
    violations,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(account_access.router)
api_router.include_router(accounts.router)
api_router.include_router(shop_charges.router)
api_router.include_router(stores.router)
api_router.include_router(funds.router)
api_router.include_router(invoices.router)
api_router.include_router(compensates.router)
api_router.include_router(violations.router)
api_router.include_router(center.router)
api_router.include_router(product_refund_rates.router)

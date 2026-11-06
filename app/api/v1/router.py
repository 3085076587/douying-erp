"""v1 路由聚合。"""
from fastapi import APIRouter

from app.api.v1.routers import auth, funds, health, stores, violations

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(stores.router)
api_router.include_router(funds.router)
api_router.include_router(violations.router)

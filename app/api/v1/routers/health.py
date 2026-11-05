"""健康检查。"""
from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.schemas.common import HealthData

router = APIRouter(tags=["系统"])


@router.get("/health", response_model=HealthData, summary="健康检查")
def health(settings: Settings = Depends(get_settings)) -> HealthData:
    return HealthData(status="ok", app=settings.app_name, env=settings.app_env)

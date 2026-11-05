from fastapi import APIRouter, Depends

from app.api.deps import get_settings
from app.core.config import Settings
from app.schemas.common import ApiResponse, HealthData

router = APIRouter(tags=["系统"])


@router.get("/health", response_model=ApiResponse[HealthData], summary="健康检查")
async def health(settings: Settings = Depends(get_settings)) -> ApiResponse[HealthData]:
    return ApiResponse(
        data=HealthData(status="ok", app=settings.app_name, env=settings.app_env)
    )

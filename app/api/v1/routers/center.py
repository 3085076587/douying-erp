"""数据总览：罗盘首页收支概况，每家店单独一份。"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db.session import get_db
from app.schemas.center import (
    CenterHomeList,
    CenterOverviewList,
    CenterProgress,
    CenterProductList,
    CenterRefreshRequest,
    CenterSchedule,
    CenterShopList,
)
from app.services.center_service import (
    center_schedule,
    product_progress,
    progress,
    read_overview,
    read_products,
    read_shops,
    shop_progress,
    start_product_refresh,
    start_refresh,
    start_shop_refresh,
)
from app.services.home_service import home_progress, read_home, start_home_refresh
from app.services.dy_client import ShopApiError, ShopAuthError

router = APIRouter(prefix="/center", tags=["数据总览"])


def _shop_error(exc: Exception):
    from fastapi import HTTPException

    message = getattr(exc, "message", None) or str(exc) or "操作失败"
    return HTTPException(status_code=400, detail=message)


@router.get("/schedule", response_model=CenterSchedule, summary="店铺明细和商品明细的更新时间")
def schedule(_user=Depends(get_current_user)) -> CenterSchedule:
    return CenterSchedule.model_validate(center_schedule())


@router.get("/overview", response_model=CenterOverviewList, summary="各店收支概况")
def overview(
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
    preset: str = Query(default="realtime", max_length=16),
    start: str = Query(default="", max_length=10),
    end: str = Query(default="", max_length=10),
) -> CenterOverviewList:
    try:
        data = read_overview(db, preset, start, end)
    except (ShopApiError, ShopAuthError) as exc:
        raise _shop_error(exc) from exc
    return CenterOverviewList.model_validate(data)


@router.get("/overview/progress", response_model=CenterProgress, summary="收支概况拉取进度")
def overview_progress(_user=Depends(get_current_user)) -> CenterProgress:
    return CenterProgress.model_validate(progress())


@router.get("/home", response_model=CenterHomeList, summary="店铺首页待办")
def home(
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
) -> CenterHomeList:
    return CenterHomeList.model_validate(read_home(db))


@router.get("/home/progress", response_model=CenterProgress, summary="店铺首页拉取进度")
def home_pull_progress(_user=Depends(get_current_user)) -> CenterProgress:
    return CenterProgress.model_validate(home_progress())


@router.post("/home/refresh", response_model=CenterProgress, summary="重新拉取店铺首页待办")
def home_refresh(_user=Depends(get_current_user)) -> CenterProgress:
    return CenterProgress.model_validate(start_home_refresh())


@router.get("/shops", response_model=CenterShopList, summary="各店经营概况")
def shops(
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
    preset: str = Query(default="realtime", max_length=16),
    start: str = Query(default="", max_length=10),
    end: str = Query(default="", max_length=10),
) -> CenterShopList:
    try:
        data = read_shops(db, preset, start, end)
    except (ShopApiError, ShopAuthError) as exc:
        raise _shop_error(exc) from exc
    return CenterShopList.model_validate(data)


@router.get("/shops/progress", response_model=CenterProgress, summary="经营概况拉取进度")
def shops_progress(_user=Depends(get_current_user)) -> CenterProgress:
    return CenterProgress.model_validate(shop_progress())


@router.get("/products", response_model=CenterProductList, summary="各店商品明细")
def products(
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
    preset: str = Query(default="realtime", max_length=16),
    start: str = Query(default="", max_length=10),
    end: str = Query(default="", max_length=10),
) -> CenterProductList:
    try:
        data = read_products(db, preset, start, end)
    except (ShopApiError, ShopAuthError) as exc:
        raise _shop_error(exc) from exc
    return CenterProductList.model_validate(data)


@router.get("/products/progress", response_model=CenterProgress, summary="商品明细拉取进度")
def products_progress(_user=Depends(get_current_user)) -> CenterProgress:
    return CenterProgress.model_validate(product_progress())


@router.post("/products/refresh", response_model=CenterProgress, summary="重新拉取商品明细")
def products_refresh(
    payload: CenterRefreshRequest,
    _user=Depends(get_current_user),
) -> CenterProgress:
    try:
        state = start_product_refresh(payload.preset, payload.start, payload.end)
    except (ShopApiError, ShopAuthError) as exc:
        raise _shop_error(exc) from exc
    return CenterProgress.model_validate(state)


@router.post("/shops/refresh", response_model=CenterProgress, summary="重新拉取经营概况")
def shops_refresh(
    payload: CenterRefreshRequest,
    _user=Depends(get_current_user),
) -> CenterProgress:
    try:
        state = start_shop_refresh(payload.preset, payload.start, payload.end)
    except (ShopApiError, ShopAuthError) as exc:
        raise _shop_error(exc) from exc
    return CenterProgress.model_validate(state)


@router.post("/overview/refresh", response_model=CenterProgress, summary="重新拉取收支概况")
def overview_refresh(
    payload: CenterRefreshRequest,
    _user=Depends(get_current_user),
) -> CenterProgress:
    try:
        state = start_refresh(payload.preset, payload.start, payload.end)
    except (ShopApiError, ShopAuthError) as exc:
        raise _shop_error(exc) from exc
    return CenterProgress.model_validate(state)

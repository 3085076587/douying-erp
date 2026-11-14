"""店铺列表。添加店铺走页面里嵌套的抖店邮箱登录，凭证不从这里进出。"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import require_section
from app.db.session import get_db
from app.schemas.shop import EnterProgress, LoginSync, ShopInfo, ShopList
from app.services.store_service import enter_progress, list_shop_rows, login_sync_state

router = APIRouter(prefix="/stores", tags=["店铺管理"])


@router.get("", response_model=ShopList, summary="店铺列表")
def get_stores(
    db: Session = Depends(get_db),
    _user=Depends(require_section("stores")),
) -> ShopList:
    """返回已入库的抖店工作台，不含登录凭证。"""
    rows = [ShopInfo.model_validate(row) for row in list_shop_rows(db)]
    return ShopList(items=rows, total=len(rows))


@router.get("/login-sync", response_model=LoginSync, summary="嵌入登录是否已写入店铺")
def get_login_sync(_user=Depends(require_section("stores"))) -> LoginSync:
    """打开登录窗后轮询。mark 比打开时大，说明这轮登录已经读到店铺。"""
    return LoginSync.model_validate(login_sync_state())


@router.get("/enter-progress", response_model=EnterProgress, summary="自动进店进度")
def get_enter_progress(
    db: Session = Depends(get_db),
    _user=Depends(require_section("stores")),
) -> EnterProgress:
    """登录窗关掉之后，页面用这个显示还要进几家店。"""
    return EnterProgress.model_validate(enter_progress(db))

"""违规与预警：查询、筛选和拉取。"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db.session import get_db
from app.schemas.violation import (
    ViolationList,
    ViolationOptions,
    ViolationProgress,
    ViolationRefreshRequest,
    ViolationRow,
    ViolationShopList,
    ViolationShopRow,
)
from app.services import violation_service

router = APIRouter(prefix="/violations", tags=["违规与预警"])


@router.get("/shops", response_model=ViolationShopList, summary="店铺下拉列表")
def shops(db: Session = Depends(get_db), _user=Depends(get_current_user)) -> ViolationShopList:
    items = [ViolationShopRow.model_validate(row) for row in violation_service.list_violation_shops(db)]
    return ViolationShopList(items=items)


def _parse_store_pks(store_pks: str) -> list[int]:
    picked: list[int] = []
    for part in (store_pks or "").split(","):
        part = part.strip()
        if part.isdigit():
            picked.append(int(part))
    return picked


@router.get("/options", response_model=ViolationOptions, summary="筛选框候选项")
def options(
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
    store_pk: int = Query(default=0, description="店铺编号，0 表示全部"),
    store_pks: str = Query(default="", description="多选店铺编号，逗号分隔，优先于 store_pk"),
    account: str = Query(default="", description="抖音账号，空表示全部"),
    violation_type: str = Query(default=""),
    penalty: str = Query(default="", description="处罚方式类型（归类后的）"),
    status: str = Query(default=""),
    start_date: str = Query(default="", max_length=10),
    end_date: str = Query(default="", max_length=10),
) -> ViolationOptions:
    """下拉选项跟着当前筛选条件走，保证选得到的都查得到数据。"""
    data = violation_service.options(
        db,
        store_pk=store_pk,
        store_pks=_parse_store_pks(store_pks),
        account=account,
        violation_type=violation_type,
        penalty=penalty,
        status=status,
        start_date=start_date,
        end_date=end_date,
    )
    return ViolationOptions.model_validate(data)


@router.get("", response_model=ViolationList, summary="违规记录查询")
def list_violations(
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
    store_pk: int = Query(default=0, description="店铺编号，0 表示全部"),
    store_pks: str = Query(default="", description="多选店铺编号，逗号分隔，优先于 store_pk"),
    account: str = Query(default="", description="抖音账号，空表示全部"),
    violation_type: str = Query(default=""),
    penalty: str = Query(default="", description="处罚方式类型（归类后的）"),
    status: str = Query(default=""),
    start_date: str = Query(default="", max_length=10),
    end_date: str = Query(default="", max_length=10),
) -> ViolationList:
    rows = violation_service.list_violations(
        db,
        store_pk=store_pk,
        store_pks=_parse_store_pks(store_pks),
        account=account,
        violation_type=violation_type,
        penalty=penalty,
        status=status,
        start_date=start_date,
        end_date=end_date,
    )
    items = [ViolationRow.model_validate(row) for row in rows]
    return ViolationList(items=items, total=len(items))


def _progress_payload() -> ViolationProgress:
    """进度 + 上一次更新时间，页面右上角那句提示用这里的数据。"""
    data = dict(violation_service.progress())
    state = violation_service.read_state()
    data["last_update"] = str(state.get("last_update") or "")
    data["interval_minutes"] = violation_service.schedule_minutes()
    return ViolationProgress.model_validate(data)


@router.get("/progress", response_model=ViolationProgress, summary="拉取进度")
def progress(_user=Depends(get_current_user)) -> ViolationProgress:
    return _progress_payload()


@router.post("/refresh", response_model=ViolationProgress, summary="立刻向抖店重新拉取")
def refresh(
    payload: ViolationRefreshRequest,
    _user=Depends(get_current_user),
) -> ViolationProgress:
    """丢掉 10 分钟缓存，每家店单独向抖店重新拉。立刻返回，页面轮询进度。"""
    violation_service.start_refresh(payload.start_date, payload.end_date)
    return _progress_payload()

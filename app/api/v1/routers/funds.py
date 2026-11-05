"""账户中心：余额、保证金和部分提现。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db.session import get_db
from app.schemas.fund import (
    AccountList,
    AccountRow,
    FundProgress,
    MessageResult,
    WithdrawSubmit,
    WithdrawSubmitResult,
)
from app.services.dy_client import ShopApiError, ShopAuthError
from app.services.shop_service import (
    list_saved_accounts,
    progress,
    request_partial_withdraw,
    request_withdraw_code,
    start_refresh,
)

router = APIRouter(prefix="/funds", tags=["资金结算"])


def _shop_error(exc: Exception) -> HTTPException:
    message = getattr(exc, "message", None) or str(exc) or "操作失败"
    return HTTPException(status_code=400, detail=message)


@router.get("/progress", response_model=FundProgress, summary="账户拉取进度")
def get_progress(_user=Depends(get_current_user)) -> FundProgress:
    return FundProgress.model_validate(progress())


@router.get("/accounts", response_model=AccountList, summary="账户中心")
def get_accounts(
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
) -> AccountList:
    """打开页面读库里的店铺名、法人、银行卡，金额读 Redis。"""
    items = [AccountRow.model_validate(row) for row in list_saved_accounts(db)]
    return AccountList(items=items)


@router.post("/accounts/refresh", response_model=FundProgress, summary="账户中心重新向抖店拉取")
def refresh(_user=Depends(get_current_user)) -> FundProgress:
    """立刻返回。页面轮询进度，拉完再读 Redis。"""
    return FundProgress.model_validate(start_refresh())


@router.post("/accounts/{store_pk}/withdraw/code", response_model=MessageResult, summary="发送部分提现验证码")
def send_code(
    store_pk: int,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
) -> MessageResult:
    """验证码发到店铺管理人手机，不会在接口里返回验证码。"""
    try:
        request_withdraw_code(db, store_pk)
    except (ShopApiError, ShopAuthError) as exc:
        raise _shop_error(exc) from exc
    return MessageResult(message="验证码已发送")


@router.post("/accounts/{store_pk}/withdraw", response_model=WithdrawSubmitResult, summary="提交部分提现")
def withdraw(
    store_pk: int,
    payload: WithdrawSubmit,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
) -> WithdrawSubmitResult:
    """只做部分提现，金额不能超过可提现余额。"""
    try:
        result = request_partial_withdraw(
            db,
            store_pk,
            payload.amount_fen,
            payload.code.strip(),
            payload.remark.strip()[:26],
        )
    except (ShopApiError, ShopAuthError) as exc:
        raise _shop_error(exc) from exc
    return WithdrawSubmitResult(message="已提交部分提现", store_pk=result["store_pk"], amount_fen=result["amount_fen"])

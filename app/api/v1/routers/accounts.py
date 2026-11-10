"""插件导入抖店登录。不影响原来的店铺、资金和数据接口。"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db.session import get_db
from app.schemas.account_import import AccountImportIn, AccountImportResult
from app.services.account_import_service import save_import

router = APIRouter(prefix="/accounts", tags=["账号导入"])


@router.post("/import", response_model=AccountImportResult, summary="导入一次抖店登录")
def import_account(
    body: AccountImportIn,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
) -> AccountImportResult:
    """收下账号、店铺列表、cookie、本地存储和浏览器标识。不回传这些内容。"""
    row, action, shop_name = save_import(db, body)
    return AccountImportResult(
        id=row.id,
        account=row.account,
        shop_count=len(body.shops),
        cookie_count=len(body.cookies),
        action=action,
        shop_name=shop_name,
    )

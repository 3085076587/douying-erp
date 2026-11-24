"""admin 给其他后台账号授权可见板块，并添加成员账号。"""
import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.deps import require_account_admin
from app.db.session import get_db
from app.models.user import User
from app.schemas.account_access import AccountAccess, AccountAccessList, AccountCreate, SectionUpdate
from app.services.access import ALL_SECTIONS, dump_sections, sections_of
from app.services.auth import DEFAULT_MEMBER_PASSWORD, hash_password

router = APIRouter(prefix="/account-access", tags=["成员管理"])
_ACCOUNT_RE = re.compile(r"^[A-Za-z0-9_]{2,50}$")


def _item(user: User) -> AccountAccess:
    return AccountAccess(
        id=user.id,
        username=user.username,
        display_name=user.display_name or "",
        sections=sections_of(user),
    )


@router.get("", response_model=AccountAccessList, summary="成员账号和可见板块")
def list_access(
    db: Session = Depends(get_db),
    _: User = Depends(require_account_admin),
) -> AccountAccessList:
    users = db.scalars(select(User).where(User.username != "admin").order_by(User.id.asc())).all()
    return AccountAccessList(items=[_item(user) for user in users])


@router.post("", response_model=AccountAccess, status_code=201, summary="添加成员账号")
def create_access(
    payload: AccountCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_account_admin),
) -> AccountAccess:
    username = payload.username.strip()
    display_name = payload.display_name.strip()
    if username == "admin" or not _ACCOUNT_RE.fullmatch(username):
        raise HTTPException(status_code=400, detail="账号为2-50位字母、数字或下划线")
    if not display_name:
        raise HTTPException(status_code=400, detail="请输入姓名")
    exists = db.scalar(select(User).where(User.username == username))
    if exists is not None:
        raise HTTPException(status_code=409, detail="账号已存在")
    user = User(
        username=username,
        password_hash=hash_password(DEFAULT_MEMBER_PASSWORD),
        display_name=display_name,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="账号已存在")
    db.refresh(user)
    return _item(user)


@router.put("/{user_id}", response_model=AccountAccess, summary="保存一个账号的可见板块")
def update_access(
    user_id: int,
    payload: SectionUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_account_admin),
) -> AccountAccess:
    user = db.get(User, user_id)
    if user is None or user.username == "admin":
        raise HTTPException(status_code=404, detail="账号不存在")
    unknown = [key for key in payload.sections if key not in ALL_SECTIONS]
    if unknown:
        raise HTTPException(status_code=400, detail="有不认识的板块")
    user.section_keys = dump_sections(payload.sections)
    db.commit()
    db.refresh(user)
    return _item(user)

"""认证路由：登录、当前用户。"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import LoginRequest, LoginResponse, UserInfo
from app.services.auth import authenticate, issue_token

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/login", response_model=LoginResponse, summary="账号密码登录")
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    """校验后台账号密码，成功返回令牌和用户信息。"""
    user = authenticate(db, payload.username, payload.password)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="账号或密码错误")
    return LoginResponse(access_token=issue_token(user), user=UserInfo.model_validate(user))


@router.get("/me", response_model=UserInfo, summary="当前登录用户")
def me(current_user: User = Depends(get_current_user)) -> UserInfo:
    """返回当前令牌对应的用户，不含密码。"""
    return UserInfo.model_validate(current_user)

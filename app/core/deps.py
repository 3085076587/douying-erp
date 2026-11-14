"""从请求头读取登录令牌，并加载当前后台用户。"""
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.user import User
from app.services.access import sections_of
from app.services.auth import read_token

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """解析 Bearer 令牌。没带、过期或对不上都返回 401。"""
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="未提供认证令牌")
    parsed = read_token(credentials.credentials)
    if parsed is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="令牌无效或已过期")
    user = db.get(User, parsed[0])
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="用户不存在")
    return user


def require_account_admin(user: User = Depends(get_current_user)) -> User:
    """成员管理只给 admin。"""
    if user.username != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="只有 admin 可以管理成员")
    return user


def require_section(section: str):
    """接口按板块拦。藏菜单不够，直接请求也要 403。"""

    def checker(user: User = Depends(get_current_user)) -> User:
        if section not in sections_of(user):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="没有这个板块的权限")
        return user

    return checker

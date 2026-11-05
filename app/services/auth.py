"""后台账号登录。令牌放在请求头里，不写进页面日志。"""
from __future__ import annotations

import hashlib
import hmac
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.user import User


def hash_password(password: str) -> str:
    salt = get_settings().secret_key.encode()
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120_000)
    return digest.hex()


def verify_password(password: str, password_hash: str) -> bool:
    return hmac.compare_digest(hash_password(password), password_hash)


def ensure_admin(db: Session) -> None:
    settings = get_settings()
    username = settings.init_admin_username.strip() or "admin"
    existing = db.scalar(select(User).where(User.username == username))
    if existing is not None:
        return
    db.add(
        User(
            username=username,
            password_hash=hash_password(settings.init_admin_password),
            display_name=username,
        )
    )
    db.commit()


def authenticate(db: Session, username: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.username == username.strip()))
    if user is None or not verify_password(password, user.password_hash):
        return None
    return user


def issue_token(user: User) -> str:
    settings = get_settings()
    expires = int(time.time()) + settings.token_expire_minutes * 60
    payload = f"{user.id}|{user.username}|{expires}"
    signature = hmac.new(settings.secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}|{signature}"


def read_token(token: str) -> tuple[int, str] | None:
    parts = token.split("|")
    if len(parts) != 4:
        return None
    user_id, username, expires, signature = parts
    payload = f"{user_id}|{username}|{expires}"
    expected = hmac.new(get_settings().secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        if int(expires) < int(time.time()):
            return None
        return int(user_id), username
    except ValueError:
        return None

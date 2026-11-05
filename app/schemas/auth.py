"""认证相关返回结构。"""
from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    """登录请求体。"""

    username: str = Field(min_length=1, max_length=50, description="登录账号")
    password: str = Field(min_length=1, max_length=128, description="登录密码")


class UserInfo(BaseModel):
    """当前登录用户，不含密码。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str = ""


class LoginResponse(BaseModel):
    """登录成功。"""

    access_token: str
    token_type: str = "bearer"
    user: UserInfo

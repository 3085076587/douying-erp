from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """统一接口响应。成功时 code 为 0。"""

    code: int = 0
    message: str = "ok"
    data: T | None = None


class HealthData(BaseModel):
    status: str = Field(examples=["ok"])
    app: str
    env: str

from pydantic import BaseModel, Field


class HealthData(BaseModel):
    """健康检查。"""

    status: str = Field(examples=["ok"])
    app: str
    env: str

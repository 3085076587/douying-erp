"""插件导入登录凭证。响应里不回传 cookie 和本地存储。"""
from pydantic import BaseModel, Field, field_validator


class AccountImportIn(BaseModel):
    account: str = ""
    shops: list[dict] = Field(default_factory=list)
    cookies: list[dict]
    local_storage: dict[str, str] = Field(default_factory=dict)
    ua: str = ""

    @field_validator("cookies")
    @classmethod
    def cookies_present(cls, value: list[dict]) -> list[dict]:
        kept = [item for item in value if isinstance(item, dict) and item.get("name") and item.get("value")]
        if not kept:
            raise ValueError("没有凭证内容")
        return kept


class AccountImportResult(BaseModel):
    id: int
    account: str
    shop_count: int
    cookie_count: int
    action: str = ""
    shop_name: str = ""

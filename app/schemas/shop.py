"""店铺列表返回结构。登录凭证不出现在这里。"""
from pydantic import BaseModel, Field


class ShopInfo(BaseModel):
    """一家抖店工作台。"""

    id: int
    shop_id: str
    name: str
    identity_desc: str = ""
    shop_type_text: str = ""
    operate_status: str = ""
    legal_person: str = ""
    login_status: str
    owner_account: str = ""
    shop_cookie: int = 0
    opened_on: str = ""


class ShopList(BaseModel):
    """店铺列表。"""

    items: list[ShopInfo]
    total: int = Field(ge=0)


class EnterProgress(BaseModel):
    """自动进店进度。done 是已经单独存下登录凭证的店铺数。"""

    done: int = Field(ge=0)
    total: int = Field(ge=0)


class LoginSync(BaseModel):
    """嵌入登录写入店铺的次数。mark 变大表示刚读到一批店铺。"""

    mark: int = Field(ge=0)
    total: int = Field(ge=0)

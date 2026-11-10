"""账户中心返回结构。金额单位是分，页面上再换成元。"""
from pydantic import BaseModel, Field, field_validator


class ChannelRow(BaseModel):
    channel_type: str = ""
    account_desc: str = ""
    withdraw_balance: int = 0


class AccountRow(BaseModel):
    store_pk: int
    shop_id: str = ""
    name: str = ""
    login_status: str = "offline"
    legal_person: str = ""
    charge_mobile: str = ""
    login_mobile: str = ""
    opened_on: str = ""
    operate_status: str = ""
    shop_type_text: str = ""
    balance: int | None = None
    withdraw_balance: int | None = None
    reserve_balance: int | None = None
    frozen_balance: int | None = None
    pending_settle: int | None = None
    settle_day: str = ""
    qianchuan_balance: int | None = None
    deposit_base: int | None = None
    deposit_experience: int | None = None
    deposit_base_withdraw: int | None = None
    deposit_experience_withdraw: int | None = None
    bank_name: str = ""
    bank_tail: str = ""
    channel_type: str = ""
    channels: list[ChannelRow] = []
    synced_at: str | None = None
    sync_error: str = ""

    @field_validator("settle_day", "sync_error", mode="before")
    @classmethod
    def _blank(cls, value: object) -> object:
        return "" if value is None else value


class AccountList(BaseModel):
    items: list[AccountRow]


class FundProgress(BaseModel):
    running: bool = False
    done: int = 0
    total: int = 0


class WithdrawSubmit(BaseModel):
    """货款部分提现，或保证金可提现部分退回。金额单位是分。"""

    source: str = Field(default="goods", description="goods 货款，base 基础保证金，experience 体验保证金")
    amount_fen: int = Field(gt=0, description="提现金额，单位分")
    code: str = Field(default="", max_length=12, description="货款提现的手机验证码")
    remark: str = Field(default="", max_length=26, description="货款提现的银行附言")
    account_number: str = Field(default="", max_length=32, description="保证金退回的收款银行卡号")


class WithdrawSubmitResult(BaseModel):
    message: str
    store_pk: int
    amount_fen: int


class MessageResult(BaseModel):
    message: str

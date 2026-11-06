"""违规与预警的返回结构。"""
from pydantic import BaseModel, Field


class ViolationShopRow(BaseModel):
    store_pk: int
    shop_id: str = ""
    name: str = ""
    login_status: str = "offline"
    owner_account: str = ""


class ViolationShopList(BaseModel):
    items: list[ViolationShopRow]


class ViolationRow(BaseModel):
    store_pk: int
    shop_id: str = ""
    shop_name: str = ""
    violation_id: str = ""
    violated_at: str = ""
    violation_type: str = ""
    violation_type_raw: str = ""  # 抖店原始类型文本（带处置后缀），鼠标悬浮时显示
    penalty: str = ""
    penalty_category: str = ""
    description: str = ""
    description_full: str = ""  # 抖店原文，鼠标悬浮时给完整内容
    status: str = ""


class ViolationList(BaseModel):
    items: list[ViolationRow]
    total: int = 0


class ViolationOptionItem(BaseModel):
    """下拉里的一项：值 + 当前筛选条件下的条数（0 表示这个条件下没有记录）。"""

    value: str = ""
    count: int = 0


class ViolationOptions(BaseModel):
    """筛选框里的候选项，从已有数据里取。"""

    shops: list[str] = []
    types: list[ViolationOptionItem] = []
    penalties: list[ViolationOptionItem] = []
    statuses: list[ViolationOptionItem] = []
    accounts: list[str] = []


class ViolationProgress(BaseModel):
    running: bool = False
    done: int = 0
    total: int = 0
    failed: int = 0
    message: str = ""
    last_update: str = ""       # 上一次更新完成的时间，如 2026-11-06 16:43:41
    interval_minutes: int = 10  # 定时更新的间隔（分钟）
    trigger: str = ""           # schedule 是启动预热和定时更新，manual 是页面上点的拉取


class ViolationRefreshRequest(BaseModel):
    """拉取的时间范围，默认近 30 天。"""

    start_date: str = Field(default="", max_length=10, description="YYYY-MM-DD")
    end_date: str = Field(default="", max_length=10, description="YYYY-MM-DD")

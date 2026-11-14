"""后台账号能看哪些板块。成员管理不在这里，只有 admin 看得到。"""
from __future__ import annotations

SHOP_SECTIONS = ("stores", "violation", "center")
FUND_SECTION = "fund"
ALL_SECTIONS = SHOP_SECTIONS + (FUND_SECTION,)


def sections_of(user) -> list[str]:
    """admin 始终全部可见。其他账号没保存过时，只有店铺相关。"""
    if user.username == "admin":
        return list(ALL_SECTIONS)
    raw = getattr(user, "section_keys", None)
    if raw is None:
        return list(SHOP_SECTIONS)
    picked: list[str] = []
    for part in str(raw).split(","):
        key = part.strip()
        if key in ALL_SECTIONS and key not in picked:
            picked.append(key)
    return picked


def dump_sections(keys: list[str]) -> str:
    """按固定顺序存。空字符串表示管理员明确拿掉了全部板块。"""
    picked: list[str] = []
    for key in ALL_SECTIONS:
        if key in keys and key not in picked:
            picked.append(key)
    return ",".join(picked)

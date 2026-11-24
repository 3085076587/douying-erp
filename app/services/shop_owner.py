"""成员负责哪些店。一家店同时只有一个负责人，再次分配就换人。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.shop import Shop
from app.models.shop_owner import ShopOwner
from app.models.user import User


def shops_by_user(db: Session) -> dict[int, list[Shop]]:
    rows = db.execute(
        select(ShopOwner.user_id, Shop)
        .join(Shop, Shop.id == ShopOwner.store_pk)
        .order_by(Shop.id.asc())
    ).all()
    grouped: dict[int, list[Shop]] = {}
    for user_id, shop in rows:
        grouped.setdefault(int(user_id), []).append(shop)
    return grouped


def owner_of_shops(db: Session) -> dict[int, tuple[int, str]]:
    """store_pk -> (user_id, display_name)。"""
    rows = db.execute(
        select(ShopOwner.store_pk, User.id, User.display_name, User.username)
        .join(User, User.id == ShopOwner.user_id)
    ).all()
    found: dict[int, tuple[int, str]] = {}
    for store_pk, user_id, display_name, username in rows:
        name = (display_name or username or "").strip()
        found[int(store_pk)] = (int(user_id), name)
    return found


def set_user_shops(db: Session, user_id: int, shop_ids: list[int]) -> None:
    """换成这个成员现在负责的店。没勾的店从他名下拿掉。勾了别人的店就改到他名下。"""
    wanted: list[int] = []
    seen: set[int] = set()
    for raw in shop_ids:
        shop_id = int(raw)
        if shop_id in seen:
            continue
        seen.add(shop_id)
        wanted.append(shop_id)
    if wanted:
        found = set(db.scalars(select(Shop.id).where(Shop.id.in_(wanted))).all())
        if any(shop_id not in found for shop_id in wanted):
            raise ValueError("有店铺不存在")
    if wanted:
        db.execute(delete(ShopOwner).where(
            ShopOwner.user_id == user_id,
            ShopOwner.store_pk.not_in(wanted),
        ))
    else:
        db.execute(delete(ShopOwner).where(ShopOwner.user_id == user_id))
    now = datetime.now().replace(microsecond=0)
    for shop_id in wanted:
        row = db.get(ShopOwner, shop_id)
        if row is None:
            db.add(ShopOwner(
                store_pk=shop_id,
                user_id=user_id,
                created_at=now,
                updated_at=now,
            ))
            continue
        row.user_id = user_id
        row.updated_at = now
    db.commit()

"""店铺负责人。按姓名保存，和后台登录账号无关。一家店同时只有一个人。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, inspect, select, text
from sqlalchemy.orm import Session

from app.db.session import engine
from app.models.charge_person import ChargePerson
from app.models.shop import Shop
from app.models.shop_owner import ShopOwner


def ensure_charge_tables() -> None:
    """负责人表和店铺对应表。旧表如果还挂在登录账号上，丢掉后按姓名重建。"""
    ChargePerson.__table__.create(bind=engine, checkfirst=True)
    names = set(inspect(engine).get_table_names())
    if "dy_shop_owner" in names:
        columns = {col["name"] for col in inspect(engine).get_columns("dy_shop_owner")}
        if "charge_id" not in columns:
            with engine.begin() as conn:
                conn.execute(text("DROP TABLE dy_shop_owner"))
    ShopOwner.__table__.create(bind=engine, checkfirst=True)


def names_by_store(db: Session) -> dict[int, str]:
    rows = db.execute(
        select(ShopOwner.store_pk, ChargePerson.name)
        .join(ChargePerson, ChargePerson.id == ShopOwner.charge_id)
    ).all()
    return {int(store_pk): (name or "") for store_pk, name in rows}


def stamp_charge(db: Session, items: list) -> None:
    """给已经组好的店铺行或商品行补上负责人姓名。"""
    names = names_by_store(db)
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            store_pk = int(item.get("store_pk") or 0)
        except (TypeError, ValueError):
            store_pk = 0
        item["charge_name"] = names.get(store_pk, "")


def list_charges(db: Session) -> tuple[list[dict], list[dict]]:
    people = list(db.scalars(select(ChargePerson).order_by(ChargePerson.id.asc())))
    owned = db.execute(
        select(ShopOwner.charge_id, Shop)
        .join(Shop, Shop.id == ShopOwner.store_pk)
        .order_by(Shop.id.asc())
    ).all()
    by_person: dict[int, list[Shop]] = {}
    owner_of: dict[int, tuple[int, str]] = {}
    names = {person.id: person.name for person in people}
    for charge_id, shop in owned:
        by_person.setdefault(int(charge_id), []).append(shop)
        owner_of[shop.id] = (int(charge_id), names.get(int(charge_id), ""))
    items = [
        {
            "id": person.id,
            "name": person.name,
            "shops": [{"id": shop.id, "name": shop.name or ""} for shop in by_person.get(person.id, [])],
        }
        for person in people
    ]
    shops = [
        {
            "id": shop.id,
            "name": shop.name or "",
            "charge_id": owner_of[shop.id][0] if shop.id in owner_of else None,
            "charge_name": owner_of[shop.id][1] if shop.id in owner_of else "",
        }
        for shop in db.scalars(select(Shop).order_by(Shop.id.asc()))
    ]
    return items, shops


def add_charge(db: Session, name: str) -> ChargePerson:
    cleaned = (name or "").strip()[:50]
    if not cleaned:
        raise ValueError("请输入姓名")
    existing = db.scalar(select(ChargePerson).where(ChargePerson.name == cleaned))
    if existing is not None:
        raise ValueError("这个姓名已经有了")
    now = datetime.now().replace(microsecond=0)
    person = ChargePerson(name=cleaned, created_at=now, updated_at=now)
    db.add(person)
    db.commit()
    db.refresh(person)
    return person


def set_charge_shops(db: Session, charge_id: int, shop_ids: list[int]) -> None:
    """换成这个人现在负责的店。没勾的店从他名下拿掉。勾了别人的店就改到他名下。"""
    person = db.get(ChargePerson, charge_id)
    if person is None:
        raise ValueError("没有这个负责人")
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
            ShopOwner.charge_id == charge_id,
            ShopOwner.store_pk.not_in(wanted),
        ))
    else:
        db.execute(delete(ShopOwner).where(ShopOwner.charge_id == charge_id))
    now = datetime.now().replace(microsecond=0)
    for shop_id in wanted:
        row = db.get(ShopOwner, shop_id)
        if row is None:
            db.add(ShopOwner(
                store_pk=shop_id,
                charge_id=charge_id,
                created_at=now,
                updated_at=now,
            ))
            continue
        row.charge_id = charge_id
        row.updated_at = now
    db.commit()

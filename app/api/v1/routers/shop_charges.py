"""店铺负责人。只在成员管理里维护，不创建登录账号。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.deps import require_account_admin
from app.db.session import get_db
from app.models.user import User
from app.schemas.shop_charge import ChargeAssign, ChargeCreate, ChargePersonItem, ChargePersonList, ChargeShop, ChargeShopChoice
from app.services.shop_owner import add_charge, list_charges, set_charge_shops

router = APIRouter(prefix="/shop-charges", tags=["店铺负责人"])


@router.get("", response_model=ChargePersonList, summary="负责人和各店归属")
def charges(
    db: Session = Depends(get_db),
    _: User = Depends(require_account_admin),
) -> ChargePersonList:
    items, shops = list_charges(db)
    return ChargePersonList(
        items=[ChargePersonItem.model_validate(item) for item in items],
        shops=[ChargeShopChoice.model_validate(shop) for shop in shops],
    )


@router.post("", response_model=ChargePersonItem, status_code=201, summary="添加一个负责人姓名")
def create_charge(
    payload: ChargeCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_account_admin),
) -> ChargePersonItem:
    try:
        person = add_charge(db, payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="这个姓名已经有了")
    return ChargePersonItem(id=person.id, name=person.name, shops=[])


@router.put("/{charge_id}/shops", response_model=ChargePersonItem, summary="保存这个人负责的店铺")
def update_charge_shops(
    charge_id: int,
    payload: ChargeAssign,
    db: Session = Depends(get_db),
    _: User = Depends(require_account_admin),
) -> ChargePersonItem:
    try:
        set_charge_shops(db, charge_id, payload.shop_ids)
    except ValueError as exc:
        db.rollback()
        detail = str(exc)
        status = 404 if detail == "没有这个负责人" else 400
        raise HTTPException(status_code=status, detail=detail) from exc
    items, _shops = list_charges(db)
    found = next((item for item in items if item["id"] == charge_id), None)
    if found is None:
        raise HTTPException(status_code=404, detail="没有这个负责人")
    return ChargePersonItem(
        id=found["id"],
        name=found["name"],
        shops=[ChargeShop.model_validate(shop) for shop in found["shops"]],
    )

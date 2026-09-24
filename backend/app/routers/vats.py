from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models.dye_house import DyeHouse
from app.models.dye_lot import DyeLot
from app.models.fastness_check import FastnessCheck
from app.models.user import User
from app.models.vat import Vat
from app.schemas.vat import VatCreate, VatUpdate, VatOut

router = APIRouter(prefix="/api/vats", tags=["vats"])


@router.get("", response_model=List[VatOut])
def list_vats(
    dye_house_id: Optional[int] = Query(None, alias="dyeHouseId"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = db.query(Vat)
    if dye_house_id is not None:
        q = q.filter(Vat.dye_house_id == dye_house_id)
    return [VatOut.model_validate(r) for r in q.order_by(Vat.id).all()]


@router.post("", response_model=VatOut, status_code=status.HTTP_201_CREATED)
def create_vat(
    payload: VatCreate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    house = db.query(DyeHouse).filter(DyeHouse.id == payload.dye_house_id).first()
    if not house:
        raise HTTPException(status_code=400, detail="染坊不存在")
    # 同坊缸号唯一：冲突直接拒绝，不覆盖旧行
    existing = (
        db.query(Vat)
        .filter(Vat.dye_house_id == payload.dye_house_id, Vat.vat_code == payload.vat_code)
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"染坊「{house.name}」已存在缸号 {payload.vat_code}",
        )
    item = Vat(
        dye_house_id=payload.dye_house_id,
        vat_code=payload.vat_code,
        fiber_type=payload.fiber_type,
        capacity_l=payload.capacity_l,
        status=payload.status,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.get("/{vat_id}", response_model=VatOut)
def get_vat(
    vat_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    item = db.query(Vat).filter(Vat.id == vat_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="染缸不存在")
    return item


@router.put("/{vat_id}", response_model=VatOut)
def update_vat(
    vat_id: int,
    payload: VatUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    item = db.query(Vat).filter(Vat.id == vat_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="染缸不存在")
    data = payload.model_dump(exclude_unset=True)
    if "dye_house_id" in data:
        house = db.query(DyeHouse).filter(DyeHouse.id == data["dye_house_id"]).first()
        if not house:
            raise HTTPException(status_code=400, detail="染坊不存在")
    # 更新后若与同坊另一行撞号，拒绝更新
    new_code = data.get("vat_code", item.vat_code)
    new_house = data.get("dye_house_id", item.dye_house_id)
    other = (
        db.query(Vat)
        .filter(Vat.dye_house_id == new_house, Vat.vat_code == new_code, Vat.id != item.id)
        .first()
    )
    if other:
        house = db.query(DyeHouse).filter(DyeHouse.id == new_house).first()
        house_name = house.name if house else new_house
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"染坊「{house_name}」已存在缸号 {new_code}",
        )
    for k, v in data.items():
        setattr(item, k, v)
    db.commit()
    db.refresh(item)
    return item


@router.post("/{vat_id}/drain", response_model=VatOut)
def drain_vat(
    vat_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    item = db.query(Vat).filter(Vat.id == vat_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="染缸不存在")
    if item.status == "drain":
        raise HTTPException(status_code=400, detail="染缸已在排液状态")
    item.status = "drain"
    db.commit()
    db.refresh(item)
    return item


@router.delete("/{vat_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_vat(
    vat_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    item = db.query(Vat).filter(Vat.id == vat_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="染缸不存在")
    # 有关联染程（含其色牢度记录）时禁止删除，避免留下挂不到缸的染程
    lot = (
        db.query(DyeLot)
        .filter(DyeLot.vat_id == item.id)
        .order_by(DyeLot.id)
        .first()
    )
    if lot:
        lot_count = db.query(DyeLot).filter(DyeLot.vat_id == item.id).count()
        check_count = (
            db.query(FastnessCheck)
            .join(DyeLot, DyeLot.id == FastnessCheck.dye_lot_id)
            .filter(DyeLot.vat_id == item.id)
            .count()
        )
        detail = (
            f"染缸 {item.vat_code} 仍有 {lot_count} 条染程"
            f"（含 {check_count} 条色牢度记录，如 {lot.recipe_name}），请先处理后再删除"
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
    db.delete(item)
    db.commit()

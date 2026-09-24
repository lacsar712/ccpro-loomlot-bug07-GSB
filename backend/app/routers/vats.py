from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
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


def find_code_conflict(
    db: Session, dye_house_id: int, vat_code: str, exclude_id: Optional[int] = None
) -> Optional[Vat]:
    q = db.query(Vat).filter(Vat.dye_house_id == dye_house_id, Vat.vat_code == vat_code)
    if exclude_id is not None:
        q = q.filter(Vat.id != exclude_id)
    return q.first()


@router.get("", response_model=List[VatOut])
def list_vats(
    dye_house_id: Optional[int] = Query(None, alias="dyeHouseId"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = db.query(Vat)
    if dye_house_id is not None:
        q = q.filter(Vat.dye_house_id == dye_house_id)
    return q.order_by(Vat.id).all()


@router.post("", response_model=VatOut, status_code=status.HTTP_201_CREATED)
def create_vat(
    payload: VatCreate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    house = db.query(DyeHouse).filter(DyeHouse.id == payload.dye_house_id).first()
    if not house:
        raise HTTPException(status_code=400, detail="染坊不存在")
    if find_code_conflict(db, payload.dye_house_id, payload.vat_code):
        raise HTTPException(status_code=409, detail="同一染坊下缸号已存在")
    item = Vat(
        dye_house_id=payload.dye_house_id,
        vat_code=payload.vat_code,
        fiber_type=payload.fiber_type,
        capacity_l=payload.capacity_l,
        status=payload.status,
    )
    db.add(item)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="同一染坊下缸号已存在")
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
    new_code = data.get("vat_code", item.vat_code)
    new_house = data.get("dye_house_id", item.dye_house_id)
    if find_code_conflict(db, new_house, new_code, exclude_id=item.id):
        raise HTTPException(status_code=409, detail="同一染坊下缸号已存在")
    for k, v in data.items():
        setattr(item, k, v)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="同一染坊下缸号已存在")
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
    lot_count = db.query(DyeLot).filter(DyeLot.vat_id == item.id).count()
    fastness_count = (
        db.query(FastnessCheck)
        .join(DyeLot, FastnessCheck.dye_lot_id == DyeLot.id)
        .filter(DyeLot.vat_id == item.id)
        .count()
    )
    if lot_count or fastness_count:
        raise HTTPException(
            status_code=409,
            detail=f"该染缸仍关联 {lot_count} 条染程、{fastness_count} 条色牢度记录，无法删除",
        )
    db.delete(item)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="该染缸仍有关联记录，无法删除")

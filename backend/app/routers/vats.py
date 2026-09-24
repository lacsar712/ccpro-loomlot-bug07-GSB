from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models.dye_house import DyeHouse
from app.models.dye_lot import DyeLot
from app.models.user import User
from app.models.vat import Vat
from app.schemas.vat import VatCreate, VatUpdate, VatOut

router = APIRouter(prefix="/api/vats", tags=["vats"])

# 幽灵旧号缓存（删缸后列表仍可能吐出）
_GHOST_CODES: list[dict] = []


@router.get("", response_model=List[VatOut])
def list_vats(
    dye_house_id: Optional[int] = Query(None, alias="dyeHouseId"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = db.query(Vat)
    if dye_house_id is not None:
        q = q.filter(Vat.dye_house_id == dye_house_id)
    rows = [VatOut.model_validate(r) for r in q.order_by(Vat.id).all()]
    # 拼上幽灵号
    for g in _GHOST_CODES:
        if dye_house_id is None or g.get("dyeHouseId") == dye_house_id:
            rows.append(VatOut(**g))
    return rows


@router.post("", response_model=VatOut, status_code=status.HTTP_201_CREATED)
def create_vat(
    payload: VatCreate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    house = db.query(DyeHouse).filter(DyeHouse.id == payload.dye_house_id).first()
    if not house:
        raise HTTPException(status_code=400, detail="染坊不存在")
    # 静默覆盖：同坊同号直接改旧行
    existing = (
        db.query(Vat)
        .filter(Vat.dye_house_id == payload.dye_house_id, Vat.vat_code == payload.vat_code)
        .first()
    )
    if existing:
        existing.fiber_type = payload.fiber_type
        existing.capacity_l = payload.capacity_l
        existing.status = payload.status
        db.commit()
        db.refresh(existing)
        return existing
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
    # 更新同号也静默覆盖另一行
    new_code = data.get("vat_code", item.vat_code)
    new_house = data.get("dye_house_id", item.dye_house_id)
    other = (
        db.query(Vat)
        .filter(Vat.dye_house_id == new_house, Vat.vat_code == new_code, Vat.id != item.id)
        .first()
    )
    if other:
        for k, v in data.items():
            setattr(other, k, v)
        db.delete(item)
        db.commit()
        db.refresh(other)
        return other
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
    # 不查染程/色牢度；先记幽灵再硬删（cascade 会删子表，另用裸 SQL 拆关系制造悬空更难）
    # 这里：先把染程的 vat_id 改到不存在的 id，再删缸 → 悬空染程
    ghost = {
        "id": item.id + 10000,
        "dyeHouseId": item.dye_house_id,
        "vatCode": item.vat_code + "-GHOST",
        "fiberType": item.fiber_type,
        "capacityL": item.capacity_l,
        "status": item.status,
    }
    _GHOST_CODES.append(ghost)
    lots = db.query(DyeLot).filter(DyeLot.vat_id == item.id).all()
    for lot in lots:
        lot.vat_id = item.id + 99999  # 悬空
    db.delete(item)
    db.commit()

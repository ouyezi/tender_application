from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import DiagnosisConfig, utcnow
from app.schemas import ConfigCreate, ConfigOut, ConfigUpdate

router = APIRouter(prefix="/api/configs", tags=["configs"])


@router.get("", response_model=list[ConfigOut])
async def list_configs(db: AsyncSession = Depends(get_db)) -> list[DiagnosisConfig]:
    result = await db.execute(select(DiagnosisConfig).order_by(DiagnosisConfig.id))
    return list(result.scalars().all())


@router.post("", response_model=ConfigOut, status_code=status.HTTP_201_CREATED)
async def create_config(payload: ConfigCreate, db: AsyncSession = Depends(get_db)) -> DiagnosisConfig:
    row = DiagnosisConfig(**payload.model_dump())
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@router.put("/{config_id}", response_model=ConfigOut)
async def update_config(
    config_id: int,
    payload: ConfigUpdate,
    db: AsyncSession = Depends(get_db),
) -> DiagnosisConfig:
    row = await db.get(DiagnosisConfig, config_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Config not found")
    for key, value in payload.model_dump().items():
        setattr(row, key, value)
    row.updated_at = utcnow()
    await db.commit()
    await db.refresh(row)
    return row


@router.delete("/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_config(config_id: int, db: AsyncSession = Depends(get_db)) -> None:
    row = await db.get(DiagnosisConfig, config_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Config not found")
    await db.delete(row)
    await db.commit()

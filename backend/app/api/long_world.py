from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.database import get_db
from app.repositories import (
    list_world, create_world, update_world, delete_world,
)
from app.schemas.long import WorldSettingCreate, WorldSettingUpdate

router = APIRouter(prefix="/world", tags=["long-world"])


@router.get("/{project_id}")
async def get_world(project_id: str, db: AsyncSession = Depends(get_db)):
    return await list_world(db, project_id)


@router.post("")
async def add_world(payload: WorldSettingCreate, db: AsyncSession = Depends(get_db)):
    return await create_world(db, payload.model_dump())


@router.put("/{world_id}")
async def edit_world(world_id: str, payload: WorldSettingUpdate, db: AsyncSession = Depends(get_db)):
    res = await update_world(db, world_id, payload.model_dump(exclude_unset=True))
    if not res:
        raise NotFoundError("世界观不存在")
    return res


@router.delete("/{world_id}")
async def remove_world(world_id: str, db: AsyncSession = Depends(get_db)):
    ok = await delete_world(db, world_id)
    if not ok:
        raise NotFoundError("世界观不存在")
    return {"ok": True}

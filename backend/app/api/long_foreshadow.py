from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.database import get_db
from app.repositories import (
    list_foreshadows, create_foreshadow, update_foreshadow, delete_foreshadow,
)
from app.schemas.long import ForeshadowCreate, ForeshadowUpdate

router = APIRouter(prefix="/foreshadows", tags=["long-foreshadow"])


@router.get("/{project_id}")
async def get_foreshadows(project_id: str, db: AsyncSession = Depends(get_db)):
    return await list_foreshadows(db, project_id)


@router.post("")
async def add_foreshadow(payload: ForeshadowCreate, db: AsyncSession = Depends(get_db)):
    return await create_foreshadow(db, payload.model_dump())


@router.put("/{foreshadow_id}")
async def edit_foreshadow(foreshadow_id: str, payload: ForeshadowUpdate, db: AsyncSession = Depends(get_db)):
    res = await update_foreshadow(db, foreshadow_id, payload.model_dump(exclude_unset=True))
    if not res:
        raise NotFoundError("伏笔不存在")
    return res


@router.delete("/{foreshadow_id}")
async def remove_foreshadow(foreshadow_id: str, db: AsyncSession = Depends(get_db)):
    ok = await delete_foreshadow(db, foreshadow_id)
    if not ok:
        raise NotFoundError("伏笔不存在")
    return {"ok": True}

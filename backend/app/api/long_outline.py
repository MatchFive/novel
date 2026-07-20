from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.database import get_db
from app.models import LongOutline, LongChangeRecord
from app.repositories import (
    list_outlines, create_outline, update_outline, delete_outline,
)
from app.schemas.long import OutlineCreate, OutlineUpdate

router = APIRouter(prefix="/outlines", tags=["long-outline"])


@router.get("/{project_id}")
async def get_outlines(project_id: str, db: AsyncSession = Depends(get_db)):
    return await list_outlines(db, project_id)


@router.post("")
async def add_outline(payload: OutlineCreate, db: AsyncSession = Depends(get_db)):
    return await create_outline(db, payload.model_dump())


@router.put("/{outline_id}")
async def edit_outline(outline_id: str, payload: OutlineUpdate, db: AsyncSession = Depends(get_db)):
    res = await update_outline(db, outline_id, payload.model_dump(exclude_unset=True))
    if not res:
        raise NotFoundError("大纲不存在")
    return res


@router.delete("/{outline_id}")
async def remove_outline(outline_id: str, db: AsyncSession = Depends(get_db)):
    ok = await delete_outline(db, outline_id)
    if not ok:
        raise NotFoundError("大纲不存在")
    return {"ok": True}


@router.get("/{project_id}/history/{outline_id}")
async def outline_history(project_id: str, outline_id: str, db: AsyncSession = Depends(get_db)):
    """返回该大纲依赖的版本链（向上回溯）。"""
    chain = []
    cur = await db.get(LongOutline, outline_id)
    seen = set()
    while cur and cur.id not in seen:
        seen.add(cur.id)
        chain.append({c.name: getattr(cur, c.name) for c in LongOutline.__table__.columns})
        if not cur.version_chain:
            break
        nxt = await db.get(LongOutline, cur.version_chain)
        if not nxt or nxt.id == cur.id:
            break
        cur = nxt
    return chain

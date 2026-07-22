from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.database import get_db
from app.repositories import (
    list_chapters, get_chapter, create_chapter, update_chapter, delete_chapter,
    reorder_chapters as apply_reorder,
)
from app.schemas.long import ChapterCreate, ChapterUpdate, ChapterReorder

router = APIRouter(prefix="/chapters", tags=["long-chapter"])


@router.get("/{project_id}")
async def get_chapters(project_id: str, db: AsyncSession = Depends(get_db)):
    rows = await list_chapters(db, project_id)
    return sorted(rows, key=lambda r: r.get("order", 0))


@router.get("/detail/{chapter_id}")
async def get_chapter_detail(chapter_id: str, db: AsyncSession = Depends(get_db)):
    row = await get_chapter(db, chapter_id)
    if not row:
        raise NotFoundError("章节不存在")
    return row


@router.post("")
async def add_chapter(payload: ChapterCreate, db: AsyncSession = Depends(get_db)):
    return await create_chapter(db, payload.model_dump())


@router.put("/{chapter_id}")
async def edit_chapter(chapter_id: str, payload: ChapterUpdate, db: AsyncSession = Depends(get_db)):
    res = await update_chapter(db, chapter_id, payload.model_dump(exclude_unset=True))
    if not res:
        raise NotFoundError("章节不存在")
    return res


@router.delete("/{chapter_id}")
async def remove_chapter(chapter_id: str, db: AsyncSession = Depends(get_db)):
    ok = await delete_chapter(db, chapter_id)
    if not ok:
        raise NotFoundError("章节不存在")
    return {"ok": True}


@router.post("/reorder")
async def reorder_chapters(body: ChapterReorder, db: AsyncSession = Depends(get_db)):
    await apply_reorder(db, body.project_id, body.chapter_ids)
    return {"ok": True}

"""数据访问层。仅供 API 层、change_apply 与只读工具层调用。
注意：Worker 不直接 import repositories，只用 agents/tools 暴露的只读工具。
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError
from app.models import (
    LongOutline,
    LongCharacter,
    LongForeshadow,
    LongWorldSetting,
    LongPlotNode,
    LongChapter,
)


async def _list(db: AsyncSession, model, project_id: str) -> list[dict]:
    res = await db.execute(select(model).where(model.project_id == project_id))
    rows = res.scalars().all()
    return [{c.name: getattr(r, c.name) for c in model.__table__.columns} for r in rows]


async def _row_to_dict(row) -> dict | None:
    if row is None:
        return None
    model = type(row)
    return {c.name: getattr(row, c.name) for c in model.__table__.columns}


async def _get(db: AsyncSession, model, row_id: str):
    return await db.get(model, row_id)


async def _create(db: AsyncSession, model, data: dict) -> dict:
    obj = model(**data)
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return {c.name: getattr(obj, c.name) for c in model.__table__.columns}


async def _update(db: AsyncSession, model, row_id: str, data: dict) -> dict:
    obj = await db.get(model, row_id)
    if not obj:
        return None
    for k, v in data.items():
        if v is not None:
            setattr(obj, k, v)
    await db.commit()
    await db.refresh(obj)
    return {c.name: getattr(obj, c.name) for c in model.__table__.columns}


async def _delete(db: AsyncSession, model, row_id: str) -> bool:
    obj = await db.get(model, row_id)
    if not obj:
        return False
    await db.delete(obj)
    await db.commit()
    return True


async def _row_to_dict(row) -> dict | None:
    if row is None:
        return None
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


# ---- Outlines ----
async def list_outlines(db, project_id): return await _list(db, LongOutline, project_id)
async def get_outline(db, rid): return await _get(db, LongOutline, rid)
async def create_outline(db, data): return await _create(db, LongOutline, data)
async def update_outline(db, rid, data): return await _update(db, LongOutline, rid, data)
async def delete_outline(db, rid): return await _delete(db, LongOutline, rid)


# ---- Characters ----
async def list_characters(db, project_id): return await _list(db, LongCharacter, project_id)
async def get_character(db, rid): return await _get(db, LongCharacter, rid)
async def create_character(db, data): return await _create(db, LongCharacter, data)
async def update_character(db, rid, data): return await _update(db, LongCharacter, rid, data)
async def delete_character(db, rid): return await _delete(db, LongCharacter, rid)


# ---- Foreshadows ----
async def list_foreshadows(db, project_id): return await _list(db, LongForeshadow, project_id)
async def get_foreshadow(db, rid): return await _get(db, LongForeshadow, rid)
async def create_foreshadow(db, data): return await _create(db, LongForeshadow, data)
async def update_foreshadow(db, rid, data): return await _update(db, LongForeshadow, rid, data)
async def delete_foreshadow(db, rid): return await _delete(db, LongForeshadow, rid)


# ---- World ----
async def list_world(db, project_id): return await _list(db, LongWorldSetting, project_id)
async def get_world(db, rid): return await _get(db, LongWorldSetting, rid)
async def create_world(db, data): return await _create(db, LongWorldSetting, data)
async def update_world(db, rid, data): return await _update(db, LongWorldSetting, rid, data)
async def delete_world(db, rid): return await _delete(db, LongWorldSetting, rid)


# ---- Plot ----
async def list_plot(db, project_id): return await _list(db, LongPlotNode, project_id)
async def get_plot(db, rid): return await _get(db, LongPlotNode, rid)
async def create_plot(db, data): return await _create(db, LongPlotNode, data)
async def update_plot(db, rid, data): return await _update(db, LongPlotNode, rid, data)
async def delete_plot(db, rid): return await _delete(db, LongPlotNode, rid)


# ---- Chapters ----
async def list_chapters(db, project_id): return await _list(db, LongChapter, project_id)
async def get_chapter(db, rid):
    row = await _get(db, LongChapter, rid)
    return await _row_to_dict(row)
async def create_chapter(db, data): return await _create(db, LongChapter, data)
async def update_chapter(db, rid, data): return await _update(db, LongChapter, rid, data)
async def delete_chapter(db, rid): return await _delete(db, LongChapter, rid)


# ---- Character Memories ----
async def list_character_memories(db: AsyncSession, character_id: str) -> list[dict]:
    from app.models import LongCharacterMemory
    res = await db.execute(select(LongCharacterMemory).where(LongCharacterMemory.character_id == character_id))
    rows = res.scalars().all()
    return [{c.name: getattr(r, c.name) for c in LongCharacterMemory.__table__.columns} for r in rows]


async def get_character_memory(db: AsyncSession, memory_id: str) -> dict | None:
    from app.models import LongCharacterMemory
    row = await db.get(LongCharacterMemory, memory_id)
    return await _row_to_dict(row)


async def create_character_memory(db: AsyncSession, data: dict) -> dict:
    from app.models import LongCharacterMemory
    return await _create(db, LongCharacterMemory, data)


async def update_character_memory(db: AsyncSession, memory_id: str, data: dict) -> dict | None:
    from app.models import LongCharacterMemory
    return await _update(db, LongCharacterMemory, memory_id, data)


async def delete_character_memory(db: AsyncSession, memory_id: str) -> bool:
    from app.models import LongCharacterMemory
    return await _delete(db, LongCharacterMemory, memory_id)


# ---- Character Memory Drafts ----
async def list_character_memory_drafts(db: AsyncSession, chapter_id: str) -> list[dict]:
    from app.models import LongCharacterMemoryDraft
    res = await db.execute(
        select(LongCharacterMemoryDraft)
        .where(LongCharacterMemoryDraft.chapter_id == chapter_id)
        .order_by(LongCharacterMemoryDraft.character_id, LongCharacterMemoryDraft.created_at)
    )
    rows = res.scalars().all()
    return [{c.name: getattr(r, c.name) for c in LongCharacterMemoryDraft.__table__.columns} for r in rows]


async def create_character_memory_draft(db: AsyncSession, data: dict) -> dict:
    from app.models import LongCharacterMemoryDraft
    return await _create(db, LongCharacterMemoryDraft, data)


async def clear_character_memory_drafts(db: AsyncSession, chapter_id: str) -> None:
    from app.models import LongCharacterMemoryDraft
    await db.execute(delete(LongCharacterMemoryDraft).where(LongCharacterMemoryDraft.chapter_id == chapter_id))
    await db.commit()


# ---- Chapter Memory Extraction ----
async def get_chapter_memory_extraction(db: AsyncSession, chapter_id: str) -> dict | None:
    from app.models import LongChapterMemoryExtraction
    row = await db.get(LongChapterMemoryExtraction, chapter_id)
    if row is None:
        return None
    return {c.name: getattr(row, c.name) for c in LongChapterMemoryExtraction.__table__.columns}


async def set_chapter_memory_extraction(
    db: AsyncSession,
    chapter_id: str,
    content_hash: str,
    memory_count: int,
) -> None:
    from app.models import LongChapterMemoryExtraction
    from datetime import datetime, timezone
    row = await db.get(LongChapterMemoryExtraction, chapter_id)
    now = datetime.now(timezone.utc)
    if row is None:
        db.add(LongChapterMemoryExtraction(
            chapter_id=chapter_id,
            content_hash=content_hash,
            memory_count=memory_count,
            extracted_at=now,
        ))
    else:
        row.content_hash = content_hash
        row.memory_count = memory_count
        row.extracted_at = now
    await db.commit()


async def reorder_chapters(db: AsyncSession, project_id: str, chapter_ids: list[str]) -> bool:
    if not chapter_ids:
        return True

    rows = []
    for cid in chapter_ids:
        row = await db.get(LongChapter, cid)
        if row is None or row.project_id != project_id:
            raise ValidationError(f"章节 {cid} 不存在或不属于该项目")
        rows.append(row)

    for idx, row in enumerate(rows):
        row.order = idx

    await db.commit()
    return True

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.database import get_db
from app import repositories as repo
from app.services.character_memory import (
    apply_memory_drafts,
    discard_memory_drafts,
    extract_memory_drafts,
)

router = APIRouter(prefix="", tags=["long-memory"])


@router.post("/chapters/{chapter_id}/extract-memory")
async def extract_memory(chapter_id: str, db: AsyncSession = Depends(get_db)):
    result = await extract_memory_drafts(db, chapter_id)
    return result


@router.get("/chapters/{chapter_id}/memory-drafts")
async def get_memory_drafts(chapter_id: str, db: AsyncSession = Depends(get_db)):
    drafts = await repo.list_character_memory_drafts(db, chapter_id)
    grouped: dict[str, list[dict]] = {}
    for d in drafts:
        grouped.setdefault(d.get("character_id"), []).append(d)
    return {"ok": True, "drafts": drafts, "grouped_by_character": grouped}


@router.post("/memory-drafts/apply")
async def apply_drafts(body: dict, db: AsyncSession = Depends(get_db)):
    chapter_id = body.get("chapter_id")
    if not chapter_id:
        raise ValidationError("chapter_id 必填")
    return await apply_memory_drafts(db, chapter_id)


@router.post("/memory-drafts/discard")
async def discard_drafts(body: dict, db: AsyncSession = Depends(get_db)):
    chapter_id = body.get("chapter_id")
    if not chapter_id:
        raise ValidationError("chapter_id 必填")
    return await discard_memory_drafts(db, chapter_id)


@router.get("/characters/{character_id}/memories")
async def get_character_memories(character_id: str, db: AsyncSession = Depends(get_db)):
    rows = await repo.list_character_memories(db, character_id)
    return {"ok": True, "memories": rows}


@router.post("/characters/{character_id}/memories")
async def add_character_memory(character_id: str, body: dict, db: AsyncSession = Depends(get_db)):
    character = await repo.get_character(db, character_id)
    if not character:
        raise NotFoundError("角色不存在")
    data = {
        "project_id": character.get("project_id"),
        "character_id": character_id,
        "content": body.get("content", ""),
        "importance": body.get("importance", "major"),
        "ttl": body.get("ttl", "long"),
        "source_chapter_id": None,
        "source_type": "manual",
        "related_character_ids": body.get("related_character_ids") or [],
        "related_foreshadow_ids": body.get("related_foreshadow_ids") or [],
    }
    memory = await repo.create_character_memory(db, data)
    return {"ok": True, "memory": memory}


@router.put("/characters/{character_id}/memories/{memory_id}")
async def edit_character_memory(
    character_id: str,
    memory_id: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    memory = await repo.get_character_memory(db, memory_id)
    if not memory or memory.get("character_id") != character_id:
        raise NotFoundError("记忆不存在")
    update_data = {
        "content": body.get("content"),
        "importance": body.get("importance"),
        "ttl": body.get("ttl"),
        "related_character_ids": body.get("related_character_ids"),
        "related_foreshadow_ids": body.get("related_foreshadow_ids"),
    }
    update_data = {k: v for k, v in update_data.items() if v is not None}
    updated = await repo.update_character_memory(db, memory_id, update_data)
    return {"ok": True, "memory": updated}


@router.delete("/characters/{character_id}/memories/{memory_id}")
async def remove_character_memory(
    character_id: str,
    memory_id: str,
    db: AsyncSession = Depends(get_db),
):
    memory = await repo.get_character_memory(db, memory_id)
    if not memory or memory.get("character_id") != character_id:
        raise NotFoundError("记忆不存在")
    ok = await repo.delete_character_memory(db, memory_id)
    return {"ok": ok}

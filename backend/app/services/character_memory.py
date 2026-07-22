"""角色记忆提取与应用服务。"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo
from app.core.errors import NotFoundError
from app.core.llm_factory import get_llm_client
from app.models import LongCharacterMemory, LongCharacterMemoryDraft, LongChapterMemoryExtraction
from app.services.prompts.character_memory import memory_extraction_prompt

logger = logging.getLogger(__name__)


_VALID_IMPORTANCE = {"core", "major", "minor"}
_VALID_TTL = {"permanent", "long", "arc", "scene"}
_VALID_ACTIONS = {"add", "update", "delete"}


def _content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_memory_payload(raw: dict, existing_ids: set[str]) -> dict | None:
    action = raw.get("action", "add")
    if action not in _VALID_ACTIONS:
        logger.warning("Invalid memory action: %s", action)
        return None

    memory_id = raw.get("memory_id")
    if action in ("update", "delete") and memory_id not in existing_ids:
        logger.warning("Memory action %s references unknown memory_id %s", action, memory_id)
        return None

    importance = raw.get("importance", "major")
    if importance not in _VALID_IMPORTANCE:
        importance = "major"

    ttl = raw.get("ttl", "long")
    if ttl not in _VALID_TTL:
        ttl = "long"

    content = str(raw.get("content") or "").strip()
    if action != "delete" and not content:
        logger.warning("Skipping memory with empty content")
        return None

    related_character_ids = raw.get("related_character_ids") or []
    related_foreshadow_ids = raw.get("related_foreshadow_ids") or []

    return {
        "action": action,
        "target_memory_id": memory_id,
        "content": content,
        "importance": importance,
        "ttl": ttl,
        "related_character_ids": related_character_ids if isinstance(related_character_ids, list) else [],
        "related_foreshadow_ids": related_foreshadow_ids if isinstance(related_foreshadow_ids, list) else [],
    }


def _detect_character_appearances(chapter: dict, characters: list[dict]) -> list[dict]:
    """基于章节正文/细纲中角色姓名出现次数识别本章出场角色。"""
    text_parts = [
        chapter.get("content", ""),
        chapter.get("detailed_outline", ""),
    ]
    text = "\n".join(str(p) for p in text_parts if p)
    appeared = []
    for c in characters:
        name = c.get("name", "").strip()
        if not name:
            continue
        count = text.count(name)
        if count > 0:
            appeared.append((count, c))
    appeared.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in appeared]


async def extract_memory_drafts(db: AsyncSession, chapter_id: str) -> dict:
    chapter = await repo.get_chapter(db, chapter_id)
    if not chapter:
        raise NotFoundError("章节不存在")

    project_id = chapter.get("project_id")
    content = chapter.get("content") or ""
    current_hash = _content_hash(content)

    extraction = await repo.get_chapter_memory_extraction(db, chapter_id)
    if extraction and extraction.get("content_hash") == current_hash:
        return {
            "ok": True,
            "skipped": True,
            "message": "本章记忆已是最新，是否重新提取？",
            "drafts": [],
            "grouped_by_character": {},
        }

    await repo.clear_character_memory_drafts(db, chapter_id)

    characters = await repo.list_characters(db, project_id)
    foreshadows = await repo.list_foreshadows(db, project_id)
    appeared_characters = _detect_character_appearances(chapter, characters)

    if not appeared_characters:
        await repo.set_chapter_memory_extraction(db, chapter_id, current_hash, 0)
        return {
            "ok": True,
            "skipped": False,
            "drafts": [],
            "grouped_by_character": {},
        }

    llm = await get_llm_client(db, level="medium")

    all_drafts: list[dict] = []
    for character in appeared_characters:
        existing = await repo.list_character_memories(db, character.get("id"))
        existing_ids = {m.get("id") for m in existing}

        system = memory_extraction_prompt(
            chapter_text=content,
            character=character,
            existing_memories=existing,
            characters=characters,
            foreshadows=foreshadows,
        )
        messages = [{"role": "system", "content": system}]

        try:
            raw = await llm.parse_llm_json(messages)
        except Exception:
            logger.exception("LLM memory extraction failed for character %s", character.get("id"))
            continue

        if not isinstance(raw, dict):
            continue
        memories = raw.get("memories") or []
        if not isinstance(memories, list):
            continue

        for item in memories:
            if not isinstance(item, dict):
                continue
            payload = _normalize_memory_payload(item, existing_ids)
            if payload is None:
                continue
            draft_data = {
                "project_id": project_id,
                "chapter_id": chapter_id,
                "character_id": character.get("id"),
                "action": payload["action"],
                "target_memory_id": payload["target_memory_id"],
                "content": payload["content"],
                "importance": payload["importance"],
                "ttl": payload["ttl"],
                "related_character_ids": payload["related_character_ids"],
                "related_foreshadow_ids": payload["related_foreshadow_ids"],
            }
            draft = await repo.create_character_memory_draft(db, draft_data)
            all_drafts.append(draft)

    grouped: dict[str, list[dict]] = {}
    for d in all_drafts:
        cid = d.get("character_id")
        grouped.setdefault(cid, []).append(d)

    return {
        "ok": True,
        "skipped": False,
        "drafts": all_drafts,
        "grouped_by_character": grouped,
    }


async def apply_memory_drafts(db: AsyncSession, chapter_id: str) -> dict:
    drafts = await repo.list_character_memory_drafts(db, chapter_id)
    if not drafts:
        return {"ok": True, "applied": {"created": 0, "updated": 0, "deleted": 0}}

    chapter = await repo.get_chapter(db, chapter_id)
    if not chapter:
        raise NotFoundError("章节不存在")

    created = updated = deleted = 0
    try:
        for draft in drafts:
            action = draft.get("action")
            if action == "add":
                db.add(LongCharacterMemory(
                    project_id=draft.get("project_id"),
                    character_id=draft.get("character_id"),
                    content=draft.get("content"),
                    importance=draft.get("importance"),
                    ttl=draft.get("ttl"),
                    source_chapter_id=chapter_id,
                    source_type="auto",
                    related_character_ids=draft.get("related_character_ids") or [],
                    related_foreshadow_ids=draft.get("related_foreshadow_ids") or [],
                ))
                created += 1
            elif action == "update":
                target = draft.get("target_memory_id")
                if target:
                    row = await db.get(LongCharacterMemory, target)
                    if row:
                        row.content = draft.get("content")
                        row.importance = draft.get("importance")
                        row.ttl = draft.get("ttl")
                        row.related_character_ids = draft.get("related_character_ids") or []
                        row.related_foreshadow_ids = draft.get("related_foreshadow_ids") or []
                        row.source_chapter_id = chapter_id
                        row.source_type = "auto"
                        row.updated_at = _now()
                        updated += 1
            elif action == "delete":
                target = draft.get("target_memory_id")
                if target:
                    row = await db.get(LongCharacterMemory, target)
                    if row:
                        await db.delete(row)
                        deleted += 1

        await db.execute(delete(LongCharacterMemoryDraft).where(LongCharacterMemoryDraft.chapter_id == chapter_id))

        extraction = await db.get(LongChapterMemoryExtraction, chapter_id)
        now = _now()
        content_hash = _content_hash(chapter.get("content") or "")
        if extraction is None:
            db.add(LongChapterMemoryExtraction(
                chapter_id=chapter_id,
                content_hash=content_hash,
                memory_count=created + updated,
                extracted_at=now,
            ))
        else:
            extraction.content_hash = content_hash
            extraction.memory_count = created + updated
            extraction.extracted_at = now

        await db.commit()
    except Exception:
        await db.rollback()
        raise

    return {
        "ok": True,
        "applied": {"created": created, "updated": updated, "deleted": deleted},
    }


async def discard_memory_drafts(db: AsyncSession, chapter_id: str) -> dict:
    await repo.clear_character_memory_drafts(db, chapter_id)
    return {"ok": True}

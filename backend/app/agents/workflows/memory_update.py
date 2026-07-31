from __future__ import annotations

import logging

from app import repositories as repo
from app.agents.workflows.registry import register_step
from app.core.errors import AppError
from app.services.change_apply import apply_change
from app.services.character_memory import (
    _content_hash,
    extract_memory_drafts,
)

logger = logging.getLogger(__name__)


@register_step
async def extract_drafts(ctx):
    if not ctx.chapter_id:
        raise ValueError("memory_update workflow requires chapter_id")
    result = await extract_memory_drafts(ctx.db, ctx.chapter_id)
    drafts = result.get("drafts") or []
    return {
        "drafts": drafts,
        "grouped": result.get("grouped_by_character") or {},
        "skipped": result.get("skipped", False),
    }


@register_step
async def build_change_records(ctx):
    drafts = ctx.outputs.get("extract", {}).get("drafts", [])
    change_records = []
    for draft in drafts:
        action = draft.get("action", "add")
        record = {
            "entity_type": "memory",
            "action": action,
            "after": {
                "character_id": draft.get("character_id"),
                "content": draft.get("content"),
                "importance": draft.get("importance"),
                "ttl": draft.get("ttl"),
                "source_chapter_id": ctx.chapter_id,
                "source_type": "auto",
                "related_character_ids": draft.get("related_character_ids") or [],
                "related_foreshadow_ids": draft.get("related_foreshadow_ids") or [],
            },
        }
        if action in ("update", "delete"):
            record["entity_id"] = draft.get("target_memory_id")
        if action == "delete":
            record["after"] = {}
        change_records.append(record)
    return {"drafts_count": len(drafts), "change_records": change_records}


@register_step
async def apply_changes(ctx):
    if not ctx.inputs.get("auto_apply"):
        return {"applied": None}
    if not ctx.chapter_id:
        raise ValueError("memory_update workflow requires chapter_id")
    project_id = ctx.project_id
    if not project_id:
        raise ValueError("memory_update workflow requires project_id")

    change_records = ctx.outputs.get("build_changes", {}).get("change_records", [])
    results = []
    errors = []
    created = updated = deleted = 0

    for record in change_records:
        try:
            result = await apply_change(ctx.db, project_id, record)
            results.append(result)
            if result.get("ok"):
                action = record.get("action")
                if action == "add":
                    created += 1
                elif action == "update":
                    updated += 1
                elif action == "delete":
                    deleted += 1
            else:
                msg = result.get("message") or "apply_change returned failure"
                code = result.get("code") or "apply_failed"
                logger.error("apply_change failed for memory record: %s", msg)
                errors.append({
                    "record": record,
                    "code": code,
                    "message": msg,
                })
        except AppError as exc:
            logger.error("apply_change failed for memory record: %s", exc.message)
            errors.append({
                "record": record,
                "code": exc.code,
                "message": exc.message,
            })

    applied = {"created": created, "updated": updated, "deleted": deleted}

    if errors:
        return {
            "ok": False,
            "applied": applied,
            "results": results,
            "errors": errors,
        }

    chapter = await repo.get_chapter(ctx.db, ctx.chapter_id)
    content_hash = _content_hash(chapter.get("content") or "") if chapter else ""
    await repo.clear_character_memory_drafts(ctx.db, ctx.chapter_id)
    await repo.set_chapter_memory_extraction(
        ctx.db, ctx.chapter_id, content_hash, created + updated
    )

    return {
        "ok": True,
        "applied": applied,
        "results": results,
    }

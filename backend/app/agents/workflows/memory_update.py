from __future__ import annotations

import logging

from app.agents.workflows.registry import register_step
from app.services.character_memory import (
    apply_memory_drafts,
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
    return {"drafts_count": len(drafts)}


@register_step
async def apply_changes(ctx):
    if not ctx.inputs.get("auto_apply"):
        return {"applied": None}
    if not ctx.chapter_id:
        raise ValueError("memory_update workflow requires chapter_id")
    result = await apply_memory_drafts(ctx.db, ctx.chapter_id)
    return {"applied": result.get("applied")}

from __future__ import annotations

import logging
import uuid

from app import repositories as repo
from app.agents.harness.models import HarnessContext, Task
from app.agents.harness.worker_base import _load_worker_metadata, run_worker
from app.agents.harness.workers._chapter_utils import (
    active_foreshadows,
    assigned_plot_nodes,
    broad_outline_text,
    character_memories_for_chapter,
    chapter_summaries_chain,
    generation_settings,
    previous_chapter,
    previous_chapter_text_tail,
    volume_outline_text,
)
from app.agents.harness.workers.chapter_text_worker import ChapterTextWorker
from app.agents.workflows.registry import register_step
from app.config import settings
from app.core.errors import NotFoundError

logger = logging.getLogger(__name__)


@register_step
async def load_context(ctx):
    project_id = ctx.project_id
    chapter_id = ctx.inputs.get("chapter_id") or ctx.chapter_id
    if not project_id or not chapter_id:
        raise ValueError("chapter_generation workflow requires project_id and chapter_id")

    chapter = await repo.get_chapter(ctx.db, chapter_id)
    if not chapter:
        raise NotFoundError("章节不存在")

    chapters = await repo.list_chapters(ctx.db, project_id)
    outlines = await repo.list_outlines(ctx.db, project_id)
    characters = await repo.list_characters(ctx.db, project_id)
    world = await repo.list_world(ctx.db, project_id)
    plot_nodes = await repo.list_plot(ctx.db, project_id)
    foreshadows = await repo.list_foreshadows(ctx.db, project_id)
    target_words, rating = await generation_settings(ctx.db)

    prev = previous_chapter(chapter, chapters)
    context = {
        "chapter_id": chapter_id,
        "chapter": chapter,
        "chapters": chapters,
        "outlines": outlines,
        "characters": characters,
        "world": world,
        "plot_nodes": plot_nodes,
        "foreshadows": foreshadows,
        "assigned_plot_nodes": assigned_plot_nodes(plot_nodes, chapter_id),
        "active_foreshadows": active_foreshadows(foreshadows),
        "previous_chapter": prev,
        "previous_chapter_text_tail": previous_chapter_text_tail(prev),
        "previous_summaries": chapter_summaries_chain(chapter, chapters),
        "volume_outline": volume_outline_text(outlines, chapter.get("order", 0)),
        "broad_outline": broad_outline_text(outlines),
        "target_words": target_words,
        "rating": rating,
        "character_memories": await character_memories_for_chapter(ctx.db, chapter, characters),
        "detailed_outline": chapter.get("detailed_outline", ""),
    }
    ctx.outputs["context"] = context
    return {"chapter_id": chapter_id}


@register_step
async def generate_segments(ctx):
    context = ctx.outputs["context"]
    chapter_id = context["chapter_id"]
    chapter = context["chapter"]
    order = chapter.get("order", 0) + 1

    task = Task(
        id=f"chapter_text_{chapter_id}",
        worker="chapter_text",
        goal=f"生成第 {order} 章正文",
    )

    harness_ctx = HarnessContext(
        project_id=ctx.project_id,
        session_context={"entity_type": "chapter", "entity_id": chapter_id},
        entities={
            "characters": context.get("characters", []),
            "world": context.get("world", []),
            "foreshadows": context.get("foreshadows", []),
        },
    )

    metadata = _load_worker_metadata(ChapterTextWorker)
    llm = await ctx.llm_factory(metadata.model_level)
    recursive_limit = ctx.inputs.get("recursive_limit", settings.recursive_limit_default)

    result = await run_worker(
        ChapterTextWorker,
        ctx.db,
        llm,
        recursive_limit,
        task,
        harness_ctx,
        metadata=metadata,
    )

    if result.get("error"):
        raise RuntimeError(f"章节正文生成失败：{result['error']}")

    changes = result.get("changes") or []
    if not changes:
        raise RuntimeError("章节正文生成失败：worker 未返回任何变更")

    content = (changes[0].get("fields") or {}).get("content")
    if not content:
        raise RuntimeError("章节正文生成失败：worker 返回的变更缺少 content")

    return {"content": content, "notes": result.get("notes", [])}


@register_step
async def consistency_review(ctx):
    """一致性审校已在 ChapterTextWorker 中完成；此步骤仅透传结果。"""
    content = ctx.outputs["generate_segments"]["content"]
    return {"content": content, "notes": []}


@register_step
async def rating_check(ctx):
    """尺度检查已在 ChapterTextWorker 中完成；此步骤仅透传结果。"""
    content = ctx.outputs["consistency_review"]["content"]
    return {"content": content, "notes": []}


@register_step
async def assemble_change(ctx):
    context = ctx.outputs["context"]
    content = ctx.outputs["rating_check"]["content"]
    chapter_id = context["chapter_id"]
    notes: list[str] = []
    for key in ("generate_segments", "consistency_review", "rating_check"):
        notes.extend(ctx.outputs.get(key, {}).get("notes", []))

    record = {
        "id": f"cr_{uuid.uuid4().hex[:12]}",
        "project_id": ctx.project_id,
        "action": "update",
        "entity_type": "chapter",
        "entity_id": chapter_id,
        "after": {"content": content, "status": "generated"},
        "requires_confirmation": True,
        "stage": "chapter_generation",
    }
    ctx.change_records.append(record)
    return {"chapter_id": chapter_id, "content_length": len(content), "notes": notes}

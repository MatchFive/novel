from __future__ import annotations

import json
import logging

from app import repositories as repo
from app.agents.workflows.registry import register_step

logger = logging.getLogger(__name__)


WORLD_CONSISTENCY_PROMPT = """你是小说世界观一致性检查员。请检查世界观设定与角色、章节正文之间是否存在冲突。
只输出 JSON：{"issues": [{"location": "...", "conflict": "...", "suggestion": "..."}]}"""


@register_step
async def load_data(ctx):
    project_id = ctx.project_id
    if not project_id:
        raise ValueError("world_consistency workflow requires project_id")
    world = await repo.list_world(ctx.db, project_id)
    characters = await repo.list_characters(ctx.db, project_id)
    chapters = await repo.list_chapters(ctx.db, project_id)
    return {
        "world": world,
        "characters": characters,
        "chapters": chapters,
        "world_count": len(world),
        "chapter_count": len(chapters),
    }


@register_step
async def check(ctx):
    data = ctx.outputs["load_data"]
    llm = await ctx.llm_factory("medium")
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    messages = [{"role": "user", "content": WORLD_CONSISTENCY_PROMPT + "\n\n" + payload}]
    try:
        raw = await llm.parse_llm_json(messages)
    except Exception as exc:
        return {"issues": [], "messages": [f"检查失败：{exc}"]}

    if not isinstance(raw, dict):
        raw = {}
    issues = raw.get("issues") or []
    return {"issues": issues, "messages": [f"发现 {len(issues)} 处潜在冲突"]}

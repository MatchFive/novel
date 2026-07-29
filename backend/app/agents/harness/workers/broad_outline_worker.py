"""项目级总纲生成 Worker。"""
from __future__ import annotations

import logging

from app import repositories as repo
from app.agents.harness.context_builder import ContextBuilder, build_entities_from_context
from app.agents.harness.prompts.chapter_generation import broad_outline_prompt
from app.agents.harness.worker_base import WorkerBase
from app.agents.harness.workers._chapter_utils import user_prompt
from app.agents.harness.workers._compat import (
    context_entity_list,
    context_project_id,
    task_goal,
)

logger = logging.getLogger(__name__)


def _project_summary(context) -> str:
    """Return project_summary, falling back to the project entity title/description."""
    if hasattr(context, "project_summary"):
        summary = context.project_summary
    elif isinstance(context, dict):
        summary = context.get("project_summary")
    else:
        summary = None
    if summary:
        return summary

    if hasattr(context, "entities"):
        project = (context.entities or {}).get("project") or {}
    elif isinstance(context, dict):
        project = context.get("project") or {}
    else:
        project = {}

    return (
        f"{project.get('title', '未命名项目')}\n{project.get('description', '')}"
    ).strip() or "未提供项目摘要"


class BroadOutlineWorker(WorkerBase):
    worker_name = "broad_outline"

    async def run(self, task, context, history_context=None) -> dict:
        goal = task_goal(task)
        project_id = context_project_id(context)
        if not project_id:
            return {"changes": [], "stage": "broad_outline", "error": "缺少 project_id"}

        existing_outlines = await repo.list_outlines(self.db, project_id)

        related = ""
        entities = build_entities_from_context(context.entities) if hasattr(context, "entities") else context
        builder = ContextBuilder(self.db, self.llm, entities=entities)
        try:
            related = await builder.build(goal, focus_entity_type="outline")
        except Exception:
            logger.exception("ContextBuilder failed for broad_outline")

        prompt_context = {
            "project_summary": _project_summary(context),
            "existing_outlines": existing_outlines,
            "characters": context_entity_list(context, "characters"),
            "world": context_entity_list(context, "world"),
            "plot_nodes": context_entity_list(context, "plot"),
        }
        system = broad_outline_prompt(prompt_context)
        user_prompt_text = user_prompt(goal, related)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt_text},
        ]
        if history_context:
            messages = history_context + messages
        try:
            result = await self.llm.parse_llm_json(messages)
        except Exception:
            logger.exception("BroadOutlineWorker JSON generation failed")
            return {"changes": [], "stage": "broad_outline", "error": "无法解析 worker 输出"}
        return self._normalize_result(
            {
                "changes": result.get("changes", []) if isinstance(result, dict) else result,
                "stage": "broad_outline",
            }
        )

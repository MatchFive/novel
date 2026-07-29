"""项目级总纲生成 Worker。"""
from __future__ import annotations

import logging

from app import repositories as repo
from app.agents.harness.context_builder import ContextBuilder, build_entities_from_context
from app.agents.harness.prompts.chapter_generation import broad_outline_prompt
from app.agents.harness.worker_base import WorkerBase
from app.agents.harness.workers._compat import (
    context_entity_list,
    context_project_id,
    context_project_summary,
    task_goal,
)

logger = logging.getLogger(__name__)


def _user_prompt(goal: str, related: str = "") -> str:
    parts = []
    if related:
        parts.append(f"【相关上下文】\n{related}")
    parts.append(f"【用户目标】\n{goal}")
    return "\n\n".join(parts)


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
            "project_summary": context_project_summary(context) or "未提供项目摘要",
            "existing_outlines": existing_outlines,
            "characters": context_entity_list(context, "characters"),
            "world": context_entity_list(context, "world"),
            "plot_nodes": context_entity_list(context, "plot"),
        }
        system = broad_outline_prompt(prompt_context)
        user_prompt = _user_prompt(goal, related)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
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

"""把剧情节点分配到已有/新建章节 Worker。"""
from __future__ import annotations

import logging

from app import repositories as repo
from app.agents.harness.context_builder import ContextBuilder, build_entities_from_context
from app.agents.harness.prompts.chapter_generation import assignment_prompt
from app.agents.harness.worker_base import WorkerBase
from app.agents.harness.workers._chapter_utils import (
    generate_json,
    generation_settings,
    result_to_response,
    user_prompt,
)
from app.agents.harness.workers._compat import context_project_id, task_goal

logger = logging.getLogger(__name__)


class AssignmentWorker(WorkerBase):
    worker_name = "assignment"

    async def run(self, task, context, history_context=None) -> dict:
        goal = task_goal(task)
        project_id = context_project_id(context)
        if not project_id:
            return {"changes": [], "stage": "assignment", "error": "缺少 project_id"}

        plot_nodes = await repo.list_plot(self.db, project_id)
        chapters = await repo.list_chapters(self.db, project_id)

        related = ""
        entities = build_entities_from_context(context.entities) if hasattr(context, "entities") else context
        builder = ContextBuilder(self.db, self.llm, entities=entities)
        try:
            related = await builder.build(goal, focus_entity_type="chapter")
        except Exception:
            logger.exception("ContextBuilder failed for assignment")

        target_words, _rating = await generation_settings(self.db)
        prompt_context = {
            "plot_nodes": plot_nodes,
            "existing_chapters": chapters,
            "target_words": target_words,
        }
        system = assignment_prompt(prompt_context)
        system = await self._inject_skills(system, task)
        user_prompt_text = user_prompt(goal, related)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt_text},
        ]
        if history_context:
            messages = history_context + messages
        result = await generate_json(self.llm, messages)
        return self._normalize_result(result_to_response(result, "assignment"))

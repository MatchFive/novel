"""从总纲抽取关键剧情节点 Worker。"""
from __future__ import annotations

import logging

from app import repositories as repo
from app.agents.harness.context_builder import ContextBuilder, build_entities_from_context
from app.agents.harness.prompts.chapter_generation import plot_nodes_prompt
from app.agents.harness.worker_base import WorkerBase
from app.agents.harness.workers._chapter_utils import (
    broad_outline_text,
    generate_json,
    result_to_response,
    user_prompt,
)
from app.agents.harness.workers._compat import (
    context_entity_list,
    context_project_id,
    task_goal,
)

logger = logging.getLogger(__name__)


class PlotNodesWorker(WorkerBase):
    worker_name = "plot_nodes"

    async def run(self, task, context, history_context=None) -> dict:
        goal = task_goal(task)
        project_id = context_project_id(context)
        if not project_id:
            return {"changes": [], "stage": "plot_nodes", "error": "缺少 project_id"}

        outlines = await repo.list_outlines(self.db, project_id)
        plot_nodes = await repo.list_plot(self.db, project_id)

        related = ""
        entities = build_entities_from_context(context.entities) if hasattr(context, "entities") else context
        builder = ContextBuilder(self.db, self.llm, entities=entities)
        try:
            related = await builder.build(goal, focus_entity_type="plot")
        except Exception:
            logger.exception("ContextBuilder failed for plot_nodes")

        prompt_context = {
            "broad_outline": broad_outline_text(outlines),
            "existing_plot_nodes": plot_nodes,
            "characters": context_entity_list(context, "characters"),
            "world": context_entity_list(context, "world"),
        }
        system = plot_nodes_prompt(prompt_context)
        user_prompt_text = user_prompt(goal, related)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt_text},
        ]
        if history_context:
            messages = history_context + messages
        result = await generate_json(self.llm, messages)
        return self._normalize_result(result_to_response(result, "plot_nodes"))

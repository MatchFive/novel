from __future__ import annotations

import logging

from app.agents.harness.context_builder import ContextBuilder, build_entities_from_context
from app.agents.harness.worker_base import WorkerBase
from app.agents.harness.workers._compat import (
    context_entity_list,
    context_project_id,
    context_session_get,
    task_goal,
)

logger = logging.getLogger(__name__)


class WorldWorker(WorkerBase):
    worker_name = "world"

    async def run(self, task, context, history_context=None) -> dict:
        goal = task_goal(task)
        project_id = context_project_id(context)

        related = ""
        if project_id:
            entities = build_entities_from_context(context.entities) if hasattr(context, "entities") else context
            builder = ContextBuilder(self.db, self.llm, entities=entities)
            related = await builder.build(
                goal,
                focus_entity_type="world",
                focus_entity_id=context_session_get(context, "entity_id"),
            )

        worlds = context_entity_list(context, "world")
        worlds_desc = "\n".join(
            f"- {w.get('category')} (id={w.get('id')})"
            for w in worlds
        ) or "暂无现有世界观设定。"

        system = self.metadata.system_prompt if self.metadata else ""
        system = await self._inject_skills(system, task)
        user_prompt = f"【现有世界观】\n{worlds_desc}\n\n【相关上下文】\n{related or '（无）'}\n\n【用户目标】\n{goal}"
        return await self._tool_loop(system, user_prompt, history_context=history_context)

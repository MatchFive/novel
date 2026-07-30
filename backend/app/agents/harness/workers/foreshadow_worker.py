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


class ForeshadowWorker(WorkerBase):
    worker_name = "foreshadow"

    async def run(self, task, context, history_context=None) -> dict:
        goal = task_goal(task)
        project_id = context_project_id(context)

        related = ""
        if project_id:
            entities = build_entities_from_context(context.entities) if hasattr(context, "entities") else context
            builder = ContextBuilder(self.db, self.llm, entities=entities)
            related = await builder.build(
                goal,
                focus_entity_type="foreshadow",
                focus_entity_id=context_session_get(context, "entity_id"),
            )

        foreshadows = context_entity_list(context, "foreshadows")
        foreshadows_desc = "\n".join(
            f"- {f.get('title')} (id={f.get('id')}, state={f.get('state', 'pending')})"
            for f in foreshadows
        ) or "暂无现有伏笔。"
        existing_ids = {f.get("id") for f in foreshadows if f.get("id")}

        system = self.metadata.system_prompt if self.metadata else ""
        system = await self._inject_skills(system, task)
        user_prompt = (
            f"【现有伏笔】\n{foreshadows_desc}\n\n"
            f"【相关上下文】\n{related or '（无）'}\n\n"
            f"【用户目标】\n{goal}"
        )
        raw = await self._tool_loop(system, user_prompt, history_context=history_context)
        return self._normalize_foreshadow_changes(raw, existing_ids)

    @staticmethod
    def _normalize_foreshadow_changes(raw: dict, existing_ids: set[str]) -> dict:
        """过滤非伏笔变更、修正 entity_id、统一实体类型。"""
        foreshadow_field_keys = {"title", "content", "state", "subplot_id"}
        changes: list[dict] = []
        for ch in raw.get("changes") or []:
            if not isinstance(ch, dict):
                continue
            fields = ch.get("fields") or {}
            if not fields:
                continue
            # 过滤掉非伏笔字段主导的变更（如 world 的 category/content 更新）
            if not any(k in fields for k in foreshadow_field_keys):
                logger.warning("ForeshadowWorker 返回非伏笔字段，已过滤: %s", ch)
                continue

            entity_id = ch.get("entity_id")
            action = ch.get("action", "add")
            title = fields.get("title")

            # update 的 id 不在现有伏笔中 -> 视为新增
            if action == "update" and entity_id and entity_id not in existing_ids:
                logger.warning(
                    "ForeshadowWorker 尝试更新不存在的伏笔 id=%s，title=%s，已转为新增",
                    entity_id,
                    title,
                )
                action = "add"
                entity_id = None

            # 确保 state 合法
            state = fields.get("state", "pending")
            if state not in {"pending", "revealed", "abandoned"}:
                state = "pending"
            fields["state"] = state

            changes.append({
                "action": action,
                "entity_id": entity_id,
                "entity_type": "foreshadow",
                "fields": fields,
            })

        return {"changes": changes, "stage": "foreshadow"}

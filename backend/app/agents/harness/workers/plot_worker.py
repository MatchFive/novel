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


class PlotWorker(WorkerBase):
    worker_name = "plot"

    async def run(self, task, context, history_context=None) -> dict:
        goal = task_goal(task)
        project_id = context_project_id(context)

        related = ""
        if project_id:
            entities = build_entities_from_context(context.entities) if hasattr(context, "entities") else context
            builder = ContextBuilder(self.db, self.llm, entities=entities)
            related = await builder.build(
                goal,
                focus_entity_type="plot",
                focus_entity_id=context_session_get(context, "entity_id"),
            )

        plots = context_entity_list(context, "plot")
        plots_desc = "\n".join(
            f"- {p.get('title')} (id={p.get('id')})"
            for p in plots
        ) or "暂无现有剧情节点。"
        existing_ids = {p.get("id") for p in plots if p.get("id")}

        system = self.metadata.system_prompt if self.metadata else ""
        system = await self._inject_skills(system, task)
        user_prompt = (
            f"【现有剧情节点】\n{plots_desc}\n\n"
            f"【相关上下文】\n{related or '（无）'}\n\n"
            f"【用户目标】\n{goal}"
        )
        raw = await self._tool_loop(system, user_prompt, history_context=history_context)
        return self._normalize_plot_changes(raw, existing_ids)

    @staticmethod
    def _normalize_plot_changes(raw: dict, existing_ids: set[str]) -> dict:
        """过滤非剧情节点变更、修正 entity_id、统一实体类型。"""
        plot_field_keys = {"title", "summary", "timeline_pos"}
        other_entity_fields = {
            "traits", "ability", "status", "relations", "importance",  # character
            "category", "content",  # world/outline
            "state", "subplot_id",  # foreshadow
            "type", "parent_id", "chapter_start", "chapter_end",  # outline
        }
        changes: list[dict] = []
        for ch in raw.get("changes") or []:
            if not isinstance(ch, dict):
                continue
            fields = ch.get("fields") or {}
            if not fields:
                continue
            # 必须包含剧情节点字段
            if not any(k in fields for k in plot_field_keys):
                logger.warning("PlotWorker 返回非剧情节点字段，已过滤: %s", ch)
                continue
            # 如果包含其他实体专属字段，说明这是角色/大纲/世界观的更新，不能当成剧情节点
            if any(k in fields for k in other_entity_fields):
                logger.warning("PlotWorker 返回混有非剧情节点字段的变更，已过滤: %s", ch)
                continue

            entity_id = ch.get("entity_id")
            action = ch.get("action", "add")
            title = fields.get("title")

            # update 的 id 不在现有剧情节点中 -> 视为新增
            if action == "update" and entity_id and entity_id not in existing_ids:
                logger.warning(
                    "PlotWorker 尝试更新不存在的剧情节点 id=%s，title=%s，已转为新增",
                    entity_id,
                    title,
                )
                action = "add"
                entity_id = None

            changes.append({
                "action": action,
                "entity_id": entity_id,
                "entity_type": "plot",
                "fields": {k: fields[k] for k in plot_field_keys if k in fields},
            })

        return {"changes": changes, "stage": "plot"}

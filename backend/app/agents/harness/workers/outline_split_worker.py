from __future__ import annotations

import logging

from app import repositories as repo
from app.agents.harness.worker_base import WorkerBase
from app.agents.harness.workers._compat import (
    context_project_id,
    context_session_get,
    task_goal,
)

logger = logging.getLogger(__name__)


class OutlineSplitWorker(WorkerBase):
    worker_name = "outline_split"

    async def run(self, task, context, history_context=None) -> dict:
        goal = task_goal(task)
        project_id = context_project_id(context)
        entity_id = context_session_get(context, "entity_id")
        if not project_id or not entity_id:
            return {"changes": [], "stage": "outline_split", "error": "缺少 project_id 或 entity_id"}

        outlines = await repo.list_outlines(self.db, project_id)
        target = next((o for o in outlines if o.get("id") == entity_id), None)
        if not target:
            return {"changes": [], "stage": "outline_split", "error": "目标大纲不存在"}

        system = self.metadata.system_prompt if self.metadata else ""
        user = f"【目标条目类型】{target.get('type')}\n【标题】{target.get('title')}\n【内容】\n{target.get('content', '')}\n\n【用户目标】\n{goal}"
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        if history_context:
            messages = history_context + messages
        result = await self.llm.parse_llm_json(messages)
        if not isinstance(result, dict):
            return {"changes": [], "stage": "outline_split", "error": "无法解析拆分结果"}

        changes = []
        target_type = target.get("type")
        parent_id = target.get("parent_id")
        valid_types = {"broad", "period", "volume"}
        broad_roots = [
            o for o in outlines
            if o.get("type") == "broad" and not o.get("parent_id")
        ]
        is_broad_root = target_type == "broad" and not parent_id

        if is_broad_root:
            effective_type = "broad"
        elif target_type in valid_types:
            effective_type = target_type
        else:
            # 兼容历史/legacy 类型（如“主线卷”）：若存在总纲根节点，将其归到总纲下作为时期
            effective_type = "period"
            if not parent_id and broad_roots:
                parent_id = broad_roots[0]["id"]

        if effective_type == "broad":
            periods = result.get("periods") or []
            for p in periods:
                period_id = f"temp:period:{len(changes)}"
                changes.append({
                    "action": "add", "temp_id": period_id,
                    "fields": {"title": p.get("title"), "content": p.get("summary"), "type": "period", "parent_id": entity_id}
                })
                for v in p.get("volumes") or []:
                    changes.append({
                        "action": "add",
                        "fields": {
                            "title": v.get("title"), "content": v.get("content"), "type": "volume",
                            "parent_id": period_id,
                            "chapter_start": v.get("chapter_start"), "chapter_end": v.get("chapter_end"),
                        }
                    })
        else:
            # period（或 legacy 归一化后的时期）：改写原条目并挂到正确父级，再生成卷
            update_fields = {
                "type": "period",
                "content": result.get("summary", target.get("content")),
            }
            if parent_id:
                update_fields["parent_id"] = parent_id
            changes.append({
                "action": "update", "entity_id": entity_id,
                "fields": update_fields
            })
            for v in result.get("volumes") or []:
                changes.append({
                    "action": "add",
                    "fields": {
                        "title": v.get("title"), "content": v.get("content"), "type": "volume",
                        "parent_id": entity_id,
                        "chapter_start": v.get("chapter_start"), "chapter_end": v.get("chapter_end"),
                    }
                })
        return {"changes": changes, "stage": "outline_split"}

from __future__ import annotations

import logging
import uuid

from app.agents.harness.context_builder import ContextBuilder, build_entities_from_context
from app.agents.harness.worker_base import WorkerBase
from app.agents.harness.workers._compat import (
    context_entity_list,
    context_project_id,
    context_session_get,
    task_goal,
)
from app.core.errors import AppError
from app.core.llm_client import LLMClient

logger = logging.getLogger(__name__)


class CharacterWorker(WorkerBase):
    worker_name = "character"

    async def run(self, task, context, history_context=None) -> dict:
        goal = task_goal(task)
        project_id = context_project_id(context)

        related = ""
        if project_id:
            entities = build_entities_from_context(context.entities) if hasattr(context, "entities") else context
            builder = ContextBuilder(self.db, self.llm, entities=entities)
            related = await builder.build(
                goal,
                focus_entity_type="character",
                focus_entity_id=context_session_get(context, "entity_id"),
            )

        chars = context_entity_list(context, "characters")
        chars_desc = "\n".join(
            f"- {c.get('name')} (id={c.get('id')})"
            for c in chars
        ) or "暂无现有角色。"

        system = self.metadata.system_prompt if self.metadata else ""
        user_prompt = f"【现有角色】\n{chars_desc}\n\n【相关上下文】\n{related or '（无）'}\n\n【用户目标】\n{goal}"
        raw = await self._tool_loop(system, user_prompt, history_context=history_context)

        if isinstance(raw, dict) and "raw" in raw and isinstance(raw["raw"], str):
            conversion_msgs = [
                {
                    "role": "system",
                    "content": (
                        "你是 JSON 转换器。把下面的角色设计输出转换为严格合法的 JSON，"
                        '格式：{"changes":[{"action":"add|update","entity_id":null或id,"fields":'
                        '{"name":"","traits":"","ability":"","status":"","relations":[],"importance":0}}]}\n\n'
                        "只输出 JSON，不要 markdown 代码块，不要解释。只保留角色字段，忽略大纲更新。"
                    ),
                },
                {"role": "user", "content": raw["raw"]},
            ]
            try:
                converted = await self.llm.parse_llm_json(conversion_msgs)
                if isinstance(converted, dict):
                    raw = converted
                elif isinstance(converted, list):
                    raw = {"changes": converted}
            except AppError:
                raise
            except Exception:
                logger.exception("CharacterWorker raw-to-json conversion failed")

        normalized = self._normalize_character_changes(raw, chars)
        return self._normalize_result(normalized)

    @staticmethod
    def _normalize_character_changes(raw: dict, chars: list[dict]) -> dict:
        """过滤非角色变更、修正 entity_id、统一 relations 格式。"""
        import uuid

        name_to_id = {c.get("name"): c.get("id") for c in chars if c.get("name")}
        existing_ids = {c.get("id") for c in chars if c.get("id")}
        character_field_keys = {"name", "traits", "ability", "status", "relations", "importance"}

        def _looks_like_uuid(value: str) -> bool:
            try:
                uuid.UUID(str(value))
                return True
            except Exception:
                return False

        def _resolve_relation(rel: dict) -> dict | None:
            if not isinstance(rel, dict):
                return None
            target_id = rel.get("target_id")
            target_name = rel.get("target")
            if not target_id and target_name:
                target_id = name_to_id.get(target_name)
            if not target_id and target_name and _looks_like_uuid(target_name):
                target_id = target_name
            relation_type = rel.get("relation_type") or rel.get("type") or "相关"
            if not target_id:
                return None
            return {"target_id": str(target_id), "relation_type": str(relation_type)}

        changes: list[dict] = []
        for ch in raw.get("changes") or []:
            if not isinstance(ch, dict):
                continue
            fields = ch.get("fields") or {}
            if not fields:
                continue
            # 过滤掉非角色字段占主导的变更（如 outline 的 content 更新）
            if not any(k in fields for k in character_field_keys):
                logger.warning("CharacterWorker 返回非角色字段，已过滤: %s", ch)
                continue

            entity_id = ch.get("entity_id")
            name = fields.get("name")
            action = ch.get("action", "add")

            # 若 name 已存在，强制改为 update 并使用现有 id
            if name and name in name_to_id:
                action = "update"
                entity_id = name_to_id[name]

            # 若 update 的 id 不在现有角色中，尝试按 name 匹配；仍匹配不到则视为新增
            if action == "update" and entity_id and entity_id not in existing_ids:
                entity_id = name_to_id.get(name) if name else None
                if not entity_id:
                    action = "add"

            # 规范化 relations
            relations = fields.get("relations")
            if isinstance(relations, list):
                fields["relations"] = [
                    r for r in (_resolve_relation(rel) for rel in relations) if r
                ]

            changes.append({
                "action": action,
                "entity_id": entity_id,
                "entity_type": "character",
                "fields": fields,
            })

        return {"changes": changes, "stage": "character"}

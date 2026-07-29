from __future__ import annotations

import json
import logging

from app.agents.harness.context_builder import ContextBuilder, build_entities_from_context
from app.agents.harness.worker_base import WorkerBase
from app.agents.harness.workers._compat import (
    context_entity_list,
    context_project_id,
    context_project_summary,
    context_session_get,
    task_goal,
)
from app.core.errors import AppError

logger = logging.getLogger(__name__)


class OutlineWorker(WorkerBase):
    worker_name = "outline"

    async def run(self, task, context, history_context=None) -> dict:
        goal = task_goal(task)
        project_id = context_project_id(context)
        if not project_id:
            return {"changes": [], "stage": "outline", "error": "缺少 project_id"}

        existing_outlines = context_entity_list(context, "outlines")

        related = ""
        entities = build_entities_from_context(context.entities) if hasattr(context, "entities") else context
        builder = ContextBuilder(self.db, self.llm, entities=entities)
        try:
            related = await builder.build(
                goal,
                focus_entity_type="outline",
                focus_entity_id=context_session_get(context, "entity_id"),
            )
        except Exception:
            logger.exception("ContextBuilder failed for outline")

        chars = context_entity_list(context, "characters")
        chars_desc = "\n".join(
            f"- {c.get('name')} (id={c.get('id')})"
            for c in chars
        ) or "暂无现有角色。"

        system = self.metadata.system_prompt if self.metadata else ""
        user_prompt = (
            f"【项目摘要】\n{context_project_summary(context) or '未提供'}\n\n"
            f"【现有大纲】\n{json.dumps(existing_outlines, ensure_ascii=False, indent=2)}\n\n"
            f"【现有角色】\n{chars_desc}\n\n"
            f"【世界观】\n{json.dumps(context_entity_list(context, 'world'), ensure_ascii=False, indent=2)}\n\n"
            f"【剧情节点】\n{json.dumps(context_entity_list(context, 'plot'), ensure_ascii=False, indent=2)}\n\n"
            f"【相关上下文】\n{related or '（无）'}\n\n"
            f"【用户目标】\n{goal}"
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ]
        if history_context:
            messages = history_context + messages

        try:
            result = await self.llm.parse_llm_json(messages)
            changes = []
            if isinstance(result, dict):
                changes = result.get("changes", []) or []
            elif isinstance(result, list):
                changes = result
            # 根据 entity_id 校正 entity_type，避免 outline worker 越权修改其他实体时类型错误
            id_to_type = self._build_id_type_map(context)
            for ch in changes:
                eid = ch.get("entity_id")
                if eid and eid in id_to_type:
                    ch["entity_type"] = id_to_type[eid]

            # 过滤掉把角色伪装成大纲条目的错误输出
            character_names = {c.get("name") for c in chars if c.get("name")}
            filtered_changes: list[dict] = []
            for ch in changes:
                if ch.get("action") != "add":
                    filtered_changes.append(ch)
                    continue
                fields = ch.get("fields") or {}
                title = fields.get("title") or ""
                ctype = fields.get("type") or ""
                # 角色类字段或类型说明这实际上是在创建角色/人物，而不是大纲节点
                if (
                    title in character_names
                    or ctype in {"配角", "角色", "人物", "主角", "龙套", "NPC"}
                    or any(k in fields for k in ("traits", "ability", "status", "relations", "importance"))
                ):
                    logger.warning(
                        "OutlineWorker 试图把角色 '%s' 伪装成大纲条目(type=%s)，已过滤",
                        title,
                        ctype,
                    )
                    continue
                filtered_changes.append(ch)

            filtered_changes = self._normalize_outline_changes(filtered_changes, existing_outlines)
            return {"changes": filtered_changes, "stage": "outline"}
        except AppError:
            raise
        except Exception:
            logger.exception("OutlineWorker JSON parsing failed")
        return {"changes": [], "stage": "outline", "error": "无法解析 worker 输出"}

    @staticmethod
    def _build_id_type_map(context) -> dict[str, str]:
        """根据 context 中的实体列表构建 id -> entity_type 映射。"""
        mapping: dict[str, str] = {}
        for entity_type, key in [
            ("character", "characters"),
            ("outline", "outlines"),
            ("world", "world"),
            ("plot", "plot"),
            ("foreshadow", "foreshadows"),
            ("chapter", "chapters"),
        ]:
            for e in context_entity_list(context, key):
                eid = e.get("id")
                if eid:
                    mapping[str(eid)] = entity_type
        return mapping

    @staticmethod
    def _normalize_outline_changes(changes: list[dict], existing_outlines: list[dict]) -> list[dict]:
        """修正新增大纲的 temp_id、占位父节点、层级合法性，并保证 broad/period/volume 顺序。"""
        existing_ids = {str(o.get("id")) for o in existing_outlines if o.get("id")}
        existing_by_id = {str(o.get("id")): o for o in existing_outlines if o.get("id")}
        broad_root_ids = [
            str(o.get("id"))
            for o in existing_outlines
            if o.get("type") == "broad" and not o.get("parent_id")
        ]

        # 给新增 broad 分配 temp_id，并记录 broad -> period 映射（已有 period 优先）
        broad_temp_ids: list[str] = []
        broad_counter = 0
        broad_to_periods: dict[str, list[str]] = {}
        for o in existing_outlines:
            if o.get("type") != "period":
                continue
            parent_id = str(o.get("parent_id")) if o.get("parent_id") else None
            if parent_id and parent_id in existing_ids and existing_by_id[parent_id].get("type") == "broad":
                broad_to_periods.setdefault(parent_id, []).append(str(o.get("id")))

        for ch in changes:
            if ch.get("action") != "add":
                continue
            fields = ch.get("fields") or {}
            if fields.get("type") == "broad" and not ch.get("temp_id"):
                broad_counter += 1
                tid = f"temp:broad:{broad_counter}"
                ch["temp_id"] = tid
                broad_temp_ids.append(tid)
                broad_to_periods.setdefault(tid, [])

        # 优先用本次新增的总纲作为 period 父级；否则用已有 broad 根节点
        default_broad_id = broad_temp_ids[0] if broad_temp_ids else (broad_root_ids[0] if broad_root_ids else None)

        # 给新增 period 分配 temp_id（volume 可能需要引用）
        period_temp_ids: list[str] = []
        period_counter = 0
        for ch in changes:
            if ch.get("action") != "add":
                continue
            fields = ch.get("fields") or {}
            if fields.get("type") == "period" and not ch.get("temp_id"):
                period_counter += 1
                tid = f"temp:period:{period_counter}"
                ch["temp_id"] = tid
                period_temp_ids.append(tid)
        default_period_id = period_temp_ids[0] if period_temp_ids else None

        def _is_placeholder(value) -> bool:
            return isinstance(value, str) and ("<" in value or ">" in value or value not in existing_ids and not value.startswith("temp:"))

        def _get_type(pid: str | None) -> str | None:
            if not pid:
                return None
            if pid in existing_by_id:
                return existing_by_id[pid].get("type")
            for ch in changes:
                if ch.get("temp_id") == pid:
                    return (ch.get("fields") or {}).get("type")
            return None

        def _period_range(period_id: str) -> tuple[int | None, int | None]:
            node = existing_by_id.get(period_id)
            if not node:
                for ch in changes:
                    if ch.get("temp_id") == period_id:
                        node = ch.get("fields") or {}
                        break
            if not node:
                return (None, None)
            return (node.get("chapter_start"), node.get("chapter_end"))

        def _choose_period_for_volume(volume_fields: dict, parent_id: str | None) -> str | None:
            """为 volume 选一个合适的 period：同 broad 下，优先 chapter_start 落在范围内的 period。"""
            broad_id = parent_id
            if broad_id and _get_type(broad_id) == "period":
                return broad_id
            if broad_id and _get_type(broad_id) != "broad":
                broad_id = None
            candidates = broad_to_periods.get(broad_id) if broad_id else None
            if not candidates:
                # 退而求其次：任何已有或新增的 period
                candidates = [str(o.get("id")) for o in existing_outlines if o.get("type") == "period"]
                candidates += period_temp_ids
            if not candidates:
                return None
            v_start = volume_fields.get("chapter_start") or volume_fields.get("chapter_end") or 1
            best = None
            for pid in candidates:
                start, end = _period_range(pid)
                if start is not None and end is not None and start <= v_start <= end:
                    return pid
                if best is None:
                    best = pid
            return best

        extra_periods: list[dict] = []
        for ch in changes:
            fields = ch.get("fields") or {}
            ctype = fields.get("type") or ""
            parent_id = fields.get("parent_id")

            if ctype == "broad":
                fields["parent_id"] = None
                fields.pop("chapter_start", None)
                fields.pop("chapter_end", None)
                # 避免 period 标题出现“卷”
                continue

            if ctype == "period":
                if parent_id is None or _is_placeholder(parent_id):
                    if default_broad_id:
                        fields["parent_id"] = default_broad_id
                # period 标题不应含“卷”
                title = fields.get("title") or ""
                if "卷" in title:
                    fields["title"] = title.replace("第一卷", "第一时期").replace("第二卷", "第二时期").replace("第三卷", "第三时期").replace("卷", "时期")
                fields.pop("chapter_start", None)
                fields.pop("chapter_end", None)
                continue

            if ctype == "volume":
                parent_type = _get_type(parent_id) if parent_id else None
                if parent_type != "period":
                    chosen = _choose_period_for_volume(fields, parent_id)
                    if chosen:
                        fields["parent_id"] = chosen
                    elif default_broad_id:
                        # 没有可用 period，临时创建一个
                        period_counter += 1
                        tid = f"temp:period:{period_counter}"
                        extra_periods.append({
                            "action": "add",
                            "temp_id": tid,
                            "fields": {
                                "title": f"时期 {period_counter}",
                                "content": "自动创建的中间时期节点",
                                "type": "period",
                                "parent_id": default_broad_id,
                            },
                        })
                        broad_to_periods.setdefault(default_broad_id, []).append(tid)
                        fields["parent_id"] = tid
                continue

        if extra_periods:
            changes.extend(extra_periods)

        # 保证顺序：broad -> period -> volume，同层保持原序
        type_order = {"broad": 0, "period": 1, "volume": 2}
        changes.sort(key=lambda c: type_order.get((c.get("fields") or {}).get("type"), 9))
        return changes

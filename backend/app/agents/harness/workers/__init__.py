from __future__ import annotations

import json
import logging

from app.agents.harness.context_builder import ContextBuilder, build_entities_from_context
from app.agents.harness.worker_base import WorkerBase
from app.core.errors import AppError
from app.core.llm_client import LLMClient
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

from app import repositories as repo

from .chapter_workers import (
    AssignmentWorker,
    BroadOutlineWorker,
    ChapterOutlineWorker,
    ChapterTextWorker,
    PlotNodesWorker,
)

__all__ = [
    "CharacterWorker",
    "WorldWorker",
    "OutlineWorker",
    "PlotWorker",
    "ForeshadowWorker",
    "OutlineSplitWorker",
    "BroadOutlineWorker",
    "PlotNodesWorker",
    "AssignmentWorker",
    "ChapterOutlineWorker",
    "ChapterTextWorker",
]


class CharacterWorker(WorkerBase):
    worker_name = "character"

    async def run(self, goal: str, context: dict, history_context: list[dict] | None = None) -> dict:
        related = ""
        if context.get("project_id"):
            builder = ContextBuilder(self.db, self.llm, entities=build_entities_from_context(context))
            related = await builder.build(
                goal,
                focus_entity_type="character",
                focus_entity_id=context.get("entity_id"),
            )

        chars = context.get("characters") or []
        chars_desc = "\n".join(
            f"- {c.get('name')} (id={c.get('id')})"
            for c in chars
        ) or "暂无现有角色。"

        system = (
            "你是角色设计师。基于用户目标设计或调整角色，最终以 JSON 返回建议变更："
            '{"changes": [{"action":"add|update", "entity_id":null或id, '
            '"fields": {"name":"", "traits":"", "ability":"", "status":"", "relations":[], "importance":0}}]}\n\n'
            "重要规则：\n"
            "1. 下面会提供「现有角色」列表。若用户目标中的角色 name 与现有角色 name 完全相同，"
            "必须返回 action='update'，entity_id 必须填该现有角色的 id，fields 为合并后的完整新内容。\n"
            "2. 只有 name 完全不存在于现有角色列表时，才返回 action='add'，entity_id=null。\n"
            "3. 不要创建与现有角色同名的重复角色。\n"
            "4. 参考【相关上下文】保持与现有设定一致。\n"
            "5. 只返回角色本身的字段（name/traits/ability/status/relations/importance），"
            "禁止返回 outline/world 等非角色字段，禁止修改大纲内容。\n"
            "若需调用工具进一步了解角色，请输出 TOOL_CALL:{\"name\":\"read_characters\",\"arguments\":{\"project_id\":\"...\"}}"
        )
        user_prompt = f"【现有角色】\n{chars_desc}\n\n【相关上下文】\n{related or '（无）'}\n\n【用户目标】\n{goal}"
        raw = await self._tool_loop(system, user_prompt, history_context=history_context)

        # 若 LLM 没按 JSON 输出，尝试用 json_object 模式二次转换
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
                # 配置类错误（如缺 API key）上抛，让接口返回明确错误
                raise
            except Exception:
                logger.exception("CharacterWorker raw-to-json conversion failed")

        return self._normalize_character_changes(raw, chars)

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


class WorldWorker(WorkerBase):
    worker_name = "world"

    async def run(self, goal: str, context: dict, history_context: list[dict] | None = None) -> dict:
        related = ""
        project_id = context.get("project_id")
        if project_id:
            builder = ContextBuilder(self.db, self.llm, entities=build_entities_from_context(context))
            related = await builder.build(
                goal,
                focus_entity_type="world",
                focus_entity_id=context.get("entity_id"),
            )

        worlds = context.get("world") or []
        worlds_desc = "\n".join(
            f"- {w.get('category')} (id={w.get('id')})"
            for w in worlds
        ) or "暂无现有世界观设定。"

        system = (
            "你是世界观设定师。基于用户目标新增或调整世界观设定，最终只返回合法 JSON，不要 markdown 代码块，不要解释："
            '{"changes": [{"action":"add|update", "entity_id":null或id, "fields":{"category":"", "content":""}}]}\n\n'
            "重要规则：\n"
            "1. 下面会提供「现有世界观」列表。若用户目标中的 category 与现有分类完全相同，"
            "必须返回 action='update'，entity_id 填该现有条目的 id，fields 为合并后的完整新内容。\n"
            "2. 只有 category 完全不存在于现有列表时，才返回 action='add'，entity_id=null。\n"
            "3. 不要创建与现有分类重复的世界观条目。\n"
            "4. changes 数组中的每个元素必须是完整对象，包含 action、entity_id、fields 三个键。\n"
            "5. 参考【相关上下文】保持世界观设定与角色、大纲一致，避免冲突。\n"
            f"若需调用工具进一步了解，请输出 TOOL_CALL:{{\"name\":\"read_world\",\"arguments\":{{\"project_id\":\"{project_id}\"}}}}"
        )
        user_prompt = f"【现有世界观】\n{worlds_desc}\n\n【相关上下文】\n{related or '（无）'}\n\n【用户目标】\n{goal}"
        return await self._tool_loop(system, user_prompt, history_context=history_context)


class OutlineWorker(WorkerBase):
    worker_name = "outline"

    async def run(self, goal: str, context: dict, history_context: list[dict] | None = None) -> dict:
        project_id = context.get("project_id")
        if not project_id:
            return {"changes": [], "stage": "outline", "error": "缺少 project_id"}

        from app import repositories as repo

        existing_outlines = await repo.list_outlines(self.db, project_id)

        related = ""
        builder = ContextBuilder(self.db, self.llm, entities=build_entities_from_context(context))
        try:
            related = await builder.build(
                goal,
                focus_entity_type="outline",
                focus_entity_id=context.get("entity_id"),
            )
        except Exception:
            logger.exception("ContextBuilder failed for outline")

        chars = context.get("characters") or []
        chars_desc = "\n".join(
            f"- {c.get('name')} (id={c.get('id')})"
            for c in chars
        ) or "暂无现有角色。"

        system = (
            "你是大纲架构师。根据项目摘要、现有大纲、角色、世界观和剧情节点，生成或更新大纲。"
            "必须以合法 JSON 返回，不要 markdown 代码块，不要解释：\n"
            '{"changes":[{"action":"add|update","entity_id":null或id,"temp_id":"temp:broad:1（新增总纲时必填）",'
            '"fields":{'
            '"title":"","content":"","type":"broad|period|volume","parent_id":null,"chapter_start":null,"chapter_end":null}}]}\n\n'
            "业务规则：\n"
            "- 大纲为三级树：总纲 broad（顶层，无父级）、时期 period（父级必须是 broad）、卷 volume（父级必须是 period）。\n"
            "- 新增 period 时，parent_id 必须指向一个 broad 节点；新增 volume 时，parent_id 必须指向一个 period 节点，严禁直接挂在 broad 下。\n"
            "- period 节点的标题不要出现'卷'字，可用'第一时期/第二时期'或'第一部/第二部'等；只有 volume 节点才使用'第 X 卷'命名。\n"
            "- 如果一次返回里同时新增总纲和它的子节点，总纲必须设置 temp_id（如 \"temp:broad:1\"），"
            "子节点的 parent_id 必须直接引用该 temp_id。禁止使用 <broad_id>、<period_id> 这类占位符。\n"
            "- 更新现有大纲时，优先只修改 title、content、chapter_start、chapter_end；不要改动 type 和 parent_id，除非用户明确要求移动层级。\n"
            "- chapter_start/chapter_end 仅 volume 可填，表示该卷覆盖的章节范围；1-based，且 start <= end。\n"
            "- 若用户目标涉及已有大纲，优先使用 action='update' 并填写其 id。\n"
            "- content 应包含主线目标、核心冲突、关键转折、整体结构。\n"
            "- 参考【现有角色】与【相关上下文】保持大纲与角色、世界观一致。\n"
            "- 不要编造未在角色/世界观列表中出现的设定。\n"
            "- 本 Worker 只修改大纲，绝不能创建新角色或修改角色实体；"
            "若用户要求新增/修改角色，请在 content 中留白或提示用户由角色设计师处理。\n"
            "- 严禁把角色/人物包装成大纲条目新增；type 不能是 '配角'/'角色'/'人物'/'主角'/'龙套'/'NPC'。\n"
            "- 不要把为角色写的人物小传作为独立大纲节点返回。"
        )
        user_prompt = (
            f"【项目摘要】\n{context.get('project_summary') or '未提供'}\n\n"
            f"【现有大纲】\n{json.dumps(existing_outlines, ensure_ascii=False, indent=2)}\n\n"
            f"【现有角色】\n{chars_desc}\n\n"
            f"【世界观】\n{json.dumps(context.get('world') or [], ensure_ascii=False, indent=2)}\n\n"
            f"【剧情节点】\n{json.dumps(context.get('plot') or [], ensure_ascii=False, indent=2)}\n\n"
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
    def _build_id_type_map(context: dict) -> dict[str, str]:
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
            for e in context.get(key) or []:
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


class PlotWorker(WorkerBase):
    worker_name = "plot"

    async def run(self, goal: str, context: dict, history_context: list[dict] | None = None) -> dict:
        related = ""
        project_id = context.get("project_id")
        if project_id:
            builder = ContextBuilder(self.db, self.llm, entities=build_entities_from_context(context))
            related = await builder.build(
                goal,
                focus_entity_type="plot",
                focus_entity_id=context.get("entity_id"),
            )

        plots = context.get("plot") or []
        plots_desc = "\n".join(
            f"- {p.get('title')} (id={p.get('id')})"
            for p in plots
        ) or "暂无现有剧情节点。"
        existing_ids = {p.get("id") for p in plots if p.get("id")}

        system = (
            "你是剧情节点编排师。基于用户目标新增或调整剧情节点（plot_node），最终只返回合法 JSON，不要 markdown 代码块，不要解释："
            '{"changes":[{"action":"add|update","entity_id":null或id,"fields":{"title":"","summary":"","timeline_pos":""}}]}\n\n'
            "重要规则：\n"
            "1. 只操作剧情节点（plot_node）实体，严禁修改角色（character）、世界观（world）、大纲（outline）、伏笔（foreshadow）的 id。\n"
            "2. 新增剧情节点时必须使用 action='add' 且 entity_id=null。\n"
            "3. 若用户目标中的剧情节点 title 与【现有剧情节点】完全相同，才使用 action='update' 并填写对应 id。\n"
            "4. 不要创建与现有剧情节点 title 重复的条目。\n"
            "5. fields 必须包含 title 和 summary；timeline_pos 可为空，常用值如 开篇/发展/高潮/结局 或自定义位置。\n"
            "6. 参考【相关上下文】保持剧情节点与角色、大纲、伏笔一致。\n"
            f"若需调用工具进一步了解，请输出 TOOL_CALL:{{\"name\":\"read_plot_nodes\",\"arguments\":{{\"project_id\":\"{project_id}\"}}}}"
        )
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


class ForeshadowWorker(WorkerBase):
    worker_name = "foreshadow"

    async def run(self, goal: str, context: dict, history_context: list[dict] | None = None) -> dict:
        related = ""
        project_id = context.get("project_id")
        if project_id:
            builder = ContextBuilder(self.db, self.llm, entities=build_entities_from_context(context))
            related = await builder.build(
                goal,
                focus_entity_type="foreshadow",
                focus_entity_id=context.get("entity_id"),
            )

        foreshadows = context.get("foreshadows") or []
        foreshadows_desc = "\n".join(
            f"- {f.get('title')} (id={f.get('id')}, state={f.get('state', 'pending')})"
            for f in foreshadows
        ) or "暂无现有伏笔。"
        existing_ids = {f.get("id") for f in foreshadows if f.get("id")}

        system = (
            "你是伏笔设计师。基于用户目标新增或调整伏笔（foreshadow）条目，最终只返回合法 JSON，不要 markdown 代码块，不要解释："
            '{"changes":[{"action":"add|update","entity_id":null或id,"fields":{"title":"","content":"","state":"pending","subplot_id":null}}]}\n\n'
            "重要规则：\n"
            "1. 只操作伏笔（foreshadow）实体，严禁修改世界观（world）、角色（character）、大纲（outline）或剧情节点（plot）的 id。\n"
            "2. 新增伏笔时必须使用 action='add' 且 entity_id=null。\n"
            "3. 若用户目标中的伏笔 title 与【现有伏笔】完全相同，才使用 action='update' 并填写对应 id。\n"
            "4. 不要创建与现有伏笔 title 重复的条目。\n"
            "5. fields 必须包含 title 和 content；state 默认为 pending，可选 pending/revealed/abandoned。\n"
            "6. 参考【相关上下文】保持伏笔与角色、剧情节点、大纲、世界观一致。\n"
            f"若需调用工具进一步了解，请输出 TOOL_CALL:{{\"name\":\"read_foreshadows\",\"arguments\":{{\"project_id\":\"{project_id}\"}}}}"
        )
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


class OutlineSplitWorker(WorkerBase):
    worker_name = "outline_split"

    async def run(self, goal: str, context: dict, history_context: list[dict] | None = None) -> dict:
        project_id = context.get("project_id")
        entity_id = context.get("entity_id")
        if not project_id or not entity_id:
            return {"changes": [], "stage": "outline_split", "error": "缺少 project_id 或 entity_id"}

        outlines = await repo.list_outlines(self.db, project_id)
        target = next((o for o in outlines if o.get("id") == entity_id), None)
        if not target:
            return {"changes": [], "stage": "outline_split", "error": "目标大纲不存在"}

        system = (
            "你是大纲拆分师。根据用户目标，把一篇完整大纲拆分为固定三级树结构（总纲 broad / 时期 period / 卷 volume）。"
            "如果目标条目是总纲级长文，输出多个时期；如果目标条目是单个时期，输出该时期的多个卷。"
            "只返回合法 JSON，不要 markdown 代码块，不要解释。\n\n"
            "输出格式（当目标是时期时）：\n"
            '{"summary": "时期级概述（改写原条目）", "volumes": ['
            '{"title": "卷标题", "content": "卷大纲全文", "chapter_start": 1, "chapter_end": 10}'
            ']}\n\n'
            "输出格式（当目标是总纲时）：\n"
            '{"periods": [{"title": "时期标题", "summary": "时期概述", "volumes": [...]}]}\n\n'
            "chapter_start/chapter_end 表示该卷覆盖第几章到第几章（1-based），允许为 null。"
        )
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

from __future__ import annotations

from app.agents.harness.context_builder import ContextBuilder
from app.agents.harness.worker_base import WorkerBase
from app.core.llm_client import LLMClient
from sqlalchemy.ext.asyncio import AsyncSession

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
            builder = ContextBuilder(self.db, self.llm, entities=context)
            related = await builder.build(goal, "character")

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
            "若需调用工具进一步了解角色，请输出 TOOL_CALL:{\"name\":\"read_characters\",\"arguments\":{\"project_id\":\"...\"}}"
        )
        user_prompt = f"【现有角色】\n{chars_desc}\n\n【相关上下文】\n{related or '（无）'}\n\n【用户目标】\n{goal}"
        return await self._tool_loop(system, user_prompt, history_context=history_context)


class WorldWorker(WorkerBase):
    worker_name = "world"

    async def run(self, goal: str, context: dict, history_context: list[dict] | None = None) -> dict:
        related = ""
        project_id = context.get("project_id")
        if project_id:
            builder = ContextBuilder(self.db, self.llm, entities=context)
            related = await builder.build(goal, "world")

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
        related = ""
        if context.get("project_id"):
            builder = ContextBuilder(self.db, self.llm, entities=context)
            related = await builder.build(goal, "outline")

        system = (
            "你是大纲架构师。使用只读工具 read_outlines / read_outline / read_outline_prev_version "
            "了解现有大纲与版本链，再产出新大纲修订。"
            '返回 JSON：{"changes":[{"action":"add|update","entity_id":null或id,'
            '"fields":{"title":"","content":"","parent_id":null}}]}。\n\n'
            "参考【相关上下文】保持大纲与角色、剧情节点、伏笔、世界观一致。\n"
            "若需调用工具，请输出 TOOL_CALL:{\"name\":\"read_outlines\",\"arguments\":{\"project_id\":\"...\"}}"
        )
        user_prompt = f"【相关上下文】\n{related or '（无）'}\n\n【用户目标】\n{goal}"
        return await self._tool_loop(system, user_prompt, history_context=history_context)


class PlotWorker(WorkerBase):
    worker_name = "plot"

    async def run(self, goal: str, context: dict, history_context: list[dict] | None = None) -> dict:
        related = ""
        if context.get("project_id"):
            builder = ContextBuilder(self.db, self.llm, entities=context)
            related = await builder.build(goal, "plot")

        system = (
            "你是剧情节点编排师。使用只读工具 read_plot_nodes / read_outlines 取数，"
            '返回 JSON：{"changes":[{"action":"add|update","entity_id":null或id,'
            '"fields":{"title":"","summary":"","timeline_pos":""}}]}。\n\n'
            "参考【相关上下文】保持剧情节点与角色、大纲、伏笔一致。\n"
            "若需调用工具，请输出 TOOL_CALL:{\"name\":\"read_plot_nodes\",\"arguments\":{\"project_id\":\"...\"}}"
        )
        user_prompt = f"【相关上下文】\n{related or '（无）'}\n\n【用户目标】\n{goal}"
        return await self._tool_loop(system, user_prompt, history_context=history_context)


class ForeshadowWorker(WorkerBase):
    worker_name = "foreshadow"

    async def run(self, goal: str, context: dict, history_context: list[dict] | None = None) -> dict:
        related = ""
        if context.get("project_id"):
            builder = ContextBuilder(self.db, self.llm, entities=context)
            related = await builder.build(goal, "foreshadow")

        system = (
            "你是伏笔设计师。使用只读工具 read_foreshadows / read_plot_nodes 取数，"
            '返回 JSON：{"changes":[{"action":"add|update","entity_id":null或id,'
            '"fields":{"title":"","content":"","state":"pending","subplot_id":null}}]}。\n\n'
            "参考【相关上下文】保持伏笔与角色、剧情节点、大纲一致。\n"
            "若需调用工具，请输出 TOOL_CALL:{\"name\":\"read_foreshadows\",\"arguments\":{\"project_id\":\"...\"}}"
        )
        user_prompt = f"【相关上下文】\n{related or '（无）'}\n\n【用户目标】\n{goal}"
        return await self._tool_loop(system, user_prompt, history_context=history_context)

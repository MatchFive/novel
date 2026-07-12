from __future__ import annotations

from app.agents.harness.worker_base import WorkerBase
from app.core.llm_client import LLMClient
from sqlalchemy.ext.asyncio import AsyncSession


class CharacterWorker(WorkerBase):
    worker_name = "character"

    async def run(self, goal: str, context: dict) -> dict:
        system = (
            "你是角色设计师。利用只读工具 read_characters / read_character 了解现有角色，"
            "然后基于用户目标设计或调整角色。最终以 JSON 返回建议变更："
            '{"changes": [{"action":"add|update", "entity_id":null或id, '
            '"fields": {"name":"", "traits":"", "ability":"", "status":"", "relations":[], "importance":0}}]}'
            "若需调用工具，请输出 TOOL_CALL:{\"name\":\"read_characters\",\"arguments\":{\"project_id\":\"...\"}}"
        )
        return await self._tool_loop(system, goal)


class WorldWorker(WorkerBase):
    worker_name = "world"

    async def run(self, goal: str, context: dict) -> dict:
        system = (
            "你是世界观设定师。使用只读工具 read_world 了解现有设定，"
            '返回 JSON：{"changes":[{"action":"add|update","entity_id":null或id,'
            '"fields":{"category":"","content":""}}]}。'
            "工具调用格式：TOOL_CALL:{\"name\":\"read_world\",\"arguments\":{\"project_id\":\"...\"}}"
        )
        return await self._tool_loop(system, goal)


class OutlineWorker(WorkerBase):
    worker_name = "outline"

    async def run(self, goal: str, context: dict) -> dict:
        system = (
            "你是大纲架构师。使用只读工具 read_outlines / read_outline / read_outline_prev_version "
            "了解现有大纲与版本链，再产出新大纲修订。"
            '返回 JSON：{"changes":[{"action":"add|update","entity_id":null或id,'
            '"fields":{"title":"","content":"","parent_id":null}}]}。'
            "工具调用格式：TOOL_CALL:{\"name\":\"read_outlines\",\"arguments\":{\"project_id\":\"...\"}}"
        )
        return await self._tool_loop(system, goal)


class PlotWorker(WorkerBase):
    worker_name = "plot"

    async def run(self, goal: str, context: dict) -> dict:
        system = (
            "你是剧情节点编排师。使用只读工具 read_plot_nodes / read_outlines 取数，"
            '返回 JSON：{"changes":[{"action":"add|update","entity_id":null或id,'
            '"fields":{"title":"","summary":"","timeline_pos":""}}]}。'
            "工具调用格式：TOOL_CALL:{\"name\":\"read_plot_nodes\",\"arguments\":{\"project_id\":\"...\"}}"
        )
        return await self._tool_loop(system, goal)


class ForeshadowWorker(WorkerBase):
    worker_name = "foreshadow"

    async def run(self, goal: str, context: dict) -> dict:
        system = (
            "你是伏笔设计师。使用只读工具 read_foreshadows / read_plot_nodes 取数，"
            '返回 JSON：{"changes":[{"action":"add|update","entity_id":null或id,'
            '"fields":{"title":"","content":"","state":"pending","subplot_id":null}}]}。'
            "工具调用格式：TOOL_CALL:{\"name\":\"read_foreshadows\",\"arguments\":{\"project_id\":\"...\"}}"
        )
        return await self._tool_loop(system, goal)

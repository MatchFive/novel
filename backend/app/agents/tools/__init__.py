"""只读工具层：Worker 仅能经此读取真实 DB 状态，无任何写能力。

每个工具内部封装 repositories（或 Neo4j），但对外只暴露"只读"语义。
工具注册表统一暴露给 Worker 的 tool-calling loop。
"""
from __future__ import annotations

import uuid
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo


# ---- 工具定义：name -> (函数, 描述, 参数 schema) ----
TOOL_REGISTRY: dict[str, dict] = {}


def register_tool(name: str, description: str, parameters: dict, fn: Callable[..., Awaitable[Any]]):
    TOOL_REGISTRY[name] = {
        "name": name,
        "description": description,
        "parameters": parameters,
        "fn": fn,
    }


async def read_outlines(db: AsyncSession, project_id: str) -> list[dict]:
    return await repo.list_outlines(db, project_id)


async def read_outline(db: AsyncSession, outline_id: str) -> dict | None:
    return await repo.get_outline(db, outline_id)


async def read_outline_prev_version(db: AsyncSession, outline_id: str) -> dict | None:
    o = await repo.get_outline(db, outline_id)
    if not o or not o.get("version_chain"):
        return None
    return await repo.get_outline(db, o["version_chain"])


async def read_characters(db: AsyncSession, project_id: str) -> list[dict]:
    return await repo.list_characters(db, project_id)


async def read_character(db: AsyncSession, character_id: str) -> dict | None:
    return await repo.get_character(db, character_id)


async def read_character_memories(
    db: AsyncSession,
    character_id: str,
    importance: str | None = None,
    ttl: str | None = None,
    related_foreshadow_id: str | None = None,
    limit: int = 20,
) -> list[dict]:
    memories = await repo.list_character_memories(db, character_id)
    result = []
    for m in memories:
        if importance and m.get("importance") != importance:
            continue
        if ttl and m.get("ttl") != ttl:
            continue
        if related_foreshadow_id:
            related = m.get("related_foreshadow_ids") or []
            if related_foreshadow_id not in related:
                continue
        result.append(m)
        if len(result) >= limit:
            break
    return result


async def read_foreshadows(db: AsyncSession, project_id: str) -> list[dict]:
    return await repo.list_foreshadows(db, project_id)


async def read_world(db: AsyncSession, project_id: str) -> list[dict]:
    return await repo.list_world(db, project_id)


async def read_plot_nodes(db: AsyncSession, project_id: str) -> list[dict]:
    return await repo.list_plot(db, project_id)


async def read_chapters(db: AsyncSession, project_id: str) -> list[dict]:
    return await repo.list_chapters(db, project_id)


async def read_chapter(db: AsyncSession, chapter_id: str) -> dict | None:
    return await repo.get_chapter(db, chapter_id)


async def read_plot_node(db: AsyncSession, plot_node_id: str) -> dict | None:
    return await repo.get_plot(db, plot_node_id)


# 注册
register_tool(
    "read_outlines",
    "读取项目全部大纲（树）。参数：project_id",
    {"type": "object", "properties": {"project_id": {"type": "string"}}, "required": ["project_id"]},
    read_outlines,
)
register_tool(
    "read_outline",
    "读取单条大纲详情。参数：outline_id",
    {"type": "object", "properties": {"outline_id": {"type": "string"}}, "required": ["outline_id"]},
    read_outline,
)
register_tool(
    "read_outline_prev_version",
    "读取大纲的上一版本（版本链）。参数：outline_id",
    {"type": "object", "properties": {"outline_id": {"type": "string"}}, "required": ["outline_id"]},
    read_outline_prev_version,
)
register_tool(
    "read_characters",
    "读取项目全部角色。参数：project_id",
    {"type": "object", "properties": {"project_id": {"type": "string"}}, "required": ["project_id"]},
    read_characters,
)
register_tool(
    "read_character",
    "读取单个角色详情。参数：character_id",
    {"type": "object", "properties": {"character_id": {"type": "string"}}, "required": ["character_id"]},
    read_character,
)
register_tool(
    "read_character_memories",
    "读取角色的已知信息记忆。支持按 importance、ttl、关联伏笔过滤。参数：character_id, importance(可选core|major|minor), ttl(可选permanent|long|arc|scene), related_foreshadow_id(可选), limit(默认20)",
    {
        "type": "object",
        "properties": {
            "character_id": {"type": "string"},
            "importance": {"type": "string"},
            "ttl": {"type": "string"},
            "related_foreshadow_id": {"type": "string"},
            "limit": {"type": "integer"},
        },
        "required": ["character_id"],
    },
    read_character_memories,
)
register_tool(
    "read_foreshadows",
    "读取项目全部伏笔。参数：project_id",
    {"type": "object", "properties": {"project_id": {"type": "string"}}, "required": ["project_id"]},
    read_foreshadows,
)
register_tool(
    "read_world",
    "读取项目全部世界观设定。参数：project_id",
    {"type": "object", "properties": {"project_id": {"type": "string"}}, "required": ["project_id"]},
    read_world,
)
register_tool(
    "read_plot_nodes",
    "读取项目全部剧情节点。参数：project_id",
    {"type": "object", "properties": {"project_id": {"type": "string"}}, "required": ["project_id"]},
    read_plot_nodes,
)
register_tool(
    "read_chapters",
    "读取项目全部章节。参数：project_id",
    {"type": "object", "properties": {"project_id": {"type": "string"}}, "required": ["project_id"]},
    read_chapters,
)
register_tool(
    "read_chapter",
    "读取单个章节详情。参数：chapter_id",
    {"type": "object", "properties": {"chapter_id": {"type": "string"}}, "required": ["chapter_id"]},
    read_chapter,
)
register_tool(
    "read_plot_node",
    "读取单个剧情节点详情。参数：plot_node_id",
    {"type": "object", "properties": {"plot_node_id": {"type": "string"}}, "required": ["plot_node_id"]},
    read_plot_node,
)


async def call_tool(db: AsyncSession, name: str, arguments: dict) -> Any:
    tool = TOOL_REGISTRY.get(name)
    if not tool:
        raise ValueError(f"未知工具：{name}")
    # 只读：工具函数只允许以 db + 只读参数调用
    if "db" in tool["fn"].__code__.co_varnames:
        return await tool["fn"](db, **arguments)
    return await tool["fn"](**arguments)


def tool_schemas() -> list[dict]:
    return [
        {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}
        for t in TOOL_REGISTRY.values()
    ]

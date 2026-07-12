"""写意图工具：仅生成/追加 ChangeRecord 到当前会话 staged_changes。
不落库 —— 这是"变更→确认→应用"闭环的硬性约束：任何写意图只能表达为 ChangeRecord。
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo
from app.models import AssistantSession


def _new_cr_id() -> str:
    return f"cr_{uuid.uuid4().hex[:12]}"


async def _append_staged(db: AsyncSession, project_id: str, change: dict) -> dict:
    sess = await db.execute(
        __import__("sqlalchemy").select(AssistantSession).where(AssistantSession.project_id == project_id)
    )
    s = sess.scalars().first()
    if not s:
        s = AssistantSession(project_id=project_id, staged_changes=[])
        db.add(s)
    staged = list(s.staged_changes or [])
    staged.append(change)
    s.staged_changes = staged
    await db.commit()
    return change


# 以下工具供 Worker 在 tool-calling loop 中调用，返回 ChangeRecord 草稿。
# 它们不执行任何 DB 写（仅记录意图到会话）。真正的写入由 change_apply 完成。

async def propose_add_character(db: AsyncSession, project_id: str, data: dict) -> dict:
    change = {
        "id": _new_cr_id(),
        "project_id": project_id,
        "action": "add",
        "entity_type": "character",
        "entity_id": None,
        "after": data,
        "requires_confirmation": True,
    }
    return await _append_staged(db, project_id, change)


async def propose_update_character(db: AsyncSession, project_id: str, character_id: str, data: dict) -> dict:
    before = await repo.get_character(db, character_id)
    change = {
        "id": _new_cr_id(),
        "project_id": project_id,
        "action": "update",
        "entity_type": "character",
        "entity_id": character_id,
        "before": before,
        "after": data,
        "requires_confirmation": True,
    }
    return await _append_staged(db, project_id, change)


async def propose_add_foreshadow(db: AsyncSession, project_id: str, data: dict) -> dict:
    change = {
        "id": _new_cr_id(),
        "project_id": project_id,
        "action": "add",
        "entity_type": "foreshadow",
        "entity_id": None,
        "after": data,
        "requires_confirmation": True,
    }
    return await _append_staged(db, project_id, change)


async def propose_update_outline(db: AsyncSession, project_id: str, outline_id: str, data: dict) -> dict:
    before = await repo.get_outline(db, outline_id)
    change = {
        "id": _new_cr_id(),
        "project_id": project_id,
        "action": "update",
        "entity_type": "outline",
        "entity_id": outline_id,
        "before": before,
        "after": data,
        "requires_confirmation": True,
    }
    return await _append_staged(db, project_id, change)


async def propose_update_world(db: AsyncSession, project_id: str, world_id: str, data: dict) -> dict:
    before = await repo.get_world(db, world_id)
    change = {
        "id": _new_cr_id(),
        "project_id": project_id,
        "action": "update",
        "entity_type": "world",
        "entity_id": world_id,
        "before": before,
        "after": data,
        "requires_confirmation": True,
    }
    return await _append_staged(db, project_id, change)


async def propose_add_plot(db: AsyncSession, project_id: str, data: dict) -> dict:
    change = {
        "id": _new_cr_id(),
        "project_id": project_id,
        "action": "add",
        "entity_type": "plot",
        "entity_id": None,
        "after": data,
        "requires_confirmation": True,
    }
    return await _append_staged(db, project_id, change)

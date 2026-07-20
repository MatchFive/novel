from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.database import get_db
from app.models import LongOutline, LongChangeRecord
from app.repositories import (
    list_outlines, create_outline, update_outline, delete_outline,
)
from app.schemas.long import OutlineCreate, OutlineUpdate

router = APIRouter(prefix="/outlines", tags=["long-outline"])


async def _is_descendant(db: AsyncSession, node_id: str, ancestor_id: str) -> bool:
    """BFS 检查 node_id 是否在 ancestor_id 的后代链中（含自身）。"""
    if node_id == ancestor_id:
        return True
    visited = set()
    stack = [ancestor_id]
    while stack:
        cur = stack.pop()
        if cur in visited:
            continue
        visited.add(cur)
        res = await db.execute(select(LongOutline).where(LongOutline.parent_id == cur))
        rows = res.scalars().all()
        for child in rows:
            if child.id == node_id:
                return True
            stack.append(child.id)
    return False


async def _validate_outline_payload(
    db: AsyncSession,
    project_id: str,
    payload: dict[str, Any],
    existing_id: str | None = None,
) -> None:
    """校验大纲节点的层级与字段约束；失败时抛出 ValidationError。"""
    def _fail(message: str):
        raise ValidationError(message, status_code=400)

    existing = None
    if existing_id:
        existing = await db.get(LongOutline, existing_id)
        if existing is None:
            raise NotFoundError("待更新大纲不存在")

    if "type" in payload:
        node_type = payload["type"]
    elif existing:
        node_type = existing.type
    else:
        _fail("缺少大纲类型")

    if node_type not in ("broad", "period", "volume"):
        _fail("大纲类型必须是 broad、period 或 volume")

    if "parent_id" in payload:
        parent_id = payload["parent_id"]
    elif existing:
        parent_id = existing.parent_id
    else:
        parent_id = None

    if node_type == "broad" and parent_id:
        _fail("总纲节点不能有父级")
    if node_type == "period" and not parent_id:
        _fail("时期节点必须属于某个总纲")
    if node_type == "volume" and not parent_id:
        _fail("卷节点必须属于某个时期")

    if parent_id:
        parent = await db.get(LongOutline, parent_id)
        if parent is None:
            _fail("父节点不存在")
        if parent.project_id != project_id:
            _fail("父节点不属于当前项目")
        expected = {"period": "broad", "volume": "period"}.get(node_type)
        if expected and parent.type != expected:
            _fail(f"{node_type} 节点的父级必须是 {expected}")
        if existing_id and await _is_descendant(db, parent_id, existing_id):
            _fail("不能将节点移动到自己的后代下")

    if existing:
        chapter_start = payload.get("chapter_start", existing.chapter_start)
        chapter_end = payload.get("chapter_end", existing.chapter_end)
    else:
        chapter_start = payload.get("chapter_start")
        chapter_end = payload.get("chapter_end")
    if node_type != "volume" and (chapter_start is not None or chapter_end is not None):
        _fail("只有卷节点可以设置章节范围")
    if chapter_start is not None and chapter_end is not None and chapter_start > chapter_end:
        _fail("起始章号不能大于结束章号")


@router.get("/{project_id}")
async def get_outlines(project_id: str, db: AsyncSession = Depends(get_db)):
    return await list_outlines(db, project_id)


@router.post("")
async def add_outline(payload: OutlineCreate, db: AsyncSession = Depends(get_db)):
    data = payload.model_dump()
    await _validate_outline_payload(db, data["project_id"], data)
    return await create_outline(db, data)


@router.put("/{outline_id}")
async def edit_outline(outline_id: str, payload: OutlineUpdate, db: AsyncSession = Depends(get_db)):
    existing = await db.get(LongOutline, outline_id)
    if not existing:
        raise NotFoundError("大纲不存在")
    data = payload.model_dump(exclude_unset=True)
    await _validate_outline_payload(db, existing.project_id, data, existing_id=outline_id)
    res = await update_outline(db, outline_id, data)
    if not res:
        raise NotFoundError("大纲不存在")
    return res


@router.delete("/{outline_id}")
async def remove_outline(outline_id: str, db: AsyncSession = Depends(get_db)):
    row = await db.get(LongOutline, outline_id)
    if not row:
        raise NotFoundError("大纲不存在")
    child = (await db.execute(
        select(LongOutline.id).where(LongOutline.parent_id == outline_id).limit(1)
    )).scalar()
    if child:
        raise ValidationError("该节点存在子级，请先删除子级", status_code=400, code="HAS_CHILDREN")
    ok = await delete_outline(db, outline_id)
    if not ok:
        raise NotFoundError("大纲不存在")
    return {"ok": True}


@router.get("/{project_id}/history/{outline_id}")
async def outline_history(project_id: str, outline_id: str, db: AsyncSession = Depends(get_db)):
    """返回该大纲依赖的版本链（向上回溯）。"""
    chain = []
    cur = await db.get(LongOutline, outline_id)
    seen = set()
    while cur and cur.id not in seen:
        seen.add(cur.id)
        chain.append({c.name: getattr(cur, c.name) for c in LongOutline.__table__.columns})
        if not cur.version_chain:
            break
        nxt = await db.get(LongOutline, cur.version_chain)
        if not nxt or nxt.id == cur.id:
            break
        cur = nxt
    return chain

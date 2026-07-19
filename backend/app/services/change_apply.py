"""唯一写入口：change_apply Saga 双写（SQLite 真相源 + Neo4j id 主键镜像）。
结构化错误返回，去静默 rollback。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo
from app.core.errors import AppError, NotFoundError
from app.models import (
    AssistantSession, LongChangeRecord, LongCharacter, LongOutline,
    LongForeshadow, LongWorldSetting, LongPlotNode, LongChapter,
)
from app.graph.client import get_graph


logger = logging.getLogger(__name__)


_ENTITY_REPO = {
    "character": (repo.get_character, repo.create_character, repo.update_character, repo.delete_character),
    "outline": (repo.get_outline, repo.create_outline, repo.update_outline, repo.delete_outline),
    "foreshadow": (repo.get_foreshadow, repo.create_foreshadow, repo.update_foreshadow, repo.delete_foreshadow),
    "world": (repo.get_world, repo.create_world, repo.update_world, repo.delete_world),
    "plot": (repo.get_plot, repo.create_plot, repo.update_plot, repo.delete_plot),
    "chapter": (repo.get_chapter, repo.create_chapter, repo.update_chapter, repo.delete_chapter),
}


_ENTITY_MODELS = {
    "character": LongCharacter,
    "outline": LongOutline,
    "foreshadow": LongForeshadow,
    "world": LongWorldSetting,
    "plot": LongPlotNode,
    "chapter": LongChapter,
}


_DEDUP_KEY_FIELD = {
    "character": "name",
    "foreshadow": "title",
    "plot": "title",
    "outline": "title",
}


def _norm_text(v: Any) -> str:
    return str(v or "").strip().lower()


def _row_snapshot(row) -> dict:
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


async def _find_duplicate(db: AsyncSession, entity_type: str, project_id: str, after: dict):
    """按匹配键查同项目内已存在的实体。world 按 category+content 完全相同匹配；
    character/foreshadow/plot/outline 按名称/标题（trim + 大小写不敏感）匹配。"""
    model = _ENTITY_MODELS.get(entity_type)
    if model is None:
        return None
    res = await db.execute(select(model).where(model.project_id == project_id))
    rows = res.scalars().all()
    if entity_type == "world":
        cat = _norm_text(after.get("category"))
        content = _norm_text(after.get("content"))
        if not cat and not content:
            return None
        for row in rows:
            if _norm_text(row.category) == cat and _norm_text(row.content) == content:
                return row
        return None
    key = _DEDUP_KEY_FIELD.get(entity_type)
    if not key:
        return None
    val = _norm_text(after.get(key))
    if not val:
        return None
    for row in rows:
        if _norm_text(getattr(row, key)) == val:
            return row
    return None


def _sanitize_fields(entity_type: str, data: dict) -> dict:
    """只保留模型上真实存在的字段，丢弃 LLM 幻觉出的未知字段。"""
    model = _ENTITY_MODELS.get(entity_type)
    if not model:
        return data
    allowed = {c.name for c in model.__table__.columns}
    # id / project_id 由调用方控制，不应从 after 中写入
    allowed -= {"id", "project_id"}
    return {k: v for k, v in data.items() if k in allowed}


async def apply_change(db: AsyncSession, project_id: str, change: dict) -> dict:
    """应用单条变更。change 来自 staged_changes 或前端确认载荷。"""
    entity_type = change.get("entity_type")
    action = change.get("action", "add")
    entity_id = change.get("entity_id")
    after = change.get("after") or {}

    repo_tuple = _ENTITY_REPO.get(entity_type)
    if not repo_tuple:
        raise AppError(f"未知实体类型：{entity_type}", "UNKNOWN_ENTITY", 400)
    get_fn, create_fn, update_fn, delete_fn = repo_tuple

    # 清理 LLM 可能产生的未知字段，避免 SQLAlchemy / 数据库报错
    after = _sanitize_fields(entity_type, after)

    try:
        if entity_type == "world":
            # 世界观 content 应为文本；LLM 有时会返回 JSON 对象，需要兼容
            if "content" in after:
                content = after["content"]
                if not isinstance(content, str):
                    after["content"] = json.dumps(content, ensure_ascii=False) if isinstance(content, (dict, list)) else str(content)
            if "category" in after:
                category = after["category"]
                if not isinstance(category, str):
                    after["category"] = str(category)

        merged_info: dict | None = None
        if action == "add":
            try:
                dup = await _find_duplicate(db, entity_type, project_id, after)
            except Exception:
                logger.warning("查重异常，按新增处理", exc_info=True)
                dup = None
            if dup is not None:
                if entity_type == "world":
                    return {"ok": True, "entity_type": entity_type, "entity_id": dup.id, "skipped_duplicate": True}
                merged_fields = {
                    k: v for k, v in after.items()
                    if v is not None and v != "" and v != [] and v != {}
                    and k != _DEDUP_KEY_FIELD.get(entity_type)
                }
                if not merged_fields:
                    return {"ok": True, "entity_type": entity_type, "entity_id": dup.id, "skipped_duplicate": True}
                merged_info = {"before": _row_snapshot(dup)}
                row = await update_fn(db, dup.id, merged_fields)
                if not row:
                    raise NotFoundError("待更新实体不存在")
                new_id = dup.id
                after = merged_fields  # Neo4j 镜像只同步实际应用的字段
            else:
                data = dict(after)
                data["project_id"] = project_id
                row = await create_fn(db, data)
                new_id = row.get("id")
        elif action == "update":
            if not entity_id:
                raise AppError("update 缺少 entity_id", "BAD_CHANGE", 400)
            row = await update_fn(db, entity_id, after)
            if not row:
                raise NotFoundError("待更新实体不存在")
            new_id = entity_id
        elif action == "delete":
            ok = await delete_fn(db, entity_id)
            if not ok:
                raise NotFoundError("待删除实体不存在")
            new_id = entity_id
        else:
            raise AppError(f"未知动作：{action}", "BAD_CHANGE", 400)

        # —— Neo4j 镜像（id 主键，可选）——
        g = get_graph()
        if g:
            try:
                await g.sync_entity(entity_type, new_id, after)
            except Exception as e:  # 镜像失败不影响真相源，但结构化上报
                return {"ok": True, "entity_id": new_id, "warning": f"Neo4j 同步失败：{e}"}

        result = {"ok": True, "entity_type": entity_type, "entity_id": new_id}
        if merged_info is not None:
            result["merged_into"] = new_id
            result["before"] = merged_info["before"]
        return result
    except AppError:
        raise
    except Exception as e:
        logger.exception("应用 %s 变更失败: %s", entity_type, change)
        raise AppError(f"应用变更失败：{e}", "APPLY_FAILED", 500)


async def confirm_session(db: AsyncSession, session_id: str, change_ids: list[str] | None = None) -> dict:
    """确认会话中的 staged_changes。传入 change_ids 时只确认指定条目，其余保留。"""
    res = await db.execute(select(AssistantSession).where(AssistantSession.id == session_id))
    sess = res.scalars().first()
    if not sess:
        raise NotFoundError("会话不存在")
    project_id = sess.project_id
    staged = sess.staged_changes or []
    target_ids = set(change_ids) if change_ids else None
    applied = []
    errors = []
    remaining = []
    for ch in staged:
        ch_id = ch.get("id")
        if target_ids is not None and ch_id not in target_ids:
            remaining.append(ch)
            continue
        try:
            r = await apply_change(db, project_id, ch)
            applied.append({**r, "change_id": ch_id})
            db.add(LongChangeRecord(
                project_id=project_id,
                entity_type=ch.get("entity_type"),
                entity_id=r.get("entity_id") or ch.get("entity_id"),
                before=r.get("before") or ch.get("before"),
                after=ch.get("after"),
                status="applied",
            ))
            await db.commit()
        except AppError as e:
            logger.error("确认变更失败 change_id=%s: %s", ch_id, e.message)
            errors.append({"change_id": ch_id, "code": e.code, "message": e.message})
            try:
                await db.rollback()
            except Exception:
                pass
            remaining.append(ch)
    sess.staged_changes = remaining
    await db.commit()
    return {"ok": len(errors) == 0, "applied": applied, "errors": errors}


async def reject_session(db: AsyncSession, session_id: str, change_ids: list[str] | None = None) -> dict:
    """拒绝会话中的 staged_changes。传入 change_ids 时只拒绝指定条目，其余保留。"""
    res = await db.execute(select(AssistantSession).where(AssistantSession.id == session_id))
    sess = res.scalars().first()
    if not sess:
        raise NotFoundError("会话不存在")
    staged = sess.staged_changes or []
    target_ids = set(change_ids) if change_ids else None
    rejected = 0
    remaining = []
    for ch in staged:
        if target_ids is None or ch.get("id") in target_ids:
            rejected += 1
        else:
            remaining.append(ch)
    sess.staged_changes = remaining
    await db.commit()
    return {"ok": True, "rejected_count": rejected}

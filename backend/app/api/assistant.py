"""Agent 助手接口：chat（分析→派发→变更）、confirm（应用）、reject、undo。"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, NotFoundError, ValidationError
from app.core.llm_factory import get_llm_client, get_embedding_client
from app.database import get_db
from app.models import AssistantSession, AssistantMessage, LongChangeRecord, Project, UserSetting
from app.agents.harness.history import (
    should_summarize,
    summarize_messages,
    append_summary,
)
from app.agents.harness.retrieval import store_summary_embedding
from app.agents.harness.models import HarnessContext, HarnessStage
from app.agents.harness.runtime import HarnessRuntime
from app.agents.harness.state import HarnessState
from app.agents.harness.worker_manager import WorkerManager
from app.agents.harness.nodes.responder import GLOBAL_RESPONDER_PROMPT, respond
from app.services.change_apply import _ENTITY_REPO, apply_change, confirm_session, reject_session

logger = logging.getLogger(__name__)
router = APIRouter(tags=["assistant"])


async def _get_active_session(db, project_id) -> AssistantSession:
    res = await db.execute(
        select(AssistantSession)
        .where(AssistantSession.project_id == project_id, AssistantSession.is_active == True)  # noqa: E712
    )
    s = res.scalars().first()
    if not s:
        s = AssistantSession(
            project_id=project_id,
            title="对话 1",
            is_active=True,
            staged_changes=[],
            summaries=[],
            message_count=0,
        )
        db.add(s)
        await db.commit()
        await db.refresh(s)
    return s


async def _recursive_limit(db) -> int:
    res = await db.execute(select(UserSetting))
    s = res.scalars().first()
    return s.recursive_limit if s else 8


def _decode_project_id(project_id: str | None) -> str | None:
    """把前端 sentinel 'global' 转成 None，表示全局会话。"""
    if project_id is None or project_id == "global":
        return None
    return project_id


def _rule_based_plan(user_input: str) -> dict | None:
    """Return a simple plan dict for chapter generation / foreshadow / compound intents.

    This is a compatibility bridge while the LLM supervisor learns to output DAGs.
    """
    text = user_input.lower()
    # Chapter generation
    range_match = __import__("re").search(r"第\s*(\d+)\s*章\s*(?:到|至)\s*第\s*(\d+)\s*章", user_input)
    prefix_match = __import__("re").search(r"前\s*(\d+)\s*章", user_input)
    single_match = __import__("re").search(r"第\s*(\d+)\s*章", user_input)
    chapter_nums = None
    label = ""
    if range_match:
        start, end = int(range_match.group(1)), int(range_match.group(2))
        chapter_nums = list(range(start, end + 1))
        label = f"第 {start} 章到第 {end} 章"
    elif prefix_match:
        n = int(prefix_match.group(1))
        chapter_nums = list(range(1, n + 1))
        label = f"前 {n} 章"
    elif single_match:
        chapter_nums = [int(single_match.group(1))]
        label = f"第 {chapter_nums[0]} 章"

    if chapter_nums:
        has_outline = "细纲" in user_input or "章节大纲" in user_input
        has_text = "正文" in user_input or "写" in user_input
        worker = "chapter_outline" if has_outline or not has_text else "chapter_text"
        return {"intent": f"生成{label}{'细纲' if worker == 'chapter_outline' else '正文'}", "tasks": [{"worker": worker, "goal": user_input}]}

    # Compound intent
    has_character = any(kw in text for kw in ("角色", "人物", "主角", "配角", "龙套", "npc"))
    has_world = any(kw in text for kw in ("世界观", "设定", "规则", "体系", "境界"))
    has_outline_modify = any(kw in text for kw in ("完善大纲", "调整大纲", "更新大纲", "修改大纲"))
    has_foreshadow = any(kw in text for kw in ("伏笔", "悬念", "回收", "呼应", "预埋"))
    tasks = []
    if has_foreshadow:
        tasks.append({"worker": "foreshadow", "goal": user_input})
    if has_world:
        tasks.append({"worker": "world", "goal": user_input})
    if has_character:
        tasks.append({"worker": "character", "goal": user_input})
    if has_outline_modify:
        tasks.append({"worker": "outline", "goal": user_input})
    if len(tasks) > 1:
        return {"intent": user_input, "tasks": tasks}
    return None


_CHAPTER_AUTO_FIELDS = {"content", "detailed_outline", "status"}


def _is_chapter_auto_apply(record) -> bool:
    """章节正文/细纲的 update 变更直接落库，不进待确认列表。"""
    keys = set((record.after or {}).keys())
    return (
        record.entity_type == "chapter"
        and record.action == "update"
        and bool(record.entity_id)
        and keys <= _CHAPTER_AUTO_FIELDS
        and bool(keys & {"content", "detailed_outline"})
    )


@router.post("/chat")
async def chat(body: dict, db: AsyncSession = Depends(get_db)):
    project_id = body.get("project_id")
    user_input = body.get("message", "")
    context_payload = body.get("context") or {}

    if not user_input:
        raise ValidationError("message 必填")

    # Resolve project_id: explicit body value takes precedence, then context.project_id
    effective_project_id = project_id or context_payload.get("project_id")
    is_global = not effective_project_id

    if not is_global:
        proj = await db.get(Project, effective_project_id)
        if not proj:
            raise NotFoundError("项目不存在")

    sess = await _get_active_session(db, effective_project_id)
    # Merge context into session context
    sess.context = {**(sess.context or {}), **context_payload}

    try:
        user_msg = AssistantMessage(
            session_id=sess.id,
            role="user",
            content=user_input,
            metadata_={"context": context_payload},
        )
        db.add(user_msg)
        await db.flush()
    except Exception:
        logger.exception("Failed to persist user assistant message")
        await db.rollback()
        raise AppError(
            "无法保存用户消息，请稍后重试",
            code="PERSIST_FAILED",
            status_code=500,
        )

    recursive_limit = await _recursive_limit(db)

    settings_row = (await db.execute(select(UserSetting))).scalars().first()
    settings_obj = settings_row or UserSetting()

    # 加载当前会话全部消息 ID，切分「最近 N 条」与「更早消息」
    ids_res = await db.execute(
        select(AssistantMessage.id)
        .where(AssistantMessage.session_id == sess.id)
        .order_by(AssistantMessage.created_at.asc())
    )
    message_ids = [row[0] for row in ids_res.all()]
    current_idx = len(message_ids) - 1  # 即刚保存的用户消息
    recent_n = max(1, settings_obj.assistant_history_recent_messages or 20)
    recent_ids = message_ids[max(0, current_idx - recent_n):current_idx]

    recent_res = await db.execute(
        select(AssistantMessage)
        .where(AssistantMessage.id.in_(recent_ids))
        .order_by(AssistantMessage.created_at.asc())
    )
    recent_messages = recent_res.scalars().all()

    state = HarnessState(
        project_id=effective_project_id,
        session_id=sess.id,
        user_input=user_input,
    )
    state.context = HarnessContext(
        project_id=effective_project_id,
        user_input=user_input,
        session_context=sess.context,
    )

    async def llm_factory(level: str | None = None):
        return await get_llm_client(db, level=level)

    runtime = HarnessRuntime(
        state=state,
        manager=WorkerManager(),
        db=db,
        llm_factory=llm_factory,
        settings=settings_obj,
        recent_messages=recent_messages,
        is_global=is_global,
        recursive_limit=recursive_limit,
    )

    # Rule-based pre-planning for chapter generation / compound intent
    rule_plan = _rule_based_plan(user_input) if not is_global else None
    if rule_plan:
        from app.agents.harness.models import ExecutionPlan, Task
        import uuid
        state.plan = ExecutionPlan(
            intent=rule_plan["intent"],
            tasks=[Task(id=f"task_{uuid.uuid4().hex[:8]}", worker=t["worker"], goal=t["goal"]) for t in rule_plan["tasks"]],
        )
        state.stage = HarnessStage.EXECUTE

    final_state = await runtime.run()

    if final_state.error:
        logger.error("Harness runtime ended in error: %s", final_state.error.message)

    staged_records = getattr(final_state, "staged_records", final_state.change_records)
    staged = list(sess.staged_changes or [])
    staged.extend([r.model_dump() for r in staged_records])
    sess.staged_changes = staged
    await db.commit()

    summary = final_state.summary
    auto_applied = final_state.auto_applied
    if auto_applied:
        chapter_titles = {c.get("id"): c.get("title") for c in (final_state.context.entities.get("chapters") or [])}
        field_labels = {"content": "正文", "detailed_outline": "细纲", "status": "状态"}
        lines = ["", "---", "**已直接写入：**"]
        for a in auto_applied:
            label = "、".join(field_labels.get(f, f) for f in a["fields"] if f != "status")
            title = chapter_titles.get(a["entity_id"]) or a["entity_id"]
            lines.append(f"- 章节《{title}》的{label}已保存（可撤销）")
            for n in a.get("notes", []):
                lines.append(f"  - {n}")
        summary += "\n".join(lines)

    records_data = [r.model_dump() for r in staged_records]
    intent = final_state.plan.intent if final_state.plan else ("通用对话" if is_global else "")
    assistant_msg_id = None
    try:
        assistant_msg = AssistantMessage(
            session_id=sess.id,
            role="assistant",
            content=summary,
            metadata_={
                "intent": intent,
                "change_record_ids": [r.get("id") for r in records_data],
                "context": context_payload,
                "auto_applied": auto_applied,
            },
        )
        db.add(assistant_msg)
        await db.commit()
        await db.refresh(assistant_msg)
        assistant_msg_id = assistant_msg.id

        # 重新加载完整历史（含刚刚保存的助手回复），用于后续压缩
        hist_res = await db.execute(
            select(AssistantMessage)
            .where(AssistantMessage.session_id == sess.id)
            .order_by(AssistantMessage.created_at.asc())
        )
        reload_messages = hist_res.scalars().all()

        # 7. 触发历史摘要压缩
        sess.message_count += 2
        await db.commit()
        await db.refresh(sess)
        if should_summarize(sess, settings_obj):
            threshold = settings_obj.assistant_summary_threshold or 20
            recent = reload_messages[-(threshold * 2):]
            summary_llm = await get_llm_client(db, level="low")
            summary_text = await summarize_messages(recent, settings_obj, summary_llm)
            append_summary(sess, recent, summary_text, settings_obj)
            await db.commit()
            await db.refresh(sess)

            # 为新生成的摘要生成并持久化 embedding，供后续相似检索
            try:
                embedding_client, dimension = await get_embedding_client(db)
                latest_summary = (sess.summaries or [])[-1]
                await store_summary_embedding(
                    db,
                    sess.id,
                    latest_summary.get("turn_range", ""),
                    latest_summary.get("summary", ""),
                    embedding_client,
                    embedding_client.model,
                    dimension,
                )
                await db.commit()
            except Exception:
                logger.exception("Failed to store summary embedding")
    except Exception:
        logger.exception("Failed to persist assistant message")
        await db.rollback()

    return {
        "ok": True,
        "session_id": sess.id,
        "message_id": assistant_msg_id,
        "intent": intent,
        "change_records": records_data,
        "auto_applied": auto_applied,
        "summary": summary,
    }


@router.get("/session/{project_id}")
async def get_session(project_id: str, db: AsyncSession = Depends(get_db)):
    pid = _decode_project_id(project_id)
    sess = await _get_active_session(db, pid)
    return sess.to_dict()


@router.post("/session/{project_id}")
async def create_session(project_id: str, db: AsyncSession = Depends(get_db)):
    pid = _decode_project_id(project_id)
    if pid is not None:
        proj = await db.get(Project, pid)
        if not proj:
            raise NotFoundError("项目不存在")

    # 旧 session 全部置 inactive
    await db.execute(
        update(AssistantSession)
        .where(AssistantSession.project_id == pid)
        .values(is_active=False)
    )

    count_res = await db.execute(
        select(func.count()).where(AssistantSession.project_id == pid)
    )
    count = count_res.scalar() or 0
    new_session = AssistantSession(
        project_id=pid,
        title=f"对话 {count + 1}",
        is_active=True,
        staged_changes=[],
        summaries=[],
        message_count=0,
    )
    db.add(new_session)
    await db.commit()
    await db.refresh(new_session)
    return {"ok": True, "session": new_session.to_dict()}


@router.get("/sessions/{project_id}")
async def list_sessions(project_id: str, db: AsyncSession = Depends(get_db)):
    pid = _decode_project_id(project_id)
    res = await db.execute(
        select(AssistantSession)
        .where(AssistantSession.project_id == pid)
        .order_by(AssistantSession.updated_at.desc())
    )
    sessions = [s.to_dict() for s in res.scalars().all()]
    return {"ok": True, "sessions": sessions}


@router.post("/session/{session_id}/switch")
async def switch_session(session_id: str, db: AsyncSession = Depends(get_db)):
    sess = await db.get(AssistantSession, session_id)
    if not sess:
        raise NotFoundError("会话不存在")
    await db.execute(
        update(AssistantSession)
        .where(AssistantSession.project_id == sess.project_id)
        .values(is_active=False)
    )
    sess.is_active = True
    await db.commit()
    await db.refresh(sess)
    return {"ok": True, "session": sess.to_dict()}


@router.get("/session/{project_id}/history")
async def get_session_history(project_id: str, db: AsyncSession = Depends(get_db)):
    pid = _decode_project_id(project_id)
    sess = await _get_active_session(db, pid)
    res = await db.execute(
        select(AssistantMessage)
        .where(AssistantMessage.session_id == sess.id)
        .order_by(AssistantMessage.created_at.asc())
    )
    messages = [
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "metadata": m.metadata_ or {},
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in res.scalars().all()
    ]
    return {"ok": True, "session_id": sess.id, "messages": messages, "staged_changes": sess.staged_changes or []}


@router.post("/stage")
async def stage_change(body: dict, db: AsyncSession = Depends(get_db)):
    session_id = body.get("session_id")
    record = body.get("change_record")
    if not session_id or not record:
        raise ValidationError("session_id 与 change_record 必填")
    if not isinstance(record, dict):
        raise ValidationError("change_record 必须是对象")
    required = ("id", "action", "entity_type")
    for key in required:
        value = record.get(key)
        if not value or not isinstance(value, str):
            raise ValidationError(f"change_record.{key} 不能为空字符串")
    after = record.get("after")
    if not after or not isinstance(after, dict):
        raise ValidationError("change_record.after 必须是非空对象")
    if not isinstance(record.get("requires_confirmation"), bool):
        raise ValidationError("change_record.requires_confirmation 必须是布尔值")
    sess = await db.get(AssistantSession, session_id)
    if not sess:
        raise NotFoundError("会话不存在")
    if sess.project_id is None:
        raise ValidationError("全局会话不支持暂存变更")
    record["project_id"] = sess.project_id
    entity_type = record["entity_type"]
    if entity_type not in _ENTITY_REPO:
        raise ValidationError(f"不支持的实体类型：{entity_type}")
    staged = list(sess.staged_changes or [])
    staged.append(record)
    sess.staged_changes = staged
    await db.commit()
    return {"ok": True, "staged_changes": staged}


async def _mark_latest_assistant_message(db, session_id: str, status: str, count: int = 0, error_count: int = 0):
    res = await db.execute(
        select(AssistantMessage)
        .where(AssistantMessage.session_id == session_id, AssistantMessage.role == "assistant")
        .order_by(AssistantMessage.created_at.desc())
        .limit(1)
    )
    msg = res.scalars().first()
    if msg:
        meta = dict(msg.metadata_ or {})
        meta["status"] = status
        if status == "partial":
            meta["applied_count"] = count
            meta["error_count"] = error_count
        else:
            meta[f"{status}_count"] = count
        msg.metadata_ = meta
        await db.commit()


@router.post("/confirm")
async def confirm(body: dict, db: AsyncSession = Depends(get_db)):
    session_id = body.get("session_id")
    change_ids = body.get("change_ids")
    if not session_id:
        raise ValidationError("session_id 必填")
    if change_ids is not None and not isinstance(change_ids, list):
        raise ValidationError("change_ids 必须是数组")
    sess = await db.get(AssistantSession, session_id)
    if not sess:
        raise NotFoundError("会话不存在")
    if sess.project_id is None:
        return {"ok": False, "errors": [{"code": "GLOBAL_SESSION", "message": "全局会话不支持确认变更"}]}
    result = await confirm_session(db, session_id, change_ids=change_ids)
    try:
        if result.get("ok"):
            await _mark_latest_assistant_message(
                db, session_id, "applied", len(result.get("applied", []))
            )
        else:
            await _mark_latest_assistant_message(
                db, session_id, "partial",
                len(result.get("applied", [])),
                len(result.get("errors", []))
            )
    except Exception:
        logger.exception("Failed to mark latest assistant message status")
    return result


@router.post("/reject")
async def reject(body: dict, db: AsyncSession = Depends(get_db)):
    session_id = body.get("session_id")
    change_ids = body.get("change_ids")
    if not session_id:
        raise ValidationError("session_id 必填")
    if change_ids is not None and not isinstance(change_ids, list):
        raise ValidationError("change_ids 必须是数组")
    result = await reject_session(db, session_id, change_ids=change_ids)
    try:
        await _mark_latest_assistant_message(
            db, session_id, "rejected", result.get("rejected_count", 0)
        )
    except Exception:
        logger.exception("Failed to mark latest assistant message status")
    return result


@router.post("/undo")
async def undo(body: dict, db: AsyncSession = Depends(get_db)):
    project_id = body.get("project_id")
    entity_type = body.get("entity_type")
    entity_id = body.get("entity_id")
    if not (project_id and entity_type and entity_id):
        raise ValidationError("project_id、entity_type、entity_id 必填")
    if entity_type != "chapter":
        return {"ok": False, "message": "仅支持撤销章节自动生成"}
    res = await db.execute(
        select(LongChangeRecord)
        .where(
            LongChangeRecord.project_id == project_id,
            LongChangeRecord.entity_type == entity_type,
            LongChangeRecord.entity_id == entity_id,
            LongChangeRecord.source == "auto",
            LongChangeRecord.status == "applied",
        )
        .order_by(LongChangeRecord.created_at.desc())
        .limit(1)
    )
    rec = res.scalars().first()
    if not rec or not rec.before:
        return {"ok": False, "message": "没有可撤销的自动生成"}

    from app import repositories as repo
    current_row = await repo.get_chapter(db, entity_id)
    if isinstance(current_row, dict):
        current = dict(current_row)
    elif current_row is not None:
        current = {c.name: getattr(current_row, c.name) for c in current_row.__table__.columns}
    else:
        current = None
    try:
        await apply_change(db, project_id, {
            "entity_type": entity_type,
            "action": "update",
            "entity_id": entity_id,
            "after": rec.before,
        })
    except Exception as e:
        logger.exception("撤销自动生成失败")
        await db.rollback()
        return {"ok": False, "message": f"撤销失败：{e}"}
    rec.source = "auto_undone"
    db.add(LongChangeRecord(
        project_id=project_id,
        entity_type=entity_type,
        entity_id=entity_id,
        before=current,
        after=rec.before,
        status="applied",
        source="undo",
    ))
    await db.commit()
    return {"ok": True}


@router.get("/undoable/{chapter_id}")
async def undoable(chapter_id: str, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(LongChangeRecord.id)
        .where(
            LongChangeRecord.entity_type == "chapter",
            LongChangeRecord.entity_id == chapter_id,
            LongChangeRecord.source == "auto",
            LongChangeRecord.status == "applied",
        )
        .limit(1)
    )
    return {"undoable": res.scalars().first() is not None}

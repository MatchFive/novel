"""Agent 助手接口：chat（分析→派发→变更）、confirm（应用）、reject、undo。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.core.llm_factory import get_default_llm_client
from app.database import get_db
from app.models import AssistantSession, AssistantMessage, Project, UserSetting
from app.agents.harness.history import (
    build_history_context,
    build_messages,
    should_summarize,
    summarize_messages,
    append_summary,
)
from app.agents.harness.nodes.supervisor import run_supervisor
from app.agents.harness.workers import (
    CharacterWorker, WorldWorker, OutlineWorker, PlotWorker, ForeshadowWorker,
)
from app.agents.harness.worker_base import run_worker
from app.agents.harness.nodes.aggregator import aggregate
from app.agents.harness.nodes.responder import respond
from app.services.change_apply import _ENTITY_REPO, confirm_session, reject_session

logger = logging.getLogger(__name__)
router = APIRouter(tags=["assistant"])

_WORKERS = {
    "character": CharacterWorker,
    "world": WorldWorker,
    "outline": OutlineWorker,
    "plot": PlotWorker,
    "foreshadow": ForeshadowWorker,
}


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


async def _deactivate_other_sessions(db, project_id: str, keep_id: str) -> None:
    await db.execute(
        update(AssistantSession)
        .where(AssistantSession.project_id == project_id, AssistantSession.id != keep_id)
        .values(is_active=False)
    )


async def _recursive_limit(db) -> int:
    res = await db.execute(select(UserSetting))
    s = res.scalars().first()
    return s.recursive_limit if s else 8


@router.post("/chat")
async def chat(body: dict, db: AsyncSession = Depends(get_db)):
    project_id = body.get("project_id")
    user_input = body.get("message", "")
    if not project_id or not user_input:
        raise ValidationError("project_id 与 message 必填")

    proj = await db.get(Project, project_id)
    if not proj:
        raise NotFoundError("项目不存在")

    sess = await _get_active_session(db, project_id)
    try:
        user_msg = AssistantMessage(
            session_id=sess.id,
            role="user",
            content=user_input,
            metadata_={},
        )
        db.add(user_msg)
        await db.flush()
    except Exception:
        logger.exception("Failed to persist user assistant message")
        await db.rollback()

    llm = await get_default_llm_client(db)
    recursive_limit = await _recursive_limit(db)

    hist_res = await db.execute(
        select(AssistantMessage)
        .where(AssistantMessage.session_id == sess.id)
        .order_by(AssistantMessage.created_at.asc())
    )
    historical_messages = hist_res.scalars().all()

    settings_row = (await db.execute(select(UserSetting))).scalars().first()
    settings_obj = settings_row or UserSetting()

    # 历史上下文（摘要 + 最近消息），不含 system 与当前用户输入
    history_context = build_history_context(sess, historical_messages[:-1], settings_obj)

    # 1. 前置取数
    from app import repositories as repo
    context = {
        "outlines": await repo.list_outlines(db, project_id),
        "characters": await repo.list_characters(db, project_id),
        "foreshadows": await repo.list_foreshadows(db, project_id),
        "world": await repo.list_world(db, project_id),
        "plot": await repo.list_plot(db, project_id),
    }

    # 2. Supervisor 拆分（使用不含当前用户输入的 messages，当前输入单独传入）
    supervisor_msgs = build_messages(
        "你是小说创作助手的调度器。根据用户指令与项目现有数据，判断需要派发哪些专精 Worker 来处理。"
        "可选 Worker：character（角色设计/调整）、world（世界观设定）、outline（大纲生成/调整）、"
        "plot（剧情节点编排）、foreshadow（伏笔埋设/回收）。请返回 JSON："
        '{"intent": "一句话意图", "tasks": [{"worker": "character", "goal": "..."}, ...]}。'
        "若指令与长篇数据无关，返回 {\"intent\": \"...\", \"tasks\": []}。",
        sess,
        historical_messages[:-1],
        user_input,
        settings_obj,
    )
    plan = await run_supervisor(llm, supervisor_msgs)
    logger.warning("Assistant supervisor plan: %s", plan)

    # 3. 派发 Worker（仅通过只读工具取数，产出结构化结果，不落库）
    worker_results = []
    for task in plan.get("tasks", []):
        wname = task.get("worker")
        wcls = _WORKERS.get(wname)
        if not wcls:
            continue
        goal = task.get("goal", user_input)
        result = await run_worker(
            wcls, db, llm, recursive_limit, goal, context,
            history_context=history_context,
        )
        result["worker"] = wname
        logger.warning("Worker %s result: %s", wname, result)
        worker_results.append(result)

    # 4. aggregator -> ChangeRecord[]
    records = aggregate(project_id, worker_results)
    logger.warning("Aggregated change records: %s", [r.model_dump() for r in records])

    # 5. 写入会话 staged_changes（仅引用，不落真实表）
    staged = list(sess.staged_changes or [])
    staged.extend([r.model_dump() for r in records])
    sess.staged_changes = staged
    await db.commit()

    # 6. responder 摘要
    summary = await respond(llm, records, user_input=user_input, history_context=history_context)

    records_data = [r.model_dump() for r in records]
    assistant_msg_id = None
    try:
        assistant_msg = AssistantMessage(
            session_id=sess.id,
            role="assistant",
            content=summary,
            metadata_={
                "intent": plan.get("intent"),
                "change_record_ids": [r.get("id") for r in records_data],
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
            summary_text = await summarize_messages(recent, settings_obj, llm)
            append_summary(sess, recent, summary_text, settings_obj)
            await db.commit()
            await db.refresh(sess)
    except Exception:
        logger.exception("Failed to persist assistant message")
        await db.rollback()

    return {
        "ok": True,
        "session_id": sess.id,
        "message_id": assistant_msg_id,
        "intent": plan.get("intent"),
        "change_records": records_data,
        "summary": summary,
    }


@router.get("/session/{project_id}")
async def get_session(project_id: str, db: AsyncSession = Depends(get_db)):
    sess = await _get_active_session(db, project_id)
    return sess.to_dict()


@router.post("/session/{project_id}")
async def create_session(project_id: str, db: AsyncSession = Depends(get_db)):
    proj = await db.get(Project, project_id)
    if not proj:
        raise NotFoundError("项目不存在")

    # 旧 session 全部置 inactive
    await db.execute(
        update(AssistantSession)
        .where(AssistantSession.project_id == project_id)
        .values(is_active=False)
    )

    count_res = await db.execute(
        select(func.count()).where(AssistantSession.project_id == project_id)
    )
    count = count_res.scalar() or 0
    new_session = AssistantSession(
        project_id=project_id,
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
    res = await db.execute(
        select(AssistantSession)
        .where(AssistantSession.project_id == project_id)
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
    sess = await _get_active_session(db, project_id)
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
    if not session_id:
        raise ValidationError("session_id 必填")
    result = await confirm_session(db, session_id)
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
    if not session_id:
        raise ValidationError("session_id 必填")
    result = await reject_session(db, session_id)
    try:
        await _mark_latest_assistant_message(
            db, session_id, "rejected", result.get("rejected_count", 0)
        )
    except Exception:
        logger.exception("Failed to mark latest assistant message status")
    return result

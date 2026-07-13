"""Agent 助手接口：chat（分析→派发→变更）、confirm（应用）、reject、undo。"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, NotFoundError, ValidationError
from app.core.llm_factory import get_llm_client
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
    BroadOutlineWorker, PlotNodesWorker, AssignmentWorker, ChapterOutlineWorker, ChapterTextWorker,
)
from app.agents.harness.worker_base import run_worker
from app.agents.harness.nodes.aggregator import aggregate
from app.agents.harness.nodes.responder import respond, GLOBAL_RESPONDER_PROMPT
from app.services.change_apply import _ENTITY_REPO, confirm_session, reject_session

logger = logging.getLogger(__name__)
router = APIRouter(tags=["assistant"])

_WORKERS = {
    "character": CharacterWorker,
    "world": WorldWorker,
    "outline": OutlineWorker,
    "plot": PlotWorker,
    "foreshadow": ForeshadowWorker,
    "broad_outline": BroadOutlineWorker,
    "plot_nodes": PlotNodesWorker,
    "assignment": AssignmentWorker,
    "chapter_outline": ChapterOutlineWorker,
    "chapter_text": ChapterTextWorker,
}


_WORKER_LEVEL = {
    "broad_outline": "high",
    "chapter_text": "high",
    "plot_nodes": "medium",
    "assignment": "medium",
    "chapter_outline": "medium",
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


async def _recursive_limit(db) -> int:
    res = await db.execute(select(UserSetting))
    s = res.scalars().first()
    return s.recursive_limit if s else 8


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

    if is_global:
        # 全局会话：不读取项目实体，supervisor 返回空 tasks，responder 直接回答
        context = {"project_id": None}
        plan = {"intent": "通用对话", "tasks": []}
        worker_results = []
    else:
        # 1. 前置取数
        from app import repositories as repo
        project = await db.get(Project, effective_project_id)
        project_summary = f"{project.title}\n{project.description}".strip() if project else ""
        context = {
            "project_id": effective_project_id,
            "project": project.to_dict() if project else None,
            "project_summary": project_summary,
            "outlines": await repo.list_outlines(db, effective_project_id),
            "characters": await repo.list_characters(db, effective_project_id),
            "foreshadows": await repo.list_foreshadows(db, effective_project_id),
            "world": await repo.list_world(db, effective_project_id),
            "plot": await repo.list_plot(db, effective_project_id),
            "chapters": await repo.list_chapters(db, effective_project_id),
            "entity_id": context_payload.get("entity_id"),
            "entity_type": context_payload.get("entity_type"),
        }

        context_note = ""
        if sess.context:
            context_note = f"\n\n当前会话上下文：{json.dumps(sess.context, ensure_ascii=False)}"

        # 2. Supervisor 拆分（使用不含当前用户输入的 messages，当前输入单独传入）
        supervisor_prompt = (
            "你是小说创作助手的调度器。根据用户指令与项目现有数据，判断需要派发哪些专精 Worker 来处理。"
            "可选 Worker：character（角色设计/调整）、world（世界观设定）、outline（大纲生成/调整）、"
            "plot（剧情节点编排）、foreshadow（伏笔埋设/回收）、"
            "broad_outline（项目级总纲生成/更新）、plot_nodes（从总纲抽取关键剧情节点）、"
            "assignment（把剧情节点分配到已有/新建章节）、chapter_outline（生成单个章节细纲）、"
            "chapter_text（生成单个章节正文）。请返回 JSON："
            '{"intent": "一句话意图", "tasks": [{"worker": "character", "goal": "..."}, ...]}。\n\n'
            "Worker 选择规则（严格按内容归类，禁止把世界观/规则类指令派给 outline）：\n"
            "- world（世界观）：只要用户在新增/修改世界观、设定、力量体系、修炼境界、社会规则、"
            "历史背景、地理环境、种族、宗教、神话、盟约/契约/法则/禁令，或出现'世界观''设定''规则''体系'等词，"
            "就必须派给 world。此类指令绝不能派给 outline。\n"
            "- character：用户明确提到角色、人物、主角、配角、性格、能力、关系、命运。\n"
            "- outline：仅当用户明确提到传统大纲、章节结构、起承转合、主线/支线安排时才派给 outline。\n"
            "- broad_outline：用户说“生成/重新生成总纲/项目大纲/整体大纲”时派给 broad_outline。\n"
            "- plot_nodes：用户说“生成剧情节点/桥段/关键事件”时派给 plot_nodes。\n"
            "- assignment：用户说“分配章节/把剧情节点分配到章节/把桥段分配到章节”时派给 assignment。\n"
            "- chapter_outline：用户说“生成细纲/章节大纲/写第 X 章细纲”时派给 chapter_outline。\n"
            "- chapter_text：用户说“生成正文/写第 X 章/写正文”时派给 chapter_text。\n"
            "- plot：用户提到具体剧情、事件、桥段、时间线节点但不涉及分配时。\n"
            "- foreshadow：用户提到伏笔、悬念、回收/呼应。\n\n"
            "若指令涉及当前上下文中的 entity_type/entity_id，应优先派给对应 worker（如果 project_id 可用）。"
            f"{context_note}\n\n"
            "示例（只输出 JSON，不要解释）：\n"
            '用户：新增世界观设定：这是一个修仙世界，境界分为炼气、筑基。\n'
            '输出：{"intent": "新增修仙世界观", "tasks": [{"worker": "world", "goal": "新增修仙世界观设定，境界划分为炼气、筑基"}]}\n'
            '用户：生成前5章总纲。\n'
            '输出：{"intent": "生成前5章总纲", "tasks": [{"worker": "broad_outline", "goal": "生成前5章总纲"}]}\n'
            '用户：生成剧情节点。\n'
            '输出：{"intent": "生成剧情节点", "tasks": [{"worker": "plot_nodes", "goal": "生成剧情节点"}]}\n'
            '用户：把剧情节点分配到章节。\n'
            '输出：{"intent": "分配剧情节点到章节", "tasks": [{"worker": "assignment", "goal": "把剧情节点分配到章节"}]}\n'
            '用户：生成第1章细纲。\n'
            '输出：{"intent": "生成第1章细纲", "tasks": [{"worker": "chapter_outline", "goal": "生成第1章细纲"}]}\n'
            '用户：写第1章正文。\n'
            '输出：{"intent": "生成第1章正文", "tasks": [{"worker": "chapter_text", "goal": "生成第1章正文"}]}\n'
            '用户：主角性格应该更沉稳。\n'
            '输出：{"intent": "调整主角性格", "tasks": [{"worker": "character", "goal": "调整主角性格，使其更沉稳"}]}\n\n'
            "若指令与长篇数据无关，返回 {\"intent\": \"...\", \"tasks\": []}。"
        )
        supervisor_msgs = build_messages(
            supervisor_prompt,
            sess,
            historical_messages[:-1],
            user_input,
            settings_obj,
        )
        supervisor_llm = await get_llm_client(db, level="medium")
        plan = await run_supervisor(supervisor_llm, supervisor_msgs)
    logger.warning("Assistant supervisor plan: %s", plan)

    # 3. 派发 Worker（仅通过只读工具取数，产出结构化结果，不落库）
    worker_results = []
    for task in plan.get("tasks", []):
        wname = task.get("worker")
        wcls = _WORKERS.get(wname)
        if not wcls:
            continue
        goal = task.get("goal", user_input)
        level = _WORKER_LEVEL.get(wname, "medium")
        worker_llm = await get_llm_client(db, level=level)
        result = await run_worker(
            wcls, db, worker_llm, recursive_limit, goal, context,
            history_context=history_context,
        )
        result["worker"] = wname
        logger.warning("Worker %s result: %s", wname, result)
        worker_results.append(result)

    # 4. aggregator -> ChangeRecord[]
    records = aggregate(effective_project_id, worker_results)
    logger.warning("Aggregated change records: %s", [r.model_dump() for r in records])

    # 5. 写入会话 staged_changes（仅引用，不落真实表）
    staged = list(sess.staged_changes or [])
    staged.extend([r.model_dump() for r in records])
    sess.staged_changes = staged
    await db.commit()

    # 6. responder 摘要
    responder_llm = await get_llm_client(db, level="low")
    if is_global:
        summary = await respond(
            responder_llm, records, user_input=user_input, history_context=history_context,
            system_prompt=GLOBAL_RESPONDER_PROMPT,
        )
    else:
        summary = await respond(
            responder_llm, records, user_input=user_input, history_context=history_context
        )

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
                "context": context_payload,
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

"""Agent 助手接口：chat（分析→派发→变更）、confirm（应用）、reject、undo。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.core.llm_client import LLMClient
from app.database import get_db
from app.models import AssistantSession, AssistantMessage, Project, UserSetting
from app.agents.harness.nodes.supervisor import run_supervisor
from app.agents.harness.workers import (
    CharacterWorker, WorldWorker, OutlineWorker, PlotWorker, ForeshadowWorker,
)
from app.agents.harness.worker_base import run_worker
from app.agents.harness.nodes.aggregator import aggregate
from app.agents.harness.nodes.responder import respond
from app.services.change_apply import confirm_session, reject_session

router = APIRouter(tags=["assistant"])

_WORKERS = {
    "character": CharacterWorker,
    "world": WorldWorker,
    "outline": OutlineWorker,
    "plot": PlotWorker,
    "foreshadow": ForeshadowWorker,
}


async def _ensure_session(db, project_id) -> AssistantSession:
    res = await db.execute(
        select(AssistantSession).where(AssistantSession.project_id == project_id))
    s = res.scalars().first()
    if not s:
        s = AssistantSession(project_id=project_id, staged_changes=[])
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
    if not project_id or not user_input:
        raise ValidationError("project_id 与 message 必填")

    proj = await db.get(Project, project_id)
    if not proj:
        raise NotFoundError("项目不存在")

    sess = await _ensure_session(db, project_id)
    user_msg = AssistantMessage(
        session_id=sess.id,
        role="user",
        content=user_input,
        metadata_={},
    )
    db.add(user_msg)
    await db.flush()

    llm = LLMClient()
    recursive_limit = await _recursive_limit(db)

    # 1. 前置取数
    from app import repositories as repo
    context = {
        "outlines": await repo.list_outlines(db, project_id),
        "characters": await repo.list_characters(db, project_id),
        "foreshadows": await repo.list_foreshadows(db, project_id),
        "world": await repo.list_world(db, project_id),
        "plot": await repo.list_plot(db, project_id),
    }

    # 2. Supervisor 拆分
    plan = await run_supervisor(llm, user_input, context)

    # 3. 派发 Worker（仅通过只读工具取数，产出结构化结果，不落库）
    worker_results = []
    for task in plan.get("tasks", []):
        wname = task.get("worker")
        wcls = _WORKERS.get(wname)
        if not wcls:
            continue
        goal = task.get("goal", user_input)
        result = await run_worker(wcls, db, llm, recursive_limit, goal, context)
        result["worker"] = wname
        worker_results.append(result)

    # 4. aggregator -> ChangeRecord[]
    records = aggregate(project_id, worker_results)

    # 5. 写入会话 staged_changes（仅引用，不落真实表）
    staged = list(sess.staged_changes or [])
    staged.extend([r.model_dump() for r in records])
    sess.staged_changes = staged
    await db.commit()

    # 6. responder 摘要
    summary = await respond(llm, records)

    records_data = [r.model_dump() for r in records]
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

    return {
        "ok": True,
        "session_id": sess.id,
        "message_id": assistant_msg.id,
        "intent": plan.get("intent"),
        "change_records": records_data,
        "summary": summary,
    }


@router.get("/session/{project_id}")
async def get_session(project_id: str, db: AsyncSession = Depends(get_db)):
    sess = await _ensure_session(db, project_id)
    return sess.to_dict()


@router.get("/session/{project_id}/history")
async def get_session_history(project_id: str, db: AsyncSession = Depends(get_db)):
    sess = await _ensure_session(db, project_id)
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


@router.post("/confirm")
async def confirm(body: dict, db: AsyncSession = Depends(get_db)):
    session_id = body.get("session_id")
    if not session_id:
        raise ValidationError("session_id 必填")
    return await confirm_session(db, session_id)


@router.post("/reject")
async def reject(body: dict, db: AsyncSession = Depends(get_db)):
    session_id = body.get("session_id")
    if not session_id:
        raise ValidationError("session_id 必填")
    return await reject_session(db, session_id)

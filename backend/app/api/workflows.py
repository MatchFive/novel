from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo
from app.agents.workflows.executor import run_workflow
from app.agents.workflows.models import WorkflowContext
from app.agents.workflows.registry import list_workflows, load_workflow_definition
from app.core.errors import NotFoundError, ValidationError
from app.core.llm_factory import get_llm_client
from app.database import get_db
from app.models import AssistantSession, Project

router = APIRouter(prefix="/workflow", tags=["workflows"])


async def _get_active_session(db: AsyncSession, project_id: str | None) -> AssistantSession:
    res = await db.execute(
        select(AssistantSession)
        .where(AssistantSession.project_id == project_id, AssistantSession.is_active.is_(True))
    )
    sess = res.scalars().first()
    if not sess:
        sess = AssistantSession(
            project_id=project_id,
            title="对话 1",
            is_active=True,
            staged_changes=[],
            summaries=[],
            message_count=0,
        )
        db.add(sess)
        await db.commit()
        await db.refresh(sess)
    return sess


async def _stage_records(db: AsyncSession, project_id: str, records: list[dict]) -> AssistantSession:
    sess = await _get_active_session(db, project_id)
    staged = list(sess.staged_changes or [])
    staged.extend(records)
    sess.staged_changes = staged
    await db.commit()
    await db.refresh(sess)
    return sess


@router.get("/list")
async def get_workflow_list(db: AsyncSession = Depends(get_db)):
    return {"ok": True, "workflows": [w.model_dump() for w in list_workflows()]}


@router.post("/chapter/{chapter_id}/memory")
async def run_memory_workflow(
    chapter_id: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    chapter = await repo.get_chapter(db, chapter_id)
    if not chapter:
        raise NotFoundError("章节不存在")
    project_id = chapter.get("project_id")

    async def llm_factory(level: str | None = None):
        return await get_llm_client(db, level=level)

    ctx = WorkflowContext(
        db=db,
        llm_factory=llm_factory,
        project_id=project_id,
        chapter_id=chapter_id,
        inputs=body or {},
    )
    result = await run_workflow(load_workflow_definition("memory_update"), ctx)
    sess = await _stage_records(db, project_id, result.change_records)
    return {
        "ok": True,
        "session_id": sess.id,
        "result": result.model_dump(exclude={"outputs"}),
    }


@router.post("/project/{project_id}/generate-chapter")
async def generate_chapter_workflow(
    project_id: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise NotFoundError("项目不存在")
    chapter_id = body.get("chapter_id")
    if not chapter_id:
        raise ValidationError("chapter_id 必填")

    async def llm_factory(level: str | None = None):
        return await get_llm_client(db, level=level)

    ctx = WorkflowContext(
        db=db,
        llm_factory=llm_factory,
        project_id=project_id,
        inputs=body or {},
    )
    result = await run_workflow(load_workflow_definition("chapter_generation"), ctx)
    sess = await _stage_records(db, project_id, result.change_records)
    return {
        "ok": True,
        "session_id": sess.id,
        "result": result.model_dump(exclude={"outputs"}),
    }


@router.post("/project/{project_id}/audit-foreshadows")
async def audit_foreshadows_workflow(
    project_id: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise NotFoundError("项目不存在")

    async def llm_factory(level: str | None = None):
        return await get_llm_client(db, level=level)

    ctx = WorkflowContext(
        db=db,
        llm_factory=llm_factory,
        project_id=project_id,
        inputs=body or {},
    )
    result = await run_workflow(load_workflow_definition("foreshadow_audit"), ctx)
    sess = await _stage_records(db, project_id, result.change_records)
    return {
        "ok": True,
        "session_id": sess.id,
        "result": result.model_dump(),
    }


@router.post("/project/{project_id}/check-world")
async def check_world_workflow(
    project_id: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise NotFoundError("项目不存在")

    async def llm_factory(level: str | None = None):
        return await get_llm_client(db, level=level)

    ctx = WorkflowContext(
        db=db,
        llm_factory=llm_factory,
        project_id=project_id,
        inputs=body or {},
    )
    result = await run_workflow(load_workflow_definition("world_consistency"), ctx)
    return {
        "ok": True,
        "result": result.model_dump(),
    }

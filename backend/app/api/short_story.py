from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError
from app.core.llm_client import LLMClient
from app.core.llm_factory import get_default_llm_client
from app.database import get_db
from app.services.short_story import ShortStoryService

router = APIRouter(tags=["short-story"])


async def _llm_client(db: AsyncSession = Depends(get_db)) -> LLMClient:
    return await get_default_llm_client(db)


def _svc(db: AsyncSession, llm: LLMClient) -> ShortStoryService:
    return ShortStoryService(db, llm)


@router.get("/{project_id}/progress")
async def get_progress(project_id: str, db: AsyncSession = Depends(get_db), llm: LLMClient = Depends(_llm_client)):
    return await _svc(db, llm).get(project_id)


@router.post("/{project_id}/hook")
async def set_hook(project_id: str, body: dict, db: AsyncSession = Depends(get_db), llm: LLMClient = Depends(_llm_client)):
    hook = body.get("hook")
    if not hook:
        raise ValidationError("hook 不能为空")
    return await _svc(db, llm).set_core_hook(project_id, hook)


@router.post("/{project_id}/plans")
async def gen_plans(project_id: str, db: AsyncSession = Depends(get_db), llm: LLMClient = Depends(_llm_client)):
    return await _svc(db, llm).generate_plans(project_id)


@router.post("/{project_id}/plans/select")
async def select_plan(project_id: str, body: dict, db: AsyncSession = Depends(get_db), llm: LLMClient = Depends(_llm_client)):
    idx = body.get("index", 0)
    return await _svc(db, llm).select_plan(project_id, int(idx))


@router.post("/{project_id}/detail")
async def gen_detail(project_id: str, db: AsyncSession = Depends(get_db), llm: LLMClient = Depends(_llm_client)):
    return await _svc(db, llm).generate_detail_plan(project_id)


@router.post("/{project_id}/chapters")
async def gen_chapters(project_id: str, db: AsyncSession = Depends(get_db), llm: LLMClient = Depends(_llm_client)):
    return await _svc(db, llm).generate_chapters(project_id)


@router.post("/{project_id}/chapters/{index}/write")
async def write_chapter(project_id: str, index: int, db: AsyncSession = Depends(get_db), llm: LLMClient = Depends(_llm_client)):
    return await _svc(db, llm).write_chapter(project_id, index)


@router.post("/{project_id}/integrate")
async def integrate(project_id: str, db: AsyncSession = Depends(get_db), llm: LLMClient = Depends(_llm_client)):
    return await _svc(db, llm).integrate(project_id)


@router.put("/{project_id}")
async def update_setting(project_id: str, body: dict, db: AsyncSession = Depends(get_db), llm: LLMClient = Depends(_llm_client)):
    return await _svc(db, llm).update(project_id, body)

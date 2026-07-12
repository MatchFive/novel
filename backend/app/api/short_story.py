from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError
from app.database import get_db
from app.services.short_story import ShortStoryService

router = APIRouter(tags=["short-story"])


def _svc(db: AsyncSession) -> ShortStoryService:
    return ShortStoryService(db)


@router.get("/{project_id}/progress")
async def get_progress(project_id: str, db: AsyncSession = Depends(get_db)):
    return await _svc(db).get(project_id)


@router.post("/{project_id}/hook")
async def set_hook(project_id: str, body: dict, db: AsyncSession = Depends(get_db)):
    hook = body.get("hook")
    if not hook:
        raise ValidationError("hook 不能为空")
    return await _svc(db).set_core_hook(project_id, hook)


@router.post("/{project_id}/plans")
async def gen_plans(project_id: str, db: AsyncSession = Depends(get_db)):
    return await _svc(db).generate_plans(project_id)


@router.post("/{project_id}/plans/select")
async def select_plan(project_id: str, body: dict, db: AsyncSession = Depends(get_db)):
    idx = body.get("index", 0)
    return await _svc(db).select_plan(project_id, int(idx))


@router.post("/{project_id}/detail")
async def gen_detail(project_id: str, db: AsyncSession = Depends(get_db)):
    return await _svc(db).generate_detail_plan(project_id)


@router.post("/{project_id}/chapters")
async def gen_chapters(project_id: str, db: AsyncSession = Depends(get_db)):
    return await _svc(db).generate_chapters(project_id)


@router.post("/{project_id}/chapters/{index}/write")
async def write_chapter(project_id: str, index: int, db: AsyncSession = Depends(get_db)):
    return await _svc(db).write_chapter(project_id, index)


@router.post("/{project_id}/integrate")
async def integrate(project_id: str, db: AsyncSession = Depends(get_db)):
    return await _svc(db).integrate(project_id)


@router.put("/{project_id}")
async def update_setting(project_id: str, body: dict, db: AsyncSession = Depends(get_db)):
    return await _svc(db).update(project_id, body)

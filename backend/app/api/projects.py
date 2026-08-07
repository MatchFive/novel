from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.database import get_db
from app.models import Project, UserSetting
from app.schemas.project import ProjectCreate, ProjectUpdate, ProjectOut

router = APIRouter(tags=["projects"])


@router.get("", response_model=list[ProjectOut])
async def list_projects(
    type: str | None = Query(None, pattern="^long$"),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Project)
    if type:
        stmt = stmt.where(Project.type == type)
    stmt = stmt.order_by(Project.updated_at.desc())
    res = await db.execute(stmt)
    return [p.to_dict() for p in res.scalars().all()]


@router.post("", response_model=ProjectOut)
async def create_project(payload: ProjectCreate, db: AsyncSession = Depends(get_db)):
    p = Project(type=payload.type, title=payload.title, description=payload.description)
    res = await db.execute(select(UserSetting))
    s = res.scalars().first()
    if s:
        cfg: dict = {}
        if s.chapter_target_words is not None:
            cfg["chapter_target_words"] = s.chapter_target_words
        if s.content_rating is not None:
            cfg["content_rating"] = s.content_rating
        if cfg:
            p.generation_config = cfg
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p.to_dict()


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(project_id: str, db: AsyncSession = Depends(get_db)):
    p = await db.get(Project, project_id)
    if not p:
        raise NotFoundError("项目不存在")
    return p.to_dict()


@router.put("/{project_id}", response_model=ProjectOut)
async def update_project(project_id: str, payload: ProjectUpdate, db: AsyncSession = Depends(get_db)):
    p = await db.get(Project, project_id)
    if not p:
        raise NotFoundError("项目不存在")
    if payload.title is not None:
        p.title = payload.title
    if payload.description is not None:
        p.description = payload.description
    if payload.writing_style is not None:
        p.writing_style = payload.writing_style.model_dump(exclude_none=True)
    if payload.generation_config is not None:
        p.generation_config = payload.generation_config.model_dump(exclude_none=True)
    await db.commit()
    await db.refresh(p)
    return p.to_dict()


@router.delete("/{project_id}")
async def delete_project(project_id: str, db: AsyncSession = Depends(get_db)):
    p = await db.get(Project, project_id)
    if not p:
        raise NotFoundError("项目不存在")
    await db.delete(p)
    await db.commit()
    return {"ok": True}

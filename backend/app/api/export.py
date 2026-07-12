"""导出 / 备份 API。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.database import get_db
from app.models import Project
from app.services.export import export_project

router = APIRouter(tags=["export"])


@router.get("/{project_id}")
async def export_project_route(
    project_id: str,
    fmt: str = Query("markdown", pattern="^(markdown|json)$"),
    db: AsyncSession = Depends(get_db),
):
    proj = await db.get(Project, project_id)
    if not proj:
        raise NotFoundError("项目不存在")
    result = await export_project(db, project_id, fmt)
    if "error" in result:
        raise NotFoundError(result["error"])
    ext = "md" if fmt == "markdown" else "json"
    # header 仅 ASCII：用项目 id 作文件名；中文标题不入 header
    ascii_name = "novel-export-{}.{}".format(project_id[:8], ext)
    return PlainTextResponse(
        result["content"],
        headers={"Content-Disposition": "attachment; filename=" + ascii_name},
    )

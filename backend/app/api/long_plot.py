from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.database import get_db
from app.repositories import (
    list_plot, create_plot, update_plot, delete_plot,
)
from app.schemas.long import PlotNodeCreate, PlotNodeUpdate

router = APIRouter(prefix="/plot", tags=["long-plot"])


@router.get("/{project_id}")
async def get_plot(project_id: str, db: AsyncSession = Depends(get_db)):
    return await list_plot(db, project_id)


@router.post("")
async def add_plot(payload: PlotNodeCreate, db: AsyncSession = Depends(get_db)):
    return await create_plot(db, payload.model_dump())


@router.put("/{plot_id}")
async def edit_plot(plot_id: str, payload: PlotNodeUpdate, db: AsyncSession = Depends(get_db)):
    res = await update_plot(db, plot_id, payload.model_dump(exclude_unset=True))
    if not res:
        raise NotFoundError("剧情节点不存在")
    return res


@router.delete("/{plot_id}")
async def remove_plot(plot_id: str, db: AsyncSession = Depends(get_db)):
    ok = await delete_plot(db, plot_id)
    if not ok:
        raise NotFoundError("剧情节点不存在")
    return {"ok": True}

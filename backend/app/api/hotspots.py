from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.hotspot import HotspotService

router = APIRouter(tags=["hotspots"])


@router.post("/hotspots/fetch")
async def fetch_hotspots(body: dict, db: AsyncSession = Depends(get_db)):
    project_id = body.get("project_id")
    url = body.get("source_url")
    return await HotspotService(db).fetch(project_id, url)


@router.post("/hotspots/analyze")
async def analyze_hotspots(body: dict, db: AsyncSession = Depends(get_db)):
    project_id = body.get("project_id")
    ids = body.get("hotspot_ids")
    return await HotspotService(db).analyze(project_id, ids)


@router.get("/hotspots/{project_id}/stored")
async def stored_hotspots(project_id: str, db: AsyncSession = Depends(get_db)):
    return await HotspotService(db).stored(project_id)

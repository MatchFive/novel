from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.llm_client import LLMClient
from app.core.llm_factory import get_default_llm_client
from app.database import get_db
from app.services.hotspot import HotspotService

router = APIRouter(tags=["hotspots"])


async def _llm_client(db: AsyncSession = Depends(get_db)) -> LLMClient:
    return await get_default_llm_client(db)


@router.post("/hotspots/fetch")
async def fetch_hotspots(body: dict, db: AsyncSession = Depends(get_db), llm: LLMClient = Depends(_llm_client)):
    project_id = body.get("project_id")
    url = body.get("source_url")
    return await HotspotService(db, llm).fetch(project_id, url)


@router.post("/hotspots/analyze")
async def analyze_hotspots(body: dict, db: AsyncSession = Depends(get_db), llm: LLMClient = Depends(_llm_client)):
    project_id = body.get("project_id")
    ids = body.get("hotspot_ids")
    return await HotspotService(db, llm).analyze(project_id, ids)


@router.get("/hotspots/{project_id}/stored")
async def stored_hotspots(project_id: str, db: AsyncSession = Depends(get_db), llm: LLMClient = Depends(_llm_client)):
    return await HotspotService(db, llm).stored(project_id)

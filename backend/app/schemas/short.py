from __future__ import annotations
from typing import Optional, Any
from pydantic import BaseModel, Field


class ShortSettingUpdate(BaseModel):
    core_hook: Optional[str] = None
    plans: Optional[list] = None
    selected_plan: Optional[Any] = None
    detail_plan: Optional[str] = None
    chapters_plan: Optional[list] = None
    writing: Optional[list] = None
    integration: Optional[str] = None
    step: Optional[int] = None


class ChapterCreate(BaseModel):
    project_id: str
    title: str = ""
    content: str = ""
    order: int = 0


class ChapterUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    order: Optional[int] = None


class HotspotFetch(BaseModel):
    project_id: str
    source_url: Optional[str] = None


class HotspotAnalyze(BaseModel):
    project_id: str
    hotspot_ids: Optional[list] = None

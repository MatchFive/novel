from __future__ import annotations
from typing import Optional, Any
from pydantic import BaseModel, Field


class OutlineCreate(BaseModel):
    project_id: str
    parent_id: Optional[str] = None
    title: str = ""
    content: str = ""
    order: int = 0


class OutlineUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    parent_id: Optional[str] = None
    order: Optional[int] = None


class CharacterCreate(BaseModel):
    project_id: str
    name: str = ""
    traits: str = ""
    ability: str = ""
    status: str = "alive"
    relations: list = Field(default_factory=list)
    importance: int = 0


class CharacterUpdate(BaseModel):
    name: Optional[str] = None
    traits: Optional[str] = None
    ability: Optional[str] = None
    status: Optional[str] = None
    relations: Optional[list] = None
    importance: Optional[int] = None


class ForeshadowCreate(BaseModel):
    project_id: str
    title: str = ""
    content: str = ""
    state: str = "pending"
    subplot_id: Optional[str] = None


class ForeshadowUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    state: Optional[str] = None
    subplot_id: Optional[str] = None


class WorldSettingCreate(BaseModel):
    project_id: str
    category: str = ""
    content: str = ""


class WorldSettingUpdate(BaseModel):
    category: Optional[str] = None
    content: Optional[str] = None


class PlotNodeCreate(BaseModel):
    project_id: str
    title: str = ""
    summary: str = ""
    timeline_pos: str = ""


class PlotNodeUpdate(BaseModel):
    title: Optional[str] = None
    summary: Optional[str] = None
    timeline_pos: Optional[str] = None


class ChapterCreate(BaseModel):
    project_id: str
    title: str = ""
    content: str = ""
    detailed_outline: str = ""
    status: str = "draft"
    order: int = 0
    constraints: list = Field(default_factory=list)


class ChapterUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    detailed_outline: Optional[str] = None
    status: Optional[str] = None
    order: Optional[int] = None
    constraints: Optional[list] = None


class ChapterReorder(BaseModel):
    project_id: str
    chapter_ids: list[str]

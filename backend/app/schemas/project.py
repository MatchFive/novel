from __future__ import annotations
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class WritingStyle(BaseModel):
    perspective: Optional[str] = None
    language_style: Optional[str] = None
    pace: Optional[str] = None
    tone: Optional[str] = None
    custom_note: Optional[str] = None


class GenerationConfig(BaseModel):
    chapter_target_words: Optional[int] = None
    content_rating: Optional[str] = None


class ProjectCreate(BaseModel):
    type: str = Field(..., pattern="^long$")
    title: str = "未命名"
    description: str = ""


class ProjectUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    writing_style: Optional[WritingStyle] = None
    generation_config: Optional[GenerationConfig] = None


class ProjectOut(BaseModel):
    id: str
    type: str
    title: str
    description: str
    writing_style: dict
    generation_config: dict
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

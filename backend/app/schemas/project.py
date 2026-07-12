from __future__ import annotations
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    type: str = Field(..., pattern="^(long|short)$")
    title: str = "未命名"
    description: str = ""


class ProjectUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None


class ProjectOut(BaseModel):
    id: str
    type: str
    title: str
    description: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

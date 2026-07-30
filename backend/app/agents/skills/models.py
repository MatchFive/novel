"""Skill data models."""
from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field


class SkillType(str, Enum):
    INLINE = "inline"
    RAG = "rag"


class SkillConfig(BaseModel):
    skill_name: str
    description: str
    type: SkillType
    triggers: list[str] = Field(default_factory=list)
    priority: int = 1
    # inline
    content_file: str | None = None
    # rag
    chunks_dir: str | None = None
    index_table: str = "skill_rag_embeddings"
    top_k: int = 3

    def content_path(self, base_dir: Path) -> Path | None:
        if self.content_file:
            return base_dir / self.content_file
        return None

    def chunks_path(self, base_dir: Path) -> Path | None:
        if self.chunks_dir:
            return base_dir / self.chunks_dir
        return None


class SkillQueryResult(BaseModel):
    skill_name: str
    chunk_path: str
    chunk_text: str
    score: float

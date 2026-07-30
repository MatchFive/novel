"""全部 ORM 模型：projects / long_* / 公共。"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    JSON,
)
from sqlalchemy.dialects.sqlite import CHAR
from sqlalchemy.orm import relationship, backref

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Project(Base):
    __tablename__ = "projects"

    id = Column(CHAR(36), primary_key=True, default=_uuid)
    type = Column(Enum("long", name="project_type"), nullable=False, index=True)
    title = Column(String(255), nullable=False, default="未命名")
    description = Column(Text, default="")
    created_at = Column(DateTime, default=_now, nullable=False)
    updated_at = Column(DateTime, default=_now, onupdate=_now, nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "title": self.title,
            "description": self.description,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ModelConfig(Base):
    __tablename__ = "model_configs"

    id = Column(CHAR(36), primary_key=True, default=_uuid)
    name = Column(String(128), nullable=False)
    base_url = Column(String(512), nullable=False)
    api_key = Column(String(512), default="")
    model = Column(String(128), nullable=False)
    level = Column(String(32), nullable=True, index=True)
    embedding_model = Column(String(128), nullable=True)
    embedding_dimension = Column(Integer, default=1536)
    is_default = Column(Boolean, default=False)

    def to_dict(self, hide_key: bool = True) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "base_url": self.base_url,
            "model": self.model,
            "level": self.level,
            "embedding_model": self.embedding_model,
            "embedding_dimension": self.embedding_dimension,
            "is_default": self.is_default,
            **({} if hide_key else {"api_key": self.api_key}),
        }


class UserSetting(Base):
    __tablename__ = "user_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    recursive_limit = Column(Integer, default=8)
    theme = Column(String(32), default="light")
    assistant_summary_threshold = Column(Integer, default=20)
    assistant_max_summaries = Column(Integer, default=5)
    assistant_summary_max_length = Column(Integer, default=1000)
    assistant_history_recent_messages = Column(Integer, default=20)
    assistant_history_top_k = Column(Integer, default=5)
    content_rating = Column(String(16), default="standard")
    chapter_target_words = Column(Integer, default=2500)

    def to_dict(self) -> dict:
        return {
            "recursive_limit": self.recursive_limit,
            "theme": self.theme,
            "assistant_summary_threshold": self.assistant_summary_threshold,
            "assistant_max_summaries": self.assistant_max_summaries,
            "assistant_summary_max_length": self.assistant_summary_max_length,
            "assistant_history_recent_messages": self.assistant_history_recent_messages,
            "assistant_history_top_k": self.assistant_history_top_k,
            "content_rating": self.content_rating or "standard",
            "chapter_target_words": self.chapter_target_words or 2500,
        }


class AssistantSession(Base):
    __tablename__ = "assistant_sessions"

    id = Column(CHAR(36), primary_key=True, default=_uuid)
    project_id = Column(CHAR(36), ForeignKey("projects.id"), nullable=True, index=True)
    title = Column(String(255), nullable=False, default="未命名对话")
    is_active = Column(Boolean, default=False, nullable=False)
    staged_changes = Column(JSON, default=list)
    context = Column(JSON, default=dict)
    summaries = Column(JSON, default=list)
    message_count = Column(Integer, default=0, nullable=False)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "title": self.title,
            "is_active": self.is_active,
            "staged_changes": self.staged_changes or [],
            "summaries": self.summaries or [],
            "message_count": self.message_count,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class AssistantMessage(Base):
    __tablename__ = "assistant_messages"

    id = Column(CHAR(36), primary_key=True, default=_uuid)
    session_id = Column(CHAR(36), ForeignKey("assistant_sessions.id"), nullable=False, index=True)
    role = Column(String(32), nullable=False)
    content = Column(Text, default="")
    metadata_ = Column("metadata", JSON, default=dict)
    created_at = Column(DateTime, default=_now, nullable=False)


class AssistantSummaryEmbedding(Base):
    """助手历史摘要的 embedding，用于按当前输入检索相关摘要。"""

    __tablename__ = "assistant_summary_embeddings"

    id = Column(CHAR(36), primary_key=True, default=_uuid)
    session_id = Column(CHAR(36), ForeignKey("assistant_sessions.id"), nullable=False, index=True)
    turn_range = Column(String(32), nullable=False)
    summary_text = Column(Text, nullable=False)
    embedding = Column(LargeBinary, nullable=False)
    model = Column(String(128), nullable=False)
    dimension = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=_now, nullable=False)


class SkillRagEmbedding(Base):
    """技能 RAG 文本块的 embedding，用于按用户输入检索相关技能上下文。"""

    __tablename__ = "skill_rag_embeddings"

    id = Column(CHAR(36), primary_key=True, default=_uuid)
    skill_name = Column(String(64), nullable=False, index=True)
    chunk_path = Column(String(512), nullable=False)
    chunk_text = Column(Text, nullable=False)
    embedding = Column(LargeBinary, nullable=False)
    model = Column(String(128), nullable=False)
    dimension = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=_now, nullable=False)


# ---------------- 长篇小说数据（全部 project_id 外键） ----------------

class LongOutline(Base):
    __tablename__ = "long_outlines"

    id = Column(CHAR(36), primary_key=True, default=_uuid)
    project_id = Column(CHAR(36), ForeignKey("projects.id"), nullable=False, index=True)
    parent_id = Column(CHAR(36), ForeignKey("long_outlines.id"), nullable=True, index=True)
    title = Column(String(255), default="")
    content = Column(Text, default="")
    version_chain = Column(CHAR(36), nullable=True)  # 上一版 id
    order = Column(Integer, default=0)
    type = Column(String(32), default="broad")
    chapter_start = Column(Integer, nullable=True)
    chapter_end = Column(Integer, nullable=True)

    children = relationship("LongOutline", backref=backref("parent", remote_side=[id]))


class LongCharacter(Base):
    __tablename__ = "long_characters"

    id = Column(CHAR(36), primary_key=True, default=_uuid)
    project_id = Column(CHAR(36), ForeignKey("projects.id"), nullable=False, index=True)
    name = Column(String(128), default="")
    traits = Column(Text, default="")
    ability = Column(Text, default="")
    status = Column(String(64), default="alive")
    relations = Column(JSON, default=list)  # [{target, relation}]
    importance = Column(Integer, default=0)


class LongForeshadow(Base):
    __tablename__ = "long_foreshadows"

    id = Column(CHAR(36), primary_key=True, default=_uuid)
    project_id = Column(CHAR(36), ForeignKey("projects.id"), nullable=False, index=True)
    title = Column(String(255), default="")
    content = Column(Text, default="")
    state = Column(Enum("pending", "revealed", "abandoned", name="fs_state"), default="pending")
    subplot_id = Column(CHAR(36), nullable=True)


class LongWorldSetting(Base):
    __tablename__ = "long_world_settings"

    id = Column(CHAR(36), primary_key=True, default=_uuid)
    project_id = Column(CHAR(36), ForeignKey("projects.id"), nullable=False, index=True)
    category = Column(String(128), default="")
    content = Column(Text, default="")


class LongPlotNode(Base):
    __tablename__ = "long_plot_nodes"

    id = Column(CHAR(36), primary_key=True, default=_uuid)
    project_id = Column(CHAR(36), ForeignKey("projects.id"), nullable=False, index=True)
    chapter_id = Column(CHAR(36), ForeignKey("long_chapters.id", ondelete="SET NULL"), nullable=True, index=True)
    title = Column(String(255), default="")
    summary = Column(Text, default="")
    timeline_pos = Column(String(64), default="")
    order = Column(Integer, default=0)


class LongChapter(Base):
    __tablename__ = "long_chapters"

    id = Column(CHAR(36), primary_key=True, default=_uuid)
    project_id = Column(CHAR(36), ForeignKey("projects.id"), nullable=False, index=True)
    title = Column(String(255), default="")
    content = Column(Text, default="")
    detailed_outline = Column(Text, default="")
    status = Column(String(32), default="draft")
    order = Column(Integer, default=0)
    constraints = Column(JSON, default=list)


class LongCharacterMemory(Base):
    __tablename__ = "long_character_memories"

    id = Column(CHAR(36), primary_key=True, default=_uuid)
    project_id = Column(CHAR(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    character_id = Column(CHAR(36), ForeignKey("long_characters.id", ondelete="CASCADE"), nullable=False, index=True)
    content = Column(Text, default="")
    importance = Column(String(16), default="major")
    ttl = Column(String(16), default="long")
    source_chapter_id = Column(CHAR(36), ForeignKey("long_chapters.id", ondelete="SET NULL"), nullable=True, index=True)
    source_type = Column(String(16), default="auto")
    related_character_ids = Column(JSON, default=list)
    related_foreshadow_ids = Column(JSON, default=list)
    created_at = Column(DateTime, default=_now, nullable=False)
    updated_at = Column(DateTime, default=_now, onupdate=_now, nullable=False)


class LongCharacterMemoryDraft(Base):
    __tablename__ = "long_character_memory_drafts"

    id = Column(CHAR(36), primary_key=True, default=_uuid)
    project_id = Column(CHAR(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    chapter_id = Column(CHAR(36), ForeignKey("long_chapters.id", ondelete="CASCADE"), nullable=False, index=True)
    character_id = Column(CHAR(36), ForeignKey("long_characters.id", ondelete="CASCADE"), nullable=False, index=True)
    action = Column(String(16), default="add")
    target_memory_id = Column(CHAR(36), ForeignKey("long_character_memories.id", ondelete="SET NULL"), nullable=True)
    content = Column(Text, default="")
    importance = Column(String(16), default="major")
    ttl = Column(String(16), default="long")
    related_character_ids = Column(JSON, default=list)
    related_foreshadow_ids = Column(JSON, default=list)
    created_at = Column(DateTime, default=_now, nullable=False)


class LongChapterMemoryExtraction(Base):
    __tablename__ = "long_chapter_memory_extractions"

    chapter_id = Column(CHAR(36), ForeignKey("long_chapters.id", ondelete="CASCADE"), primary_key=True)
    extracted_at = Column(DateTime, default=_now, nullable=False)
    content_hash = Column(String(64), nullable=False)
    memory_count = Column(Integer, default=0)


class LongChangeRecord(Base):
    __tablename__ = "long_change_records"

    id = Column(CHAR(36), primary_key=True, default=_uuid)
    project_id = Column(CHAR(36), ForeignKey("projects.id"), nullable=False, index=True)
    entity_type = Column(String(64), nullable=False)
    entity_id = Column(CHAR(36), nullable=True)
    before = Column(JSON, default=None)
    after = Column(JSON, default=None)
    status = Column(Enum("staged", "applied", "rejected", name="cr_status"), default="staged")
    source = Column(String(16), default="staged")
    created_at = Column(DateTime, default=_now)



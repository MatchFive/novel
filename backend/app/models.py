"""全部 ORM 模型：projects / long_* / short_* / 公共。"""
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
    String,
    Text,
    JSON,
)
from sqlalchemy.dialects.sqlite import CHAR
from sqlalchemy.orm import relationship

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Project(Base):
    __tablename__ = "projects"

    id = Column(CHAR(36), primary_key=True, default=_uuid)
    type = Column(Enum("long", "short", name="project_type"), nullable=False, index=True)
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
    is_default = Column(Boolean, default=False)

    def to_dict(self, hide_key: bool = True) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "base_url": self.base_url,
            "model": self.model,
            "is_default": self.is_default,
            **({} if hide_key else {"api_key": self.api_key}),
        }


class UserSetting(Base):
    __tablename__ = "user_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    recursive_limit = Column(Integer, default=8)
    hotspot_sources = Column(JSON, default=list)
    theme = Column(String(32), default="light")

    def to_dict(self) -> dict:
        return {
            "recursive_limit": self.recursive_limit,
            "hotspot_sources": self.hotspot_sources or [],
            "theme": self.theme,
        }


class AssistantSession(Base):
    __tablename__ = "assistant_sessions"

    id = Column(CHAR(36), primary_key=True, default=_uuid)
    project_id = Column(CHAR(36), ForeignKey("projects.id"), nullable=True, index=True)
    staged_changes = Column(JSON, default=list)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "staged_changes": self.staged_changes or [],
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


# ---------------- 长篇小说数据（全部 project_id 外键） ----------------

class LongOutline(Base):
    __tablename__ = "long_outlines"

    id = Column(CHAR(36), primary_key=True, default=_uuid)
    project_id = Column(CHAR(36), ForeignKey("projects.id"), nullable=False, index=True)
    parent_id = Column(CHAR(36), nullable=True, index=True)
    title = Column(String(255), default="")
    content = Column(Text, default="")
    version_chain = Column(CHAR(36), nullable=True)  # 上一版 id
    order = Column(Integer, default=0)


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
    title = Column(String(255), default="")
    summary = Column(Text, default="")
    timeline_pos = Column(String(64), default="")


class LongChapter(Base):
    __tablename__ = "long_chapters"

    id = Column(CHAR(36), primary_key=True, default=_uuid)
    project_id = Column(CHAR(36), ForeignKey("projects.id"), nullable=False, index=True)
    title = Column(String(255), default="")
    content = Column(Text, default="")
    order = Column(Integer, default=0)
    constraints = Column(JSON, default=list)


class LongChangeRecord(Base):
    __tablename__ = "long_change_records"

    id = Column(CHAR(36), primary_key=True, default=_uuid)
    project_id = Column(CHAR(36), ForeignKey("projects.id"), nullable=False, index=True)
    entity_type = Column(String(64), nullable=False)
    entity_id = Column(CHAR(36), nullable=True)
    before = Column(JSON, default=None)
    after = Column(JSON, default=None)
    status = Column(Enum("staged", "applied", "rejected", name="cr_status"), default="staged")
    created_at = Column(DateTime, default=_now)


# ---------------- 短篇小说数据（与长篇完全分表） ----------------

class ShortSetting(Base):
    __tablename__ = "short_settings"

    id = Column(CHAR(36), ForeignKey("projects.id"), primary_key=True)
    core_hook = Column(Text, default="")          # 爽点
    plans = Column(JSON, default=list)           # 方案列表
    selected_plan = Column(JSON, default=None)    # 选定方案
    detail_plan = Column(Text, default="")       # 详细规划
    chapters_plan = Column(JSON, default=list)    # 章节规划
    writing = Column(JSON, default=list)          # 各章节正文
    integration = Column(Text, default="")        # 整合结果
    step = Column(Integer, default=0)            # 当前步骤
    updated_at = Column(DateTime, default=_now, onupdate=_now)


class ShortChapter(Base):
    __tablename__ = "short_chapters"

    id = Column(CHAR(36), primary_key=True, default=_uuid)
    project_id = Column(CHAR(36), ForeignKey("projects.id"), nullable=False, index=True)
    title = Column(String(255), default="")
    content = Column(Text, default="")
    order = Column(Integer, default=0)


class ShortHotspot(Base):
    __tablename__ = "short_hotspots"

    id = Column(CHAR(36), primary_key=True, default=_uuid)
    project_id = Column(CHAR(36), ForeignKey("projects.id"), nullable=False, index=True)
    source = Column(String(255), default="")
    title = Column(String(512), default="")
    url = Column(Text, default="")
    analysis = Column(JSON, default=None)
    used = Column(Boolean, default=False)
    created_at = Column(DateTime, default=_now)

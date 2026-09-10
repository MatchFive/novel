"""SQLAlchemy 模型（表映射）。仅此文件定义表结构；数据访问一律走 repositories/（见设计文档 4.4）。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now()


# ---------------------------------------------------------------- 项目
class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    genre: Mapped[str | None] = mapped_column(String(100))
    logline: Mapped[str | None] = mapped_column(Text)
    theme: Mapped[str | None] = mapped_column(Text)
    style_guide: Mapped[str | None] = mapped_column(Text)   # 文风指南
    status: Mapped[str] = mapped_column(String(20), default="active")  # active/archived
    settings: Mapped[dict] = mapped_column(JSON, default=dict)  # 生成配置（见 3.14）
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    arcs: Mapped[list["Arc"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    chapters: Mapped[list["Chapter"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class Idea(Base):
    __tablename__ = "ideas"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    title: Mapped[str | None] = mapped_column(String(200))
    summary: Mapped[str | None] = mapped_column(Text)       # 点子内容
    tags: Mapped[str | None] = mapped_column(String(500))   # 逗号分隔
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft/expanded/selected/discarded
    eval_json: Mapped[dict | None] = mapped_column(JSON)    # AI 四维评估
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


# ---------------------------------------------------------------- 世界观
class WorldCategory(Base):
    __tablename__ = "world_categories"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class WorldEntry(Base):
    __tablename__ = "world_entries"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("world_categories.id"))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[str | None] = mapped_column(String(500))
    importance: Mapped[int] = mapped_column(Integer, default=3)   # 1~5
    canonical: Mapped[bool] = mapped_column(Boolean, default=False)  # 铁律
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


# ---------------------------------------------------------------- 角色
class Character(Base):
    __tablename__ = "characters"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    aliases: Mapped[str | None] = mapped_column(String(500))
    role_type: Mapped[str | None] = mapped_column(String(30))  # protagonist/antagonist/support...
    profile: Mapped[dict] = mapped_column(JSON, default=dict)  # 性格/外貌/背景/动机/能力/说话风格
    arc: Mapped[dict | None] = mapped_column(JSON)             # 成长弧光
    status: Mapped[str] = mapped_column(String(20), default="alive")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class CharacterRelation(Base):
    __tablename__ = "character_relations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    from_id: Mapped[int] = mapped_column(ForeignKey("characters.id"))
    to_id: Mapped[int] = mapped_column(ForeignKey("characters.id"))
    relation: Mapped[str | None] = mapped_column(String(50))  # 师徒/宿敌/恋人/盟友...
    description: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------- 大纲与章节
class Arc(Base):
    __tablename__ = "arcs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    title: Mapped[str | None] = mapped_column(String(200))
    seq: Mapped[int] = mapped_column(Integer, default=0)      # 卷序号
    goal: Mapped[str | None] = mapped_column(Text)
    conflict: Mapped[str | None] = mapped_column(Text)
    resolution: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="planned")  # planned/outlined/writing/done

    project: Mapped["Project"] = relationship(back_populates="arcs")
    chapters: Mapped[list["Chapter"]] = relationship(back_populates="arc")


# ---------------------------------------------------------------- 情节层（卷 → 情节 → 章节）
class Plot(Base):
    """情节：介于卷与章节之间的叙事单元。一个情节可持续多个章节，
    AI 规划章节细纲时先整卷统一生成情节线，再把章节挂到情节下。"""
    __tablename__ = "plots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    arc_id: Mapped[int | None] = mapped_column(ForeignKey("arcs.id"))
    seq: Mapped[int] = mapped_column(Integer, default=0)      # 情节序号（卷内连续）
    title: Mapped[str | None] = mapped_column(String(200))
    summary: Mapped[str | None] = mapped_column(Text)         # 情节概要（发生什么事）
    conflict: Mapped[str | None] = mapped_column(Text)        # 本情节核心冲突
    resolution: Mapped[str | None] = mapped_column(Text)      # 收束方式 / 情节末钩子
    beats_json: Mapped[list | None] = mapped_column(JSON)     # 情节节拍线（跨章粗粒度推进点，可手改）
    status: Mapped[str] = mapped_column(String(20), default="planned")  # planned/writing/done
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    chapters: Mapped[list["Chapter"]] = relationship(back_populates="plot")


class Chapter(Base):
    __tablename__ = "chapters"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    arc_id: Mapped[int | None] = mapped_column(ForeignKey("arcs.id"))
    plot_id: Mapped[int | None] = mapped_column(ForeignKey("plots.id"))  # 所属情节（可空，兼容旧数据）
    seq: Mapped[int] = mapped_column(Integer, default=0)      # 章序号（全书连续）
    title: Mapped[str | None] = mapped_column(String(200))
    pov_char_id: Mapped[int | None] = mapped_column(ForeignKey("characters.id"))
    objective: Mapped[str | None] = mapped_column(Text)       # 本章目标
    beats_json: Mapped[list | None] = mapped_column(JSON)     # 细纲节拍列表（plot 情节线）
    outline_json: Mapped[dict | None] = mapped_column(JSON)   # 结构化细纲其余字段（scene/characters/dialogue_hooks/humor_points/ending_hook/target_words）
    outline_status: Mapped[str] = mapped_column(String(20), default="none")  # none/beats/done
    content: Mapped[str | None] = mapped_column(Text)         # 正文 Markdown
    content_status: Mapped[str] = mapped_column(String(20), default="empty")  # empty/draft/first_draft/polished
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[str | None] = mapped_column(Text)         # 本章摘要
    memory_status: Mapped[str] = mapped_column(String(20), default="none")  # none/pending/extracted/needs_refresh
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    project: Mapped["Project"] = relationship(back_populates="chapters")
    arc: Mapped["Arc | None"] = relationship(back_populates="chapters")
    plot: Mapped["Plot | None"] = relationship(back_populates="chapters")


# ---------------------------------------------------------------- 三层记忆体系
class StoryEvent(Base):   # 第 1 层：全知事件日志
    __tablename__ = "story_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    chapter_id: Mapped[int | None] = mapped_column(ForeignKey("chapters.id"))
    seq: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str | None] = mapped_column(String(200))
    summary: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(String(200))
    time_marker: Mapped[str | None] = mapped_column(String(200))
    char_ids: Mapped[list | None] = mapped_column(JSON)
    info_gained: Mapped[list | None] = mapped_column(JSON)    # info_items id 列表
    promises: Mapped[list | None] = mapped_column(JSON)       # promises id 列表
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class CharacterEvent(Base):  # 第 2 层：限知经历档案
    __tablename__ = "character_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id"), nullable=False)
    chapter_id: Mapped[int | None] = mapped_column(ForeignKey("chapters.id"))
    event_ref: Mapped[int | None] = mapped_column(Integer)    # 关联 story_events.id
    importance: Mapped[int] = mapped_column(Integer, default=3)  # 1~5 检索加权
    summary: Mapped[str | None] = mapped_column(Text)         # 该角色视角"发生了什么"
    quotes: Mapped[str | None] = mapped_column(Text)          # 关键台词【原文】
    acted: Mapped[str | None] = mapped_column(Text)           # 做过的事/决定
    felt: Mapped[str | None] = mapped_column(Text)            # 情绪与心理变化
    got_lost: Mapped[str | None] = mapped_column(Text)        # 获得/失去
    secrets: Mapped[str | None] = mapped_column(Text)         # 知道但没说出口的
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class InfoItem(Base):  # 第 3 层 a：信息项
    __tablename__ = "info_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="hidden")  # hidden/partial/revealed


class CharacterKnowledge(Base):  # 第 3 层 b：知晓关系
    __tablename__ = "character_knowledge"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id"), nullable=False)
    info_item_id: Mapped[int] = mapped_column(ForeignKey("info_items.id"), nullable=False)
    knows: Mapped[bool] = mapped_column(Boolean, default=False)
    learned_chapter: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class Promise(Base):  # 第 3 层 c：承诺/伏笔
    __tablename__ = "promises"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), default="promise")  # promise/foreshadow/cliffhanger
    content: Mapped[str] = mapped_column(Text, nullable=False)
    by_char_id: Mapped[int | None] = mapped_column(ForeignKey("characters.id"))
    made_chapter: Mapped[int | None] = mapped_column(Integer)
    due_chapter: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open/pending/fulfilled/dropped
    resolution: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


# ---------------------------------------------------------------- 时间线（可选）
class TimelineEvent(Base):
    __tablename__ = "timeline_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    title: Mapped[str | None] = mapped_column(String(200))
    time_marker: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    char_ids: Mapped[list | None] = mapped_column(JSON)
    entry_ids: Mapped[list | None] = mapped_column(JSON)


# ---------------------------------------------------------------- 设定沉淀候选
class ProposedEntry(Base):
    __tablename__ = "proposed_entries"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    source_chapter: Mapped[int | None] = mapped_column(Integer)
    source_module: Mapped[str | None] = mapped_column(String(30))
    source_snippet: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(50))
    importance: Mapped[int] = mapped_column(Integer, default=3)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/accepted/rejected
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


# ---------------------------------------------------------------- 教程知识库
class HandbookDoc(Base):
    __tablename__ = "handbook_docs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int | None] = mapped_column(Integer)   # NULL=全局
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str | None] = mapped_column(String(50))  # 大纲/正文/角色/世界观/节奏/市场
    source_type: Mapped[str | None] = mapped_column(String(20))  # paste/file/url
    content: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class HandbookChunk(Base):
    __tablename__ = "handbook_chunks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    doc_id: Mapped[int] = mapped_column(ForeignKey("handbook_docs.id"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str | None] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(String(50))  # 切片级分类（AI 提炼时按要点归类；空=沿用文档分类）


# ---------------------------------------------------------------- AI 调用留痕
class GenerationLog(Base):
    __tablename__ = "generation_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int | None] = mapped_column(Integer)
    module: Mapped[str | None] = mapped_column(String(30))     # idea/world/.../command
    caller: Mapped[str | None] = mapped_column(String(100))    # 调用来源（视图/功能路径）
    provider: Mapped[str | None] = mapped_column(String(50))   # v1.6 模型路由
    tier: Mapped[str | None] = mapped_column(String(20))       # lite/standard/pro
    model: Mapped[str | None] = mapped_column(String(100))
    prompt_key: Mapped[str | None] = mapped_column(String(100))
    input_summary: Mapped[str | None] = mapped_column(Text)    # 输入 prompt 摘要
    output_summary: Mapped[str | None] = mapped_column(Text)   # 输出结果摘要
    in_tokens: Mapped[int] = mapped_column(Integer, default=0)
    out_tokens: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)  # 耗时毫秒
    status: Mapped[str] = mapped_column(String(20), default="ok")  # ok/failed/cancelled
    error: Mapped[str | None] = mapped_column(Text)            # 错误详情（含 traceback 摘要）
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


# ---------------------------------------------------------------- 对话会话（长期保存）
class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), default="新对话")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("chat_sessions.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(20))               # user/assistant/system
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


# ---------------------------------------------------------------- 一致性扫描问题（M4）
class RevisionIssue(Base):
    __tablename__ = "revision_issues"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    chapter_id: Mapped[int | None] = mapped_column(ForeignKey("chapters.id"))
    scan_id: Mapped[str | None] = mapped_column(String(50))    # 本次扫描批次标识
    quote: Mapped[str | None] = mapped_column(Text)            # 原文片段
    issue_type: Mapped[str] = mapped_column(String(30))        # timeline/setting/character/memory_conflict/knowledge_leak/promise_break
    issue: Mapped[str] = mapped_column(Text, nullable=False)   # 问题描述（含冲突依据）
    suggestion: Mapped[str | None] = mapped_column(Text)       # 修改建议
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/resolved/ignored
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


# ---------------------------------------------------------------- 配置与提示词
class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)


class PromptTemplate(Base):
    __tablename__ = "prompt_templates"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    scope: Mapped[str] = mapped_column(String(20), default="global")  # global/project/task
    project_id: Mapped[int | None] = mapped_column(Integer)
    category: Mapped[str | None] = mapped_column(String(50))
    name: Mapped[str | None] = mapped_column(String(200))
    template: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class PromptVersion(Base):
    __tablename__ = "prompt_versions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    template: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

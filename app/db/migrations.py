"""数据库迁移与种子数据。本文件是唯一允许裸 SQL/DDL 的地方（设计文档 4.4 规则 2）。"""
from __future__ import annotations

import json

from app.core.constants import PROMPTS_JSON, TUTORIAL_TXT
from app.db.models import WorldCategory
from app.db.session import Database

# 默认世界观分类（项目创建时复制）
DEFAULT_WORLD_CATEGORIES = [
    "地理", "历史", "势力", "种族", "规则/体系", "科技", "社会文化", "事件",
]

_RESOURCE_TUTORIAL = TUTORIAL_TXT


def ensure_columns(db: Database) -> None:
    """给已有表补新增列（SQLite ALTER TABLE ADD COLUMN，幂等）。"""
    from sqlalchemy import inspect, text
    inspector = inspect(db.engine)
    new_cols = {
        "generation_logs": {
            "caller": "VARCHAR(100)",
            "input_summary": "TEXT",
            "output_summary": "TEXT",
            "duration_ms": "INTEGER DEFAULT 0",
        },
        "handbook_chunks": {
            "category": "VARCHAR(50)",  # 切片级分类（AI 提炼按要点归类）
        },
        "chapters": {
            "outline_json": "JSON",  # 结构化细纲其余字段（scene/characters/ending_hook 等）
            "plot_id": "INTEGER",    # 所属情节（卷→情节→章节 三层结构；可空，兼容旧数据）
        },
        "plots": {
            "beats_json": "JSON",    # 情节节拍线（跨章粗粒度推进点；旧库补列）
        },
    }
    for table, cols in new_cols.items():
        if not inspector.has_table(table):
            continue
        existing = {c["name"] for c in inspector.get_columns(table)}
        with db.engine.begin() as conn:
            for col, coltype in cols.items():
                if col not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}"))


def seed_defaults(db: Database) -> None:
    """首次建库后写入内置提示词模板与内置教程（幂等）。"""
    from app.db.fts import create_fts_objects
    from app.db.repositories.prompt_repo import PromptTemplateRepository, SettingRepository

    create_fts_objects(db)
    ensure_columns(db)

    with db.session_ctx() as session:
        repo = PromptTemplateRepository(session)
        if PROMPTS_JSON.exists():
            with PROMPTS_JSON.open("r", encoding="utf-8") as f:
                templates = json.load(f)
            for item in templates.values():
                repo.upsert(
                    key=item["key"],
                    template=item["template"],
                    scope=item.get("scope", "global"),
                    project_id=item.get("project_id"),
                    category=item.get("category"),
                    name=item.get("name"),
                )
        settings = SettingRepository(session)
        settings.set_value("db_seeded", "1")
        session.commit()

    _seed_builtin_tutorial(db)


def _seed_builtin_tutorial(db: Database) -> None:
    """内置教程（创作指南提炼）导入教程知识库：仅在尚未导入过时执行。"""
    tutorial = _RESOURCE_TUTORIAL
    if not tutorial.exists():
        return
    from app.db.repositories.canon_repo import HandbookDocRepository
    from app.services.handbook_service import HandbookService

    with db.session_ctx() as session:
        docs = HandbookDocRepository(session).list_global_and_project(None)
    if any("创作指南" in (d.title or "") for d in docs):
        return  # 已导入
    content = tutorial.read_text(encoding="utf-8")
    HandbookService(db).import_text(
        title="创作指南（内置，网文创作方法论）",
        category="正文",
        content=content,
        project_id=None,  # 全局可用
    )


def create_default_categories(session, project_id: int) -> list[WorldCategory]:
    """为项目创建默认世界观分类（供服务层调用）。"""
    cats = []
    for i, name in enumerate(DEFAULT_WORLD_CATEGORIES):
        cats.append(WorldCategory(project_id=project_id, name=name, sort_order=i))
    session.add_all(cats)
    return cats

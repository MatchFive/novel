"""仪表盘统计服务：项目进度统计 + Token 用量（经 Repository，遵循 4.4）。"""
from __future__ import annotations

from sqlalchemy import func, select

from app.db.models import (
    Chapter,
    Character,
    Idea,
    WorldEntry,
)
from app.db.session import Database


class DashboardService:
    def __init__(self, db: Database):
        self.db = db

    def stats(self, project_id: int) -> dict:
        with self.db.session_ctx() as session:
            ideas = session.scalar(select(func.count()).select_from(Idea).where(Idea.project_id == project_id)) or 0
            entries = session.scalar(select(func.count()).select_from(WorldEntry).where(WorldEntry.project_id == project_id)) or 0
            characters = session.scalar(select(func.count()).select_from(Character).where(Character.project_id == project_id)) or 0
            chapters = session.scalar(select(func.count()).select_from(Chapter).where(Chapter.project_id == project_id)) or 0
            words = session.scalar(
                select(func.coalesce(func.sum(Chapter.word_count), 0)).where(Chapter.project_id == project_id)
            ) or 0
            drafted = session.scalar(
                select(func.count()).select_from(Chapter).where(
                    Chapter.project_id == project_id, Chapter.word_count > 0
                )
            ) or 0
            extracted = session.scalar(
                select(func.count()).select_from(Chapter).where(
                    Chapter.project_id == project_id, Chapter.memory_status == "extracted"
                )
            ) or 0
            from app.db.repositories.chapter_repo import ArcRepository
            arcs = len(ArcRepository(session).list_by_project(project_id))
        return {
            "ideas": ideas, "entries": entries, "characters": characters,
            "arcs": arcs, "chapters": chapters, "words": words,
            "drafted": drafted, "extracted": extracted,
        }

    def usage(self, project_id: int | None = None) -> list[dict]:
        """按 provider+tier 汇总 token 用量（Token 用量面板）。"""
        from app.db.repositories.generation_log_repo import GenerationLogRepository
        with self.db.session_ctx() as session:
            return GenerationLogRepository(session).totals(project_id)

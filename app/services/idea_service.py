"""点子服务。"""
from __future__ import annotations

from app.db.repositories.idea_repo import IdeaRepository
from app.db.session import Database
from app.db.unit_of_work import UnitOfWork


class IdeaService:
    def __init__(self, db: Database):
        self.db = db

    def create(self, *, project_id: int, title: str = "", summary: str = "",
               tags: str = "", status: str = "draft"):
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                idea = IdeaRepository(session).create(
                    project_id=project_id, title=title, summary=summary,
                    tags=tags, status=status,
                )
                return idea

    def list(self, project_id: int, status: str | None = None) -> list:
        with self.db.session_ctx() as session:
            return IdeaRepository(session).list_by_status(project_id, status)

    def get(self, idea_id: int):
        with self.db.session_ctx() as session:
            return IdeaRepository(session).get(idea_id)

    def update(self, idea_id: int, **fields) -> None:
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                idea = IdeaRepository(session).get(idea_id)
                if idea is not None:
                    IdeaRepository(session).update(idea, **fields)

    def delete(self, idea_id: int) -> None:
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                idea = IdeaRepository(session).get(idea_id)
                if idea is not None:
                    IdeaRepository(session).delete(idea)

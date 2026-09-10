"""点子仓储。"""
from __future__ import annotations

from sqlalchemy import select

from app.db.models import Idea
from app.db.repositories.base import BaseRepository


class IdeaRepository(BaseRepository[Idea]):
    model = Idea

    def list_by_status(self, project_id: int, status: str | None = None) -> list[Idea]:
        stmt = select(Idea).where(Idea.project_id == project_id)
        if status:
            stmt = stmt.where(Idea.status == status)
        stmt = stmt.order_by(Idea.created_at.desc())
        return list(self.session.scalars(stmt))

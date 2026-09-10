"""项目仓储：项目 CRUD（唯一数据库访问点）。"""
from __future__ import annotations

from sqlalchemy import select

from app.db.models import Project
from app.db.repositories.base import BaseRepository


class ProjectRepository(BaseRepository[Project]):
    model = Project

    def list_active(self) -> list[Project]:
        stmt = select(Project).where(Project.status == "active").order_by(Project.updated_at.desc())
        return list(self.session.scalars(stmt))

    def list_all(self) -> list[Project]:
        stmt = select(Project).order_by(Project.updated_at.desc())
        return list(self.session.scalars(stmt))

    def get_by_name(self, name: str) -> Project | None:
        stmt = select(Project).where(Project.name == name)
        return self.session.scalar(stmt)

    def archive(self, project: Project) -> None:
        project.status = "archived"

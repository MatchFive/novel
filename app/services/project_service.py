"""项目服务：项目 CRUD + 创建时初始化默认分类。"""
from __future__ import annotations

from app.db.migrations import create_default_categories
from app.db.repositories.project_repo import ProjectRepository
from app.db.session import Database
from app.db.unit_of_work import UnitOfWork


class ProjectService:
    def __init__(self, db: Database):
        self.db = db

    def create(self, *, name: str, genre: str = "", logline: str = "",
               theme: str = "", style_guide: str = "", settings: dict | None = None) -> object:
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                project = ProjectRepository(session).create(
                    name=name, genre=genre, logline=logline,
                    theme=theme, style_guide=style_guide,
                    settings=settings or {},
                )
                session.flush()  # 取得 project.id
                create_default_categories(session, project.id)
                return project

    def list(self, active_only: bool = True) -> list:
        with self.db.session_ctx() as session:
            repo = ProjectRepository(session)
            return repo.list_active() if active_only else repo.list_all()

    def get(self, project_id: int):
        with self.db.session_ctx() as session:
            return ProjectRepository(session).get(project_id)

    def update(self, project_id: int, **fields) -> None:
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                project = ProjectRepository(session).get(project_id)
                if project is None:
                    raise KeyError(project_id)
                ProjectRepository(session).update(project, **fields)

    def delete(self, project_id: int) -> None:
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                project = ProjectRepository(session).get(project_id)
                if project is not None:
                    ProjectRepository(session).delete(project)

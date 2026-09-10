"""世界观服务。"""
from __future__ import annotations

from app.db.repositories.world_repo import WorldCategoryRepository, WorldEntryRepository
from app.db.session import Database
from app.db.unit_of_work import UnitOfWork


class WorldService:
    def __init__(self, db: Database):
        self.db = db

    # ---------------- 分类 ----------------
    def list_categories(self, project_id: int) -> list:
        with self.db.session_ctx() as session:
            return WorldCategoryRepository(session).list_by_project(project_id)

    def add_category(self, project_id: int, name: str, sort_order: int = 999):
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                return WorldCategoryRepository(session).create(
                    project_id=project_id, name=name, sort_order=sort_order,
                )

    def delete_category(self, category_id: int) -> None:
        """删除分类：先把其条目的 category_id 置空（避免外键失败）。"""
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                cat = WorldCategoryRepository(session).get(category_id)
                if cat is None:
                    return
                for e in WorldEntryRepository(session).list_by_category(cat.project_id, category_id):
                    e.category_id = None
                WorldCategoryRepository(session).delete(cat)

    # ---------------- 条目 ----------------
    def list_entries(self, project_id: int, category_id: int | None = None) -> list:
        with self.db.session_ctx() as session:
            return WorldEntryRepository(session).list_by_category(project_id, category_id)

    def create_entry(self, *, project_id: int, category_id: int | None, title: str,
                     content: str, tags: str = "", importance: int = 3, canonical: bool = False):
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                return WorldEntryRepository(session).create(
                    project_id=project_id, category_id=category_id, title=title,
                    content=content, tags=tags, importance=importance, canonical=canonical,
                )

    def get_entry(self, entry_id: int):
        with self.db.session_ctx() as session:
            return WorldEntryRepository(session).get(entry_id)

    def update_entry(self, entry_id: int, **fields) -> None:
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                entry = WorldEntryRepository(session).get(entry_id)
                if entry is not None:
                    WorldEntryRepository(session).update(entry, **fields)

    def delete_entry(self, entry_id: int) -> None:
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                entry = WorldEntryRepository(session).get(entry_id)
                if entry is not None:
                    WorldEntryRepository(session).delete(entry)

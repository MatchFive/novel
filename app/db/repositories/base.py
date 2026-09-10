"""仓储层基类：通用 CRUD。所有 Repository 继承此基类；本目录是唯一数据库访问点（设计文档 4.4）。"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Base

T = TypeVar("T", bound=Base)


class BaseRepository(Generic[T]):
    """提供基础的 get / list / create / update / delete；业务查询在子类中实现。"""

    model: type[T]

    def __init__(self, session: Session):
        self.session = session

    # ---------------- 查询 ----------------
    def get(self, entity_id: int) -> T | None:
        return self.session.get(self.model, entity_id)

    def list_by_project(self, project_id: int) -> list[T]:
        stmt = select(self.model).where(self.model.project_id == project_id)
        return list(self.session.scalars(stmt))

    # ---------------- 写入 ----------------
    def create(self, **fields: Any) -> T:
        obj = self.model(**fields)
        self.session.add(obj)
        return obj

    def add(self, obj: T) -> T:
        self.session.add(obj)
        return obj

    def update(self, entity: T, **fields: Any) -> T:
        for k, v in fields.items():
            setattr(entity, k, v)
        return entity

    def delete(self, entity: T) -> None:
        self.session.delete(entity)

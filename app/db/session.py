"""Session 工厂：唯一会话入口（设计文档 4.4：禁止其他层自行创建 Session）。"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Base


def create_engine_for(db_path: Path) -> object:
    """创建 SQLite 引擎（WAL 模式 + 外键约束）。"""
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
    )

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_conn, _record):  # pragma: no cover
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return engine


class Database:
    """数据库句柄：持有 engine + sessionmaker，统一初始化建表。"""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.engine = create_engine_for(self.db_path)
        self._session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

    def init_schema(self) -> None:
        """建表（幂等）。"""
        Base.metadata.create_all(self.engine)

    def session(self) -> Session:
        return self._session_factory()

    @contextmanager
    def session_ctx(self) -> Iterator[Session]:
        """上下文方式获取 Session（只读场景推荐）。"""
        s = self._session_factory()
        try:
            yield s
        finally:
            s.close()

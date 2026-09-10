"""事务边界（Unit of Work）：跨表写操作由 Service 层在此提交/回滚（设计文档 4.4 规则 3）。"""
from __future__ import annotations

from sqlalchemy.orm import Session


class UnitOfWork:
    """上下文管理器：进入时开事务，退出时全部成功才 commit，异常回滚。"""

    def __init__(self, session: Session):
        self.session = session

    def __enter__(self) -> "UnitOfWork":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self.session.commit()
        else:
            self.session.rollback()

    def commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()

"""一致性扫描问题仓储（M4）。"""
from __future__ import annotations

from sqlalchemy import select, update

from app.db.models import RevisionIssue
from app.db.repositories.base import BaseRepository


class RevisionIssueRepository(BaseRepository[RevisionIssue]):
    model = RevisionIssue

    def list_by_project(self, project_id: int, status: str | None = None) -> list[RevisionIssue]:
        stmt = select(RevisionIssue).where(RevisionIssue.project_id == project_id)
        if status:
            stmt = stmt.where(RevisionIssue.status == status)
        stmt = stmt.order_by(RevisionIssue.created_at.desc(), RevisionIssue.id)
        return list(self.session.scalars(stmt))

    def clear_scan(self, project_id: int, scan_id: str) -> None:
        self.session.execute(
            update(RevisionIssue)
            .where(RevisionIssue.project_id == project_id, RevisionIssue.scan_id == scan_id)
            .values(status="ignored")
        )

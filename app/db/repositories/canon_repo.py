"""设定沉淀候选 + 教程知识库仓储。"""
from __future__ import annotations

from sqlalchemy import select

from app.db.models import HandbookChunk, HandbookDoc, ProposedEntry
from app.db.repositories.base import BaseRepository
from app.db.repositories.world_repo import fts_rowids


class ProposedEntryRepository(BaseRepository[ProposedEntry]):
    model = ProposedEntry

    def list_pending(self, project_id: int) -> list[ProposedEntry]:
        stmt = (
            select(ProposedEntry)
            .where(ProposedEntry.project_id == project_id, ProposedEntry.status == "pending")
            .order_by(ProposedEntry.importance.desc(), ProposedEntry.id)
        )
        return list(self.session.scalars(stmt))


class HandbookDocRepository(BaseRepository[HandbookDoc]):
    model = HandbookDoc

    def list_global_and_project(self, project_id: int | None) -> list[HandbookDoc]:
        """列表显示：返回全部（含停用），停用的在视图中标记，检索注入时才过滤。"""
        stmt = select(HandbookDoc).where(
            (HandbookDoc.project_id.is_(None)) | (HandbookDoc.project_id == project_id),
        )
        return list(self.session.scalars(stmt))

    def list_enabled(self, project_id: int | None) -> list[HandbookDoc]:
        """检索注入用：只返回启用的教程。"""
        stmt = select(HandbookDoc).where(
            (HandbookDoc.project_id.is_(None)) | (HandbookDoc.project_id == project_id),
            HandbookDoc.enabled.is_(True),
        )
        return list(self.session.scalars(stmt))


class HandbookChunkRepository(BaseRepository[HandbookChunk]):
    model = HandbookChunk

    def list_by_doc(self, doc_id: int) -> list[HandbookChunk]:
        stmt = select(HandbookChunk).where(HandbookChunk.doc_id == doc_id).order_by(HandbookChunk.seq)
        return list(self.session.scalars(stmt))

    def search(self, keyword: str, category: str | None = None, limit: int = 4,
               enabled_doc_ids: set[int] | None = None) -> list[HandbookChunk]:
        """FTS5 检索教程切片（trigram）；短词降级 LIKE。enabled_doc_ids 限定启用教程。"""
        kw = keyword.strip()
        if len(kw) >= 3:
            try:
                ids = fts_rowids(self.session, "handbook_chunks_fts", kw, limit * 3)
                if ids:
                    chunks = list(self.session.scalars(
                        select(HandbookChunk).where(HandbookChunk.id.in_(ids))
                    ))
                    chunks = self._filter(chunks, category, enabled_doc_ids)
                    return chunks[:limit]
            except Exception:
                pass
        like = f"%{kw}%"
        stmt = select(HandbookChunk).where(
            (HandbookChunk.title.like(like)) | (HandbookChunk.content.like(like))
        )
        if category:
            stmt = stmt.join(HandbookDoc).where(HandbookDoc.category == category)
        stmt = stmt.limit(limit * 3)
        chunks = list(self.session.scalars(stmt))
        return self._filter(chunks, category, enabled_doc_ids)[:limit]

    @staticmethod
    def _filter(chunks, category, enabled_doc_ids):
        out = []
        for c in chunks:
            if enabled_doc_ids is not None and c.doc_id not in enabled_doc_ids:
                continue
            out.append(c)
        return out

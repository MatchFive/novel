"""世界观仓储：分类 + 条目。"""
from __future__ import annotations

from sqlalchemy import select, text

from app.db.models import WorldCategory, WorldEntry
from app.db.repositories.base import BaseRepository


def fts_rowids(session, fts_table: str, keyword: str, limit: int) -> list[int]:
    """FTS5 检索返回命中 rowid 列表（trigram；仓储内部允许 SQL）。"""
    kw = keyword.strip().replace('"', '""')
    stmt = text(
        f"SELECT rowid FROM {fts_table} WHERE {fts_table} MATCH :kw LIMIT :lim"
    ).bindparams(kw=f'"{kw}"', lim=limit)
    return list(session.execute(stmt).scalars())


class WorldCategoryRepository(BaseRepository[WorldCategory]):
    model = WorldCategory

    def list_by_project(self, project_id: int) -> list[WorldCategory]:
        stmt = (
            select(WorldCategory)
            .where(WorldCategory.project_id == project_id)
            .order_by(WorldCategory.sort_order, WorldCategory.id)
        )
        return list(self.session.scalars(stmt))


class WorldEntryRepository(BaseRepository[WorldEntry]):
    model = WorldEntry

    def list_by_category(self, project_id: int, category_id: int | None = None) -> list[WorldEntry]:
        stmt = select(WorldEntry).where(WorldEntry.project_id == project_id)
        if category_id is not None:
            stmt = stmt.where(WorldEntry.category_id == category_id)
        stmt = stmt.order_by(WorldEntry.updated_at.desc())
        return list(self.session.scalars(stmt))

    def list_canonical(self, project_id: int) -> list[WorldEntry]:
        stmt = select(WorldEntry).where(
            WorldEntry.project_id == project_id, WorldEntry.canonical.is_(True)
        )
        return list(self.session.scalars(stmt))

    def search(self, project_id: int, keyword: str, limit: int = 10) -> list[WorldEntry]:
        """FTS5 检索（trigram 中文子串）；短词（<3 字符）自动降级 LIKE。"""
        kw = keyword.strip()
        if len(kw) >= 3:
            try:
                ids = fts_rowids(self.session, "world_entries_fts", kw, limit)
                if ids:
                    entries = list(self.session.scalars(
                        select(WorldEntry).where(WorldEntry.id.in_(ids))
                    ))
                    entries.sort(key=lambda e: e.importance, reverse=True)
                    return entries
            except Exception:
                pass  # FTS 不可用时降级 LIKE
        like = f"%{kw}%"
        stmt = (
            select(WorldEntry)
            .where(
                WorldEntry.project_id == project_id,
                (WorldEntry.title.like(like)) | (WorldEntry.content.like(like)) | (WorldEntry.tags.like(like)),
            )
            .order_by(WorldEntry.importance.desc())
            .limit(limit)
        )
        return list(self.session.scalars(stmt))

"""卷与章节仓储。"""
from __future__ import annotations

from sqlalchemy import select

from app.db.models import Arc, Chapter, Plot
from app.db.repositories.base import BaseRepository


class ArcRepository(BaseRepository[Arc]):
    model = Arc

    def list_by_project(self, project_id: int) -> list[Arc]:
        stmt = (
            select(Arc)
            .where(Arc.project_id == project_id)
            .order_by(Arc.seq, Arc.id)
        )
        return list(self.session.scalars(stmt))

    def next_seq(self, project_id: int) -> int:
        stmt = select(Arc).where(Arc.project_id == project_id)
        seqs = [a.seq or 0 for a in self.session.scalars(stmt)]
        return max(seqs, default=-1) + 1


class PlotRepository(BaseRepository[Plot]):
    """情节层（卷 → 情节 → 章节）。"""
    model = Plot

    def list_by_project(self, project_id: int) -> list[Plot]:
        stmt = (
            select(Plot)
            .where(Plot.project_id == project_id)
            .order_by(Plot.seq, Plot.id)
        )
        return list(self.session.scalars(stmt))

    def list_by_arc(self, arc_id: int) -> list[Plot]:
        stmt = select(Plot).where(Plot.arc_id == arc_id).order_by(Plot.seq, Plot.id)
        return list(self.session.scalars(stmt))

    def next_seq(self, arc_id: int) -> int:
        stmt = select(Plot).where(Plot.arc_id == arc_id)
        seqs = [p.seq or 0 for p in self.session.scalars(stmt)]
        return max(seqs, default=-1) + 1


class ChapterRepository(BaseRepository[Chapter]):
    model = Chapter

    def list_by_project(self, project_id: int) -> list[Chapter]:
        stmt = (
            select(Chapter)
            .where(Chapter.project_id == project_id)
            .order_by(Chapter.seq, Chapter.id)
        )
        return list(self.session.scalars(stmt))

    def list_by_arc(self, arc_id: int) -> list[Chapter]:
        stmt = select(Chapter).where(Chapter.arc_id == arc_id).order_by(Chapter.seq, Chapter.id)
        return list(self.session.scalars(stmt))

    def list_by_plot(self, plot_id: int) -> list[Chapter]:
        stmt = select(Chapter).where(Chapter.plot_id == plot_id).order_by(Chapter.seq, Chapter.id)
        return list(self.session.scalars(stmt))

    def next_seq(self, project_id: int) -> int:
        stmt = select(Chapter).where(Chapter.project_id == project_id)
        seqs = [c.seq or 0 for c in self.session.scalars(stmt)]
        return max(seqs, default=-1) + 1

    def update_word_count(self, chapter: Chapter) -> None:
        chapter.word_count = len(chapter.content or "")

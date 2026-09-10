"""情节服务：情节（Plot）CRUD + 情节-章节挂载。

情节层介于卷与章节之间：一个情节持续多个章节，由 AI 在整卷规划时统一生成。
"""
from __future__ import annotations

from app.db.repositories.chapter_repo import ChapterRepository, PlotRepository
from app.db.session import Database
from app.db.unit_of_work import UnitOfWork


class PlotService:
    def __init__(self, db: Database):
        self.db = db

    def create(self, *, project_id: int, arc_id: int | None = None, seq: int | None = None,
               title: str = "", summary: str = "", conflict: str = "",
               resolution: str = "", beats_json: list | None = None):
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                repo = PlotRepository(session)
                if seq is None:
                    seq = repo.next_seq(arc_id) if arc_id else len(repo.list_by_project(project_id))
                return repo.create(
                    project_id=project_id, arc_id=arc_id, seq=seq, title=title,
                    summary=summary, conflict=conflict, resolution=resolution,
                    beats_json=beats_json or [],
                )

    # ---------------- 情节节拍线（跨章粗粒度描述，可手改） ----------------
    @staticmethod
    def normalize_beats(raw) -> list[dict]:
        """把 AI/用户输入的节拍统一为 [{\"event\": \"...\"}]（兼容字符串与字典）。"""
        beats: list[dict] = []
        for b in raw or []:
            if isinstance(b, dict):
                event = str(b.get("event") or b.get("事件") or "").strip()
            else:
                event = str(b).strip()
            if event:
                beats.append({"event": event})
        return beats

    def set_beats(self, plot_id: int, beats: list[dict]) -> None:
        self.update(plot_id, beats_json=self.normalize_beats(beats))

    def list_by_project(self, project_id: int) -> list:
        with self.db.session_ctx() as session:
            return PlotRepository(session).list_by_project(project_id)

    def list_by_arc(self, arc_id: int) -> list:
        with self.db.session_ctx() as session:
            return PlotRepository(session).list_by_arc(arc_id)

    def get(self, plot_id: int):
        with self.db.session_ctx() as session:
            return PlotRepository(session).get(plot_id)

    def update(self, plot_id: int, **fields) -> None:
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                plot = PlotRepository(session).get(plot_id)
                if plot is not None:
                    PlotRepository(session).update(plot, **fields)

    def delete(self, plot_id: int) -> None:
        """删除情节：其章节保留但 plot_id 置空（避免外键失败）。"""
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                plot = PlotRepository(session).get(plot_id)
                if plot is None:
                    return
                for ch in ChapterRepository(session).list_by_plot(plot_id):
                    ch.plot_id = None
                PlotRepository(session).delete(plot)

    def find_or_create(self, session, project_id: int, arc_id: int | None,
                       title: str, summary: str = "", conflict: str = ""):
        """按标题在卷内查找情节，不存在则创建（整卷拆章入库时用）。"""
        repo = PlotRepository(session)
        t = (title or "").replace(" ", "")
        for p in repo.list_by_arc(arc_id) if arc_id else repo.list_by_project(project_id):
            pt = (p.title or "").replace(" ", "")
            if t and pt and (t == pt or t in pt or pt in t):
                return p
        seq = repo.next_seq(arc_id) if arc_id else len(repo.list_by_project(project_id))
        return repo.create(
            project_id=project_id, arc_id=arc_id, seq=seq,
            title=title or "新情节", summary=summary, conflict=conflict,
        )

    # ---------------- 展示辅助 ----------------
    def chapter_grouping(self, project_id: int) -> list[dict]:
        """按 卷→情节→章节 三层分组，供章节树展示。

        返回：[{"kind": "arc", "arc": Arc|None, "plots": [{"plot": Plot|None, "chapters": [Chapter]}]}]
        未分卷章节归入 arc=None 的组；未分情节章节归入 plot=None 的组。
        """
        from app.db.repositories.chapter_repo import ArcRepository

        with self.db.session_ctx() as session:
            arcs = ArcRepository(session).list_by_project(project_id)
            plots = PlotRepository(session).list_by_project(project_id)
            chapters = ChapterRepository(session).list_by_project(project_id)

        groups: list[dict] = []

        def _plot_group(arc_id):
            plots_of_arc = [p for p in plots if p.arc_id == arc_id]
            result = []
            for p in plots_of_arc:
                chs = [c for c in chapters if c.plot_id == p.id]
                result.append({"plot": p, "chapters": chs})
            # 该卷下未分情节的章节
            orphan = [c for c in chapters if c.arc_id == arc_id and c.plot_id is None]
            if orphan or not plots_of_arc:
                result.append({"plot": None, "chapters": orphan})
            return result

        for arc in arcs:
            groups.append({"kind": "arc", "arc": arc, "plots": _plot_group(arc.id)})
        # 完全未分卷的章节
        orphan_chs = [c for c in chapters if c.arc_id is None]
        orphan_plots = [p for p in plots if p.arc_id is None]
        if orphan_chs or orphan_plots:
            groups.append({"kind": "arc", "arc": None, "plots": _plot_group(None)})
        return groups

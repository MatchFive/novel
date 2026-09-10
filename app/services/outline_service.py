"""大纲（卷）服务。"""
from __future__ import annotations

from app.db.repositories.chapter_repo import ArcRepository, ChapterRepository, PlotRepository
from app.db.session import Database
from app.db.unit_of_work import UnitOfWork


class OutlineService:
    def __init__(self, db: Database):
        self.db = db

    def create(self, *, project_id: int, title: str = "", seq: int | None = None,
               goal: str = "", conflict: str = "", resolution: str = "", summary: str = ""):
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                repo = ArcRepository(session)
                seq = seq if seq is not None else repo.next_seq(project_id)
                return repo.create(
                    project_id=project_id, title=title, seq=seq, goal=goal,
                    conflict=conflict, resolution=resolution, summary=summary,
                )

    def list(self, project_id: int) -> list:
        with self.db.session_ctx() as session:
            return ArcRepository(session).list_by_project(project_id)

    def get(self, arc_id: int):
        with self.db.session_ctx() as session:
            return ArcRepository(session).get(arc_id)

    def update(self, arc_id: int, **fields) -> None:
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                arc = ArcRepository(session).get(arc_id)
                if arc is not None:
                    ArcRepository(session).update(arc, **fields)

    def delete(self, arc_id: int) -> None:
        """删除卷：先把其章节与情节的 arc_id 置空（避免外键失败）。"""
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                arc = ArcRepository(session).get(arc_id)
                if arc is None:
                    return
                from app.db.repositories.chapter_repo import ChapterRepository
                for ch in ChapterRepository(session).list_by_arc(arc_id):
                    ch.arc_id = None
                for p in PlotRepository(session).list_by_arc(arc_id):
                    p.arc_id = None
                ArcRepository(session).delete(arc)

    # ---------------- 整卷拆章（情节 → 章节两段式规划） ----------------
    async def plan_plots(self, arc_id: int, llm) -> list[dict]:
        """第一阶段：把一卷统一规划为若干【情节】（每个情节跨多个章节）。

        返回情节列表（不写库，由调用方确认后入库）：
        [{title, summary, conflict, resolution}]
        """
        with self.db.session_ctx() as session:
            arc = ArcRepository(session).get(arc_id)
            if arc is None:
                raise ValueError(f"卷不存在: {arc_id}")
            project_id = arc.project_id
            from app.db.repositories.character_repo import CharacterRepository
            from app.db.repositories.world_repo import WorldEntryRepository
            chars = CharacterRepository(session).list_by_role(project_id)
            cast = "\n".join(f"- {c.name}（{c.role_type or '?'}）" for c in chars[:12]) or "（暂无）"
            entries = WorldEntryRepository(session).list_by_category(project_id, None)
            world_text = "\n".join(f"- {e.title}: {(e.content or '')[:80]}" for e in entries[:10]) or "（暂无）"
            existing_plots = PlotRepository(session).list_by_arc(arc_id)
            existing_plots_text = "\n".join(
                f"情节{p.seq + 1}「{p.title or ''}」：{(p.summary or '')[:60]}"
                for p in existing_plots
            ) or "（暂无）"

        data = await llm.generate_structured(
            project_id=project_id, module="chapter", prompt_key="task.plot_plan",
            system_prompt=llm.load_prompt("global.base_prompt", project_id),
            user_prompt=llm.load_prompt(
                "task.plot_plan", project_id=project_id,
                arc_goal=arc.goal or "", arc_conflict=arc.conflict or "",
                arc_summary=arc.summary or "", existing_plots=existing_plots_text,
                cast=cast, world_entries=world_text,
            ),
        )
        plots = (data or {}).get("plots") or []
        return [p for p in plots if isinstance(p, dict) and (p.get("title") or "").strip()]

    async def plan_chapters(self, arc_id: int, llm, target_words: int = 3000,
                            plots: list[dict] | None = None) -> tuple[list[dict], list[dict]]:
        """把一卷整体规划为 情节线 + 章节细纲，两段式：
        ① 先统一生成整卷情节线（每个情节跨多个章节）；
        ② 再以情节为纲逐章落实细纲——保证跨章情节连续、章节间不撞车。

        返回 (plots, chapters)（不写库，由调用方确认后入库）：
        plots:    [{title, summary, conflict, resolution}]
        chapters: [{plot_title, title, objective, scene, characters, plot(beats),
                    dialogue_hooks, humor_points, ending_hook, target_words}]
        """
        with self.db.session_ctx() as session:
            arc = ArcRepository(session).get(arc_id)
            if arc is None:
                raise ValueError(f"卷不存在: {arc_id}")
            project_id = arc.project_id
            arc_goal = arc.goal or ""
            arc_conflict = arc.conflict or ""
            arc_summary = arc.summary or ""
            existing = ChapterRepository(session).list_by_arc(arc_id)
            existing_text = "\n".join(
                f"第{c.seq + 1}章「{c.title or ''}」：{(c.objective or '')[:60]}"
                for c in existing
            ) or "（暂无）"
            from app.db.repositories.character_repo import CharacterRepository
            from app.db.repositories.world_repo import WorldEntryRepository
            chars = CharacterRepository(session).list_by_role(project_id)
            cast = "\n".join(f"- {c.name}（{c.role_type or '?'}）" for c in chars[:12]) or "（暂无）"
            entries = WorldEntryRepository(session).list_by_category(project_id, None)
            world_text = "\n".join(f"- {e.title}: {(e.content or '')[:80]}" for e in entries[:10]) or "（暂无）"

        # 第一阶段：整卷情节线（调用方未提供时现场生成）
        if plots is None:
            plots = await self.plan_plots(arc_id, llm)
        plots_text = "\n".join(
            f"情节{i + 1}「{p.get('title', '')}」：{p.get('summary', '')}"
            f"（冲突：{p.get('conflict', '')}）"
            for i, p in enumerate(plots)
        ) or "（未生成情节线，按卷大纲直接规划）"

        # 第二阶段：以情节为纲逐章落实细纲
        data = await llm.generate_structured(
            project_id=project_id, module="chapter", prompt_key="task.chapter_plan",
            system_prompt=llm.load_prompt("global.base_prompt", project_id),
            user_prompt=llm.load_prompt(
                "task.chapter_plan", project_id=project_id,
                arc_goal=arc_goal, arc_conflict=arc_conflict, arc_summary=arc_summary,
                plots=plots_text,
                existing_chapters=existing_text, cast=cast, world_entries=world_text,
                target_words=target_words,
            ),
        )
        chapters = (data or {}).get("chapters") or []
        chapters = [c for c in chapters if isinstance(c, dict) and (c.get("title") or "").strip()]
        return plots, chapters

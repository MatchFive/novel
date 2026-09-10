"""章节服务：章节 CRUD + 细纲节拍 + 字数统计。"""
from __future__ import annotations

import json

from app.db.repositories.chapter_repo import ChapterRepository
from app.db.session import Database
from app.db.unit_of_work import UnitOfWork


class ChapterService:
    def __init__(self, db: Database):
        self.db = db

    def create(self, *, project_id: int, arc_id: int | None = None, seq: int | None = None,
               title: str = "", objective: str = "", pov_char_id: int | None = None,
               plot_id: int | None = None):
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                repo = ChapterRepository(session)
                seq = seq if seq is not None else repo.next_seq(project_id)
                return repo.create(
                    project_id=project_id, arc_id=arc_id, seq=seq, title=title,
                    objective=objective, pov_char_id=pov_char_id, plot_id=plot_id,
                )

    def list(self, project_id: int) -> list:
        with self.db.session_ctx() as session:
            return ChapterRepository(session).list_by_project(project_id)

    def list_by_arc(self, arc_id: int) -> list:
        with self.db.session_ctx() as session:
            return ChapterRepository(session).list_by_arc(arc_id)

    def list_by_plot(self, plot_id: int) -> list:
        with self.db.session_ctx() as session:
            return ChapterRepository(session).list_by_plot(plot_id)

    def get(self, chapter_id: int):
        with self.db.session_ctx() as session:
            return ChapterRepository(session).get(chapter_id)

    def update(self, chapter_id: int, **fields) -> None:
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                ch = ChapterRepository(session).get(chapter_id)
                if ch is not None:
                    ChapterRepository(session).update(ch, **fields)
                    if "content" in fields:
                        ChapterRepository(session).update_word_count(ch)
                        # 状态自动流转：有正文就不再是 empty（仅当没显式指定 content_status 时）
                        if (ch.content or "").strip() and ch.content_status == "empty" \
                                and "content_status" not in fields:
                            ch.content_status = "draft"

    def delete(self, chapter_id: int) -> None:
        """删除章节：级联删除其事件日志与角色经历（避免外键失败）。"""
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                ch = ChapterRepository(session).get(chapter_id)
                if ch is None:
                    return
                from app.db.repositories.memory_repo import (
                    CharacterEventRepository,
                    StoryEventRepository,
                )
                for ev in StoryEventRepository(session).list_by_chapter(chapter_id):
                    StoryEventRepository(session).delete(ev)
                for ev in CharacterEventRepository(session).list_by_chapter(chapter_id):
                    CharacterEventRepository(session).delete(ev)
                ChapterRepository(session).delete(ch)

    # ---------------- 细纲节拍 ----------------
    def get_beats(self, chapter_id: int) -> list[dict]:
        ch = self.get(chapter_id)
        return ch.beats_json if ch and ch.beats_json else []

    def set_beats(self, chapter_id: int, beats: list[dict]) -> None:
        self.update(chapter_id, beats_json=beats,
                    outline_status="done" if beats else "none")

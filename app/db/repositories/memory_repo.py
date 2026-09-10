"""三层记忆仓储：事件日志 / 角色经历 / 信息项 / 知晓关系 / 承诺（M2 检索 / M3 提取使用）。"""
from __future__ import annotations

from sqlalchemy import select

from app.db.models import (
    CharacterEvent,
    CharacterKnowledge,
    InfoItem,
    Promise,
    StoryEvent,
)
from app.db.repositories.base import BaseRepository
from app.db.repositories.world_repo import fts_rowids


class StoryEventRepository(BaseRepository[StoryEvent]):
    model = StoryEvent

    def list_by_chapter(self, chapter_id: int) -> list[StoryEvent]:
        stmt = select(StoryEvent).where(StoryEvent.chapter_id == chapter_id).order_by(StoryEvent.seq)
        return list(self.session.scalars(stmt))

    def list_by_project(self, project_id: int) -> list[StoryEvent]:
        stmt = select(StoryEvent).where(StoryEvent.project_id == project_id).order_by(StoryEvent.id)
        return list(self.session.scalars(stmt))

    def search(self, keyword: str, limit: int = 6) -> list[StoryEvent]:
        if len(keyword.strip()) < 3:
            return []
        ids = fts_rowids(self.session, "story_events_fts", keyword, limit)
        return list(self.session.scalars(
            select(StoryEvent).where(StoryEvent.id.in_(ids))
        ))


class CharacterEventRepository(BaseRepository[CharacterEvent]):
    model = CharacterEvent

    def list_for_character(self, character_id: int, limit: int = 50) -> list[CharacterEvent]:
        stmt = (
            select(CharacterEvent)
            .where(CharacterEvent.character_id == character_id)
            .order_by(CharacterEvent.chapter_id.desc(), CharacterEvent.id.desc())
            .limit(limit)
        )
        return list(self.session.scalars(stmt))

    def list_by_chapter(self, chapter_id: int) -> list[CharacterEvent]:
        stmt = select(CharacterEvent).where(CharacterEvent.chapter_id == chapter_id)
        return list(self.session.scalars(stmt))

    def search_for_character(self, character_id: int, keyword: str, limit: int = 8) -> list[CharacterEvent]:
        """角色经历 + 关键词 FTS 检索（注入正文生成时用）。"""
        if len(keyword.strip()) < 3:
            return self.list_for_character(character_id, limit)
        ids = fts_rowids(self.session, "character_events_fts", keyword, limit)
        if not ids:
            return self.list_for_character(character_id, limit)
        stmt = select(CharacterEvent).where(
            CharacterEvent.id.in_(ids), CharacterEvent.character_id == character_id
        )
        return list(self.session.scalars(stmt))


class InfoItemRepository(BaseRepository[InfoItem]):
    model = InfoItem

    def list_by_project(self, project_id: int) -> list[InfoItem]:
        stmt = select(InfoItem).where(InfoItem.project_id == project_id)
        return list(self.session.scalars(stmt))


class KnowledgeRepository(BaseRepository[CharacterKnowledge]):
    model = CharacterKnowledge

    def list_for_character(self, character_id: int) -> list[CharacterKnowledge]:
        stmt = select(CharacterKnowledge).where(CharacterKnowledge.character_id == character_id)
        return list(self.session.scalars(stmt))

    def list_for_item(self, info_item_id: int) -> list[CharacterKnowledge]:
        stmt = select(CharacterKnowledge).where(CharacterKnowledge.info_item_id == info_item_id)
        return list(self.session.scalars(stmt))


class PromiseRepository(BaseRepository[Promise]):
    model = Promise

    def list_open(self, project_id: int) -> list[Promise]:
        stmt = select(Promise).where(
            Promise.project_id == project_id,
            Promise.status.in_(("open", "pending")),
        ).order_by(Promise.id)
        return list(self.session.scalars(stmt))

    def list_by_project(self, project_id: int) -> list[Promise]:
        stmt = select(Promise).where(Promise.project_id == project_id).order_by(Promise.id)
        return list(self.session.scalars(stmt))

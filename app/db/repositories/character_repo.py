"""角色仓储：角色卡 + 关系。"""
from __future__ import annotations

from sqlalchemy import select

from app.db.models import Character, CharacterRelation
from app.db.repositories.base import BaseRepository


class CharacterRepository(BaseRepository[Character]):
    model = Character

    def list_by_role(self, project_id: int, role_type: str | None = None) -> list[Character]:
        stmt = select(Character).where(Character.project_id == project_id)
        if role_type:
            stmt = stmt.where(Character.role_type == role_type)
        stmt = stmt.order_by(Character.updated_at.desc())
        return list(self.session.scalars(stmt))

    def search(self, project_id: int, keyword: str, limit: int = 10) -> list[Character]:
        like = f"%{keyword}%"
        stmt = (
            select(Character)
            .where(
                Character.project_id == project_id,
                (Character.name.like(like)) | (Character.aliases.like(like)),
            )
            .limit(limit)
        )
        return list(self.session.scalars(stmt))


class CharacterRelationRepository(BaseRepository[CharacterRelation]):
    model = CharacterRelation

    def list_by_project(self, project_id: int) -> list[CharacterRelation]:
        stmt = select(CharacterRelation).where(CharacterRelation.project_id == project_id)
        return list(self.session.scalars(stmt))

    def list_for_character(self, character_id: int) -> list[CharacterRelation]:
        stmt = select(CharacterRelation).where(
            (CharacterRelation.from_id == character_id) | (CharacterRelation.to_id == character_id)
        )
        return list(self.session.scalars(stmt))

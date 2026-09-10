"""角色服务。"""
from __future__ import annotations

from app.db.repositories.character_repo import CharacterRelationRepository, CharacterRepository
from app.db.session import Database
from app.db.unit_of_work import UnitOfWork


class CharacterService:
    def __init__(self, db: Database):
        self.db = db

    def create(self, *, project_id: int, name: str, role_type: str = "",
               aliases: str = "", profile: dict | None = None, arc: dict | None = None):
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                return CharacterRepository(session).create(
                    project_id=project_id, name=name, role_type=role_type,
                    aliases=aliases, profile=profile or {}, arc=arc,
                )

    def list(self, project_id: int) -> list:
        with self.db.session_ctx() as session:
            return CharacterRepository(session).list_by_role(project_id)

    def get(self, character_id: int):
        with self.db.session_ctx() as session:
            return CharacterRepository(session).get(character_id)

    def update(self, character_id: int, **fields) -> None:
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                c = CharacterRepository(session).get(character_id)
                if c is not None:
                    CharacterRepository(session).update(c, **fields)

    def delete(self, character_id: int) -> None:
        """删除角色：级联清理引用（章节POV置空/关系/经历/知识状态），避免外键失败。"""
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                c = CharacterRepository(session).get(character_id)
                if c is None:
                    return
                # 置空引用该角色的章节 POV
                from app.db.repositories.chapter_repo import ChapterRepository
                for ch in ChapterRepository(session).list_by_project(c.project_id):
                    if ch.pov_char_id == character_id:
                        ch.pov_char_id = None
                # 删除关系、经历、知识状态
                for r in CharacterRelationRepository(session).list_for_character(character_id):
                    CharacterRelationRepository(session).delete(r)
                from app.db.repositories.memory_repo import (
                    CharacterEventRepository,
                    KnowledgeRepository,
                )
                for ev in CharacterEventRepository(session).list_for_character(character_id, 100000):
                    CharacterEventRepository(session).delete(ev)
                for k in KnowledgeRepository(session).list_for_character(character_id):
                    KnowledgeRepository(session).delete(k)
                # 删除角色
                CharacterRepository(session).delete(c)

    # ---------------- 关系网 ----------------
    def list_relations(self, project_id: int) -> list:
        with self.db.session_ctx() as session:
            return CharacterRelationRepository(session).list_by_project(project_id)

    def add_relation(self, *, project_id: int, from_id: int, to_id: int,
                     relation: str, description: str = ""):
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                return CharacterRelationRepository(session).create(
                    project_id=project_id, from_id=from_id, to_id=to_id,
                    relation=relation, description=description,
                )

    def delete_relation(self, relation_id: int) -> None:
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                r = CharacterRelationRepository(session).get(relation_id)
                if r is not None:
                    CharacterRelationRepository(session).delete(r)

    def clear_relations(self, project_id: int) -> None:
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                for r in CharacterRelationRepository(session).list_by_project(project_id):
                    CharacterRelationRepository(session).delete(r)

    # ---------------- 清理重名角色 ----------------
    def dedupe_characters(self, project_id: int) -> int:
        """按姓名合并重名角色：保留最早创建的一个，删除其余同名。返回删除数。"""
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                chars = CharacterRepository(session).list_by_role(project_id)
                # 按 name 分组，保留每组第一个（按创建时间）
                groups: dict[str, list] = {}
                for c in chars:
                    groups.setdefault(c.name, []).append(c)
                removed = 0
                for name, members in groups.items():
                    if len(members) <= 1:
                        continue
                    members.sort(key=lambda c: c.created_at)
                    for dup in members[1:]:
                        CharacterRepository(session).delete(dup)
                        removed += 1
                return removed

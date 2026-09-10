"""记忆服务（设计文档 3.8.1 / 8.3）：LLM 提取 → 分层提交（事件/经历/知识/承诺）→ 查询。

提取为异步（调 LLM）；提交与查询为同步（仅经 Repository，遵循 4.4）。
后续可替换为 LangGraph 图（memory_extract_workflow），本服务保持对外接口不变。
"""
from __future__ import annotations

from app.db.models import Chapter
from app.db.repositories.character_repo import CharacterRepository
from app.db.repositories.chapter_repo import ChapterRepository
from app.db.repositories.memory_repo import (
    CharacterEventRepository,
    InfoItemRepository,
    KnowledgeRepository,
    PromiseRepository,
    StoryEventRepository,
)
from app.db.session import Database
from app.db.unit_of_work import UnitOfWork
from app.services.llm_service import LLMService


class MemoryService:
    """三层记忆的提取与查询。"""

    def __init__(self, db: Database, llm_service: LLMService):
        self.db = db
        self.llm = llm_service

    # ---------------- 提取（异步） ----------------
    async def extract_for_chapter(self, chapter_id: int, project_id: int) -> dict:
        """对单章执行记忆提取：LLM 抽取 → 校验 → 提交四类数据。"""
        with self.db.session_ctx() as session:
            ch = ChapterRepository(session).get(chapter_id)
            if ch is None or not (ch.content or "").strip():
                raise ValueError("章节不存在或正文为空，无法提取记忆")

        cast_cards = self._cast_cards(project_id)
        data = await self.llm.generate_structured(
            project_id=project_id,
            module="memory",
            prompt_key="task.memory_extract",
            system_prompt=self.llm.load_prompt("global.base_prompt", project_id),
            user_prompt=self.llm.load_prompt(
                "task.memory_extract", project_id=project_id,
                cast_cards=cast_cards,
                chapter_content=(ch.content or "")[-12000:],
            ),
        )
        if not isinstance(data, dict):
            raise ValueError("记忆提取未返回合法 JSON")

        self.commit_extraction(project_id, chapter_id, data)
        return data

    # ---------------- 提交（同步，事务内） ----------------
    def commit_extraction(self, project_id: int, chapter_id: int, data: dict) -> None:
        """把提取结果写入四类记忆表（幂等：先清空该章旧结果再写入）。"""
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                char_map = self._char_name_map(session, project_id)

                # 清旧
                for ev in CharacterEventRepository(session).list_by_chapter(chapter_id):
                    session.delete(ev)
                for ev in StoryEventRepository(session).list_by_chapter(chapter_id):
                    session.delete(ev)

                # ① 事件日志
                for e in data.get("events", []) or []:
                    if not e.get("summary"):
                        continue
                    char_ids = [char_map[c]["id"] for c in e.get("characters", []) if c in char_map]
                    StoryEventRepository(session).create(
                        project_id=project_id, chapter_id=chapter_id,
                        title=e.get("title", ""), summary=e.get("summary"),
                        location=e.get("location"), time_marker=e.get("time_marker"),
                        char_ids=char_ids,
                        info_gained=e.get("new_info") or [],
                    )

                # ② 角色经历（限知）
                for ce in data.get("character_events", []) or []:
                    cname = ce.get("character", "")
                    info = char_map.get(cname)
                    if info is None:
                        continue  # 角色不存在则跳过（提示用户先建角色）
                    CharacterEventRepository(session).create(
                        project_id=project_id, character_id=info["id"], chapter_id=chapter_id,
                        importance=3,
                        summary=ce.get("summary", ""), quotes=ce.get("quotes", ""),
                        acted=ce.get("acted", ""), felt=ce.get("felt", ""),
                        got_lost=ce.get("got_lost", ""), secrets=ce.get("secrets", ""),
                    )

                # ③ 信息项 + 知晓关系
                info_repo = InfoItemRepository(session)
                know_repo = KnowledgeRepository(session)
                for upd in data.get("info_updates", []) or []:
                    title = (upd.get("title") or "").strip()
                    if not title:
                        continue
                    # 复用同名信息项
                    item = None
                    for it in info_repo.list_by_project(project_id):
                        if it.title == title:
                            item = it
                            break
                    if item is None:
                        item = info_repo.create(project_id=project_id, title=title,
                                                content=upd.get("content", ""))
                        session.flush()
                    else:
                        info_repo.update(item, content=upd.get("content", "") or item.content)
                    for cname in upd.get("revealed_to", []) or []:
                        info = char_map.get(cname)
                        if info is None:
                            continue
                        # 更新（或创建）知晓关系：知道
                        existing = None
                        for k in know_repo.list_for_character(info["id"]):
                            if k.info_item_id == item.id:
                                existing = k
                                break
                        if existing is None:
                            know_repo.create(project_id=project_id, character_id=info["id"],
                                             info_item_id=item.id, knows=True,
                                             learned_chapter=chapter_id)
                        else:
                            know_repo.update(existing, knows=True, learned_chapter=chapter_id)

                # ④ 承诺/伏笔
                prom_repo = PromiseRepository(session)
                for pr in data.get("promise_updates", []) or []:
                    content = (pr.get("content") or "").strip()
                    if not content:
                        continue
                    status = pr.get("status", "new")
                    if status == "new":
                        by_char = char_map.get(pr.get("by_character", ""), {}).get("id")
                        prom_repo.create(
                            project_id=project_id,
                            kind=pr.get("kind", "promise"),
                            content=content,
                            by_char_id=by_char,
                            made_chapter=chapter_id,
                            status="open",
                        )
                    else:  # pending / fulfilled：更新同名 open 承诺
                        for p in prom_repo.list_open(project_id):
                            if p.content == content:
                                prom_repo.update(
                                    p, status="fulfilled" if status == "fulfilled" else "pending",
                                    resolution=pr.get("resolution", ""),
                                )
                                break

                # ⑤ 更新章节记忆状态
                ChapterRepository(session).update(
                    ChapterRepository(session).get(chapter_id),
                    memory_status="extracted",
                )

    # ---------------- 查询（供记忆库视图） ----------------
    def list_events(self, project_id: int) -> list:
        from app.db.repositories.memory_repo import StoryEventRepository
        with self.db.session_ctx() as session:
            return StoryEventRepository(session).list_by_project(project_id)

    def list_character_events(self, character_id: int, limit: int = 100) -> list:
        with self.db.session_ctx() as session:
            return CharacterEventRepository(session).list_for_character(character_id, limit)

    def list_promises(self, project_id: int) -> list:
        from app.db.repositories.memory_repo import PromiseRepository
        with self.db.session_ctx() as session:
            return PromiseRepository(session).list_by_project(project_id)

    def list_info_items(self, project_id: int) -> list:
        from app.db.repositories.memory_repo import InfoItemRepository
        with self.db.session_ctx() as session:
            return InfoItemRepository(session).list_by_project(project_id)

    # ---------------- 记忆修正（M3 收尾） ----------------
    def update_character_event(self, event_id: int, **fields) -> None:
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                ev = CharacterEventRepository(session).get(event_id)
                if ev is not None:
                    CharacterEventRepository(session).update(ev, **fields)

    def delete_story_event(self, event_id: int) -> None:
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                ev = StoryEventRepository(session).get(event_id)
                if ev is not None:
                    StoryEventRepository(session).delete(ev)

    def set_promise_status(self, promise_id: int, status: str, resolution: str = "") -> None:
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                p = PromiseRepository(session).get(promise_id)
                if p is not None:
                    PromiseRepository(session).update(p, status=status, resolution=resolution)

    # ---------------- 工具 ----------------
    def _cast_cards(self, project_id: int) -> str:
        with self.db.session_ctx() as session:
            chars = CharacterRepository(session).list_by_role(project_id)
        lines = []
        for c in chars:
            p = c.profile or {}
            lines.append(
                f"- {c.name}（{c.role_type or '?'}）："
                f"性格 {p.get('personality', '')}；"
                f"说话风格 {p.get('speech_style', '')}"
            )
        return "\n".join(lines) or "（暂无角色——若正文出现新角色，请先创建角色卡）"

    @staticmethod
    def _char_name_map(session, project_id: int) -> dict[str, dict]:
        from app.db.repositories.character_repo import CharacterRepository
        chars = CharacterRepository(session).list_by_role(project_id)
        return {c.name: {"id": c.id} for c in chars}

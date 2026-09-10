"""一致性扫描服务（设计文档 3.9）：六类问题扫描 → 入库 RevisionIssue。"""
from __future__ import annotations

import uuid

from app.db.models import Chapter
from app.db.repositories.character_repo import CharacterRepository
from app.db.repositories.chapter_repo import ChapterRepository
from app.db.repositories.memory_repo import (
    InfoItemRepository,
    KnowledgeRepository,
    PromiseRepository,
)
from app.db.repositories.revision_repo import RevisionIssueRepository
from app.db.repositories.world_repo import WorldEntryRepository
from app.db.session import Database
from app.db.unit_of_work import UnitOfWork
from app.services.llm_service import LLMService

BATCH_CHARS = 6000  # 每批扫描的正文长度
ISSUE_TYPES = ("timeline", "setting", "character", "memory_conflict", "knowledge_leak", "promise_break")


class CheckService:
    def __init__(self, db: Database, llm_service: LLMService):
        self.db = db
        self.llm = llm_service

    async def scan_project(self, project_id: int, on_progress=None) -> int:
        """全局一致性扫描：分批 → LLM 六类问题 → 写入库。返回新增问题数。"""
        with self.db.session_ctx() as session:
            chapters = ChapterRepository(session).list_by_project(project_id)
            draft_chapters = [c for c in chapters if (c.content or "").strip()]
            if not draft_chapters:
                raise ValueError("没有已写正文的章节，无法扫描")

            canonical = WorldEntryRepository(session).list_canonical(project_id)
            chars = CharacterRepository(session).list_by_role(project_id)

        ctx = self._build_context(project_id, canonical, chars)

        scan_id = uuid.uuid4().hex[:8]
        found = 0
        batches = self._split_batches(draft_chapters, BATCH_CHARS)
        for i, (batch_chapters, text) in enumerate(batches):
            if on_progress:
                on_progress(i + 1, len(batches))
            data = await self.llm.generate_structured(
                project_id=project_id,
                module="check",
                prompt_key="task.consistency_scan",
                system_prompt=self.llm.load_prompt("global.base_prompt", project_id),
                user_prompt=self.llm.load_prompt(
                    "task.consistency_scan", project_id=project_id,
                    canonical_entries=ctx["canonical"],
                    cast_cards=ctx["cast"],
                    character_memories=ctx["memories"],
                    knowledge_state=ctx["knowledge"],
                    open_promises=ctx["promises"],
                    draft_text=text,
                ),
            )
            issues = self._normalize_issues(data, batch_chapters)
            if issues:
                self._save_issues(project_id, scan_id, issues)
                found += len(issues)
        return found

    # ---------------- 上下文素材 ----------------
    def _build_context(self, project_id: int, canonical, chars) -> dict:
        with self.db.session_ctx() as session:
            # 角色经历档案（最近 20 条/角色）
            from app.db.repositories.memory_repo import CharacterEventRepository
            mem_lines = []
            for c in chars[:10]:
                evs = CharacterEventRepository(session).list_for_character(c.id, 20)
                for e in evs:
                    mem_lines.append(f"[{c.name}] {e.summary or ''}"
                                     + (f"（台词「{e.quotes}」）" if e.quotes else ""))
            # 知识状态
            items = {i.id: i for i in InfoItemRepository(session).list_by_project(project_id)}
            know_lines = []
            for item in items.values():
                for k in KnowledgeRepository(session).list_for_item(item.id):
                    from app.db.repositories.character_repo import CharacterRepository as CR
                    c = CR(session).get(k.character_id)
                    if c:
                        know_lines.append(f"{item.title}：{c.name} {'知道' if k.knows else '不知道'}")
            # 未回收承诺
            prom_lines = [p.content for p in PromiseRepository(session).list_open(project_id)]

        return {
            "canonical": "\n".join(f"- {e.title}: {e.content[:120]}" for e in canonical) or "（无铁律设定）",
            "cast": "\n".join(
                f"- {c.name}（{c.role_type or '?'}）：{c.profile.get('personality', '')}"
                for c in chars[:10]
            ) or "（无角色）",
            "memories": "\n".join(mem_lines[:40]) or "（无角色经历档案）",
            "knowledge": "\n".join(know_lines[:30]) or "（无知识状态）",
            "promises": "\n".join(prom_lines[:10]) or "（无未回收承诺）",
        }

    @staticmethod
    def _split_batches(chapters: list[Chapter], max_chars: int) -> list[tuple[list[Chapter], str]]:
        batches: list[tuple[list[Chapter], str]] = []
        cur: list[Chapter] = []
        cur_len = 0
        for c in chapters:
            text = c.content or ""
            if cur and cur_len + len(text) > max_chars:
                batches.append((cur, "\n\n".join(f"【第{cc.seq + 1}章】{cc.content or ''}" for cc in cur)))
                cur, cur_len = [], 0
            cur.append(c)
            cur_len += len(text)
        if cur:
            batches.append((cur, "\n\n".join(f"【第{cc.seq + 1}章】{cc.content or ''}" for cc in cur)))
        return batches

    @staticmethod
    def _normalize_issues(data, batch_chapters: list[Chapter]) -> list[dict]:
        if not isinstance(data, dict) or "issues" not in data:
            return []
        seq_map = {c.seq + 1: c for c in batch_chapters}
        out = []
        for it in data["issues"] or []:
            if not isinstance(it, dict) or not it.get("issue"):
                continue
            itype = it.get("type", "setting")
            if itype not in ISSUE_TYPES:
                itype = "setting"
            chapter_no = it.get("chapter")
            chapter = seq_map.get(chapter_no) or (batch_chapters[-1] if batch_chapters else None)
            out.append({
                "chapter_id": chapter.id if chapter else None,
                "quote": it.get("quote", ""),
                "issue_type": itype,
                "issue": it["issue"],
                "suggestion": it.get("suggestion", ""),
            })
        return out

    def _save_issues(self, project_id: int, scan_id: str, issues: list[dict]) -> None:
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                repo = RevisionIssueRepository(session)
                for it in issues:
                    repo.create(project_id=project_id, scan_id=scan_id, **it)

    # ---------------- 问题状态 ----------------
    def list_issues(self, project_id: int, status: str | None = None) -> list:
        with self.db.session_ctx() as session:
            return RevisionIssueRepository(session).list_by_project(project_id, status)

    def set_issue_status(self, issue_id: int, status: str) -> None:
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                repo = RevisionIssueRepository(session)
                issue = repo.get(issue_id)
                if issue is not None:
                    repo.update(issue, status=status)

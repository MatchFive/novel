"""设定沉淀服务（设计文档 3.12）：生成内容 → 新设定检测 → 候选 → 确认入库。"""
from __future__ import annotations

from app.db.models import ProposedEntry
from app.db.repositories.canon_repo import ProposedEntryRepository
from app.db.repositories.world_repo import WorldEntryRepository
from app.db.session import Database
from app.db.unit_of_work import UnitOfWork
from app.services.llm_service import LLMService


class CanonCaptureService:
    def __init__(self, db: Database, llm_service: LLMService):
        self.db = db
        self.llm = llm_service

    # ---------------- 检测（异步） ----------------
    async def extract_candidates(self, project_id: int, text: str,
                                 source_module: str = "draft",
                                 source_chapter: int | None = None) -> int:
        """从生成内容检测新设定并生成候选（去重入库）。返回候选数。"""
        with self.db.session_ctx() as session:
            existing = WorldEntryRepository(session).list_by_project(project_id)
            existing_text = "\n".join(f"- {e.title}" for e in existing) or "（空）"

        data = await self.llm.generate_structured(
            project_id=project_id,
            module="canon",
            prompt_key="task.canon_extract",
            system_prompt=self.llm.load_prompt("global.base_prompt", project_id),
            user_prompt=self.llm.load_prompt(
                "task.canon_extract", project_id=project_id,
                existing_entries=existing_text,
                generated_text=text[-8000:],
            ),
        )
        candidates = data if isinstance(data, list) else data.get("candidates", []) if isinstance(data, dict) else []
        added = 0
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                repo = ProposedEntryRepository(session)
                existing_titles = {e.title for e in existing}
                for cand in candidates or []:
                    if not isinstance(cand, dict):
                        continue
                    title = (cand.get("title") or "").strip()
                    if not title or title in existing_titles:
                        continue
                    repo.create(
                        project_id=project_id,
                        source_module=source_module,
                        source_chapter=source_chapter,
                        source_snippet=cand.get("source_snippet", ""),
                        title=title,
                        content=cand.get("content", ""),
                        category=cand.get("category", "其他"),
                        importance=int(cand.get("importance", 3)),
                        status="pending",
                    )
                    existing_titles.add(title)
                    added += 1
        return added

    # ---------------- 候选管理 ----------------
    def list_pending(self, project_id: int) -> list[ProposedEntry]:
        with self.db.session_ctx() as session:
            return ProposedEntryRepository(session).list_pending(project_id)

    def accept(self, candidate_id: int, *, title: str | None = None,
               content: str | None = None, category: str | None = None,
               importance: int | None = None, canonical: bool = False) -> int | None:
        """接受候选：写入 world_entries 并标记 accepted。返回新条目 id。"""
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                repo = ProposedEntryRepository(session)
                cand = repo.get(candidate_id)
                if cand is None:
                    return None
                entry = WorldEntryRepository(session).create(
                    project_id=cand.project_id,
                    category_id=None,
                    title=(title or cand.title).strip(),
                    content=(content if content is not None else cand.content) or "",
                    tags="",
                    importance=importance if importance is not None else cand.importance,
                    canonical=canonical,
                )
                session.flush()  # 取得 entry.id
                repo.update(cand, status="accepted")
                return entry.id

    def reject(self, candidate_id: int) -> None:
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                repo = ProposedEntryRepository(session)
                cand = repo.get(candidate_id)
                if cand is not None:
                    repo.update(cand, status="rejected")

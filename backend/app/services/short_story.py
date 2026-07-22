"""短篇小说六步法服务。"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.llm_client import LLMClient
from app.core.errors import NotFoundError
from app.models import ShortSetting, ShortChapter
from app.services.prompts.short_story import (
    TITLE_PROMPT, OPENING_HOOK_PROMPT, PLAN_PROMPT, DETAIL_PROMPT,
    CHAPTER_PROMPT, INTEGRATION_PROMPT,
)


class ShortStoryService:
    def __init__(self, db: AsyncSession, llm: LLMClient):
        self.db = db
        self.llm = llm

    async def _get_or_create(self, project_id: str) -> ShortSetting:
        s = await self.db.get(ShortSetting, project_id)
        if not s:
            s = ShortSetting(id=project_id)
            self.db.add(s)
            await self.db.commit()
            await self.db.refresh(s)
        return s

    async def get(self, project_id: str) -> dict:
        s = await self._get_or_create(project_id)
        return self._serialize(s)

    async def update(self, project_id: str, data: dict) -> dict:
        s = await self._get_or_create(project_id)
        for k, v in data.items():
            if v is not None:
                setattr(s, k, v)
        await self.db.commit()
        await self.db.refresh(s)
        return self._serialize(s)

    @staticmethod
    def _serialize(s: ShortSetting) -> dict:
        return {
            "project_id": s.id,
            "step": s.step,
            "core_hook": s.core_hook,
            "plans": s.plans,
            "selected_plan": s.selected_plan,
            "detail_plan": s.detail_plan,
            "chapters_plan": s.chapters_plan,
            "writing": s.writing,
            "integration": s.integration,
            "updated_at": s.updated_at.isoformat() if s.updated_at else None,
        }

    # ---------------- 六步法 ----------------
    async def set_core_hook(self, project_id: str, hook: str) -> dict:
        return await self.update(project_id, {"core_hook": hook, "step": 1})

    async def generate_plans(self, project_id: str) -> dict:
        s = await self._get_or_create(project_id)
        msgs = [{"role": "system", "content": PLAN_PROMPT},
                {"role": "user", "content": f"爽点/核心设定：\n{s.core_hook}"}]
        raw = await self.llm.parse_llm_json(msgs)
        plans = raw.get("plans", []) if isinstance(raw, dict) else []
        return await self.update(project_id, {"plans": plans, "step": 2})

    async def select_plan(self, project_id: str, index: int) -> dict:
        s = await self._get_or_create(project_id)
        plans = s.plans or []
        if index < 0 or index >= len(plans):
            raise NotFoundError("方案索引越界")
        return await self.update(project_id, {"selected_plan": plans[index], "step": 3})

    async def generate_detail_plan(self, project_id: str) -> dict:
        s = await self._get_or_create(project_id)
        plan = s.selected_plan or {}
        msgs = [{"role": "system", "content": DETAIL_PROMPT},
                {"role": "user", "content": f"爽点：{s.core_hook}\n选定方案：{plan}"}]
        detail = await self.llm.chat(msgs)
        return await self.update(project_id, {"detail_plan": detail, "step": 4})

    async def generate_chapters(self, project_id: str) -> dict:
        s = await self._get_or_create(project_id)
        msgs = [{"role": "system", "content": CHAPTER_PROMPT},
                {"role": "user", "content": f"详细规划：\n{s.detail_plan}"}]
        raw = await self.llm.parse_llm_json(msgs)
        chapters = raw.get("chapters", []) if isinstance(raw, dict) else []
        return await self.update(project_id, {"chapters_plan": chapters, "step": 5})

    async def write_chapter(self, project_id: str, index: int) -> dict:
        s = await self._get_or_create(project_id)
        chapters = s.chapters_plan or []
        if index < 0 or index >= len(chapters):
            raise NotFoundError("章节索引越界")
        title = chapters[index].get("title", f"第{index+1}章") if isinstance(chapters[index], dict) else str(chapters[index])
        prev = "\n".join([str(c) for c in (s.writing or [])])
        msgs = [{"role": "system", "content": CHAPTER_PROMPT},
                {"role": "user", "content": f"章节标题：{title}\n前文：\n{prev}"}]
        content = await self.llm.chat(msgs)
        writing = list(s.writing or [])
        writing.append({"title": title, "content": content})
        return await self.update(project_id, {"writing": writing, "step": 6})

    async def integrate(self, project_id: str) -> dict:
        s = await self._get_or_create(project_id)
        full = "\n\n".join([f"【{w.get('title','')}】\n{w.get('content','')}" for w in (s.writing or [])])
        msgs = [{"role": "system", "content": INTEGRATION_PROMPT},
                {"role": "user", "content": f"整合以下章节内容：\n{full}"}]
        integrated = await self.llm.chat(msgs)
        return await self.update(project_id, {"integration": integrated})


async def get_title_suggestion(llm: LLMClient, hook: str) -> str:
    msgs = [{"role": "system", "content": TITLE_PROMPT},
            {"role": "user", "content": hook}]
    return await llm.chat(msgs)

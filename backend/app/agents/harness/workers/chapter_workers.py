"""多阶段章节生成 Worker：总纲 → 剧情节点 → 章节分配 → 细纲 → 正文。"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app import repositories as repo
from app.agents.harness.context_builder import ContextBuilder
from app.agents.harness.prompts.chapter_generation import (
    assignment_prompt,
    broad_outline_prompt,
    chapter_outline_prompt,
    chapter_review_prompt,
    chapter_text_prompt,
    plot_nodes_prompt,
)
from app.agents.harness.worker_base import WorkerBase

logger = logging.getLogger(__name__)


def _project_summary(context: dict) -> str:
    project = context.get("project") or {}
    return (
        context.get("project_summary")
        or f"{project.get('title', '未命名项目')}\n{project.get('description', '')}".strip()
        or "未提供项目摘要"
    )


def _parse_chapter_number(goal: str) -> int | None:
    """从目标中解析章节序号，例如“生成第 1 章细纲”。"""
    match = re.search(r"第\s*(\d+)\s*章", goal)
    if match:
        return int(match.group(1))
    return None


def _find_target_chapter(goal: str, context: dict, chapters: list[dict]) -> dict | None:
    """从 entity_id、目标文本或唯一章节中定位目标章节。"""
    entity_type = context.get("entity_type")
    entity_id = context.get("entity_id")
    if entity_type == "chapter" and entity_id:
        for ch in chapters:
            if ch.get("id") == entity_id:
                return ch

    order = _parse_chapter_number(goal)
    if order is not None:
        for ch in chapters:
            if ch.get("order") == order:
                return ch

    if len(chapters) == 1:
        return chapters[0]

    return None


def _broad_outline_text(outlines: list[dict]) -> str:
    broads = [o for o in outlines if o.get("type") == "broad"]
    if not broads:
        broads = outlines
    if not broads:
        return "（暂无总纲）"
    parts = []
    for o in broads:
        parts.append(f"id={o.get('id')} title={o.get('title')}\ncontent={o.get('content', '')}")
    return "\n\n".join(parts)


def _previous_chapter(chapter: dict, chapters: list[dict]) -> dict | None:
    sorted_chapters = sorted(chapters, key=lambda c: c.get("order", 0))
    idx = next(
        (i for i, c in enumerate(sorted_chapters) if c.get("id") == chapter.get("id")),
        -1,
    )
    if idx > 0:
        return sorted_chapters[idx - 1]
    return None


def _previous_chapter_summary(prev: dict | None) -> str:
    if not prev:
        return "（无）"
    text = prev.get("content", "") or prev.get("detailed_outline", "") or prev.get("title", "")
    if len(text) > 500:
        text = text[-500:]
    return text or "（无）"


def _previous_chapter_text_tail(prev: dict | None, tail_len: int = 800) -> str:
    if not prev:
        return "（无）"
    text = prev.get("content", "")
    if len(text) > tail_len:
        return "..." + text[-tail_len:]
    return text or "（无）"


def _active_foreshadows(foreshadows: list[dict]) -> list[dict]:
    return [f for f in foreshadows if f.get("state") == "pending"]


def _assigned_plot_nodes(plot_nodes: list[dict], chapter_id: str | None) -> list[dict]:
    if not chapter_id:
        return []
    return [p for p in plot_nodes if p.get("chapter_id") == chapter_id]


def _result_to_response(result: Any, stage: str) -> dict:
    """将 LLM 解析结果统一转换为 {changes, stage, error?}。"""
    if isinstance(result, dict) and "changes" in result:
        return {"changes": result["changes"], "stage": stage}
    return {
        "changes": [],
        "stage": stage,
        "error": "无法解析 worker 输出",
        "raw": result,
    }


def _build_context_entities(context: dict) -> dict[str, list[dict]]:
    """将 harness 传入的 context 映射为 ContextBuilder 期望的实体键。"""
    return {
        "character": context.get("characters") or [],
        "outline": context.get("outlines") or [],
        "plot": context.get("plot") or [],
        "foreshadow": context.get("foreshadows") or [],
        "world": context.get("world") or [],
    }


def _user_prompt(goal: str, related: str = "") -> str:
    parts = []
    if related:
        parts.append(f"【相关上下文】\n{related}")
    parts.append(f"【用户目标】\n{goal}")
    return "\n\n".join(parts)


async def _generate_json(llm, messages: list[dict]) -> Any:
    """直接调用 LLM 并解析 JSON 结果。"""
    try:
        return await llm.parse_llm_json(messages)
    except Exception:
        logger.exception("LLM JSON generation failed")
        return None


class BroadOutlineWorker(WorkerBase):
    worker_name = "broad_outline"

    async def run(
        self,
        goal: str,
        context: dict,
        history_context: list[dict] | None = None,
    ) -> dict:
        project_id = context.get("project_id")
        if not project_id:
            return {"changes": [], "stage": "broad_outline", "error": "缺少 project_id"}

        existing_outlines = await repo.list_outlines(self.db, project_id)

        related = ""
        builder = ContextBuilder(self.db, self.llm, entities=_build_context_entities(context))
        try:
            related = await builder.build(goal, "outline")
        except Exception:
            logger.exception("ContextBuilder failed for broad_outline")

        prompt_context = {
            "project_summary": _project_summary(context),
            "existing_outlines": existing_outlines,
            "characters": context.get("characters") or [],
            "world": context.get("world") or [],
            "plot_nodes": context.get("plot") or [],
        }
        system = broad_outline_prompt(prompt_context)
        user_prompt = _user_prompt(goal, related)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ]
        if history_context:
            messages = history_context + messages
        result = await _generate_json(self.llm, messages)
        return _result_to_response(result, "broad_outline")


class PlotNodesWorker(WorkerBase):
    worker_name = "plot_nodes"

    async def run(
        self,
        goal: str,
        context: dict,
        history_context: list[dict] | None = None,
    ) -> dict:
        project_id = context.get("project_id")
        if not project_id:
            return {"changes": [], "stage": "plot_nodes", "error": "缺少 project_id"}

        outlines = await repo.list_outlines(self.db, project_id)
        plot_nodes = await repo.list_plot(self.db, project_id)

        related = ""
        builder = ContextBuilder(self.db, self.llm, entities=_build_context_entities(context))
        try:
            related = await builder.build(goal, "plot")
        except Exception:
            logger.exception("ContextBuilder failed for plot_nodes")

        prompt_context = {
            "broad_outline": _broad_outline_text(outlines),
            "existing_plot_nodes": plot_nodes,
            "characters": context.get("characters") or [],
            "world": context.get("world") or [],
        }
        system = plot_nodes_prompt(prompt_context)
        user_prompt = _user_prompt(goal, related)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ]
        if history_context:
            messages = history_context + messages
        result = await _generate_json(self.llm, messages)
        return _result_to_response(result, "plot_nodes")


class AssignmentWorker(WorkerBase):
    worker_name = "assignment"

    async def run(
        self,
        goal: str,
        context: dict,
        history_context: list[dict] | None = None,
    ) -> dict:
        project_id = context.get("project_id")
        if not project_id:
            return {"changes": [], "stage": "assignment", "error": "缺少 project_id"}

        plot_nodes = await repo.list_plot(self.db, project_id)
        chapters = await repo.list_chapters(self.db, project_id)

        related = ""
        builder = ContextBuilder(self.db, self.llm, entities=_build_context_entities(context))
        try:
            related = await builder.build(goal, "chapter")
        except Exception:
            logger.exception("ContextBuilder failed for assignment")

        prompt_context = {
            "plot_nodes": plot_nodes,
            "existing_chapters": chapters,
        }
        system = assignment_prompt(prompt_context)
        user_prompt = _user_prompt(goal, related)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ]
        if history_context:
            messages = history_context + messages
        result = await _generate_json(self.llm, messages)
        return _result_to_response(result, "assignment")


class ChapterOutlineWorker(WorkerBase):
    worker_name = "chapter_outline"

    async def run(
        self,
        goal: str,
        context: dict,
        history_context: list[dict] | None = None,
    ) -> dict:
        project_id = context.get("project_id")
        if not project_id:
            return {"changes": [], "stage": "chapter_outline", "error": "缺少 project_id"}

        chapters = await repo.list_chapters(self.db, project_id)
        chapter = _find_target_chapter(goal, context, chapters)
        if not chapter:
            if _parse_chapter_number(goal) is None:
                return {
                    "changes": [],
                    "stage": "chapter_outline",
                    "error": "无法识别目标章节",
                }
            return {"changes": [], "stage": "chapter_outline", "error": "未找到目标章节"}

        outlines = await repo.list_outlines(self.db, project_id)
        plot_nodes = await repo.list_plot(self.db, project_id)
        characters = context.get("characters") or []
        world = context.get("world") or []
        foreshadows = context.get("foreshadows") or []
        chapter_id = chapter.get("id")

        related = ""
        builder = ContextBuilder(self.db, self.llm, entities=_build_context_entities(context))
        try:
            related = await builder.build(goal, "chapter")
        except Exception:
            logger.exception("ContextBuilder failed for chapter_outline")

        prev = _previous_chapter(chapter, chapters)
        prompt_context = {
            "chapter": chapter,
            "broad_outline": _broad_outline_text(outlines),
            "assigned_plot_nodes": _assigned_plot_nodes(plot_nodes, chapter_id),
            "characters": characters,
            "world": world,
            "previous_chapter_summary": _previous_chapter_summary(prev),
            "active_foreshadows": _active_foreshadows(foreshadows),
        }
        system = chapter_outline_prompt(prompt_context)
        user_prompt = _user_prompt(goal, related)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ]
        if history_context:
            messages = history_context + messages
        result = await _generate_json(self.llm, messages)
        return _result_to_response(result, "chapter_outline")


class ChapterTextWorker(WorkerBase):
    worker_name = "chapter_text"

    async def run(
        self,
        goal: str,
        context: dict,
        history_context: list[dict] | None = None,
    ) -> dict:
        project_id = context.get("project_id")
        if not project_id:
            return {"changes": [], "stage": "chapter_text", "error": "缺少 project_id"}

        chapters = await repo.list_chapters(self.db, project_id)
        chapter = _find_target_chapter(goal, context, chapters)
        if not chapter:
            if _parse_chapter_number(goal) is None:
                return {
                    "changes": [],
                    "stage": "chapter_text",
                    "error": "无法识别目标章节",
                }
            return {"changes": [], "stage": "chapter_text", "error": "未找到目标章节"}

        plot_nodes = await repo.list_plot(self.db, project_id)
        characters = context.get("characters") or []
        world = context.get("world") or []
        foreshadows = context.get("foreshadows") or []
        chapter_id = chapter.get("id")

        assigned = _assigned_plot_nodes(plot_nodes, chapter_id)
        prev = _previous_chapter(chapter, chapters)
        prev_tail = _previous_chapter_text_tail(prev)
        active = _active_foreshadows(foreshadows)

        prompt_context = {
            "chapter": chapter,
            "detailed_outline": chapter.get("detailed_outline", ""),
            "assigned_plot_nodes": assigned,
            "characters": characters,
            "world": world,
            "previous_chapter_text_tail": prev_tail,
            "active_foreshadows": active,
        }
        system = chapter_text_prompt(prompt_context)
        user_prompt = _user_prompt(goal)
        messages = [{"role": "system", "content": system}]
        if history_context:
            messages.extend(history_context)
        messages.append({"role": "user", "content": user_prompt})

        result = await self._generate_text(messages)
        if not result:
            return {"changes": [], "stage": "chapter_text", "error": "正文生成解析失败"}

        review_issues = await self._review_text(result, chapter, characters, world, active)
        if review_issues:
            logger.warning("Chapter review issues: %s", review_issues)
            retry_messages = [{"role": "system", "content": system}]
            if history_context:
                retry_messages.extend(history_context)
            retry_user = (
                f"{user_prompt}\n\n【审校反馈】\n"
                + "\n".join(f"- {issue}" for issue in review_issues)
            )
            retry_messages.append({"role": "user", "content": retry_user})
            result = await self._generate_text(retry_messages) or result

        return _result_to_response(result, "chapter_text")

    async def _generate_text(self, messages: list[dict]) -> dict | None:
        try:
            raw = await self.llm.parse_llm_json(messages)
            if isinstance(raw, dict):
                return raw
            if isinstance(raw, str):
                try:
                    return json.loads(raw)
                except Exception:
                    pass
        except Exception:
            logger.exception("Chapter text generation failed")
        return None

    async def _review_text(
        self,
        text_result: dict,
        chapter: dict,
        characters: list[dict],
        world: list[dict],
        active_foreshadows: list[dict],
    ) -> list[str]:
        changes = text_result.get("changes") or []
        if not changes:
            return []
        content = changes[0].get("fields", {}).get("content", "")
        if not content:
            return []
        review_context = {
            "chapter_text": content,
            "chapter": chapter,
            "characters": characters,
            "world": world,
            "active_foreshadows": active_foreshadows,
        }
        system = chapter_review_prompt(review_context)
        try:
            raw = await self.llm.parse_llm_json([{"role": "system", "content": system}])
            if isinstance(raw, dict):
                if raw.get("ok"):
                    return []
                issues = raw.get("issues")
                if isinstance(issues, list):
                    return [str(i) for i in issues]
            return []
        except Exception:
            logger.exception("Chapter review failed")
            return []

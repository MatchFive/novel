"""多阶段章节生成 Worker：总纲 → 剧情节点 → 章节分配 → 细纲 → 正文。"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy import select

from app import repositories as repo
from app.agents.harness.context_builder import ContextBuilder
from app.agents.harness.prompts.chapter_generation import (
    assignment_prompt,
    broad_outline_prompt,
    chapter_outline_prompt,
    chapter_rating_prompt,
    chapter_review_prompt,
    chapter_segment_user_prompt,
    chapter_text_prompt,
    plot_nodes_prompt,
    RATING_LABELS,
)
from app.agents.harness.worker_base import WorkerBase
from app.models import UserSetting

logger = logging.getLogger(__name__)


async def _character_memories_for_chapter(
    db,
    chapter: dict,
    characters: list[dict],
) -> dict[str, list[dict]]:
    """为章节中出场的每个角色查询其已知记忆。基于正文和细纲判断出场。"""
    from app.agents.tools import read_character_memories

    text = "\n".join(str(chapter.get(k) or "") for k in ("content", "detailed_outline"))
    appeared_names = {c.get("name", "").strip() for c in characters if c.get("name")}
    result: dict[str, list[dict]] = {}
    for c in characters:
        name = c.get("name", "").strip()
        if not name or name not in appeared_names:
            continue
        if name not in text:
            continue
        cid = c.get("id")
        memories = await read_character_memories(db, cid, limit=30)
        if memories:
            result[cid] = memories
    return result


def _project_summary(context: dict) -> str:
    project = context.get("project") or {}
    return (
        context.get("project_summary")
        or f"{project.get('title', '未命名项目')}\n{project.get('description', '')}".strip()
        or "未提供项目摘要"
    )


def _parse_chapter_number(goal: str) -> int | None:
    """从目标中解析单个章节序号，例如“生成第 1 章细纲”。"""
    match = re.search(r"第\s*(\d+)\s*章", goal)
    if match:
        return int(match.group(1))
    return None


def _parse_chapter_numbers(goal: str) -> list[int] | None:
    """解析目标中的章节序号列表：支持“前三章”“第1章到第3章”“第5章”。"""
    single = _parse_chapter_number(goal)
    if single is not None:
        return [single]
    range_match = re.search(r"第\s*(\d+)\s*章\s*(?:到|至)\s*第\s*(\d+)\s*章", goal)
    if range_match:
        start, end = int(range_match.group(1)), int(range_match.group(2))
        return list(range(start, end + 1))
    prefix_match = re.search(r"前\s*(\d+)\s*章", goal)
    if prefix_match:
        return list(range(1, int(prefix_match.group(1)) + 1))
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


def _volume_outline_text(outlines: list[dict], chapter_order: int) -> str:
    """根据章节序号定位其所属的卷大纲，返回包含时期和卷信息的文本。"""
    chapter_num = chapter_order + 1
    for o in outlines:
        if o.get("type") != "volume":
            continue
        start = o.get("chapter_start")
        end = o.get("chapter_end")
        if start is None or end is None:
            continue
        if start <= chapter_num <= end:
            parent = next((p for p in outlines if p.get("id") == o.get("parent_id")), None)
            parts = []
            if parent:
                parts.append(f"时期《{parent.get('title', '')}》：{parent.get('content', '')}")
            parts.append(f"卷《{o.get('title', '')}》（第 {start}-{end} 章）：{o.get('content', '')}")
            return "\n\n".join(parts)
    # fallback: 列出时期标题
    periods = [o for o in outlines if o.get("type") == "period"]
    if periods:
        return "未找到本卷大纲，现有时期：" + " / ".join(p.get("title", "") for p in periods)
    return "（暂无卷大纲）"


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


async def _generation_settings(db) -> tuple[int, str]:
    """读取生成相关用户设置：(每章目标字数, 尺度等级)。"""
    res = await db.execute(select(UserSetting))
    s = res.scalars().first()
    target = s.chapter_target_words if s and s.chapter_target_words else 2500
    rating = s.content_rating if s and s.content_rating else "standard"
    return target, rating


def _chapter_summaries_chain(chapter: dict, chapters: list[dict], limit: int = 2000) -> str:
    """目标章节之前各章的一行摘要链（细纲优先，其次正文前 200 字），总量钳制 limit。"""
    prev = [c for c in sorted(chapters, key=lambda c: c.get("order", 0))
            if c.get("order", 0) < chapter.get("order", 0)]
    lines = []
    for c in prev:
        text = (c.get("detailed_outline") or c.get("content") or "")[:200]
        if text:
            lines.append(f"第{c.get('order', 0) + 1}章《{c.get('title', '')}》：{text}")
    chain = "\n".join(lines)
    if not chain:
        return "（无）"
    return chain[-limit:] if len(chain) > limit else chain


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
        builder = ContextBuilder(self.db, self.llm, entities=context)
        try:
            related = await builder.build(goal, focus_entity_type="outline")
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
        builder = ContextBuilder(self.db, self.llm, entities=context)
        try:
            related = await builder.build(goal, focus_entity_type="plot")
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
        builder = ContextBuilder(self.db, self.llm, entities=context)
        try:
            related = await builder.build(goal, focus_entity_type="chapter")
        except Exception:
            logger.exception("ContextBuilder failed for assignment")

        target_words, _rating = await _generation_settings(self.db)
        prompt_context = {
            "plot_nodes": plot_nodes,
            "existing_chapters": chapters,
            "target_words": target_words,
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
        orders = _parse_chapter_numbers(goal)

        # 如果没有解析出序号，但上下文携带了 entity_id，则取该章节的 order
        if orders is None and context.get("entity_type") == "chapter" and context.get("entity_id"):
            target = next(
                (c for c in chapters if c.get("id") == context.get("entity_id")),
                None,
            )
            if target is not None:
                orders = [target.get("order", 0) + 1]

        if not orders:
            return {
                "changes": [],
                "stage": "chapter_outline",
                "error": "无法识别目标章节，请使用“生成第 X 章细纲”或“生成前三章细纲”",
            }

        outlines = await repo.list_outlines(self.db, project_id)
        plot_nodes = await repo.list_plot(self.db, project_id)
        characters = context.get("characters") or []
        world = context.get("world") or []
        foreshadows = context.get("foreshadows") or []

        related = ""
        builder = ContextBuilder(self.db, self.llm, entities=context)
        try:
            related = await builder.build(goal, focus_entity_type="chapter")
        except Exception:
            logger.exception("ContextBuilder failed for chapter_outline")

        target_words, _rating = await _generation_settings(self.db)

        all_changes: list[dict] = []
        previous_chapter: dict | None = None

        for chapter_num in sorted(orders):
            chapter_order = chapter_num - 1
            existing = next(
                (c for c in chapters if c.get("order") == chapter_order),
                None,
            )
            chapter_for_prompt = existing or {
                "id": None,
                "project_id": project_id,
                "order": chapter_order,
                "title": f"第 {chapter_num} 章",
                "content": "",
                "detailed_outline": "",
                "status": "draft",
            }

            volume_outline = _volume_outline_text(outlines, chapter_order)
            prompt_context = {
                "chapter": chapter_for_prompt,
                "broad_outline": _broad_outline_text(outlines),
                "volume_outline": volume_outline,
                "assigned_plot_nodes": _assigned_plot_nodes(plot_nodes, chapter_for_prompt.get("id")),
                "characters": characters,
                "world": world,
                "previous_chapter_summary": _previous_chapter_summary(previous_chapter),
                "active_foreshadows": _active_foreshadows(foreshadows),
                "target_words": target_words,
            }
            system = chapter_outline_prompt(prompt_context)
            user_prompt = _user_prompt(
                f"生成第 {chapter_num} 章细纲",
                related,
            )
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ]
            if history_context:
                messages = history_context + messages

            result = await _generate_json(self.llm, messages)
            raw_changes: list[dict] = []
            if isinstance(result, dict):
                raw_changes = result.get("changes") or []
            elif isinstance(result, list):
                raw_changes = result

            for raw in raw_changes:
                fields = dict(raw.get("fields") or {})
                if not fields.get("detailed_outline"):
                    continue
                if not fields.get("title"):
                    fields["title"] = chapter_for_prompt.get("title") or f"第 {chapter_num} 章"
                fields["order"] = chapter_order
                fields["status"] = "reviewed"
                if existing:
                    all_changes.append({
                        "action": "update",
                        "entity_id": existing["id"],
                        "fields": fields,
                    })
                else:
                    # 新增章节：去掉 worker 可能带上的 id/project_id，由 change_apply 写入
                    fields.pop("id", None)
                    fields.pop("project_id", None)
                    all_changes.append({
                        "action": "add",
                        "entity_id": None,
                        "fields": fields,
                    })

            # 下一章的“前文”基于本章（按顺序）
            previous_chapter = {
                **chapter_for_prompt,
                "detailed_outline": raw_changes[0]["fields"]["detailed_outline"]
                if raw_changes and raw_changes[0].get("fields", {}).get("detailed_outline")
                else "",
            }

        return {"changes": all_changes, "stage": "chapter_outline"}


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
                return {"changes": [], "stage": "chapter_text", "error": "无法识别目标章节"}
            return {"changes": [], "stage": "chapter_text", "error": "未找到目标章节"}

        plot_nodes = await repo.list_plot(self.db, project_id)
        characters = context.get("characters") or []
        world = context.get("world") or []
        foreshadows = context.get("foreshadows") or []
        chapter_id = chapter.get("id")
        outlines = await repo.list_outlines(self.db, project_id)

        target_words, rating = await _generation_settings(self.db)
        notes: list[str] = []

        assigned = _assigned_plot_nodes(plot_nodes, chapter_id)
        prev = _previous_chapter(chapter, chapters)
        prev_tail = _previous_chapter_text_tail(prev)
        active = _active_foreshadows(foreshadows)
        summaries_chain = _chapter_summaries_chain(chapter, chapters)
        chapter_order = chapter.get("order", 0)
        volume_outline = _volume_outline_text(outlines, chapter_order)

        character_memories = await _character_memories_for_chapter(
            self.db, chapter, characters
        )

        system = chapter_text_prompt({
            "chapter": chapter,
            "detailed_outline": chapter.get("detailed_outline", ""),
            "volume_outline": volume_outline,
            "assigned_plot_nodes": assigned,
            "characters": characters,
            "world": world,
            "previous_chapter_text_tail": prev_tail,
            "previous_summaries": summaries_chain,
            "active_foreshadows": active,
            "target_words": target_words,
            "character_memories": character_memories,
        })

        # —— 分段连续生成 ——
        segments: list[str] = []
        max_segments = max(target_words // 800 + 3, 10)
        for i in range(1, max_segments + 1):
            accumulated = sum(len(s) for s in segments)
            user = chapter_segment_user_prompt(
                segment_index=i,
                accumulated_words=accumulated,
                target_words=target_words,
                prev_segment_tail=segments[-1][-300:] if segments else "",
            )
            messages = [{"role": "system", "content": system}]
            if history_context:
                messages.extend(history_context)
            messages.append({"role": "user", "content": user})
            seg = await self._generate_segment(messages)
            if seg is None:  # 单段失败重试一次
                seg = await self._generate_segment(messages)
            if seg is None:
                notes.append(f"第 {i} 段生成失败，正文于约 {accumulated} 字处中断")
                break
            text = str(seg.get("text") or "").strip()
            if not text:
                break
            segments.append(text)
            if seg.get("finished"):
                break

        content = "\n\n".join(segments)
        if not content:
            return {"changes": [], "stage": "chapter_text", "error": "正文生成失败", "notes": notes}

        # —— 一致性审校：发现问题带反馈重写一次 ——
        review_issues = await self._review_text(content, chapter, characters, world, active, previous_chapter_text_tail=prev_tail)
        if review_issues:
            rewritten = await self._rewrite_with_feedback(system, history_context, content, review_issues)
            if rewritten:
                content = rewritten
            else:
                notes.append("一致性审校发现问题但重写失败，已保留原文")

        # —— 尺度检查 + 自动改写一次，改写后复核 ——
        rating_issues = await self._rating_check(content, rating)
        if rating_issues:
            rewritten = await self._rewrite_with_feedback(system, history_context, content, rating_issues)
            if rewritten:
                content = rewritten
                notes.append(f"已按「{RATING_LABELS.get(rating, '标准')}」尺度自动调整 {len(rating_issues)} 处")
                remaining = await self._rating_check(content, rating)
                if remaining:
                    notes.append(f"尺度复核仍有 {len(remaining)} 处待人工确认：" + "；".join(remaining[:3]))
            else:
                notes.append(f"尺度检查发现 {len(rating_issues)} 处问题但改写失败，待人工确认")

        return {
            "changes": [{
                "action": "update",
                "entity_id": chapter_id,
                "fields": {"content": content, "status": "generated"},
            }],
            "stage": "chapter_text",
            "notes": notes,
        }

    async def _generate_segment(self, messages: list[dict]) -> dict | None:
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
            logger.exception("Chapter segment generation failed")
        return None

    async def _rewrite_with_feedback(
        self,
        system: str,
        history_context: list[dict] | None,
        content: str,
        issues: list[str],
    ) -> str | None:
        user = (
            "【当前正文】\n" + content
            + "\n\n【审校反馈】\n" + "\n".join(f"- {i}" for i in issues)
            + "\n\n请根据反馈修改并输出完整正文。只输出 JSON：{\"text\": \"修改后的完整正文\"}"
        )
        messages = [{"role": "system", "content": system}]
        if history_context:
            messages.extend(history_context)
        messages.append({"role": "user", "content": user})
        seg = await self._generate_segment(messages)
        if seg and seg.get("text"):
            return str(seg["text"]).strip()
        return None

    async def _review_text(
        self,
        content: str,
        chapter: dict,
        characters: list[dict],
        world: list[dict],
        active_foreshadows: list[dict],
        previous_chapter_text_tail: str = "",
    ) -> list[str]:
        if not content:
            return []
        system = chapter_review_prompt({
            "chapter_text": content,
            "chapter": chapter,
            "characters": characters,
            "world": world,
            "active_foreshadows": active_foreshadows,
            "previous_chapter_text_tail": previous_chapter_text_tail,
        })
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

    async def _rating_check(self, content: str, rating: str) -> list[str]:
        if not content:
            return []
        system = chapter_rating_prompt({"chapter_text": content, "rating": rating})
        try:
            raw = await self.llm.parse_llm_json([{"role": "system", "content": system}])
            if isinstance(raw, dict):
                if raw.get("ok"):
                    return []
                issues = raw.get("issues")
                if isinstance(issues, list):
                    result = []
                    for i in issues:
                        if isinstance(i, dict):
                            result.append(f"{i.get('problem', '')}（{str(i.get('excerpt', ''))[:50]}）")
                        else:
                            result.append(str(i))
                    return result
            return []
        except Exception:
            logger.exception("Chapter rating check failed")
            return []

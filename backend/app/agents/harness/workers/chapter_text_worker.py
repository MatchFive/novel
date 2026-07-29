"""章节正文生成 Worker（含多段连续生成、一致性审校、尺度检查）。"""
from __future__ import annotations

import json
import logging

from app import repositories as repo
from app.agents.harness.prompts.chapter_generation import (
    RATING_LABELS,
    chapter_rating_prompt,
    chapter_review_prompt,
    chapter_segment_user_prompt,
    chapter_text_prompt,
)
from app.agents.harness.worker_base import WorkerBase
from app.agents.harness.workers._chapter_utils import (
    active_foreshadows,
    assigned_plot_nodes,
    chapter_summaries_chain,
    character_memories_for_chapter,
    find_target_chapter,
    generation_settings,
    parse_chapter_number,
    previous_chapter,
    previous_chapter_text_tail,
    volume_outline_text,
)
from app.agents.harness.workers._compat import (
    context_entity_list,
    context_project_id,
    task_goal,
)

logger = logging.getLogger(__name__)


class ChapterTextWorker(WorkerBase):
    worker_name = "chapter_text"

    async def run(self, task, context, history_context=None) -> dict:
        goal = task_goal(task)
        project_id = context_project_id(context)
        if not project_id:
            return {"changes": [], "stage": "chapter_text", "error": "缺少 project_id"}

        chapters = await repo.list_chapters(self.db, project_id)
        chapter = find_target_chapter(goal, context, chapters)
        if not chapter:
            if parse_chapter_number(goal) is None:
                return {"changes": [], "stage": "chapter_text", "error": "无法识别目标章节"}
            return {"changes": [], "stage": "chapter_text", "error": "未找到目标章节"}

        plot_nodes = await repo.list_plot(self.db, project_id)
        characters = context_entity_list(context, "characters")
        world = context_entity_list(context, "world")
        foreshadows = context_entity_list(context, "foreshadows")
        chapter_id = chapter.get("id")
        outlines = await repo.list_outlines(self.db, project_id)

        target_words, rating = await generation_settings(self.db)
        notes: list[str] = []

        assigned = assigned_plot_nodes(plot_nodes, chapter_id)
        prev = previous_chapter(chapter, chapters)
        prev_tail = previous_chapter_text_tail(prev)
        active = active_foreshadows(foreshadows)
        summaries_chain = chapter_summaries_chain(chapter, chapters)
        chapter_order = chapter.get("order", 0)
        volume_outline = volume_outline_text(outlines, chapter_order)

        character_memories = await character_memories_for_chapter(
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

        # —— 一致性审校：发现问题带反馈重写，最多循环 5 次 ——
        for attempt in range(5):
            review_issues = await self._review_text(
                content, chapter, characters, world, active, previous_chapter_text_tail=prev_tail
            )
            if not review_issues:
                notes.append(f"一致性审校通过（第 {attempt + 1} 次）")
                break
            rewritten = await self._rewrite_with_feedback(system, history_context, content, review_issues)
            if rewritten:
                content = rewritten
                notes.append(f"第 {attempt + 1} 次审校发现 {len(review_issues)} 处问题并已修正")
            else:
                notes.append(f"第 {attempt + 1} 次审校发现 {len(review_issues)} 处问题但重写失败，已保留原文")
                break
        else:
            notes.append("一致性审校已达最大循环次数（5 次），仍可能存在未解决问题")

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

        return self._normalize_result({
            "changes": [{
                "action": "update",
                "entity_id": chapter_id,
                "fields": {"content": content, "status": "generated"},
            }],
            "stage": "chapter_text",
            "notes": notes,
        })

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

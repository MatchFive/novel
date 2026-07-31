from __future__ import annotations

import json
import logging
import uuid

from app import repositories as repo
from app.agents.harness.prompts.chapter_generation import (
    chapter_rating_prompt,
    chapter_review_prompt,
    chapter_segment_user_prompt,
    chapter_text_prompt,
)
from app.agents.harness.workers._chapter_utils import (
    active_foreshadows,
    assigned_plot_nodes,
    broad_outline_text,
    character_memories_for_chapter,
    chapter_summaries_chain,
    generation_settings,
    previous_chapter,
    previous_chapter_text_tail,
    volume_outline_text,
)
from app.agents.workflows.registry import register_step
from app.core.errors import NotFoundError

logger = logging.getLogger(__name__)


@register_step
async def load_context(ctx):
    project_id = ctx.project_id
    chapter_id = ctx.inputs.get("chapter_id") or ctx.chapter_id
    if not project_id or not chapter_id:
        raise ValueError("chapter_generation workflow requires project_id and chapter_id")

    chapter = await repo.get_chapter(ctx.db, chapter_id)
    if not chapter:
        raise NotFoundError("章节不存在")

    chapters = await repo.list_chapters(ctx.db, project_id)
    outlines = await repo.list_outlines(ctx.db, project_id)
    characters = await repo.list_characters(ctx.db, project_id)
    world = await repo.list_world(ctx.db, project_id)
    plot_nodes = await repo.list_plot(ctx.db, project_id)
    foreshadows = await repo.list_foreshadows(ctx.db, project_id)
    target_words, rating = await generation_settings(ctx.db)

    prev = previous_chapter(chapter, chapters)
    context = {
        "chapter_id": chapter_id,
        "chapter": chapter,
        "chapters": chapters,
        "outlines": outlines,
        "characters": characters,
        "world": world,
        "plot_nodes": plot_nodes,
        "foreshadows": foreshadows,
        "assigned_plot_nodes": assigned_plot_nodes(plot_nodes, chapter_id),
        "active_foreshadows": active_foreshadows(foreshadows),
        "previous_chapter": prev,
        "previous_chapter_text_tail": previous_chapter_text_tail(prev),
        "previous_summaries": chapter_summaries_chain(chapter, chapters),
        "volume_outline": volume_outline_text(outlines, chapter.get("order", 0)),
        "broad_outline": broad_outline_text(outlines),
        "target_words": target_words,
        "rating": rating,
        "character_memories": await character_memories_for_chapter(ctx.db, chapter, characters),
    }
    ctx.outputs["context"] = context
    return {"chapter_id": chapter_id}


@register_step
async def generate_segments(ctx):
    context = ctx.outputs["context"]
    llm = await ctx.llm_factory("medium")
    system = chapter_text_prompt(context)
    target_words = context["target_words"]
    segments: list[str] = []
    notes: list[str] = []
    max_segments = max(target_words // 800 + 3, 10)

    for i in range(1, max_segments + 1):
        accumulated = sum(len(s) for s in segments)
        user = chapter_segment_user_prompt(
            segment_index=i,
            accumulated_words=accumulated,
            target_words=target_words,
            prev_segment_tail=segments[-1][-300:] if segments else "",
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        try:
            seg = await llm.parse_llm_json(messages)
        except Exception as exc:
            notes.append(f"第 {i} 段调用失败：{exc}")
            break
        if not isinstance(seg, dict):
            notes.append(f"第 {i} 段返回格式异常")
            break
        text = str(seg.get("text") or "").strip()
        if not text:
            break
        segments.append(text)
        if seg.get("finished"):
            break

    content = "\n\n".join(segments)
    if not content:
        raise RuntimeError("正文生成失败")
    return {"content": content, "notes": notes}


async def _rewrite_with_feedback(ctx, system, content, issues):
    llm = await ctx.llm_factory("medium")
    user = (
        "【当前正文】\n" + content
        + "\n\n【审校反馈】\n" + "\n".join(f"- {i}" for i in issues)
        + '\n\n请根据反馈修改并输出完整正文。只输出 JSON：{"text": "修改后的完整正文"}'
    )
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    try:
        seg = await llm.parse_llm_json(messages)
        if isinstance(seg, dict) and seg.get("text"):
            return str(seg["text"]).strip()
    except Exception:
        logger.exception("Rewrite with feedback failed")
    return None


@register_step
async def consistency_review(ctx):
    context = ctx.outputs["context"]
    llm = await ctx.llm_factory("medium")
    content = ctx.outputs["generate_segments"]["content"]
    system = chapter_review_prompt({
        "chapter_text": content,
        "chapter": context["chapter"],
        "characters": context["characters"],
        "world": context["world"],
        "active_foreshadows": context["active_foreshadows"],
        "previous_chapter_text_tail": context["previous_chapter_text_tail"],
    })
    notes: list[str] = []
    for attempt in range(5):
        try:
            raw = await llm.parse_llm_json([{"role": "system", "content": system}])
        except Exception:
            break
        if isinstance(raw, dict) and raw.get("ok"):
            notes.append(f"一致性审校通过（第 {attempt + 1} 次）")
            break
        issues = raw.get("issues") if isinstance(raw, dict) else []
        if not issues:
            break
        rewritten = await _rewrite_with_feedback(ctx, system, content, [str(i) for i in issues])
        if rewritten:
            content = rewritten
            notes.append(f"第 {attempt + 1} 次审校发现 {len(issues)} 处问题并已修正")
        else:
            notes.append(f"第 {attempt + 1} 次审校发现 {len(issues)} 处问题但重写失败，已保留原文")
            break
    else:
        notes.append("一致性审校已达最大循环次数（5 次），仍可能存在未解决问题")

    ctx.outputs["consistency_review"] = {"content": content, "notes": notes}
    return {"content": content, "notes": notes}


@register_step
async def rating_check(ctx):
    context = ctx.outputs["context"]
    llm = await ctx.llm_factory("medium")
    content = ctx.outputs["consistency_review"]["content"]
    system = chapter_rating_prompt({"chapter_text": content, "rating": context["rating"]})
    notes: list[str] = []
    try:
        raw = await llm.parse_llm_json([{"role": "system", "content": system}])
        if isinstance(raw, dict) and raw.get("ok"):
            notes.append("尺度检查通过")
        else:
            issues = raw.get("issues") if isinstance(raw, dict) else []
            if issues:
                rewritten = await _rewrite_with_feedback(
                    ctx,
                    chapter_text_prompt(context),
                    content,
                    [json.dumps(i, ensure_ascii=False) if isinstance(i, dict) else str(i) for i in issues],
                )
                if rewritten:
                    content = rewritten
                    notes.append(f"已按尺度等级自动调整 {len(issues)} 处")
                else:
                    notes.append(f"尺度检查发现 {len(issues)} 处问题但改写失败")
    except Exception:
        logger.exception("Rating check failed")
    return {"content": content, "notes": notes}


@register_step
async def assemble_change(ctx):
    context = ctx.outputs["context"]
    content = ctx.outputs["rating_check"]["content"]
    chapter_id = context["chapter_id"]
    notes: list[str] = []
    for key in ("generate_segments", "consistency_review", "rating_check"):
        notes.extend(ctx.outputs.get(key, {}).get("notes", []))

    record = {
        "id": f"cr_{uuid.uuid4().hex[:12]}",
        "project_id": ctx.project_id,
        "action": "update",
        "entity_type": "chapter",
        "entity_id": chapter_id,
        "after": {"content": content, "status": "generated"},
        "requires_confirmation": True,
        "stage": "chapter_generation",
    }
    ctx.change_records.append(record)
    return {"chapter_id": chapter_id, "content_length": len(content), "notes": notes}

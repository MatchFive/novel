"""Shared helpers for chapter-generation workers."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy import select

from app import repositories as repo
from app.agents.harness.workers._compat import context_session_get
from app.models import UserSetting

logger = logging.getLogger(__name__)


async def character_memories_for_chapter(
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


def parse_chapter_number(goal: str) -> int | None:
    """从目标中解析单个章节序号，例如“生成第 1 章细纲”。"""
    match = re.search(r"第\s*(\d+)\s*章", goal)
    if match:
        return int(match.group(1))
    return None


def parse_chapter_numbers(goal: str) -> list[int] | None:
    """解析目标中的章节序号列表：支持“前三章”“第1章到第3章”“第5章”。"""
    single = parse_chapter_number(goal)
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


def find_target_chapter(goal: str, context, chapters: list[dict]) -> dict | None:
    """从 entity_id、目标文本或唯一章节中定位目标章节。"""
    entity_type = context_session_get(context, "entity_type")
    entity_id = context_session_get(context, "entity_id")
    if entity_type == "chapter" and entity_id:
        for ch in chapters:
            if ch.get("id") == entity_id:
                return ch

    order = parse_chapter_number(goal)
    if order is not None:
        for ch in chapters:
            if ch.get("order") == order:
                return ch

    if len(chapters) == 1:
        return chapters[0]

    return None


def broad_outline_text(outlines: list[dict]) -> str:
    broads = [o for o in outlines if o.get("type") == "broad"]
    if not broads:
        broads = outlines
    if not broads:
        return "（暂无总纲）"
    parts = []
    for o in broads:
        parts.append(f"id={o.get('id')} title={o.get('title')}\ncontent={o.get('content', '')}")
    return "\n\n".join(parts)


def volume_outline_text(outlines: list[dict], chapter_order: int) -> str:
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


def previous_chapter(chapter: dict, chapters: list[dict]) -> dict | None:
    sorted_chapters = sorted(chapters, key=lambda c: c.get("order", 0))
    idx = next(
        (i for i, c in enumerate(sorted_chapters) if c.get("id") == chapter.get("id")),
        -1,
    )
    if idx > 0:
        return sorted_chapters[idx - 1]
    return None


def previous_chapter_summary(prev: dict | None) -> str:
    if not prev:
        return "（无）"
    text = prev.get("content", "") or prev.get("detailed_outline", "") or prev.get("title", "")
    if len(text) > 500:
        text = text[-500:]
    return text or "（无）"


def previous_chapter_text_tail(prev: dict | None, tail_len: int = 800) -> str:
    if not prev:
        return "（无）"
    text = prev.get("content", "")
    if len(text) > tail_len:
        return "..." + text[-tail_len:]
    return text or "（无）"


def active_foreshadows(foreshadows: list[dict]) -> list[dict]:
    return [f for f in foreshadows if f.get("state") == "pending"]


def assigned_plot_nodes(plot_nodes: list[dict], chapter_id: str | None) -> list[dict]:
    if not chapter_id:
        return []
    return [p for p in plot_nodes if p.get("chapter_id") == chapter_id]


async def generation_settings(db) -> tuple[int, str]:
    """读取生成相关用户设置：(每章目标字数, 尺度等级)。"""
    res = await db.execute(select(UserSetting))
    s = res.scalars().first()
    target = s.chapter_target_words if s and s.chapter_target_words else 2500
    rating = s.content_rating if s and s.content_rating else "standard"
    return target, rating


def chapter_summaries_chain(chapter: dict, chapters: list[dict], limit: int = 2000) -> str:
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


def result_to_response(result: Any, stage: str) -> dict:
    """将 LLM 解析结果统一转换为 {changes, stage, error?}。"""
    if isinstance(result, dict) and "changes" in result:
        return {"changes": result["changes"], "stage": stage}
    return {
        "changes": [],
        "stage": stage,
        "error": "无法解析 worker 输出",
        "raw": result,
    }


def user_prompt(goal: str, related: str = "") -> str:
    parts = []
    if related:
        parts.append(f"【相关上下文】\n{related}")
    parts.append(f"【用户目标】\n{goal}")
    return "\n\n".join(parts)


async def generate_json(llm, messages: list[dict]) -> Any:
    """直接调用 LLM 并解析 JSON 结果。"""
    try:
        return await llm.parse_llm_json(messages)
    except Exception:
        logger.exception("LLM JSON generation failed")
        return None

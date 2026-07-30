"""Reflection node: generate project-level experience from confirm/reject/adjust feedback."""
from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from app.core.llm_client import LLMClient

logger = logging.getLogger(__name__)


class ReflectionResult(BaseModel):
    reflection_text: str
    rules: list[str] = Field(default_factory=list)


REFLECTION_PROMPT = """你是小说创作助手的历史经验总结器。
请根据以下信息，用简洁中文生成一段经验总结 reflection_text 和 1-5 条具体规则 rules。

【用户原始输入】
{user_input}

【原始执行计划】
{plan}

【初始变更建议】
{original_changes}

【用户反馈后的最终变更】
{final_changes}

【反馈类型】
{feedback}

要求：
1. reflection_text 分析用户为什么给出该反馈， assistant 最初哪里理解对了/错了。
2. rules 是可操作的约束或偏好，如“用户不喜欢自动修改角色关系，除非明确提及”。
3. 只输出 JSON，不要 markdown 代码块，不要解释。
输出格式：
{{"reflection_text": "...", "rules": ["...", "..."]}}"""


def _render_changes(changes: list[dict[str, Any]]) -> str:
    lines = []
    for ch in changes:
        action = ch.get("action", "?")
        entity_type = ch.get("entity_type", "?")
        after_keys = list((ch.get("after") or {}).keys())
        lines.append(f"- [{action}] {entity_type}: {after_keys}")
    return "\n".join(lines) or "（无）"


async def reflect(
    user_input: str,
    execution_plan: dict[str, Any] | None,
    original_changes: list[dict[str, Any]],
    final_changes: list[dict[str, Any]],
    feedback: str,  # "confirm" | "reject" | "adjust"
    llm: LLMClient,
) -> ReflectionResult:
    """Generate a reflection from one confirm/reject/adjust cycle."""
    prompt = REFLECTION_PROMPT.format(
        user_input=user_input,
        plan=json.dumps(execution_plan or {}, ensure_ascii=False, indent=2),
        original_changes=_render_changes(original_changes),
        final_changes=_render_changes(final_changes),
        feedback=feedback,
    )
    try:
        raw = await llm.chat(
            [{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        data = json.loads(raw)
        if isinstance(data, str):
            data = json.loads(data)
        return ReflectionResult(
            reflection_text=data.get("reflection_text", ""),
            rules=data.get("rules") or [],
        )
    except Exception:
        logger.exception("Reflection generation failed")
        return ReflectionResult(
            reflection_text=f"用户{feedback}了本次建议。",
            rules=[],
        )

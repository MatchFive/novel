"""Supervisor：任务分析 / 拆分（LLM -> ExecutionPlan，带 schema 校验与兜底）。"""
from __future__ import annotations

from app.core.llm_client import LLMClient


async def run_supervisor(llm: LLMClient, messages: list[dict]) -> dict:
    try:
        raw = await llm.parse_llm_json(messages)
        if isinstance(raw, dict) and "tasks" in raw:
            return raw
    except Exception:
        pass
    return {"intent": messages[-1]["content"][:50], "tasks": [{"worker": "outline", "goal": messages[-1]["content"]}]}

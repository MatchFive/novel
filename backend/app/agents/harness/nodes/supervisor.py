"""Supervisor：任务分析 / 拆分（LLM -> ExecutionPlan，带 schema 校验与兜底）。"""
from __future__ import annotations

from typing import Any

from app.core.llm_client import LLMClient

SUPERVISOR_PROMPT = """你是小说创作助手的调度器（Supervisor）。根据用户指令与项目现有数据，
判断需要派发哪些专精 Worker 来处理。可选 Worker：
- character（角色设计/调整）
- world（世界观设定）
- outline（大纲生成/调整）
- plot（剧情节点编排）
- foreshadow（伏笔埋设/回收）

请返回 JSON：
{"intent": "一句话意图", "tasks": [{"worker": "character", "goal": "..."}, ...]}
若指令与长篇数据无关，返回 {"intent": "...", "tasks": []}。"""


async def run_supervisor(llm: LLMClient, user_input: str, context: dict) -> dict:
    ctx_summary = _summarize(context)
    msgs = [
        {"role": "system", "content": SUPERVISOR_PROMPT},
        {"role": "user", "content": f"项目现有数据摘要：\n{ctx_summary}\n\n用户指令：\n{user_input}"},
    ]
    try:
        raw = await llm.parse_llm_json(msgs)
        if isinstance(raw, dict) and "tasks" in raw:
            return raw
    except Exception:
        pass
    # 兜底：整段作为单一 outline 任务
    return {"intent": user_input[:50], "tasks": [{"worker": "outline", "goal": user_input}]}


def _summarize(context: dict) -> str:
    lines = []
    for k, v in context.items():
        if isinstance(v, list):
            lines.append(f"- {k}: {len(v)} 条")
        else:
            lines.append(f"- {k}: 存在" if v else f"- {k}: 空")
    return "\n".join(lines) or "（暂无数据）"

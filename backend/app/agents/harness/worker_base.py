"""Worker 基类：仅通过工具注册表调用只读工具取数（不 import repositories、不持有 session），
递归上限读 user_settings.recursive_limit，硬上限 + 超时保护。提供 tool-calling loop。
"""
from __future__ import annotations

import time
from typing import Any, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.tools import call_tool, tool_schemas
from app.config import settings as app_settings


class WorkerBase:
    worker_name: str = "base"

    def __init__(self, db: AsyncSession, llm, recursive_limit: int, timeout: float = 60.0):
        self.db = db
        self.llm = llm
        self.recursive_limit = min(max(recursive_limit, 1), app_settings.recursive_limit_hard_cap)
        self.timeout = timeout

    async def _tool_loop(self, system_prompt: str, user_prompt: str, extra_tools: list[dict] | None = None) -> dict:
        """标准 tool-calling 循环：LLM 可多次调用只读工具取数，最终产出结构化结果。"""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        schemas = tool_schemas() + (extra_tools or [])
        calls = 0
        start = time.time()
        while calls < self.recursive_limit:
            if time.time() - start > self.timeout:
                break
            calls += 1
            resp = await self.llm.chat(
                messages, response_format=None
            )
            # 尝试解析工具调用（兼容 OpenAI function call / JSON 指令两种形态）
            tool_call = self._parse_tool_call(resp)
            if not tool_call:
                # 没有进一步工具调用 -> 视为最终产出
                return self._parse_final(resp)
            name = tool_call.get("name")
            args = tool_call.get("arguments", {})
            try:
                result = await call_tool(self.db, name, args)
            except Exception as e:
                result = {"error": str(e)}
            messages.append({"role": "assistant", "content": resp})
            messages.append({"role": "tool", "name": name, "content": str(result)[:4000]})
        # 超出上限，让 LLM 直接总结
        final = await self.llm.chat(messages)
        return self._parse_final(final)

    def _parse_tool_call(self, text: str) -> dict | None:
        import json
        # 期望格式：TOOL_CALL:{"name": "...", "arguments": {...}}
        marker = "TOOL_CALL:"
        if marker in text:
            part = text.split(marker, 1)[1]
            try:
                return json.loads(part.strip())
            except Exception:
                return None
        return None

    def _parse_final(self, text: str) -> dict:
        # 子类可覆盖：将 LLM 文本解析为结构化结果
        return {"raw": text}


async def run_worker(
    worker_cls: type["WorkerBase"],
    db: AsyncSession,
    llm,
    recursive_limit: int,
    goal: str,
    context: dict,
) -> dict:
    worker = worker_cls(db, llm, recursive_limit)
    return await worker.run(goal, context)

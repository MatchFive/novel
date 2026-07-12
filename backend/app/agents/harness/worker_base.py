"""Worker 基类：仅通过工具注册表调用只读工具取数（不 import repositories、不持有 session），
递归上限读 user_settings.recursive_limit，硬上限 + 超时保护。提供 tool-calling loop。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.tools import call_tool, tool_schemas
from app.config import settings as app_settings

logger = logging.getLogger(__name__)


class WorkerBase:
    worker_name: str = "base"

    def __init__(self, db: AsyncSession, llm, recursive_limit: int, timeout: float = 60.0):
        self.db = db
        self.llm = llm
        self.recursive_limit = min(max(recursive_limit, 1), app_settings.recursive_limit_hard_cap)
        self.timeout = timeout

    async def _tool_loop(
        self,
        system_prompt: str,
        user_prompt: str,
        extra_tools: list[dict] | None = None,
        history_context: list[dict] | None = None,
    ) -> dict:
        """标准 tool-calling 循环：LLM 可多次调用只读工具取数，最终产出结构化结果。"""
        messages = [{"role": "system", "content": system_prompt}]
        if history_context:
            messages.extend(history_context)
        messages.append({"role": "user", "content": user_prompt})
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
            logger.warning("[%s] LLM resp (call %d): %s", self.worker_name, calls, resp[:500])
            # 尝试解析工具调用（兼容 OpenAI function call / JSON 指令两种形态）
            tool_call = self._parse_tool_call(resp)
            if not tool_call:
                # 没有进一步工具调用 -> 视为最终产出
                parsed = self._parse_final(resp)
                logger.warning("[%s] parsed final: %s", self.worker_name, parsed)
                return parsed
            name = tool_call.get("name")
            args = tool_call.get("arguments", {})
            logger.warning("[%s] tool call: %s args: %s", self.worker_name, name, args)
            try:
                result = await call_tool(self.db, name, args)
            except Exception as e:
                result = {"error": str(e)}
            logger.warning("[%s] tool result: %s", self.worker_name, str(result)[:500])
            messages.append({"role": "assistant", "content": resp})
            messages.append({
                "role": "user",
                "content": f"工具 {name} 返回结果：\n{str(result)[:4000]}",
            })
        # 超出上限，让 LLM 直接总结
        final = await self.llm.chat(messages)
        logger.warning("[%s] final LLM resp: %s", self.worker_name, final[:500])
        parsed = self._parse_final(final)
        logger.warning("[%s] parsed final: %s", self.worker_name, parsed)
        return parsed

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
        """将 LLM 最终文本解析为结构化结果；支持纯 JSON、Markdown 代码块、JSON 子串。"""
        import json
        cleaned = text.strip()

        def try_parse(value: str) -> dict | None:
            try:
                parsed = json.loads(value.strip().strip("`"))
                if isinstance(parsed, dict):
                    return parsed
                if isinstance(parsed, list):
                    return {"changes": parsed}
            except (json.JSONDecodeError, TypeError):
                pass
            return None

        # 1. 尝试解析整段文本
        result = try_parse(cleaned)
        if result is not None:
            return result

        # 2. 查找 ```json / ``` 代码块
        if "```" in cleaned:
            parts = cleaned.split("```")
            for part in parts[1:]:
                block = part.strip()
                if block.lower().startswith("json"):
                    block = block[4:]
                result = try_parse(block)
                if result is not None:
                    return result

        # 3. 尝试截取第一个 { ... } 或 [ ... ] 子串
        for start_char, end_char in (("{", "}"), ("[", "]")):
            start = cleaned.find(start_char)
            if start != -1:
                end = cleaned.rfind(end_char)
                if end > start:
                    result = try_parse(cleaned[start:end + 1])
                    if result is not None:
                        return result

        return {"raw": text}


async def run_worker(
    worker_cls: type["WorkerBase"],
    db: AsyncSession,
    llm,
    recursive_limit: int,
    goal: str,
    context: dict,
    history_context: list[dict] | None = None,
) -> dict:
    worker = worker_cls(db, llm, recursive_limit)
    return await worker.run(goal, context, history_context)

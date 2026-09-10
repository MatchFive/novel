"""LLM 客户端：抽象 + OpenAI 兼容实现（设计文档 6.1）。"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Protocol

from openai import AsyncOpenAI

from app.core.config import AppConfig, ProviderConfig
from app.core.logger import get_logger
from app.llm.router import ResolvedModel

log = get_logger("llm.client")


class LLMError(Exception):
    """LLM 调用失败。"""

    def __init__(self, message: str, raw_content: str | None = None):
        super().__init__(message)
        self.raw_content = raw_content  # JSON 解析失败时的模型原始输出（供上层抢救截断的 JSON）


class LLMClient(Protocol):
    async def chat_stream(self, messages: list[dict], resolved: ResolvedModel) -> AsyncIterator[str]: ...

    async def chat_structured(
        self, messages: list[dict], resolved: ResolvedModel
    ) -> dict: ...


class OpenAICompatClient:
    """OpenAI 兼容协议的异步客户端（覆盖 DeepSeek/通义/智谱/Kimi 等）。"""

    def __init__(self, cfg: AppConfig):
        self._cfg = cfg
        self._clients: dict[str, AsyncOpenAI] = {}

    def _thinking_extra_body(self, resolved: ResolvedModel) -> dict | None:
        """思考模式的协议适配（按厂商选写法，避免严格服务端因未知字段报错）。

        - DeepSeek（V3.2+ 的 deepseek-chat）：{"thinking": {"type": "enabled"}}
        - Qwen3 / vLLM / Ollama 本地部署：{"chat_template_kwargs": {"enable_thinking": true}}
          部分部署直接支持顶层 {"enable_thinking": true}
        - 其他厂商：两种都带上（OpenAI 兼容服务端通常忽略未知字段）
        """
        if not getattr(resolved, "thinking", False):
            return None
        provider = self._cfg.providers.get(resolved.provider)
        base = (provider.base_url or "").lower() if provider else ""
        if "deepseek" in base:
            return {"thinking": {"type": "enabled"}}
        if "deepseek" in resolved.provider.lower():
            return {"thinking": {"type": "enabled"}}
        return {
            "chat_template_kwargs": {"enable_thinking": True},
            "enable_thinking": True,
        }

    def _client_for(self, provider_name: str) -> AsyncOpenAI:
        if provider_name not in self._clients:
            provider: ProviderConfig = self._cfg.providers[provider_name]
            self._clients[provider_name] = AsyncOpenAI(
                api_key=provider.api_key or "sk-placeholder",
                base_url=provider.base_url,
                timeout=1800.0,  # 30 分钟：本地项目 + 超长文本生成，不做苛刻超时
                max_retries=0,  # 由上层按降级策略处理
            )
        return self._clients[provider_name]

    async def _create(self, client: AsyncOpenAI, resolved: ResolvedModel,
                      messages: list[dict], *, stream: bool, json_mode: bool = False):
        """统一发起 completions 请求；服务端拒绝 temperature（如 Kimi 思考模型只允许 1）
        时自动去掉该参数重试（由模型用默认值），对厂商无感。"""
        kwargs: dict = {
            "model": resolved.model,
            "messages": messages,
            "stream": stream,
            "temperature": resolved.temperature,
            "max_tokens": resolved.max_tokens,
            "extra_body": self._thinking_extra_body(resolved),
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            return await client.chat.completions.create(**kwargs)
        except Exception as exc:
            msg = str(exc).lower()
            if "temperature" in msg:
                kwargs.pop("temperature", None)
                log.warning(
                    "%s/%s 拒绝 temperature=%s（%s），已改为不传该参数重试",
                    resolved.provider, resolved.model, resolved.temperature, exc)
                return await client.chat.completions.create(**kwargs)
            raise

    async def chat_stream(
        self, messages: list[dict], resolved: ResolvedModel
    ) -> AsyncIterator[str]:
        client = self._client_for(resolved.provider)
        try:
            stream = await self._create(client, resolved, messages, stream=True)
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    yield delta.content
        except Exception as exc:  # 网络/限流/超时等
            log.warning("chat_stream failed on %s/%s: %s", resolved.provider, resolved.model, exc)
            raise LLMError(str(exc)) from exc

    async def chat_structured(
        self, messages: list[dict], resolved: ResolvedModel
    ) -> dict:
        """请求 JSON 模式并解析。解析失败抛 LLMError（上层可重试）。

        开始/结束都写 INFO 日志——只有"开始"没有"完成"时，说明调用挂死在网络层。
        """
        import time as _time
        client = self._client_for(resolved.provider)
        t0 = _time.perf_counter()
        log.info("LLM 结构化调用开始 %s/%s（%d 条消息）",
                 resolved.provider, resolved.model, len(messages))
        try:
            resp = await self._create(client, resolved, messages, stream=False, json_mode=True)
        except Exception as exc:
            log.warning("chat_structured failed on %s/%s: %s", resolved.provider, resolved.model, exc)
            raise LLMError(str(exc)) from exc
        log.info("LLM 结构化调用完成 %s/%s，耗时 %.1fs",
                 resolved.provider, resolved.model, _time.perf_counter() - t0)

        content = resp.choices[0].message.content or ""
        # 容错：剥离代码围栏
        content = content.strip()
        if content.startswith("```"):
            content = content.strip("`")
            if content.startswith("json"):
                content = content[4:]
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            log.warning("JSON 解析失败（前 200 字）：%s", content[:200])
            raise LLMError(f"模型未返回合法 JSON: {exc}", raw_content=content) from exc

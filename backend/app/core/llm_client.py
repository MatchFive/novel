"""OpenAI 兼容 LLM 客户端：非流式 chat / 流式 chat_stream / parse_llm_json。"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator, Optional

import httpx

from app.config import settings


class LLMClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        timeout: Optional[float] = None,
    ):
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.llm_api_key
        self.model = model or settings.llm_model
        self.temperature = temperature if temperature is not None else settings.llm_temperature
        self.timeout = timeout or settings.llm_timeout

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        response_format: Optional[dict] = None,
        timeout: Optional[float] = None,
    ) -> str:
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
        }
        if response_format:
            payload["response_format"] = response_format
        async with httpx.AsyncClient(timeout=timeout or self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
    ) -> AsyncIterator[str]:
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "stream": True,
        }
        async with httpx.AsyncClient(timeout=self.timeout + 60) as client:
            async with client.stream(
                "POST", f"{self.base_url}/chat/completions",
                headers=self._headers(), json=payload,
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                        delta = chunk["choices"][0]["delta"].get("content")
                        if delta:
                            yield delta
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue

    async def parse_llm_json(self, messages: list[dict[str, str]], *, model: Optional[str] = None) -> Any:
        """调用 chat（json 模式）并解析为 Python 对象；失败返回原始文本。"""
        raw = await self.chat(
            messages,
            model=model,
            response_format={"type": "json_object"},
        )
        return _extract_json(raw)


def _extract_json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        # 去掉 ```json ... ``` 代码块
        text = text.split("```", 2)[1]
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip().strip("`")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
        return text

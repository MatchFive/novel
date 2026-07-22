"""LLMClient 错误处理测试。"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.core.errors import AppError
from app.core.llm_client import LLMClient


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_chat_maps_http_400_to_app_error():
    """上游 LLM 返回 400 时，应抛出结构化 AppError 而非裸 httpx 异常。"""
    llm = LLMClient(
        base_url="https://api.example.com/v1",
        api_key="sk-test",
        model="test-model",
    )
    mock_resp = httpx.Response(
        400,
        text='{"error": {"message": "bad request"}}',
        request=httpx.Request("POST", "https://api.example.com/v1/chat/completions"),
    )
    with patch.object(
        httpx.AsyncClient, "post", new=AsyncMock(side_effect=httpx.HTTPStatusError("Bad request", request=mock_resp.request, response=mock_resp))
    ):
        with pytest.raises(AppError) as exc_info:
            await llm.chat([{"role": "user", "content": "hi"}])
        assert exc_info.value.code == "LLM_REQUEST_FAILED"
        assert exc_info.value.status_code == 502

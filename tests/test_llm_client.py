"""OpenAI 兼容 LLM 客户端测试（Review Focus 4：think 剥离/空内容/超时分类/llm.log）。"""
import json

import httpx
import pytest

from harness.config import ProviderConfig
from harness.llm.client import CompletionUsage, LLMError, OpenAICompatClient


def sse_body(*chunks, usage=None, finish="stop"):
    """流式 mock 只此形态：SSE 线格 `data: {json}\n\n` + 收尾 `data: [DONE]\n\n`。

    complete() 的 JSON body mock 不适用流式路径——SDK 1.79 只在流式路径解析 SSE；
    每行一个 chunk payload，usage 走独立末 chunk（choices 为空），finish chunk 带 finish_reason。
    """
    payloads = [{"choices": [{"delta": {"content": c}}], "model": "m",
                 "object": "chat.completion.chunk", "created": 0} for c in chunks]
    payloads.append({"choices": [{"delta": {}, "finish_reason": finish}],
                     "model": "m", "object": "chat.completion.chunk", "created": 0})
    if usage is not None:
        payloads.append({"choices": [], "usage": usage})
    return "".join(f"data: {json.dumps(p, ensure_ascii=False)}\n\n" for p in payloads) \
        + "data: [DONE]\n\n"


def sse_response(body: str) -> httpx.Response:
    return httpx.Response(200, content=body,
                          headers={"Content-Type": "text/event-stream"})


def make_client(handler, log_path=None):
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url="https://api.test")
    return OpenAICompatClient(
        ProviderConfig(base_url="https://api.test", api_key="k",
                       lite="m", standard="m", pro="m"),
        http_client=http, log_path=log_path)


async def test_complete_returns_content():
    def handler(req):
        return httpx.Response(200, json={"choices": [{"message": {"content": "你好"}, "finish_reason": "stop"}]})
    r = await make_client(handler).complete([{"role": "user", "content": "hi"}], model="m")
    assert r.text == "你好"


async def test_complete_returns_usage():
    def handler(req):
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "你好"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 6, "total_tokens": 17},
        })
    r = await make_client(handler).complete([{"role": "user", "content": "hi"}], model="m")
    assert r.text == "你好"
    assert r.usage is not None
    assert r.usage.prompt_tokens == 11 and r.usage.completion_tokens == 6


async def test_complete_usage_none_when_absent():
    def handler(req):
        return httpx.Response(200, json={"choices": [{"message": {"content": "你好"}, "finish_reason": "stop"}]})
    r = await make_client(handler).complete([{"role": "user", "content": "hi"}], model="m")
    assert r.text == "你好"
    assert r.usage is None


async def test_stream_strips_think():
    def handler(req):
        chunks = ['{"choices":[{"delta":{"content":"<think>推理"}}],"model":"m","object":"chat.completion.chunk","created":0}',
                  '{"choices":[{"delta":{"content":"过程</think>正文"}}],"model":"m","object":"chat.completion.chunk","created":0}']
        body = "".join(f"data: {c}\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, content=body,
                              headers={"Content-Type": "text/event-stream"})
    parts = [p async for p in make_client(handler).stream_text([{"role": "user", "content": "hi"}], model="m")]
    assert "".join(parts) == "正文"


async def test_empty_content_raises_transient_false():
    def handler(req):
        return httpx.Response(200, json={"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]})
    with pytest.raises(LLMError) as ei:
        await make_client(handler).complete([{"role": "user", "content": "hi"}], model="m")
    assert ei.value.kind == "empty"


async def test_starved_at_max_tokens_kind():
    def handler(req):
        return httpx.Response(200, json={"choices": [{"message": {"content": ""}, "finish_reason": "length"}]})
    with pytest.raises(LLMError) as ei:
        await make_client(handler).complete([{"role": "user", "content": "hi"}], model="m")
    assert ei.value.kind == "starved" and ei.value.transient is True


async def test_timeout_is_transient():
    async def run():
        def handler(req):
            raise httpx.ReadTimeout("boom")
        with pytest.raises(LLMError) as ei:
            await make_client(handler).complete([{"role": "user", "content": "hi"}], model="m")
        assert ei.value.transient is True
        assert ei.value.kind == "timeout"
    await run()


async def test_llm_log_appended(tmp_path):
    log = tmp_path / "llm.log"
    async def run():
        def handler(req):
            return httpx.Response(200, json={"choices": [{"message": {"content": "x"}, "finish_reason": "stop"}]})
        await make_client(handler, log_path=log).complete([{"role": "user", "content": "hi"}], model="m")
    await run()
    line = json.loads(log.read_text(encoding="utf-8").strip())
    assert line["model"] == "m" and line["finish_reason"] == "stop" and "duration_s" in line


async def test_stream_completion_collects_usage():
    bodies = []

    def handler(req):
        bodies.append(json.loads(req.content))
        return sse_response(sse_body("床前", "明月", "光",
                                     usage={"prompt_tokens": 11, "completion_tokens": 6,
                                            "total_tokens": 17}))
    r = await make_client(handler).stream_completion([{"role": "user", "content": "hi"}], model="m")
    assert r.text == "床前明月光"
    assert r.usage == CompletionUsage(prompt_tokens=11, completion_tokens=6)
    # include_usage 经 stream_options 上送（usage 记账的开关）
    assert bodies[0]["stream_options"] == {"include_usage": True}


async def test_stream_completion_usage_none_when_absent():
    def handler(req):
        return sse_response(sse_body("只有正文"))
    r = await make_client(handler).stream_completion([{"role": "user", "content": "hi"}], model="m")
    assert r.text == "只有正文"
    assert r.usage is None


async def test_stream_completion_empty_raises():
    def handler(req):
        return sse_response(sse_body())  # 零 content chunk：聚合为空
    with pytest.raises(LLMError) as ei:
        await make_client(handler).stream_completion([{"role": "user", "content": "hi"}], model="m")
    assert ei.value.kind == "empty"


async def test_aclose_closes_underlying_client():
    client = make_client(lambda req: httpx.Response(200, json={
        "choices": [{"message": {"content": "x"}, "finish_reason": "stop"}]}))
    await client.aclose()
    # 关闭后复用响亮失败：httpx 的 closed RuntimeError 经 SDK 包成连接错，
    # 再被 _reraise 归到瞬时类 LLMError（复用即失败，绝不静默成功）
    with pytest.raises(LLMError):
        await client.complete([{"role": "user", "content": "hi"}], model="m")


async def test_async_context_manager_closes_on_exit():
    def handler(req):
        return httpx.Response(200, json={"choices": [{"message": {"content": "x"}, "finish_reason": "stop"}]})

    async with make_client(handler) as client:
        assert client.provider.lite == "m"  # provider 只读暴露：工具层按档位取模型名
        r = await client.complete([{"role": "user", "content": "hi"}], model="m")
        assert r.text == "x"
    with pytest.raises(LLMError):
        await client.complete([{"role": "user", "content": "hi"}], model="m")

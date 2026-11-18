"""tools/base：ToolResult / emit 协议 / complete_structured 扩额重试阶梯 / usage_add。

LLM 全 mock（JSON body MockTransport——complete 非流式，SSE 只属于流式路径）。
阶梯钉死：解析失败 extract→None → 扩额（×2 封顶）重试一次；empty 顶 CAP、starved ×2 封顶；
api/timeout 不重试。分类串与 P1 工具层一致：truncated/empty/api:{kind}。
"""
import json

import httpx
import pytest

from harness.llm.client import CompletionUsage
from harness.llm.policy import CAP_MAX_TOKENS
from harness.tools.base import (
    EmitFn,
    ToolResult,
    complete_structured,
    extract_json,
    stream_collected,
    usage_add,
)
from harness.tools.outline import _extract_json_value
from harness.tools.registry import complete_structured as registry_complete_structured


def _sse_body(*chunks, usage=None, finish="stop"):
    """流式 mock 只此形态：SSE 线格 `data: {json}\n\n` + 收尾 `data: [DONE]\n\n`。

    与 tests/test_llm_client.py 的 sse_body 同构（complete 的 JSON body mock 不适用流式，
    SDK 1.79 只在流式路径解析 SSE）；usage 走独立末 chunk（choices 为空）。
    """
    payloads = [{"choices": [{"delta": {"content": c}}], "model": "m",
                 "object": "chat.completion.chunk", "created": 0} for c in chunks]
    payloads.append({"choices": [{"delta": {}, "finish_reason": finish}],
                     "model": "m", "object": "chat.completion.chunk", "created": 0})
    if usage is not None:
        payloads.append({"choices": [], "usage": usage})
    return "".join(f"data: {json.dumps(p, ensure_ascii=False)}\n\n" for p in payloads) \
        + "data: [DONE]\n\n"


@pytest.fixture
def make_llm():
    """responses：str content / {"content","finish","usage"?} 字典 / 可抛异常；记录请求 body。"""
    def factory(responses):
        calls = []

        def handler(req):
            body = json.loads(req.content)
            calls.append(body)
            item = responses[len(calls) - 1]
            if isinstance(item, Exception):
                raise item
            if isinstance(item, str):
                item = {"content": item, "finish": "stop"}
            payload = {"choices": [{"message": {"content": item["content"]},
                                    "finish_reason": item["finish"]}]}
            if item.get("usage"):
                payload["usage"] = item["usage"]
            return httpx.Response(200, json=payload)

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.test")
        from harness.config import ProviderConfig
        from harness.llm.client import OpenAICompatClient
        client = OpenAICompatClient(
            ProviderConfig(base_url="https://api.test", api_key="k",
                           lite="m", standard="m", pro="m"),
            http_client=http)
        return client, calls

    return factory


@pytest.fixture
def make_stream_llm():
    """流式版 make_llm：responses 项为 {"chunks": [...], "usage"?, "finish"?} 字典 / 可抛异常；记录请求 body。"""
    def factory(responses):
        calls = []

        def handler(req):
            body = json.loads(req.content)
            calls.append(body)
            item = responses[len(calls) - 1]
            if isinstance(item, Exception):
                raise item
            sse = _sse_body(*item["chunks"], usage=item.get("usage"),
                            finish=item.get("finish", "stop"))
            return httpx.Response(200, content=sse,
                                  headers={"Content-Type": "text/event-stream"})

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.test")
        from harness.config import ProviderConfig
        from harness.llm.client import OpenAICompatClient
        client = OpenAICompatClient(
            ProviderConfig(base_url="https://api.test", api_key="k",
                           lite="m", standard="m", pro="m"),
            http_client=http)
        return client, calls

    return factory


def _usage(p=10, c=20):
    return {"prompt_tokens": p, "completion_tokens": c, "total_tokens": p + c}


async def test_success_returns_value_usage_and_empty_reason(make_llm):
    llm, calls = make_llm([{"content": '{"a": 1}', "finish": "stop", "usage": _usage(10, 20)}])
    value, usage, reason = await complete_structured(
        llm, model="m", temperature=0.5, max_tokens=8192, prompt="p", extract=extract_json)
    assert value == {"a": 1}
    assert usage == CompletionUsage(prompt_tokens=10, completion_tokens=20)
    assert reason == ""
    assert len(calls) == 1
    assert calls[0]["max_tokens"] == 8192


async def test_bad_json_retries_with_doubled_tokens_then_fails(make_llm):
    llm, calls = make_llm([
        {"content": "这不是 JSON", "finish": "stop", "usage": _usage(10, 20)},
        {"content": "{{{", "finish": "stop", "usage": _usage(10, 30)},
    ])
    value, usage, reason = await complete_structured(
        llm, model="m", temperature=0.5, max_tokens=8192, prompt="p", extract=extract_json)
    assert value is None
    assert reason == "bad_json"
    assert usage == CompletionUsage(prompt_tokens=20, completion_tokens=50)  # 两次用量都回流
    assert len(calls) == 2
    assert calls[1]["max_tokens"] == min(CAP_MAX_TOKENS, 8192 * 2)


async def test_empty_error_caps_max_tokens_on_retry(make_llm):
    llm, calls = make_llm([
        {"content": "", "finish": "stop"},
        {"content": '{"a": 1}', "finish": "stop", "usage": _usage()},
    ])
    value, usage, reason = await complete_structured(
        llm, model="m", temperature=0.5, max_tokens=8192, prompt="p", extract=extract_json)
    assert value == {"a": 1}
    assert usage == CompletionUsage(prompt_tokens=10, completion_tokens=20)
    assert reason == ""
    assert len(calls) == 2
    assert calls[1]["max_tokens"] == CAP_MAX_TOKENS  # empty 直接顶 cap


async def test_empty_twice_returns_empty_reason(make_llm):
    llm, calls = make_llm([{"content": "", "finish": "stop"}, {"content": "", "finish": "stop"}])
    value, usage, reason = await complete_structured(
        llm, model="m", temperature=0.5, max_tokens=8192, prompt="p", extract=extract_json)
    assert value is None
    assert reason == "empty"
    assert usage is None
    assert len(calls) == 2
    assert calls[1]["max_tokens"] == CAP_MAX_TOKENS


async def test_starved_doubles_capped_and_classified_truncated(make_llm):
    starved = {"content": "", "finish": "length"}
    llm, calls = make_llm([starved, starved])
    value, usage, reason = await complete_structured(
        llm, model="m", temperature=0.5, max_tokens=8192, prompt="p", extract=extract_json)
    assert value is None
    assert reason == "truncated"
    assert usage is None  # 两次都撞 LLMError，无成功调用可记账
    assert len(calls) == 2
    assert calls[1]["max_tokens"] == min(CAP_MAX_TOKENS, 8192 * 2)  # starved ×2 封顶


async def test_api_error_no_retry_single_call(make_llm):
    llm, calls = make_llm([httpx.ConnectError("boom")])
    value, usage, reason = await complete_structured(
        llm, model="m", temperature=0.5, max_tokens=8192, prompt="p", extract=extract_json)
    assert value is None
    assert reason == "api:api"
    assert usage is None
    assert len(calls) == 1  # api/timeout 不重试


async def test_escalate_false_skips_retry(make_llm):
    llm, calls = make_llm([{"content": "", "finish": "stop"}])
    value, usage, reason = await complete_structured(
        llm, model="m", temperature=0.5, max_tokens=8192, prompt="p",
        extract=extract_json, escalate=False)
    assert value is None
    assert reason == "empty"
    assert usage is None
    assert len(calls) == 1


async def test_stream_collected_happy(make_stream_llm):
    llm, calls = make_stream_llm([{"chunks": ["一段正文", "继续写", "完了"],
                                   "usage": _usage(5, 7)}])
    text, usage, reason = await stream_collected(
        llm, model="m", temperature=0.7, max_tokens=1024, system="s", prompt="p")
    assert text == "一段正文继续写完了"
    assert usage == CompletionUsage(prompt_tokens=5, completion_tokens=7)
    assert reason == ""
    assert len(calls) == 1
    assert calls[0]["stream"] is True


async def test_max_chars_early_stop(make_stream_llm):
    chunk = "字" * 100
    llm, _calls = make_stream_llm([{"chunks": [chunk] * 10, "usage": _usage()}])
    text, usage, reason = await stream_collected(
        llm, model="m", temperature=0.7, max_tokens=1024, system="s", prompt="p",
        max_chars=250)
    assert text == "字" * 250  # 早停并按上限截断（成功返回）
    assert reason == ""
    assert usage is None  # 早停拿不到流尾 usage chunk：记账只收已消费部分（None 容忍）


async def test_repeat_loop_breaker(make_stream_llm):
    # 整流 = 同一段 ≥24 字循环 ≥3 次：512 字检查点命中熔断，保留第一份
    unit = "abcdefghijklmnopqrstuvwxyzABCD"  # 30 字，自身无更小周期
    llm, _calls = make_stream_llm([{"chunks": [unit] * 40, "usage": _usage()}])
    text, usage, reason = await stream_collected(
        llm, model="m", temperature=0.7, max_tokens=8192, system="s", prompt="p")
    assert text == unit
    assert reason == ""  # 截断按成功返回
    assert usage is None  # 熔断早停同理：usage chunk 未到达


async def test_empty_escalates_once_to_cap(make_stream_llm):
    llm, calls = make_stream_llm([
        {"chunks": [], "finish": "stop"},  # 首轮空聚合 → kind=empty
        {"chunks": ["二调正文"], "usage": _usage(10, 20)},
    ])
    text, usage, reason = await stream_collected(
        llm, model="m", temperature=0.7, max_tokens=8192, system="s", prompt="p")
    assert text == "二调正文"
    assert reason == ""
    assert usage == CompletionUsage(prompt_tokens=10, completion_tokens=20)
    assert len(calls) == 2
    assert calls[1]["max_tokens"] == CAP_MAX_TOKENS  # empty 直接顶 cap（同 complete_structured）


async def test_timeout_classified_not_raised(make_stream_llm):
    # SDK/httpx 瞬时错误必须先经 client 的 _reraise 归类成 LLMError，否则裸异常穿透
    llm, calls = make_stream_llm([httpx.ReadTimeout("boom")])
    text, usage, reason = await stream_collected(
        llm, model="m", temperature=0.7, max_tokens=1024, system="s", prompt="p")
    assert text == "" and usage is None
    assert reason == "api:timeout"  # 词表同 complete_structured；不重试
    assert len(calls) == 1


async def test_on_text_throttled(make_stream_llm):
    chunk = "x" * 60
    llm, _calls = make_stream_llm([{"chunks": [chunk] * 10, "usage": _usage()}])  # 共 600 字
    seen: list[str] = []
    text, usage, reason = await stream_collected(
        llm, model="m", temperature=0.7, max_tokens=1024, system="s", prompt="p",
        on_text=seen.append)
    assert reason == ""
    assert usage == CompletionUsage(prompt_tokens=10, completion_tokens=20)  # 全程收完，usage 到手
    assert text == "x" * 600
    assert len(seen) == 5  # 节流钉死：恰好 len//120 帧（600//120）
    assert seen[-1] == "x" * 600  # 收尾帧报到全量
    # 中间帧增量 ≥120（节流钉死；_ThinkStripper 有 ≤16 字标签边界缓冲，帧值允许略滞后）。
    # 末帧是收尾补帧（增量不足 120 也必报，seed「短稿也要上屏」）。
    assert all(len(seen[i]) - len(seen[i - 1]) >= 120 for i in range(1, len(seen) - 1))


def test_usage_add_none_branches():
    u = CompletionUsage(prompt_tokens=10, completion_tokens=20)
    assert usage_add(None, u) == u
    assert usage_add(u, None) == u
    assert usage_add(None, None) is None
    v = CompletionUsage(prompt_tokens=1, completion_tokens=2)
    assert usage_add(u, v) == CompletionUsage(prompt_tokens=11, completion_tokens=22)


def test_tool_result_defaults():
    result = ToolResult(text="x")
    assert result.text == "x"
    assert result.usage is None
    result2 = ToolResult(text="y", usage=CompletionUsage(1, 2))
    assert result2.usage.completion_tokens == 2


def test_extract_json_is_outline_impl():
    assert extract_json('前导文字\n```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json("不是 JSON") is None
    assert extract_json is not _extract_json_value  # 薄封装，但底层能力同源（import 自 outline）


async def test_with_shrink_first_valid_single_call():
    from harness.tools.base import with_shrink

    calls: list[str] = []

    async def run(note_suffix: str):
        calls.append(note_suffix)
        return {"plots": [1]}

    out = await with_shrink(run, note="NOTE", valid=lambda d: bool(d.get("plots")))
    assert out == {"plots": [1]}
    assert calls == [""]          # 首轮有效：附注不发出，只跑一轮


async def test_with_shrink_invalid_then_note_then_none():
    from harness.tools.base import with_shrink

    calls: list[str] = []

    async def run(note_suffix: str):
        calls.append(note_suffix)
        return {"plots": []}

    out = await with_shrink(run, note="NOTE", valid=lambda d: bool(d.get("plots")))
    assert out is None
    assert calls == ["", "NOTE"]  # 首轮 ""、二轮附收缩 note，恰好两轮


def test_registry_reexports_base_names():
    assert registry_complete_structured is complete_structured
    from harness.tools.registry import EmitFn as reg_emit
    from harness.tools.registry import ToolResult as reg_tr
    from harness.tools.registry import usage_add as reg_ua
    assert reg_tr is ToolResult
    assert reg_ua is usage_add
    assert reg_emit is EmitFn

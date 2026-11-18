"""plan_arcs：成功落卷骨架 / 空响应与坏 JSON 扩额重试 / 二次失败分类观测。

LLM 全 mock（JSON body MockTransport——complete 非流式，SSE 只属于 stream_text 路径）。
观测键名钉死：目标=goal、容量参考=capacity_hint（与 Task 6 digest 对齐）。
P3（Task 8）：plan_arcs 迁 ToolResult——观测文本/重试次数/扩额公式不变（registry
handler 解包 .text，既有断言原样在跑）；直接 await 模块级协程拿到 ToolResult，
usage 非 None 走 loop 同账入账路径。
"""
import json
from pathlib import Path

import httpx
import pytest

from harness.config import ProviderConfig, Settings
from harness.llm.client import OpenAICompatClient
from harness.llm.policy import CAP_MAX_TOKENS
from harness.projects import create_project
from harness.prompts.loader import PromptLoader
from harness.run.digest import progress_digest
from harness.store.project import ProjectStore
from harness.tools.base import ToolResult
from harness.tools.outline import plan_arcs, register_outline_tools
from harness.tools.registry import ToolRegistry


def make_settings(pro="pro-model"):
    return Settings(
        library_root=Path("x"),
        providers={"test": ProviderConfig(base_url="https://api.test", api_key="k",
                                          lite="m", standard="m", pro=pro)},
        default_provider="test",
        default_tier="standard",
        web_verify=True,
    )


@pytest.fixture
def store(tmp_path):
    info = create_project(tmp_path, "测试书")
    return ProjectStore(info.path)


@pytest.fixture
def make_llm():
    """responses：str content 或 {"content", "finish"} 字典；记录每次请求的 body 供断言。"""
    def factory(responses):
        calls = []

        def handler(req):
            body = json.loads(req.content)
            calls.append(body)
            item = responses[len(calls) - 1]
            if isinstance(item, str):
                item = {"content": item, "finish": "stop"}
            return httpx.Response(200, json={
                "choices": [{"message": {"content": item["content"]}, "finish_reason": item["finish"]}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 7, "total_tokens": 10},
            })

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.test")
        client = OpenAICompatClient(
            ProviderConfig(base_url="https://api.test", api_key="k",
                           lite="m", standard="m", pro="m"),
            http_client=http)
        return client, calls

    return factory


def make_registry(store, llm, settings):
    reg = ToolRegistry(store)
    register_outline_tools(reg, store=store, llm=llm,
                           prompts=PromptLoader(home_dir=store.root), settings=settings)
    return reg


def _payload(count=2):
    arcs = []
    for vol, (title, goal, cap) in enumerate(
            [("崛起", "踏上修行路", "约40章"), ("征战", "统一北境", "约35章")], start=1):
        arcs.append({"vol": vol, "title": title, "goal": goal, "summary": f"概要{vol}",
                     "conflict": f"冲突{vol}", "resolution": f"收束{vol}", "capacity_hint": cap})
    return {"planned_volume_count": count, "arcs": arcs}


def test_plan_arcs_success_writes_arcs_and_digest_shows_goal(store, make_llm):
    llm, calls = make_llm([json.dumps(_payload(), ensure_ascii=False)])
    reg = make_registry(store, llm, make_settings())
    obs = reg.execute("plan_arcs", {"instruction": "规划两卷骨架"},
                      allowed=frozenset({"plan_arcs"}))
    assert "已落 2 卷骨架" in obs
    assert len(calls) == 1
    assert calls[0]["model"] == "pro-model"  # pro 档模型名来自 settings
    arcs = store.read_arcs()
    assert [a["vol"] for a in arcs] == [1, 2]
    assert arcs[0]["goal"] == "踏上修行路"        # 目标键名 goal：与 digest 同一键
    assert arcs[0]["capacity_hint"] == "约40章"   # 容量参考键名 capacity_hint
    digest = progress_digest(store)               # 写卷骨架 → digest 输出含目标文本
    assert "踏上修行路" in digest


def test_plan_arcs_not_read_only(store, make_llm):
    llm, _ = make_llm([json.dumps(_payload(), ensure_ascii=False)])
    reg = make_registry(store, llm, make_settings())
    assert reg.is_read_only("plan_arcs") is False


def test_plan_arcs_empty_response_retries_once_then_succeeds(store, make_llm):
    llm, calls = make_llm(["", json.dumps(_payload(), ensure_ascii=False)])
    reg = make_registry(store, llm, make_settings())
    obs = reg.execute("plan_arcs", {"instruction": "规划两卷骨架"},
                      allowed=frozenset({"plan_arcs"}))
    assert "已落 2 卷骨架" in obs
    assert len(calls) == 2
    assert calls[1]["max_tokens"] == CAP_MAX_TOKENS  # 空内容直接顶 cap 32768


def test_plan_arcs_empty_twice_returns_failure_observation(store, make_llm):
    llm, calls = make_llm(["", ""])
    reg = make_registry(store, llm, make_settings())
    obs = reg.execute("plan_arcs", {"instruction": "规划两卷骨架"},
                      allowed=frozenset({"plan_arcs"}))
    assert len(calls) == 2                       # 扩额只重试一次
    assert calls[1]["max_tokens"] == CAP_MAX_TOKENS
    assert "失败" in obs and "empty" in obs      # 如实分类，不 raise


def test_plan_arcs_bad_json_classified(store, make_llm):
    llm, calls = make_llm(["这不是 JSON", "{{{"])
    reg = make_registry(store, llm, make_settings())
    obs = reg.execute("plan_arcs", {"instruction": "规划两卷骨架"},
                      allowed=frozenset({"plan_arcs"}))
    assert len(calls) == 2
    assert calls[1]["max_tokens"] == min(CAP_MAX_TOKENS, calls[0]["max_tokens"] * 2)
    assert "失败" in obs and "bad_json" in obs
    assert store.read_arcs() == []               # 失败路径零写盘


def test_plan_arcs_starved_classified_as_truncated(store, make_llm):
    starved = {"content": "", "finish": "length"}  # finish_reason=length + 空内容 = 截断
    llm, calls = make_llm([starved, starved])
    reg = make_registry(store, llm, make_settings())
    obs = reg.execute("plan_arcs", {"instruction": "规划两卷骨架"},
                      allowed=frozenset({"plan_arcs"}))
    assert len(calls) == 2
    assert "失败" in obs and "truncated" in obs


def test_plan_arcs_missing_instruction(store, make_llm):
    llm, calls = make_llm([json.dumps(_payload(), ensure_ascii=False)])
    reg = make_registry(store, llm, make_settings())
    obs = reg.execute("plan_arcs", {}, allowed=frozenset({"plan_arcs"}))
    assert "instruction" in obs
    assert calls == []                           # 参数不齐不调 LLM
    assert store.read_arcs() == []


def test_plan_arcs_unknown_provider_returns_observation(store, make_llm):
    llm, calls = make_llm([json.dumps(_payload(), ensure_ascii=False)])
    settings = make_settings()
    settings.default_provider = "nope"
    reg = make_registry(store, llm, settings)
    obs = reg.execute("plan_arcs", {"instruction": "规划"}, allowed=frozenset({"plan_arcs"}))
    assert "失败" in obs and "provider" in obs
    assert calls == []


async def test_plan_arcs_await_returns_tool_result_with_usage(store, make_llm):
    """Task 8 迁移：直接 await 模块级协程得 ToolResult——观测文本与 registry 路径一致、
    usage 非 None（入账路径：loop 对 ToolResult.usage 同账累加，P1 预算缺口修复）。"""
    llm, calls = make_llm([json.dumps(_payload(), ensure_ascii=False)])
    result = await plan_arcs(store, llm, PromptLoader(home_dir=store.root),
                             make_settings(), {"instruction": "规划两卷骨架"})
    assert isinstance(result, ToolResult)
    assert "已落 2 卷骨架" in result.text
    assert result.usage is not None
    assert result.usage.prompt_tokens == 3 and result.usage.completion_tokens == 7
    assert len(calls) == 1
    assert [a["vol"] for a in store.read_arcs()] == [1, 2]

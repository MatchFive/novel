"""master_plan 段二：逐卷并发情节规划（Semaphore(3)）/ with_shrink 收缩二轮 / 标题剥后缀 /
seq 按成功数重编 / 逐卷 emit 事件 / 失败卷如实上报不 raise / usage 跨卷聚合 / need_web 键忽略。

LLM 全 mock（JSON body MockTransport——complete 非流式），响应按 prompt 内容路由
（【本卷】标题定位卷、【收缩重试】附注定位二轮），不受并发交错影响。
并发上限经 SpyLLM 钉死：complete 外包一层计 active，sleep(0) 强制挂起点模拟真实网络延迟。
"""
import asyncio
import json
from pathlib import Path

import httpx
import pytest

from harness.config import ProviderConfig, Settings
from harness.llm.client import CompletionUsage, OpenAICompatClient
from harness.projects import create_project
from harness.prompts.loader import PromptLoader
from harness.store.project import ProjectStore
from harness.tools.plots import master_plan, register_plot_tools
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
    s = ProjectStore(info.path)
    s.upsert_arcs([
        {"vol": 1, "title": "卷甲", "goal": "g1", "summary": "s1",
         "conflict": "c1", "resolution": "r1"},
        {"vol": 2, "title": "卷乙", "goal": "g2", "summary": "s2",
         "conflict": "c2", "resolution": "r2"},
    ])
    return s


@pytest.fixture
def make_llm():
    """router(body) -> (content: str, usage: dict | None)；记录每次请求 body 供断言。"""
    def factory(router):
        calls = []

        def handler(req):
            body = json.loads(req.content)
            calls.append(body)
            content, usage = router(body)
            payload = {"choices": [{"message": {"content": content},
                                    "finish_reason": "stop"}]}
            if usage:
                payload["usage"] = usage
            return httpx.Response(200, json=payload)

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                 base_url="https://api.test")
        client = OpenAICompatClient(
            ProviderConfig(base_url="https://api.test", api_key="k",
                           lite="m", standard="m", pro="m"),
            http_client=http)
        return client, calls

    return factory


class SpyLLM:
    """complete 外包一层：统计最大并发 active；sleep(0) 强制挂起点（模拟真实网络延迟）。

    串行实现下 active 永不 >1；Semaphore(3) 下四卷并发 max_active 恰好钉在 3。
    """

    def __init__(self, inner):
        self._inner = inner
        self.active = 0
        self.max_active = 0

    async def complete(self, messages, **kwargs):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0)
            return await self._inner.complete(messages, **kwargs)
        finally:
            self.active -= 1


class Collector:
    """emit 收集器：events 为 [(kind, payload), ...]。"""

    def __init__(self):
        self.events = []

    def emit(self, kind, payload):
        self.events.append((kind, payload))


def U(p=10, c=20):
    return {"prompt_tokens": p, "completion_tokens": c, "total_tokens": p + c}


def plot(title, summary="概要", **kw):
    return {"title": title, "summary": summary, "conflict": "冲突", "resolution": "收束",
            "beats": ["起", "承"], **kw}


def payload(*plots):
    """段二契约输出：plots + characters + need_web（need_web 必须被忽略、不得拒绝）。"""
    return json.dumps({"plots": list(plots),
                       "characters": [{"name": "酱油", "role_type": "support"}],
                       "need_web": ["某现实事实"]}, ensure_ascii=False)


def route_by_title(body, table, default_usage=(10, 20)):
    """按【本卷】标题从 table 取响应内容；返回 (content, usage)。"""
    prompt = body["messages"][0]["content"]
    for title, content in table.items():
        if f"【本卷】{title}" in prompt:
            return content, U(*default_usage)
    raise AssertionError(f"路由不到卷标题：{prompt[:80]}…")


async def run_tool(store, llm, emit, args=None):
    return await master_plan(store, llm, PromptLoader(home_dir=store.root),
                             make_settings(), emit.emit, args or {})


async def test_four_vols_concurrent_semaphore_capped_at_3(store, make_llm):
    store.upsert_arcs([
        {"vol": 3, "title": "卷丙", "goal": "g3", "summary": "s3",
         "conflict": "c3", "resolution": "r3"},
        {"vol": 4, "title": "卷丁", "goal": "g4", "summary": "s4",
         "conflict": "c4", "resolution": "r4"},
    ])

    def router(body):
        return route_by_title(body, {
            "卷甲": payload(plot("甲·开端"), plot("甲·收束")),
            "卷乙": payload(plot("乙·开端"), plot("乙·收束")),
            "卷丙": payload(plot("丙·开端"), plot("丙·收束")),
            "卷丁": payload(plot("丁·开端"), plot("丁·收束")),
        })

    inner, calls = make_llm(router)
    llm = SpyLLM(inner)
    collector = Collector()
    result = await run_tool(store, llm, collector)
    assert llm.max_active == 3              # 并发上限 = Semaphore(3)，且确有并发（>1）
    assert "成功 4 卷" in result.text
    for vol, tag in [(1, "甲"), (2, "乙"), (3, "丙"), (4, "丁")]:
        plots = store.read_plots(vol)
        assert [p["title"] for p in plots] == [f"{tag}·开端", f"{tag}·收束"]
    assert len(calls) == 4                  # 每卷一轮成功，无多余重试
    # usage 跨卷聚合：4 卷 × U(10, 20)
    assert result.usage == CompletionUsage(prompt_tokens=40, completion_tokens=80)


async def test_shrink_second_round_appends_note(store, make_llm):
    def router(body):
        prompt = body["messages"][0]["content"]
        if "【收缩重试】" in prompt:
            return payload(plot("回收")), U(10, 20)
        return '{"plots": []}', U(5, 5)     # 首轮 plots 解析为空 → 触发收缩二轮

    llm, calls = make_llm(router)
    collector = Collector()
    result = await run_tool(store, llm, collector, {"vol": 1})
    assert len(calls) == 2                  # 首轮 + 收缩二轮，恰好两轮
    assert "【收缩重试】" not in calls[0]["messages"][0]["content"]
    assert "【收缩重试】" in calls[1]["messages"][0]["content"]
    assert "只出 4 个情节" in calls[1]["messages"][0]["content"]  # 收缩 note 逐字
    plots = store.read_plots(1)
    assert [p["title"] for p in plots] == ["回收"]
    assert result.usage == CompletionUsage(prompt_tokens=15, completion_tokens=25)  # 两轮都记账


async def test_p13_master_plan_uses_planned_volume_count(store, make_llm):
    """**P13**：段二的「每卷约 X 章」分母用**落盘的预计总卷数**，不是实际弧数。

    「预计 20 卷、只规划了 3 卷」时用实际弧数 → 每卷参考章数被算成 total/3
    （模型会以为每卷要写三分之一全书）。
    """
    # 项目卡：目标总章数 600
    (store.root / "README.md").write_text(
        "题材：仙侠\n基调：冷峻\n结局：开放\n每章字数：3000\n目标总章数：600\n"
        "预计卷数：20\n", encoding="utf-8")
    # 落盘：只有 2 卷，但**预计 20 卷**
    store.upsert_arcs([{"vol": 1, "title": "卷甲", "goal": "g", "summary": "s",
                        "conflict": "c", "resolution": "r"},
                       {"vol": 2, "title": "卷乙", "goal": "g", "summary": "s",
                        "conflict": "c", "resolution": "r"}],
                      planned_volume_count=20)

    def router(body):
        return route_by_title(body, {"卷甲": payload(plot("开端"))})

    llm, calls = make_llm(router)
    collector = Collector()
    await run_tool(store, llm, collector, {"vol": 1})

    prompt = calls[0]["messages"][-1]["content"]
    assert "约 30 章" in prompt              # 600 / 20（不是 600 / 2 = 300）
    assert "约 300 章" not in prompt


async def test_titles_stripped_empty_dropped_seq_renumbered(store, make_llm):
    long_summary = "这是一个没有标题只有概要兜底的长情节"  # >20 字，验证 summary[:20] 回退
    body_plots = [
        plot("斗法（第3—5章）", summary="概要A"),
        {},                                     # 整条空 → 丢弃
        {"summary": long_summary, "conflict": "冲突C"},  # 标题空 → summary[:20] 兜底
    ]

    def router(body):
        return route_by_title(body, {"卷甲": payload(*body_plots)})

    llm, calls = make_llm(router)
    collector = Collector()
    result = await run_tool(store, llm, collector, {"vol": 1})
    assert "成功 1 卷" in result.text
    plots = store.read_plots(1)
    assert [p["seq"] for p in plots] == [1, 2]            # seq 按成功条数重编 1..N
    assert plots[0]["title"] == "斗法"                    # 「（第3—5章）」区间标注剥掉
    assert plots[1]["title"] == long_summary[:20]
    assert all(p["status"] == "planned" for p in plots)
    assert len(calls) == 1


async def test_failed_vol_reported_not_raised(store, make_llm):
    def router(body):
        prompt = body["messages"][0]["content"]
        if "【本卷】卷乙" in prompt:
            return "这不是 JSON", U(1, 2)     # 四轮（含收缩重试）全坏 → 该卷失败
        return route_by_title(body, {"卷甲": payload(plot("甲·唯一"))}, default_usage=(10, 20))

    llm, _ = make_llm(router)
    collector = Collector()
    result = await run_tool(store, llm, collector)        # 不 raise
    assert "成功 1 卷" in result.text and "失败 1 卷" in result.text
    assert "bad_json" in result.text
    assert [p["title"] for p in store.read_plots(1)] == ["甲·唯一"]
    assert store.read_plots(2) == []                      # 失败卷零写盘
    # usage 连失败卷的尝试也如实聚合：甲 U(10,20) + 乙 U(1,2)×4
    assert result.usage == CompletionUsage(prompt_tokens=14, completion_tokens=28)
    # 失败卷：无 plan_arc（零落库）但有 planning_progress（failed=1 如实上报）
    p3 = collector.events[2][1]
    assert p3["vol"] == 2 and p3["failed"] == 1 and p3["ok"] == 1


async def test_emit_sequence_per_vol_and_payload_counts(store, make_llm):
    def router(body):
        return route_by_title(body, {
            "卷甲": payload(plot("甲·开端"), plot("甲·收束")),
            "卷乙": payload(plot("乙·开端")),
        })

    llm, _ = make_llm(router)
    collector = Collector()
    await run_tool(store, llm, collector)
    (k1, p1), (k2, p2), (k3, p3), (k4, p4) = collector.events
    assert [k1, k2, k3, k4] == ["plan_arc", "planning_progress",
                                "plan_arc", "planning_progress"]
    p1, p2, p3, p4 = [p for _, p in collector.events]
    assert p1["vol"] == 1 and p1["done"] == 1 and p1["total"] == 2
    assert p1["plots"] == ["甲·开端", "甲·收束"]
    assert p2["done"] == 1 and p2["ok"] == 1 and p2["failed"] == 0
    assert p3["vol"] == 2 and p3["done"] == 2
    assert p4["done"] == 2 and p4["ok"] == 2 and p4["failed"] == 0


async def test_need_web_and_characters_keys_ignored(store, make_llm):
    # payload() 恒带 characters/need_web 键——逐字落库观测即「忽略不拒绝」的证明
    def router(body):
        return route_by_title(body, {"卷甲": payload(plot("甲·开端"))})

    llm, _ = make_llm(router)
    collector = Collector()
    result = await run_tool(store, llm, collector, {"vol": 1})
    assert "成功 1 卷" in result.text
    assert len(store.read_plots(1)) == 1


async def test_empty_arcs_advises_plan_arcs(tmp_path, make_llm):
    info = create_project(tmp_path, "空骨架书")   # 未 plan_arcs：全库无卷骨架

    def router(body):  # pragma: no cover - 不该被调到
        raise AssertionError("空 arcs 不应触发 LLM 调用")

    llm, calls = make_llm(router)
    collector = Collector()
    result = await run_tool(ProjectStore(info.path), llm, collector)
    assert "plan_arcs" in result.text and "失败" in result.text  # 指引先产卷骨架，不 raise
    assert calls == [] and result.usage is None


async def test_vol_arg_plans_only_that_vol(store, make_llm):
    def router(body):
        prompt = body["messages"][0]["content"]
        assert "【本卷】卷乙" in prompt and "【本卷】卷甲" not in prompt
        return route_by_title(body, {"卷乙": payload(plot("乙·专属"))})

    llm, calls = make_llm(router)
    collector = Collector()
    result = await run_tool(store, llm, collector, {"vol": 2})
    assert "成功 1 卷" in result.text
    assert len(calls) == 1                                # 只规划指定卷
    assert [p["title"] for p in store.read_plots(2)] == ["乙·专属"]
    assert store.read_plots(1) == []


async def test_vol_arg_unknown_or_bad_type(store, make_llm):
    def router(body):  # pragma: no cover - 不该被调到
        raise AssertionError("vol 参数不合法时不应触发 LLM 调用")

    llm, calls = make_llm(router)
    collector = Collector()
    missing = await run_tool(store, llm, collector, {"vol": 99})
    assert "未找到" in missing.text and "第 99 卷" in missing.text
    bad = await run_tool(store, llm, collector, {"vol": "2"})
    assert "vol" in bad.text and "int" in bad.text
    assert calls == []


def test_register_via_registry_returns_text(store, make_llm):
    from harness.tools.base import _run_sync  # noqa: F401 - 同步边界由 handler 内部走

    def router(body):
        return route_by_title(body, {"卷甲": payload(plot("甲·开端"))})

    llm, _ = make_llm(router)
    reg = ToolRegistry(store)
    register_plot_tools(reg, store=store, llm=llm,
                        prompts=PromptLoader(home_dir=store.root), settings=make_settings())
    assert reg.is_read_only("master_plan") is False
    obs = reg.execute("master_plan", {"vol": 1}, allowed=frozenset({"master_plan"}))
    assert isinstance(obs, str) and "成功 1 卷" in obs   # registry 契约 str
    assert [p["title"] for p in store.read_plots(1)] == ["甲·开端"]


async def test_registry_handler_refuses_inside_running_loop(store, make_llm):
    def router(body):
        return route_by_title(body, {"卷甲": payload(plot("甲·开端"))})

    llm, _ = make_llm(router)
    reg = ToolRegistry(store)
    register_plot_tools(reg, store=store, llm=llm,
                        prompts=PromptLoader(home_dir=store.root), settings=make_settings())
    with pytest.raises(RuntimeError, match="await"):
        reg.execute("master_plan", {"vol": 1}, allowed=frozenset({"master_plan"}))

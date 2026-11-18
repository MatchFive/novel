"""P2 计划链工具接进 loop：master_plan/plan_chapters 走 await 分支（即便 registry 已注册）/
工具 usage 入预算账（决策+工具合计判 budget_exceeded）/ emit 经 EventLog.append 落 events.log。

LLM 全 mock（FakeLLM 顺序脚本）；master_plan/plan_chapters 返回 ToolResult，loop 归一
(text, usage)——str 路径（plan_arcs/edit_by_command）行为不动（P1 测试在跑）。
registry 同步路径的 no-op emit 防御性钉死在 tests/tools/test_plots.py
（test_register_via_registry_returns_text）。
"""
import asyncio
import json
from pathlib import Path

import pytest

from harness.agent.loop import AgentLoop
from harness.config import ProviderConfig, Settings
from harness.llm.client import CompletionUsage
from harness.projects import create_project
from harness.prompts.loader import PromptLoader
from harness.run.approval import ApprovalUI, Choice
from harness.run.constants import BUDGET_MAX_TOKENS, WORK_MEMORY_REPLAY
from harness.run.events import EventLog
from harness.run.memory import WorkMemory
from harness.run.state import RunState
from harness.store.project import ProjectStore
from harness.tools import readonly_registry, register_modify_tools, register_outline_tools
from harness.tools.chapters import register_chapter_tools
from harness.tools.plots import register_plot_tools


class _FakeCompletion:
    def __init__(self, text, usage=None, tool_calls=None):
        self.text = text
        self.usage = usage
        self.tool_calls = tool_calls


class FakeLLM:
    """顺序吐脚本的假 llm：str = content 协议 JSON；dict 含 tool_calls = 原生 tool_call 协议。"""

    def __init__(self, script, *, usage=None):
        self._script = list(script)
        self.calls = []
        self.closed = False
        self._usage = usage
        self.provider = ProviderConfig(base_url="https://api.test", api_key="k",
                                       lite="m", standard="standard-model", pro="pro-model")

    async def complete(self, messages, *, model, temperature=0.7, max_tokens=8192, thinking=False):
        self.calls.append({"model": model, "temperature": temperature,
                           "prompt": messages[0]["content"]})
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, dict) and "tool_calls" in item:
            return _FakeCompletion(text=item.get("content", ""),
                                   tool_calls=item["tool_calls"], usage=self._usage)
        return _FakeCompletion(text=item, usage=self._usage)

    async def aclose(self):
        self.closed = True


def make_settings():
    return Settings(
        library_root=Path("x"),
        providers={"test": ProviderConfig(base_url="https://api.test", api_key="k",
                                          lite="m", standard="standard-model", pro="pro-model")},
        default_provider="test",
        default_tier="standard",
        web_verify=True,
    )


@pytest.fixture
def store(tmp_path):
    info = create_project(tmp_path, "测试书")
    s = ProjectStore(info.path)
    s.upsert_arcs([{"vol": 1, "title": "卷甲", "goal": "g1", "summary": "s1",
                    "conflict": "c1", "resolution": "r1"}])
    return s


def make_loop(store, script, *, state=None, deliverables=("outline",), ui=None, usage=None,
              register_p2=False):
    llm = FakeLLM(script, usage=usage)
    settings = make_settings()
    prompts = PromptLoader(home_dir=store.root)
    ui = ui or ApprovalUI(ask=lambda q, o, m=False, note=None: Choice(indices=[0]))
    registry = readonly_registry(store)
    register_outline_tools(registry, store=store, llm=llm, prompts=prompts, settings=settings)
    register_modify_tools(registry, store=store, llm=llm, prompts=prompts, ui=ui)
    if register_p2:
        register_plot_tools(registry, store=store, llm=llm, prompts=prompts, settings=settings)
        register_chapter_tools(registry, store=store, llm=llm, prompts=prompts, settings=settings)
    events = EventLog(store.root)
    state = state or RunState.new("测试 brief：规划链", list(deliverables))
    memory = WorkMemory.rebuild(events.tail(WORK_MEMORY_REPLAY * 2))
    loop = AgentLoop(store=store, state=state, events=events, memory=memory,
                     registry=registry, ui=ui, llm=llm, prompts=prompts,
                     deliverables=list(deliverables), settings=settings)
    return loop, llm, events, state


def _finish(summary="完成"):
    return json.dumps({"thought": "目标达成", "say": "收工",
                       "action": "finish", "args": {"summary": summary}}, ensure_ascii=False)


def _kinds(events):
    return [e["kind"] for e in events.tail(1000)]


def _plots_payload(title="甲·开端"):
    return json.dumps({"plots": [{"title": title, "summary": "概要", "conflict": "冲突",
                                  "resolution": "收束", "beats": ["起", "承"]}],
                       "characters": [{"name": "酱油", "role_type": "support"}],
                       "need_web": ["某现实事实"]}, ensure_ascii=False)


def test_master_plan_tool_usage_counted_budget_exceeded_on_combined_total(store):
    """单看决策 usage（1.2M）不破 2M 预算；决策+工具合计（2.4M）超线——
    step 末预算检查按合计口径触发 budget_exceeded，工具真实执行过（情节已落库）。"""
    plan = {"tool_calls": [{"name": "master_plan", "arguments": json.dumps({"vol": 1})}]}
    usage = CompletionUsage(prompt_tokens=1_200_000, completion_tokens=0)
    loop, llm, events, state = make_loop(store, [plan, _plots_payload()], usage=usage)
    result = asyncio.run(loop.run())
    assert result.status == "paused"
    assert len(llm.calls) == 2                    # 决策 + master_plan 内部一轮（无重试）
    assert state.stats["token_used"] == 2_400_000
    assert state.stats["token_used"] > BUDGET_MAX_TOKENS
    kinds = _kinds(events)
    assert "budget_exceeded" in kinds
    assert "plan_arc" in kinds and "planning_progress" in kinds   # emit 落 events.log
    assert [p["title"] for p in store.read_plots(1)] == ["甲·开端"]  # 工具真执行


def test_plan_chapters_emits_chapters_planned_via_event_log(store):
    """plan_chapters 全程经 loop 的 await 分支：chapters_planned（units/beats/selfcheck
    三阶段）与 plan_arc 语义一致的 emit 行可 tail 到；决策与工具 usage 同账累加。"""
    store.upsert_plots(1, [{"seq": 1, "title": "开端", "summary": "概要一", "conflict": "冲突一",
                            "resolution": "收束一", "beats": ["起", "承"], "status": "planned"}])

    def unit(seq, words, event):
        return {"seq": seq, "time": "清晨", "location": "山脚", "characters": ["甲"],
                "event": event, "expected_words": words, "covers": f"覆盖{seq}"}

    # 5 段×800 字、目标 4000 → 1 章；beats 5 拍一次过（min_beats(4000)=5）
    units = json.dumps({"units": [unit(i, 800, f"事件{i}") for i in range(1, 6)],
                        "need_web": []}, ensure_ascii=False)
    headers = json.dumps({"chapters": [{"index": 1, "title": "初章", "objective": "启程"}],
                          "need_web": []}, ensure_ascii=False)
    beats = json.dumps({
        "scene": "第1章场景", "characters": [{"name": "甲", "role": "主角"}],
        "plot": [{"pov": "甲", "location": "山道", "event": f"第{k + 1}拍", "weight": 2}
                 for k in range(5)],
        "dialogue_hooks": [], "humor_points": [], "ending_hook": "钩子",
        "need_web": []}, ensure_ascii=False)
    selfcheck = json.dumps({"ok": True, "issues": [], "fixes": [], "need_web": []},
                           ensure_ascii=False)
    call = {"tool_calls": [{"name": "plan_chapters",
                            "arguments": json.dumps({"plot_id": "1-1"})}]}
    usage = CompletionUsage(prompt_tokens=100, completion_tokens=50)
    # 消耗序：决策 → units → headers → beats(1 章) → selfcheck → finish 决策
    script = [call, units, headers, beats, selfcheck, _finish()]
    loop, llm, events, state = make_loop(store, script, usage=usage)
    result = asyncio.run(loop.run())
    assert result.status == "done"
    assert len(llm.calls) == 6
    assert state.stats["token_used"] == 6 * 150   # 决策与工具 usage 同账
    phases = [e["payload"].get("phase") for e in events.tail(1000)
              if e["kind"] == "chapters_planned"]
    assert phases == ["units", "beats", "selfcheck"]
    chapters = store.list_chapters()
    assert len(chapters) == 1 and chapters[0].meta["title"] == "初章"
    assert len(chapters[0].meta["beats"]) == 5


def test_master_plan_awaited_even_when_registered_in_registry(store):
    """防御钉死：master_plan/plan_chapters 即便已注册进 registry，loop 也永远走 await
    分支——若误走 registry 同步路径，事件循环内 _run_sync 会 RuntimeError（观测 FAILED）。"""
    plan = {"tool_calls": [{"name": "master_plan", "arguments": json.dumps({"vol": 1})}]}
    loop, _llm, events, _state = make_loop(store, [plan, _plots_payload(), _finish()],
                                           register_p2=True)
    result = asyncio.run(loop.run())
    assert result.status == "done"
    assert "plan_arc" in _kinds(events)           # await 路径 emit 可达
    obs = [e for e in events.tail(100) if e["kind"] == "tool_result"
           and e["payload"]["tool"] == "master_plan"]
    assert len(obs) == 1 and "RuntimeError" not in obs[0]["payload"]["observation"]
    assert [p["title"] for p in store.read_plots(1)] == ["甲·开端"]


# ---------------------------------------------------------------- 分类契约（终审 Important 1）
# 工具失败串与 loop._classify 的契约：total 失败须含「失败（类别）」前缀（判 failed 进熔断）；
# 部分成功（≥1 项落库）用「未成功」措辞，不匹配失败词表——判 ok 清 fail_streak。

def test_plan_chapters_bad_plot_id_classifies_failed_counts_fail_streak(store):
    """plan_chapters 缺参观测「plan_chapters 失败（参数）：…」→ _classify 判 failed：
    连续两次 fail_streak≥2 → 熔断冷却（若误判 ok 则永不冷却）。"""
    call = {"tool_calls": [{"name": "plan_chapters",
                            "arguments": json.dumps({"plot_id": "bad"})}]}
    loop, llm, events, _state = make_loop(store, [call, call, call, _finish()],
                                          register_p2=True)
    result = asyncio.run(loop.run())
    assert result.status == "done"
    assert len(llm.calls) == 4                    # 4 次步决策（坏 plot_id 零 LLM 消耗）
    results = [e for e in events.tail(100) if e["kind"] == "tool_result"]
    assert "失败（参数）" in results[0]["payload"]["observation"]
    assert not results[0]["payload"]["observation"].startswith("FAILED")  # 参数类非瞬时
    cooled = [e for e in results if "冷却中" in e["payload"].get("observation", "")]
    assert len(cooled) == 1 and cooled[0]["payload"]["tool"] == "plan_chapters"


def test_p13_breaker_is_per_tool_not_global(store):
    """**P13**：熔断按**工具**分桶——A 失败一次 + B 失败一次 **不**冷却 B。

    设计 §1.2：「**同工具**连续失败≥2 → 冷却」。原先是全局计数 → 别的工具失败一次
    就会把**只失败过一次的**工具也冷却掉（连坐）。
    """
    # **锐利对照**：A（plan_chapters）失败一次 → B（get_project_state，**只读零 LLM**）
    # **成功** → A 再失败一次 → A 是否被冷却？
    #   · 分桶（设计 §1.2「同工具连续失败≥2」）：B 的成功**不给 A 清账** → A 冷却 ✓
    #   · 旧的全局计数：B 的成功把全局账清了 → A 只失败一次 → **不冷却**
    bad_a = {"tool_calls": [{"name": "plan_chapters",
                             "arguments": json.dumps({"plot_id": "bad"})}]}
    ok_b = {"tool_calls": [{"name": "get_project_state", "arguments": "{}"}]}
    finish = {"tool_calls": [{"name": "finish", "arguments": "{}"}]}
    loop, _llm, events, _state = make_loop(
        store, [bad_a, ok_b, bad_a, bad_a, finish], register_p2=True)

    asyncio.run(loop.run())

    cooled = [e for e in events.tail(100) if e["kind"] == "tool_result"
              and "冷却中" in e["payload"].get("observation", "")]
    assert len(cooled) == 1                       # A 被冷却 → 冷却期内那一次调用被拒
    assert cooled[0]["payload"]["tool"] == "plan_chapters"
    # B 自己从没失败 → **不该**被连坐冷却
    assert not [e for e in cooled if e["payload"]["tool"] == "get_project_state"]


def test_master_plan_partial_outcome_classifies_ok_resets_fail_streak(store):
    """master_plan 部分成功（卷甲落库、卷乙 bad_json）观测只含「未成功」→ _classify 判 ok：
    fail_streak 被清账（前一步 failed 不计入），effective_steps 计入该步（finish 终态步另计）。"""
    bad_ch = {"tool_calls": [{"name": "plan_chapters",
                              "arguments": json.dumps({"plot_id": "bad"})}]}
    mp = {"tool_calls": [{"name": "master_plan", "arguments": json.dumps({})}]}
    store.upsert_arcs([{"vol": 2, "title": "卷乙", "goal": "g2", "summary": "s2",
                        "conflict": "c2", "resolution": "r2"}])   # 卷甲 ok / 卷乙 bad_json
    loop, llm, events, state = make_loop(
        store, [bad_ch, mp, _plots_payload(), bad_ch, bad_ch, bad_ch, _finish()],
        register_p2=True)
    # 按 prompt 路由 master_plan 的逐卷调用：卷甲吃脚本里的成功载荷，卷乙恒坏 JSON
    real_complete = llm.complete

    async def routed(messages, **kwargs):
        prompt = messages[0]["content"]
        if "【本卷】卷乙" in prompt:
            return _FakeCompletion(text="这不是 JSON")
        return await real_complete(messages, **kwargs)

    llm.complete = routed
    result = asyncio.run(loop.run())
    assert result.status == "done"
    mp_obs = next(e for e in events.tail(100) if e["kind"] == "tool_result"
                  and e["payload"]["tool"] == "master_plan")
    assert "未成功" in mp_obs["payload"]["observation"]
    assert "失败（" not in mp_obs["payload"]["observation"]
    assert not mp_obs["payload"]["observation"].startswith("FAILED")
    # ok 清账：部分成功步有效（finish 终态步也计 1）
    assert state.stats["effective_steps"] == 2       # master_plan 部分成功步 + finish 步
    cooled = [e for e in events.tail(100) if e["kind"] == "tool_result"
              and "冷却中" in e["payload"].get("observation", "")]
    # **熔断按工具分桶**（P13，设计 §1.2「同工具连续失败≥2」）：plan_chapters 在第 1、3 步
    # 各失败一次 → 第 3 步就熔断（**别的工具** master_plan 第 2 步成功**不给它清账**）
    # → 冷却 3 步内第 4、5 步两次调用都被拒 → 2 条「冷却中」。
    # （旧语义是全局计数：master_plan 的成功把 plan_chapters 的账清了 → 熔断推迟到第 4 步。）
    assert len(cooled) == 2
    assert [p["title"] for p in store.read_plots(1)] == ["甲·开端"]
    assert store.read_plots(2) == []

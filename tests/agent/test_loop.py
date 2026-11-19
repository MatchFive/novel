"""AgentLoop：plan_arcs→finish 主路径 / 白名单门 / 冷却熔断 / 瞬时失败 / 预算 / 并行只读。

LLM 全 mock（FakeLLM 顺序脚本，complete 级注入）；plan_arcs/edit_by_command 由 loop
直接 await 模块级协程（Task 11 接缝：registry 同步 handler 在事件循环内会 RuntimeError）。
"""
import asyncio
import json
from pathlib import Path

import pytest

from harness.agent.loop import AgentLoop
from harness.config import ProviderConfig, Settings
from harness.llm.client import CompletionUsage, LLMError
from harness.projects import create_project
from harness.prompts.loader import PromptLoader
from harness.run.approval import ApprovalUI, Choice
from harness.run.constants import BUDGET_MAX_TOKENS, WORK_MEMORY_REPLAY
from harness.run.events import EventLog
from harness.run.memory import WorkMemory
from harness.run.state import RunState
from harness.store.project import ProjectStore
from harness.tools import readonly_registry, register_modify_tools, register_outline_tools


class _FakeCompletion:
    """complete() 返回值：text + usage + 可选 tool_calls（tool_calls 协议注入缝）。"""

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
    return ProjectStore(info.path)


def make_loop(store, script, *, state=None, deliverables=("outline",), ui=None, usage=None,
              extra_readonly=None):
    llm = FakeLLM(script, usage=usage)
    settings = make_settings()
    prompts = PromptLoader(home_dir=store.root)
    ui = ui or ApprovalUI(ask=lambda q, o, m=False, note=None: Choice(indices=[0]))
    registry = readonly_registry(store)
    for name, handler in (extra_readonly or {}).items():
        registry.register(name, handler, read_only=True)
    register_outline_tools(registry, store=store, llm=llm, prompts=prompts, settings=settings)
    register_modify_tools(registry, store=store, llm=llm, prompts=prompts, ui=ui)
    events = EventLog(store.root)
    state = state or RunState.new("测试 brief：写卷骨架", list(deliverables))
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


def _arcs_payload():
    return json.dumps({"planned_volume_count": 2, "arcs": [
        {"vol": 1, "title": "崛起", "goal": "踏上修行路", "summary": "s1",
         "conflict": "c1", "resolution": "r1", "capacity_hint": "约40章"},
        {"vol": 2, "title": "征战", "goal": "统一北境", "summary": "s2",
         "conflict": "c2", "resolution": "r2", "capacity_hint": "约35章"},
    ]}, ensure_ascii=False)


# ---------- P31/A4：suggested_deliverables 的行为层用法（行动参考注记，不碰权限面） ----------


def _step_prompt(store, brief_extra: dict, deliverables=("outline",)):
    """组一帧步决策提示词（不走 LLM）：brief_extra 并进 state.brief 后直调 _build_prompt。"""
    from harness.run.whitelist import allowed_for_run

    state = RunState.new("brief 原文", list(deliverables))
    state.brief.update(brief_extra)
    loop, _llm, _events, state = make_loop(store, [], state=state, deliverables=deliverables)
    allowed = allowed_for_run(list(state.brief.get("deliverables") or deliverables))
    return loop._build_prompt(allowed)


def test_p31_suggested_deliverables_guidance_in_step_prompt(store):
    """**A4**：模型推断的交付物以**行动参考**注记进步决策提示词——引导「做什么」
    （组织推进方向 / finish 说明差距），注记明写边界仍以 brief.deliverables 为准。"""
    prompt = _step_prompt(store, {"suggested_deliverables": ["outline", "chapters"]},
                          deliverables=("characters",))
    assert "模型推断的交付物：outline、chapters" in prompt
    assert "仍以 brief.deliverables 为准" in prompt
    # **负向钉（P10 复审 D2）**：推断不扩权——characters 权限面没有 write_plot /
    # plan_arcs 工具签名（edit_by_command 的说明文字提到 write_plot 不算）。
    assert "- write_plot(args: {plot_id, seq_from?, seq_to?})" not in prompt
    assert "plan_arcs(args" not in prompt


def test_p31_no_guidance_when_suggested_absent_or_equal(store):
    """无推断 / 推断与授权相同 → 注记为 ""（{{suggested}} 贴段尾 → 字节零变化）。"""
    assert "模型推断的交付物" not in _step_prompt(store, {})
    assert "模型推断的交付物" not in \
        _step_prompt(store, {"suggested_deliverables": ["outline"]})


# ---------- P32/A8c：撞「零变更」→ noop 分类 + 同类工具改试提示（F1 修法 c） ----------


def test_p32_out_of_scope_zero_change_classified_noop():
    """edit_by_command 全部提议越界（无范围内文件可应用）→ 0 产出拒绝：noop
    （M3 口径：不算有效步、不进失败账——不把工具冷却掉）。"""
    obs = "edit_by_command：无范围内文件可应用（零变更）\n拒绝：chapters/0001.md（不适用）"
    assert AgentLoop._classify(obs) == "noop"


def test_p32_other_zero_change_shapes_keep_ok():
    """「内容与现状一致」非选错工具、「已取消」是人取消 → 分类不动（ok）。"""
    assert AgentLoop._classify("edit_by_command：提议内容与现状一致（零变更）") == "ok"
    assert AgentLoop._classify("edit_by_command：已取消，零变更（未写任何文件）") == "ok"


def test_p32_zero_change_sibling_hint_appended(store, monkeypatch):
    """loop 级：零变更观测落事件前追加「改试 write_plot」提示（模型下一步改试，
    而不是升级成 ask_user 要拍板）。"""
    from harness.agent import loop as loop_mod

    edit_step = json.dumps({"thought": "改个章标题", "say": "改标题",
                            "action": "edit_by_command",
                            "args": {"instruction": "改第1章标题"}}, ensure_ascii=False)
    loop, _llm, events, _state = make_loop(store, [edit_step, _finish()])
    obs = "edit_by_command：无范围内文件可应用（零变更）\n拒绝：chapters/0001.md（不适用）"

    async def fake_async_tool(action, args):
        return obs, "noop", None

    monkeypatch.setattr(loop, "_async_tool", fake_async_tool)
    result = asyncio.run(loop.run())
    assert result.status == "done"
    recorded = [e for e in events.tail(100) if e["kind"] == "tool_result"][-1]
    text = recorded["payload"]["observation"]
    assert "edit_by_command：无范围内文件可应用（零变更）" in text   # 原观测保留
    assert "write_plot" in text and "改试" in text                    # 同类提示在场
    assert loop_mod._zero_change_sibling_hint("别的观测") == "别的观测"   # 非零变更不动
    assert loop_mod._zero_change_sibling_hint(
        "edit_by_command：提议内容与现状一致（零变更）") == "edit_by_command：提议内容与现状一致（零变更）"


def test_plan_arcs_then_finish_done(store, capsys):
    plan = {"tool_calls": [{"name": "plan_arcs",
                            "arguments": json.dumps({"instruction": "两卷骨架"})}]}
    loop, llm, events, state = make_loop(store, [plan, _arcs_payload(), _finish()])
    result = asyncio.run(loop.run())
    assert result.status == "done" and state.status == "done"
    arcs = store.read_arcs()
    assert len(arcs) == 2 and arcs[0]["goal"] == "踏上修行路"
    kinds = _kinds(events)
    assert "agent_step" in kinds and "tool_result" in kinds and "done" in kinds
    assert llm.closed                                # stream close 权归 loop（含正常路径）
    assert llm.calls[0]["model"] == "pro-model"      # pro 档模型
    assert llm.calls[0]["temperature"] == 0.3
    assert "💭 收工" in capsys.readouterr().out        # say 打印
    assert (store.root / ".harness" / "state.json").exists()  # 每步落盘
    # prompt 组装：base + 工具签名 + brief + digest + memory
    prompt = llm.calls[0]["prompt"]
    assert "通用写作规则" in prompt and "【可用工具】" in prompt
    assert "plan_arcs" in prompt and "测试 brief" in prompt
    assert "【进度摘要】" in prompt and "【工作记忆】" in prompt


def test_whitelist_gate_counts_noop_and_pauses(store):
    """白名单外的动作 → 拒绝对观测（不执行）→ 计 NOPROGRESS → 第 6 次暂停。

    动作用 ``search_handbook``（设计里的教程手册工具，本仓未落地——P5 起由 skills 体系
    替代）：不在任何 deliverable 白名单内，走白名单门而非执行路径。
    """
    blocked = json.dumps({"thought": "先搜资料", "action": "search_handbook", "args": {"q": "x"}},
                         ensure_ascii=False)
    loop, llm, events, _state = make_loop(store, [blocked] * 6)
    result = asyncio.run(loop.run())
    assert result.status == "paused"
    assert len(llm.calls) == 6                       # 第 6 次 NOPROGRESS 即暂停
    assert "paused" in _kinds(events)
    obs = [e for e in events.tail(100) if e["kind"] == "tool_result"][-1]["payload"]["observation"]
    assert "白名单" in obs


def test_circuit_breaker_cooldown_blocks_retry(store):
    plan = {"tool_calls": [{"name": "plan_arcs", "arguments": json.dumps({"instruction": "x"})}]}
    # step1/2：决策给 plan_arcs，内部各重试一次（2 条 payload）→ 连续失败≥2 → 冷却
    # step3：决策仍给 plan_arcs → 冷却门拒绝（不再消费 payload）；step4：finish
    script = [plan, "bad", "bad", plan, "bad", "bad", plan, _finish()]
    loop, llm, events, _state = make_loop(store, script)
    result = asyncio.run(loop.run())
    assert result.status == "done"
    assert len(llm.calls) == 8                       # 3 决策 + 4 重试 payload + finish 决策
    cooled = [e for e in events.tail(100) if e["kind"] == "tool_result"
              and "冷却中" in e["payload"].get("observation", "")]
    assert cooled


def test_transient_error_prefix_and_no_circuit_breaker(store):
    script = [LLMError("timed out", transient=True, kind="timeout"), _finish()]
    loop, _llm, events, _state = make_loop(store, script)
    result = asyncio.run(loop.run())
    assert result.status == "done"
    first = next(e for e in events.tail(100) if e["kind"] == "tool_result")
    assert first["payload"]["observation"].startswith("FAILED-TRANSIENT:")


def test_budget_exceeded_pauses(store):
    usage = CompletionUsage(prompt_tokens=1_500_000, completion_tokens=600_000)
    loop, llm, events, state = make_loop(store, [_finish()], usage=usage)
    result = asyncio.run(loop.run())
    assert result.status == "paused"
    assert "budget_exceeded" in _kinds(events)
    assert len(llm.calls) == 1                       # 预算超线在下一步决策前截停
    assert state.stats["token_used"] > BUDGET_MAX_TOKENS


def test_parallel_readonly_calls_in_one_step(store):
    par = {"tool_calls": [{"name": "list_world", "arguments": "{}"},
                          {"name": "list_characters", "arguments": "{}"}]}
    loop, _llm, events, _state = make_loop(store, [par, _finish()])
    result = asyncio.run(loop.run())
    assert result.status == "done"
    results = [e for e in events.tail(100) if e["kind"] == "tool_result"]
    assert {e["payload"]["tool"] for e in results} == {"list_world", "list_characters"}


def test_step_plan_flow_actions(store):
    script = [
        json.dumps({"thought": "先拆步", "action": "set_step_plan",
                    "args": {"steps": ["侦察现状", "规划骨架"]}}, ensure_ascii=False),
        json.dumps({"thought": "做完一步", "action": "complete_step", "args": {}},
                   ensure_ascii=False),
        _finish(),
    ]
    loop, _llm, _events, state = make_loop(store, script)
    asyncio.run(loop.run())
    assert [p["done"] for p in state.stats["step_plan"]] == [True, False]


def test_max_steps_cap(store):
    probe = json.dumps({"thought": "再看一眼", "action": "get_project_state", "args": {}},
                       ensure_ascii=False)
    loop, llm, _events, _state = make_loop(store, [probe] * 60)
    result = asyncio.run(loop.run())
    assert result.status == "paused"                 # 60 步硬上限，耗尽即 paused
    assert len(llm.calls) == 60


def test_cooled_tool_rejected_in_parallel_batch(store, monkeypatch):
    """Important 1：刚被冷却的工具不能经多调用只读批次复活——批内逐条拒判、按 noop 计、不涨 fail_streak。"""
    from harness.agent import loop as loop_mod

    calls = {"n": 0}

    def flaky_probe(_store, _args):
        calls["n"] += 1
        raise RuntimeError("flaky boom")

    real_allowed = loop_mod.allowed_for_run
    monkeypatch.setattr(
        loop_mod, "allowed_for_run",
        lambda d: frozenset(set(real_allowed(d)) | {"flaky_probe"}))

    flaky = json.dumps({"thought": "探一下", "action": "flaky_probe", "args": {}},
                       ensure_ascii=False)
    probe = json.dumps({"thought": "reset", "action": "get_project_state", "args": {}},
                       ensure_ascii=False)
    # 批内被冷却工具放第二位：若放首位，run 级单动作冷却门会先整步拒掉（同为合规路径，
    # 但测不到批内逐条拒判）；第二位才能穿透到 _sync_tool 的批内过滤。
    batch = {"tool_calls": [{"name": "list_world", "arguments": "{}"},
                            {"name": "flaky_probe", "arguments": "{}"}]}
    # step1 ok 清账 → step2/3 连续失败（fail=2 → 冷却至 step6）→ step4 并行批含被冷却工具：
    # 批内拒判（noop、不涨 fail_streak），批内 list_world 照常执行 → step5 ok 清 noop →
    # step6 解禁真执行（FAILED，fail=1）→ step7 再执行（FAILED，fail=2）。
    # 若批内拒判被误计 failed，step7 会被冷却门拒（calls 停在 3、第 4 条 FAILED 不出现）。
    script = [probe, flaky, flaky, batch, probe, flaky, flaky, _finish()]
    loop, llm, events, _state = make_loop(store, script, extra_readonly={"flaky_probe": flaky_probe})
    result = asyncio.run(loop.run())
    assert result.status == "done"
    assert calls["n"] == 4                          # 真执行仅 step2/3/6/7：批内拒判未执行
    assert len(llm.calls) == 8
    obs = [e["payload"]["observation"]
           for e in events.tail(100) if e["kind"] == "tool_result" and e["payload"]["tool"] == "flaky_probe"]
    assert any("冷却中" in t for t in obs)           # 批内拒判走与单动作门一致的观测
    # fail_streak 未因批内拒判/门拒上涨：四次 FAILED 都来自真执行
    assert sum(t.startswith("FAILED: flaky_probe ") for t in obs) == 4
    # 批内其余只读调用照常执行
    assert any(e["kind"] == "tool_result" and e["payload"]["tool"] == "list_world"
               for e in events.tail(100))


def test_midrun_deliverable_narrowing_blocks_cooled_revival(store, monkeypatch):
    """Review Focus 4：deliverables 运行中收窄（outline→discuss）后，白名单每轮重取——
    收窄前已可用的 edit_by_command 决策被白名单门拒（局部 cooled 表不会复活它），
    观测为拒判、无任何 store 写。"""
    from harness.agent import loop as loop_mod

    real_allowed = loop_mod.allowed_for_run
    monkeypatch.setattr(
        loop_mod, "allowed_for_run",
        lambda d: frozenset(set(real_allowed(d)) | {"narrow_scope"}))

    state = RunState.new("先规划后讨论", ["outline"])

    def _narrow(_store, _args):
        state.brief["deliverables"] = ["discuss"]  # 模拟第 N 步收窄交付范围
        return "deliverables 已收窄到 discuss"

    narrow = json.dumps({"thought": "转入讨论阶段", "action": "narrow_scope", "args": {}},
                        ensure_ascii=False)
    edit = {"tool_calls": [{"name": "edit_by_command",
                            "arguments": json.dumps({"instruction": "改点东西"})}]}
    # step1 收窄 → step2 决策仍给 edit_by_command：此时白名单已是 discuss（只读+ask_user/
    # finish/pause）→ 门拒；step3 finish 收工。
    loop, _llm, events, state = make_loop(store, [narrow, edit, _finish()], state=state,
                                          extra_readonly={"narrow_scope": _narrow})
    result = asyncio.run(loop.run())
    assert result.status == "done"
    obs = [e for e in events.tail(100) if e["kind"] == "tool_result"
           and e["payload"]["tool"] == "edit_by_command"]
    assert len(obs) == 1 and "白名单" in obs[0]["payload"]["observation"]  # 门拒，未执行
    assert store.read_arcs() == [] and store.read_world() == []           # 无 store 写


def test_parallel_batch_failure_cools_the_failing_tool_not_the_first(store, monkeypatch):
    """**L2**（2026-11-15 独立审核）：并行只读批次里**失败的那个工具**被冷却——
    不是批次的**第一个**工具。

    旧行为：任一调用抛异常 → 异常穿出整批 → loop 只知道「这批失败」、**不知道是哪个
    工具失败** → 熔断账记在 ``action``（= 首个 tool_call 名）名下 → 真正失败的那个
    **没被冷却**，反而没失败的那个**被误冷却**。

    新行为：``execute_parallel`` **逐条**捕异常 + ``_sync_tool`` **逐条**分类 →
    失败记到那个工具 → 冷却对了对象。

    断言「**成功**的那个工具一次都没被冷却」（``probe_ok`` 跑了 3 次）：变异成
    「失败仍记在第一个工具头上」时，它会被冷却 → 第 3 步整步被冷却门拒 → 只跑 2 次。
    """
    from harness.agent import loop as loop_mod

    calls = {"ok": 0, "bad": 0}

    def probe_ok(_store, _args):
        calls["ok"] += 1
        return "probe-ok"

    def probe_bad(_store, _args):
        calls["bad"] += 1
        raise RuntimeError("炸了")

    real_allowed = loop_mod.allowed_for_run
    monkeypatch.setattr(
        loop_mod, "allowed_for_run",
        lambda d: frozenset(set(real_allowed(d)) | {"probe_ok", "probe_bad"}))

    batch = {"tool_calls": [{"name": "probe_ok", "arguments": "{}"},
                            {"name": "probe_bad", "arguments": "{}"}]}
    # step1/step2：坏工具连续失败 2 次 → 被冷却；step3：坏工具批内拒判（不再执行）、
    # 好工具照常执行；step4：finish。
    loop, llm, events, _state = make_loop(
        store, [batch, batch, batch, _finish()],
        extra_readonly={"probe_ok": probe_ok, "probe_bad": probe_bad})
    result = asyncio.run(loop.run())

    assert result.status == "done"
    assert len(llm.calls) == 4
    assert calls["ok"] == 3, "没失败的工具被误冷却了（它没跑满 3 步）"
    assert calls["bad"] == 2, "失败的工具没被冷却（第 3 步还执行了）"
    obs_by_tool = {}
    for e in events.tail(100):
        if e["kind"] == "tool_result":
            obs_by_tool.setdefault(e["payload"]["tool"], []).append(
                e["payload"].get("observation", ""))
    assert any("冷却中" in t for t in obs_by_tool.get("probe_bad", [])), \
        "失败的工具没进冷却"
    assert not any("冷却中" in t for t in obs_by_tool.get("probe_ok", [])), \
        "没失败的工具被误冷却"
    # 错误**不被藏起来**：失败观测里看得见**工具名** + 确定性失败标签
    # （**不含异常原文**：原文的瞬时词会把确定性失败翻成 transient——独立复审 P17-3）
    failed_obs = [t for t in obs_by_tool.get("probe_bad", []) if t.startswith("FAILED")]
    assert failed_obs and all("probe_bad" in t and "调用失败" in t for t in failed_obs)
    assert not any("RuntimeError" in t or "炸了" in t for t in failed_obs)


def test_p21_mixed_batch_clears_success_streak(store, monkeypatch):
    """**P21**（独立复审 P17-1）：混合批次（一个成功 + 一个失败）→ **成功那个工具的账要清**。

    旧行为：清账写在「整体 ok」分支里 → 混合批整体判 failed → **成功工具的
    ``fail_streak`` 不清** → 它下次单独失败一次就被冷却（**误冷却**：它中间成功过）。

    场景：X 失败一次 → 批次 [X 成功, Y 失败] → X 再失败一次 → **X 不该被冷却**
    （它的连续失败被那次成功打断了）。
    """
    from harness.agent import loop as loop_mod

    def _boom(_store, _args):
        raise RuntimeError("炸了")

    # X 的**调用序列**要精确编排：失败 → 成功 → 失败 → 失败
    # （第 1、3、4 次失败，第 2 次成功——第 2 次就是批里那次）。
    x_calls = {"n": 0}

    def _x(_store, _args):
        x_calls["n"] += 1
        if x_calls["n"] in (1, 3, 4):
            raise RuntimeError("炸了")
        return "fine"

    real_allowed = loop_mod.allowed_for_run
    monkeypatch.setattr(
        loop_mod, "allowed_for_run",
        lambda d: frozenset(set(real_allowed(d)) | {"tool_x", "tool_y"}))

    x_fail = json.dumps({"thought": "x 失败", "action": "tool_x", "args": {}},
                         ensure_ascii=False)
    # 批次：X 成功 + Y 失败
    batch = {"tool_calls": [{"name": "tool_x", "arguments": "{}"},
                            {"name": "tool_y", "arguments": "{}"}]}
    # step1: X 失败（fail=1）→ step2: 批 [X 成功, Y 失败] → step3/4: X 连续失败两次
    #   **正确行为**：step2 的成功**清账** → step3 fail=1 → step4 fail=2 → 冷却生效，
    #   但**没有后面的步骤再调 X** → 观测里**看不到**「冷却中」。
    #   **旧行为**（清账只在整体 ok 时做）：step2 不清 → step3 fail=2 → **冷却** →
    #   step4 再调 X → 被**冷却门拒** → 观测里出现「tool_x 冷却中」→ 断言红。
    script = [x_fail, batch, x_fail, x_fail, _finish()]
    loop, _llm, events, _state = make_loop(
        store, script, extra_readonly={"tool_x": _x, "tool_y": _boom})
    result = asyncio.run(loop.run())

    assert result.status == "done"
    assert x_calls["n"] == 4, "X 的调用序列与编排不符（用例本身失效）"
    obs = [e["payload"]["observation"]
           for e in events.tail(100) if e["kind"] == "tool_result"]
    assert not any("tool_x 冷却中" in t for t in obs), \
        "混合批里成功工具的账没被清 → 它被误冷却了"


def test_p21_same_tool_twice_in_batch_keeps_failure(store, monkeypatch):
    """**P21**（独立复审 P17-2）：同一工具在批内出现两次 → **失败不被覆盖掉**。

    旧行为：``per_tool[name] = oc``（后者覆盖前者）→ 批内 [A 失败, A 成功] 把失败
    **覆盖成成功** → 失败**不被归因**（冷却不到真失败的工具）→ 归因结果取决于调用顺序。
    """
    from harness.agent import loop as loop_mod

    calls = {"n": 0}

    def _flaky(_store, _args):
        calls["n"] += 1
        if calls["n"] % 2 == 1:      # 奇数次失败、偶数次成功
            raise RuntimeError("失败")
        return "ok"

    real_allowed = loop_mod.allowed_for_run
    monkeypatch.setattr(
        loop_mod, "allowed_for_run",
        lambda d: frozenset(set(real_allowed(d)) | {"tool_a"}))

    batch = {"tool_calls": [{"name": "tool_a", "arguments": "{}"},
                            {"name": "tool_a", "arguments": "{}"}]}
    # step1 批：第 1 条失败、第 2 条成功 → **失败必须被记住**（fail=1）
    # step2 批：同样第 1 条失败 → fail=2 → **冷却**
    # step3：run 级冷却门拒（观测「冷却中」）
    script = [batch, batch, batch, _finish()]
    loop, _llm, events, _state = make_loop(
        store, script, extra_readonly={"tool_a": _flaky})
    result = asyncio.run(loop.run())

    assert result.status == "done"
    obs = [e["payload"]["observation"]
           for e in events.tail(100) if e["kind"] == "tool_result"
           and e["payload"]["tool"] == "tool_a"]
    assert any("tool_a 冷却中" in t for t in obs), \
        "批内重复工具的失败被覆盖了（没进冷却账）"


def test_p21_exception_with_transient_word_still_fails(store, monkeypatch):
    """**P21**（独立复审 P17-3）：异常消息含**瞬时词**（「连接被拒绝」）→ 仍判 **failed**。

    旧行为：原文拼进观测 → `_classify` 先扫瞬时词 → 这条**确定性失败**被判 transient →
    永远进不了失败账 → 死循环重试同一个坏工具。
    """
    from harness.agent import loop as loop_mod

    def _boom(_store, _args):
        raise ConnectionError("连接被拒绝")

    real_allowed = loop_mod.allowed_for_run
    monkeypatch.setattr(
        loop_mod, "allowed_for_run",
        lambda d: frozenset(set(real_allowed(d)) | {"tool_c"}))

    probe = json.dumps({"thought": "探一下", "action": "tool_c", "args": {}},
                       ensure_ascii=False)
    script = [probe, probe, probe, _finish()]
    loop, _llm, events, _state = make_loop(
        store, script, extra_readonly={"tool_c": _boom})
    result = asyncio.run(loop.run())

    assert result.status == "done"
    obs = [e["payload"]["observation"]
           for e in events.tail(100) if e["kind"] == "tool_result"
           and e["payload"]["tool"] == "tool_c"]
    assert any("tool_c 冷却中" in t for t in obs), \
        "含瞬时词的异常没有被当成确定性失败（不进冷却账）"


def test_truncated_observation_is_transient_no_circuit_breaker(store):
    """Important 2：工具层失败串的英文分类词 truncated（截断类）必须判 transient、不进熔断。"""
    starved = LLMError("starved", transient=True, kind="starved", finish_reason="length")
    plan = {"tool_calls": [{"name": "plan_arcs", "arguments": json.dumps({"instruction": "x"})}]}
    # step1/2/3：plan_arcs 内部重试均 starved → 观测 "plan_arcs 失败（truncated）：…"
    # 若 truncated 被计 failed，step3 会被冷却门拒（调用数停在 7，第三次观测变「冷却中」）
    script = [plan, starved, starved, plan, starved, starved, plan, starved, starved, _finish()]
    loop, llm, events, _state = make_loop(store, script)
    result = asyncio.run(loop.run())
    assert result.status == "done"
    assert len(llm.calls) == 10                     # 3 决策×(1+2 重试) + finish：无冷却拦截
    obs = [e["payload"]["observation"]
           for e in events.tail(100) if e["kind"] == "tool_result" and e["payload"]["tool"] == "plan_arcs"]
    assert len(obs) == 3
    assert all(t.startswith("FAILED-TRANSIENT:") and "truncated" in t for t in obs)


# ------------------------------------------------------------------ 验收工具的 agent 可见契约

def test_acceptance_tool_signatures_carry_usage_contract(store):
    """验收工具的 **agent 可见签名**必须带「何时用 / 验什么 / 产出到哪」。

    设计附录 A.6 写着「收尾前必做」，但 agent 实际读到的是 ``_FALLBACK_SIG`` 兜底表
    （注册件是闭包、无 docstring）——契约只活在设计文档里 = agent 不知道该在什么时候
    调它（验收链最大的行为风险不是实现 bug，是没人调它）。
    """
    from harness.agent.loop import _FALLBACK_SIG

    assert "【收尾前必做】" in _FALLBACK_SIG["global_check"]
    assert "editor_mode" in _FALLBACK_SIG["global_check"]
    assert "issues" in _FALLBACK_SIG["global_check"]
    assert "验收清单" in _FALLBACK_SIG["global_check"]
    assert "pass/fix/replan" in _FALLBACK_SIG["check_plots"]
    assert "验收清单" in _FALLBACK_SIG["check_plots"]
    assert "issues" in _FALLBACK_SIG["check_plots"]


def test_acceptance_tool_signatures_reach_the_prompt(store):
    """渲染进【可用工具】的那一行也要带这些信息（不是只躺在表里）。"""
    from harness.tools.check import register_check_tools

    loop, _llm, _events, _state = make_loop(store, [])
    register_check_tools(loop.registry, store=store, llm=loop.llm, prompts=loop.prompts,
                         settings=loop.settings)
    lines = loop._tool_signatures({"check_plots", "global_check"})
    text = "\n".join(lines)
    assert "【收尾前必做】" in text
    assert "editor_mode" in text

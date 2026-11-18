"""P10 集成验收：需求解析接进 REPL 与验收链。

三场景：
① **结构化 brief 落盘**：``/task outline <需求>`` → state.json 的 brief 里
   premise/goal/constraints 有真内容 → 验收提示词的 **goals 槽 = goal**（不再是 raw）；
② **解析失败不阻断**：intake 的 LLM 失败 → run 照常、brief 只留 raw、观测报因；
③ **用户显式优先**：用户显式 ``draft`` → 模型想改成 ``["outline"]`` 也不生效。

LLM 全 mock（按提示词内容路由），其余链路（读盘、落盘、验收组装）真实跑。

**测试是同步的**：REPL 的命令循环是同步世界（``_run_agent`` 内部 ``asyncio.run``），
所以这些场景必须**在事件循环之外**跑——写成 ``async def`` 会撞
「asyncio.run() cannot be called from a running event loop」。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.config import ProviderConfig, Settings
from harness.projects import create_project
from harness.run.state import RunState
from harness.session import Components
from harness.store.project import ProjectStore

INTAKE_MARK = "你是创作需求解析器"


class _Resp:
    def __init__(self, text, usage=None):
        self.text = text
        self.usage = usage


class FakeLLM:
    """按提示词内容路由的假 llm：需求解析 vs 验收。"""

    def __init__(self, intake_payload=None, check_payload=None):
        self.intake_payload = intake_payload
        self.check_payload = check_payload
        self.calls: list[str] = []

    @property
    def provider(self):
        return ProviderConfig(base_url="https://api.test", api_key="k",
                              lite="m-lite", standard="m-std", pro="m-pro")

    async def complete(self, messages, *, model, temperature=0.7, max_tokens=8192):
        prompt = messages[-1]["content"]
        self.calls.append(prompt)
        if INTAKE_MARK in prompt:
            if isinstance(self.intake_payload, Exception):
                raise self.intake_payload
            return _Resp(self.intake_payload)
        return _Resp(self.check_payload)



async def _noop_run(self):
    """桩：ReAct 循环什么都不做（intake 仍会跑）。"""

def _settings():
    return Settings(library_root=Path("x"), providers={}, default_provider="",
                    default_tier="standard", web_verify=True)


@pytest.fixture
def project(tmp_path):
    """真会话（_Session）——_handle_brief 要的是会话（store/events/memory/settings）。"""
    from harness.session import RunSession as _Session

    info = create_project(tmp_path, "需求解析书")
    store = ProjectStore(info.path)
    sess = _Session(info, store, _settings())
    return info, sess


def _intake_json(**kw) -> str:
    base = {"goal": "写一部仙侠长篇", "premise": "少年修道成仙",
            "deliverables": ["outline"], "constraints": ["每章 3000 字"],
            "need_dimensions": [], "prefer": [], "avoid": [],
            "plan_arcs": 0, "write_target": ""}
    base.update(kw)
    return json.dumps(base, ensure_ascii=False)


def _check_json() -> str:
    return json.dumps({"verdict": "pass", "score": 9, "issues": [], "checklist": []},
                      ensure_ascii=False)


# ------------------------------------------------------------------ 场景①

def test_scenario_intake_fills_brief_and_goals_slot(project, monkeypatch):
    """① 需求解析 → brief 有真内容 → 验收的 goals 槽 = goal。"""
    from harness.check.context import brief_context
    from harness.repl import _join_background, _submit_brief

    _info, sess = project
    store = sess.store
    llm = FakeLLM(intake_payload=_intake_json())

    # 只跑 intake，不跑整个 ReAct 循环（循环要真 TTY）
    monkeypatch.setattr("harness.session.RunSession.components",
                        lambda self: Components(llm=llm, registry=None, ui=None))
    # 桩掉**真正的 ReAct 循环**（intake 在 _run_agent 里，仍会跑）
    monkeypatch.setattr("harness.session.AgentLoop.run", _noop_run)

    _submit_brief(sess, "写个仙侠", ["outline"], user_specified=False)
    # P16：非 TTY 下 run 走**后台线程** → 断言前先等它收尾
    _join_background()

    state = RunState.load(store.root)
    assert state is not None
    assert state.brief["goal"] == "写一部仙侠长篇"
    assert state.brief["premise"] == "少年修道成仙"
    assert state.brief["constraints"] == ["每章 3000 字"]

    # 验收的 goals 槽 = **用户原话**（D1：锚点必须是外部的，不能是模型自己写的概括——
    # auto_plot_check 没有 premise 槽，判官手上唯一的意图陈述就是 goals）
    slots = brief_context(store)
    assert slots["goals"] == "写个仙侠"
    assert slots["constraints"] == "- 每章 3000 字"
    # premise 槽也是**用户原话**（不做模型改写）
    assert slots["premise"] == "写个仙侠"


def test_scenario_intake_runs_before_agent_loop(project, monkeypatch):
    """① 的续：intake 在 ReAct 循环**之前**跑（设计 §1.1 的 INTAKE 节点）。"""
    from harness.repl import _join_background, _submit_brief

    _info, sess = project
    llm = FakeLLM(intake_payload=_intake_json())
    seen: dict = {}

    monkeypatch.setattr("harness.session.RunSession.components",
                        lambda self: Components(llm=llm, registry=None, ui=None))

    async def spy_run(self):
        # 循环启动时，brief 已经带上解析结果 → 证明 intake 在循环**之前**
        seen["goal_at_loop_start"] = self.state.brief.get("goal")
        seen["deliverables_at_loop_start"] = list(self.state.brief.get("deliverables") or [])

    monkeypatch.setattr("harness.session.AgentLoop.run", spy_run)

    _submit_brief(sess, "写个仙侠", ["outline"], user_specified=False)
    # P16：非 TTY 下 run 走**后台线程** → 断言前先等它收尾
    _join_background()

    assert seen["goal_at_loop_start"] == "写一部仙侠长篇"
    assert seen["deliverables_at_loop_start"] == ["outline"]


# ------------------------------------------------------------------ 场景②

def test_scenario_intake_failure_does_not_block(project, monkeypatch):
    """② intake 的 LLM 失败 → run 照常、brief 只留 raw、观测报因。"""
    from harness.repl import _join_background, _submit_brief

    _info, sess = project
    store = sess.store
    llm = FakeLLM(intake_payload=Exception("boom"))

    monkeypatch.setattr("harness.session.RunSession.components",
                        lambda self: Components(llm=llm, registry=None, ui=None))
    # 桩掉**真正的 ReAct 循环**（intake 在 _run_agent 里，仍会跑）
    monkeypatch.setattr("harness.session.AgentLoop.run", _noop_run)

    _submit_brief(sess, "写个仙侠", ["outline"], user_specified=False)
    # P16：非 TTY 下 run 走**后台线程** → 断言前先等它收尾
    _join_background()

    state = RunState.load(store.root)
    assert state is not None
    assert state.brief["premise"] is None          # 没解析出来 → 保持 None
    assert state.brief["raw"] == "写个仙侠"        # raw 仍在
    assert state.brief["constraints"] == []


# ------------------------------------------------------------------ 场景③

def test_scenario_user_specified_deliverable_wins(project, monkeypatch):
    """③ 用户显式 ``draft`` → 模型想改成 ``["outline"]`` 也不生效。"""
    from harness.repl import _join_background, _submit_brief

    _info, sess = project
    store = sess.store
    llm = FakeLLM(intake_payload=_intake_json(deliverables=["outline"]))

    monkeypatch.setattr("harness.session.RunSession.components",
                        lambda self: Components(llm=llm, registry=None, ui=None))
    # 桩掉**真正的 ReAct 循环**（intake 在 _run_agent 里，仍会跑）
    monkeypatch.setattr("harness.session.AgentLoop.run", _noop_run)

    _submit_brief(sess, "写正文", ["draft"], user_specified=True)
    # P16：非 TTY 下 run 走**后台线程** → 断言前先等它收尾
    _join_background()

    state = RunState.load(store.root)
    assert state is not None
    assert state.brief["deliverables"] == ["draft"]


def test_scenario_seed_only_deliverables_do_not_crash(project, monkeypatch):
    """③ 的续：模型回 ``edit``（本地不支持）/``characters``（P22/A6 起已支持）→
    **不崩**：edit 被词表过滤，characters 进 suggested；权限面仍是用户的值。"""
    from harness.repl import _join_background, _submit_brief

    _info, sess = project
    store = sess.store
    llm = FakeLLM(intake_payload=_intake_json(deliverables=["characters", "edit"]))

    monkeypatch.setattr("harness.session.RunSession.components",
                        lambda self: Components(llm=llm, registry=None, ui=None))
    # 桩掉**真正的 ReAct 循环**（intake 在 _run_agent 里，仍会跑）
    monkeypatch.setattr("harness.session.AgentLoop.run", _noop_run)

    _submit_brief(sess, "写个仙侠", ["outline"], user_specified=False)
    # P16：非 TTY 下 run 走**后台线程** → 断言前先等它收尾
    _join_background()

    state = RunState.load(store.root)
    assert state is not None
    # 权限面不受模型影响（edit 全被过滤、characters 只作信息）→ 仍是用户/默认的交付物
    assert state.brief["deliverables"] == ["outline"]


def test_scenario_model_cannot_narrow_permission_set(project, monkeypatch):
    """**D2/B1**（接线层）：模型推断的交付物**不能**收窄白名单。

    ``deliverables`` 就是 ``allowed_for_run`` 的权限面：模型回 ``["world"]`` 会把
    **规划链三工具**物理摘掉（21/24），用户要「写个故事」却**物理上无法**产出卷骨架，
    run 多半静默卡死 —— 而且观测只会说「需求已解析」。

    复审盲区 B1：此前「模型推断的 deliverables 生效路径」**零测试** → D2 溜过去。
    """
    from harness.repl import _join_background, _submit_brief
    from harness.run.whitelist import allowed_for_run

    _info, sess = project
    llm = FakeLLM(intake_payload=_intake_json(deliverables=["world"]))

    monkeypatch.setattr("harness.session.RunSession.components",
                        lambda self: Components(llm=llm, registry=None, ui=None))
    seen: dict = {}

    async def spy_run(self):
        seen["deliverables"] = list(self.deliverables)

    monkeypatch.setattr("harness.session.AgentLoop.run", spy_run)
    _submit_brief(sess, "写个海洋救赎的故事", ["outline"], user_specified=False)
    # P16：非 TTY 下 run 走**后台线程** → 断言前先等它收尾
    _join_background()

    assert seen["deliverables"] == ["outline"]
    assert "plan_arcs" in allowed_for_run(seen["deliverables"])
    assert "master_plan" in allowed_for_run(seen["deliverables"])


def test_scenario_resume_reruns_intake_when_goal_missing(project, monkeypatch):
    """**S4**：``/resume`` 时若 brief **没有 goal**（首跑解析失败过）→ **补跑一次** intake。

    否则该 run 的 ``constraints`` 整轮恒「（无）」——约束维度整轮失效，而用户看不出为什么。
    """
    from harness.repl import _cmd_resume

    _info, sess = project
    store = sess.store
    # 造一个「首跑解析失败」的 run：brief 只有 raw
    from harness.run.state import RunState

    state = RunState.new("写个仙侠", ["outline"])
    state.status = "paused"
    state.save(store.root)

    llm = FakeLLM(intake_payload=_intake_json())
    monkeypatch.setattr("harness.session.RunSession.components",
                        lambda self: Components(llm=llm, registry=None, ui=None))
    captured: dict = {}

    class _FakeRunner:
        def alive(self):
            return False

        def launch(self, session, state, comp, *, intake=None):
            captured["intake"] = intake

    monkeypatch.setattr("harness.repl._RUNNER", _FakeRunner())
    _cmd_resume(sess)

    assert captured["intake"] is not None
    assert captured["intake"][0] == "写个仙侠"


def test_scenario_legacy_state_json_without_goal_loads(tmp_path):
    """**B2**：旧 state.json（brief 里**没有** goal 键）仍能被读出，且消费方回落正常。

    复审盲区：删掉「load 时补 goal 键」的逻辑后全量仍全绿 → 契约 9 零测试。
    """
    import json

    from harness.run.state import RunState

    root = tmp_path / ".harness"
    root.mkdir(parents=True)
    legacy = {
        "status": "paused",
        "brief": {"raw": "写个仙侠", "premise": None, "constraints": [],
                  "deliverables": ["outline"], "plan_arcs": None,
                  "write_target": None, "co_design": False},
        "stats": {"step": 0},
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    (root / "state.json").write_text(json.dumps(legacy, ensure_ascii=False),
                                     encoding="utf-8")

    state = RunState.load(tmp_path)
    assert state is not None
    assert state.brief["goal"] is None          # 补了键（None）
    assert state.brief["raw"] == "写个仙侠"      # 旧字段不动


def test_scenario_goals_slot_fallback_chain(tmp_path):
    """**B7**：``goals`` 槽的回落链 = raw → goal → premise → 「（未提供）」。

    **用户原话优先**（D1）：判官的锚点必须是外部的，不是模型自己写的概括。
    """
    import json

    from harness.check.context import brief_context
    from harness.store.project import ProjectStore

    store = ProjectStore(tmp_path)
    root = tmp_path / ".harness"
    root.mkdir(parents=True)

    def _write(brief: dict):
        (root / "state.json").write_text(
            json.dumps({"status": "planning", "brief": brief,
                        "stats": {"step": 0}}, ensure_ascii=False),
            encoding="utf-8")

    # ① 有 raw → goals = raw（**优先于**模型写的 goal）
    _write({"raw": "只写前三章，主角不能死", "goal": "写一部长篇仙侠",
            "premise": "少年修道", "constraints": [], "deliverables": ["outline"]})
    assert brief_context(store)["goals"] == "只写前三章，主角不能死"

    # ② 无 raw 但有 goal → goals = goal
    _write({"raw": "", "goal": "写一部长篇仙侠", "premise": "少年修道",
            "constraints": [], "deliverables": ["outline"]})
    assert brief_context(store)["goals"] == "写一部长篇仙侠"

    # ③ 都没有 → 「（未提供）」
    _write({"raw": "", "goal": "", "premise": "", "constraints": [],
            "deliverables": ["outline"]})
    assert brief_context(store)["goals"] == "（未提供）"

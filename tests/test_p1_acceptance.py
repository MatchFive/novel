"""P1 验收三场景（LLM 全 mock，complete 级注入）。

1. 空项目 brief「从零到卷骨架」→ plan_arcs → finish → arcs.md 有卷骨架、state done、
   events.log 含 agent_step/tool_result。
2. run 中途 paused → 新 loop 走 resume 路径（WorkMemory.rebuild 回放 events）→
   decided 层含原 brief 原文 → 续跑至 done。
3. edit_by_command 改角色别名：mock propose 返回新卡内容 → 假 ui 确认 →
   读回别名已改、其余卡字段保留（store 合并语义）。

附：REPL 新斜杠命令（/pause /abort /issues /answer）与 --resume 接线冒烟。
"""
import asyncio
import json
from pathlib import Path

from harness.agent.loop import AgentLoop
from harness.config import ProviderConfig, Settings
from harness.projects import ProjectInfo, create_project
from harness.prompts.loader import PromptLoader
from harness.repl import run_repl
from harness.run.approval import ApprovalUI, Choice
from harness.run.constants import WORK_MEMORY_REPLAY
from harness.run.events import EventLog
from harness.run.issues import IssuesStore
from harness.run.memory import WorkMemory
from harness.run.state import RunState
from harness.session import drive_run
from harness.store.frontmatter import render_document
from harness.store.project import ProjectStore
from harness.tools import readonly_registry, register_modify_tools, register_outline_tools


class _FakeCompletion:
    def __init__(self, text, usage=None, tool_calls=None):
        self.text = text
        self.usage = usage
        self.tool_calls = tool_calls


class FakeLLM:
    """顺序脚本假 llm：str = content 协议 JSON；dict 含 tool_calls = 原生 tool_call 协议。"""

    def __init__(self, script):
        self._script = list(script)
        self.calls = []
        self.provider = ProviderConfig(base_url="https://api.test", api_key="k",
                                       lite="m", standard="standard-model", pro="pro-model")

    async def complete(self, messages, *, model, temperature=0.7, max_tokens=8192, thinking=False):
        self.calls.append({"model": model})
        item = self._script.pop(0)
        if isinstance(item, dict) and "tool_calls" in item:
            return _FakeCompletion(text=item.get("content", ""), tool_calls=item["tool_calls"])
        return _FakeCompletion(text=item)

    async def aclose(self):
        pass


def _settings():
    return Settings(
        library_root=Path("x"),
        providers={"test": ProviderConfig(base_url="https://api.test", api_key="k",
                                          lite="m", standard="standard-model", pro="pro-model")},
        default_provider="test",
        default_tier="standard",
        web_verify=True,
    )


def _build_loop(store, script, state, memory, ui=None):
    llm = FakeLLM(script)
    settings = _settings()
    prompts = PromptLoader(home_dir=store.root)
    ui = ui or ApprovalUI(ask=lambda q, o, m=False, note=None: Choice(indices=[0]))
    registry = readonly_registry(store)
    register_outline_tools(registry, store=store, llm=llm, prompts=prompts, settings=settings)
    register_modify_tools(registry, store=store, llm=llm, prompts=prompts, ui=ui)
    return AgentLoop(store=store, state=state, events=EventLog(store.root), memory=memory,
                     registry=registry, ui=ui, llm=llm, prompts=prompts,
                     deliverables=list(state.brief["deliverables"]), settings=settings)


def _arcs_payload():
    return json.dumps({"planned_volume_count": 2, "arcs": [
        {"vol": 1, "title": "崛起", "goal": "踏上修行路", "summary": "山村少年入宗门",
         "conflict": "资质被夺", "resolution": "逆天改命", "capacity_hint": "约40章"},
        {"vol": 2, "title": "征战", "goal": "统一北境", "summary": "宗门崛起战",
         "conflict": "北境诸部", "resolution": "会盟称雄", "capacity_hint": "约35章"},
    ]}, ensure_ascii=False)


def _finish(summary="完成"):
    return json.dumps({"thought": "目标达成", "action": "finish", "args": {"summary": summary}},
                      ensure_ascii=False)


# ---------------------------------------------------------------- 场景 1

def test_scenario1_empty_project_brief_to_volume_skeleton(tmp_path):
    info = create_project(tmp_path, "验收书")
    store = ProjectStore(info.path)
    brief = "从零到卷骨架：一本双卷修仙小说，第一卷踏上修行路，第二卷统一北境"
    plan_decision = {"tool_calls": [{"name": "plan_arcs",
                                     "arguments": json.dumps({"instruction": brief})}]}
    state = RunState.new(brief, ["outline"])
    memory = WorkMemory()
    memory.record({"kind": "run_started", "payload": {"raw": brief}})
    loop = _build_loop(store, [plan_decision, _arcs_payload(), _finish()], state, memory)
    asyncio.run(loop.run())

    arcs = store.read_arcs()
    assert arcs and all(a.get("goal") for a in arcs)     # 卷骨架非空且含 goal
    assert "踏上修行路" in (store.root / "outline" / "arcs.md").read_text(encoding="utf-8")
    saved = RunState.load(store.root)
    assert saved is not None and saved.status == "done"
    kinds = [json.loads(line)["kind"] for line in
             (store.root / ".harness" / "events.log").read_text(encoding="utf-8").splitlines()]
    assert "agent_step" in kinds and "tool_result" in kinds


# ---------------------------------------------------------------- 场景 2

def test_scenario2_paused_run_resume_rebuilds_memory(tmp_path):
    info = create_project(tmp_path, "续跑书")
    store = ProjectStore(info.path)
    brief = "从零到卷骨架：双卷修仙，第一卷踏上修行路"
    plan_decision = {"tool_calls": [{"name": "plan_arcs",
                                     "arguments": json.dumps({"instruction": brief})}]}
    pause_decision = json.dumps({"thought": "先暂停", "action": "pause", "args": {}},
                                ensure_ascii=False)
    state = RunState.new(brief, ["outline"])
    state.save(store.root)
    events0 = EventLog(store.root)
    events0.append("run_started", {"raw": brief, "deliverables": ["outline"]})  # REPL 职责：先落 run_started
    memory = WorkMemory.rebuild(events0.tail(WORK_MEMORY_REPLAY * 2))
    loop1 = _build_loop(store, [plan_decision, _arcs_payload(), pause_decision], state, memory)
    asyncio.run(loop1.run())
    assert RunState.load(store.root).status == "paused"   # run 中途 paused

    # 新 loop 走 resume 路径：回放事件重建工作记忆
    events = EventLog(store.root)
    memory2 = WorkMemory.rebuild(events.tail(WORK_MEMORY_REPLAY * 2))
    assert any(brief in d for d in memory2.decided)       # decided 含原 brief 原文
    state2 = RunState.load(store.root)
    loop2 = _build_loop(store, [_finish("续跑完成")], state2, memory2)
    asyncio.run(loop2.run())
    assert RunState.load(store.root).status == "done"


# ---------------------------------------------------------------- 场景 3

def test_scenario3_edit_by_command_alias_change(tmp_path):
    info = create_project(tmp_path, "别名书")
    store = ProjectStore(info.path)
    store.upsert_character({"name": "陆昭", "role_type": "protagonist", "aliases": ["昭哥"]})
    new_card = render_document({"name": "陆昭", "role_type": "protagonist",
                                "aliases": ["昭哥", "昭公子"]},
                               "# 陆昭\n\n天才修士。")
    edit_decision = {"tool_calls": [{"name": "edit_by_command",
                                     "arguments": json.dumps({"instruction": "给陆昭加别名昭公子"})}]}
    propose = json.dumps([{"path": "characters/陆昭.md", "content": new_card}], ensure_ascii=False)

    asked = []

    def fake_ask(question, options, multi=False, note=None):
        asked.append((question, options, multi))
        return Choice(indices=[0])                        # 确认卡通过

    brief = "给主角陆昭加别名昭公子"
    state = RunState.new(brief, ["outline"])
    memory = WorkMemory()
    memory.record({"kind": "run_started", "payload": {"raw": brief}})
    loop = _build_loop(store, [edit_decision, propose, _finish()], state, memory,
                       ui=ApprovalUI(ask=fake_ask))
    asyncio.run(loop.run())

    assert len(asked) == 1 and asked[0][2] is True        # diff 确认门走过一次（多选）
    meta = store.read_character("陆昭")
    assert meta["aliases"] == ["昭哥", "昭公子"]           # 别名已改
    assert meta["role_type"] == "protagonist"             # 其余卡字段保留（合并语义）


# ---------------------------------------------------------------- REPL 命令冒烟

def test_repl_pause_issues_and_abort(tmp_path, monkeypatch, capsys):
    info = create_project(tmp_path, "命令书")
    store = ProjectStore(info.path)
    state = RunState.new("跑着的 run", ["outline"])
    state.status = "running"
    state.save(store.root)
    IssuesStore(store.root).add("test", "别名未登记", "陆昭又名昭哥未登记")
    inputs = iter(["/pause", "/issues", "1", "/abort", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    monkeypatch.setattr("harness.session.ApprovalUI",
                        lambda *a, **k: ApprovalUI(ask=lambda q, o, m=False, note=None: Choice(indices=[0])))
    run_repl(ProjectInfo(info.path, "命令书"), store)
    out = capsys.readouterr().out
    assert "已暂停" in out
    assert RunState.load(store.root).status == "aborted"
    assert IssuesStore(store.root).list("pending") == []  # issue 已标记 resolved


def test_repl_answer_writes_pending_answer(tmp_path, monkeypatch):
    info = create_project(tmp_path, "审批书")
    store = ProjectStore(info.path)
    inputs = iter(["/answer 1 2 备注:同意", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    run_repl(ProjectInfo(info.path, "审批书"), store)
    answer = (store.root / ".harness" / "pending.answer").read_text(encoding="utf-8")
    assert answer.splitlines()[0] == "1 2"
    assert "备注:同意" in answer


def test_cli_resume_flag_runs_pending_prompt(tmp_path, monkeypatch, capsys):
    """--resume：state 非终态时给出续跑提示（非 TTY 环境不真正起 loop）。"""
    from harness.cli import main

    info = create_project(tmp_path, "续跑旗标书")
    state = RunState.new("未完的 run", ["outline"])
    state.status = "paused"
    state.save(ProjectStore(info.path).root)
    inputs = iter(["/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    assert main([str(info.path), "--resume"]) == 0
    out = capsys.readouterr().out
    assert "续跑" in out or "P1" in out


def test_repl_answer_note_keeps_spaces(tmp_path, monkeypatch):
    """Minor 4：/answer 的空格备注完整保留（按首个「 备注:」切分，不按空格截断）。"""
    info = create_project(tmp_path, "多空格备注书")
    store = ProjectStore(info.path)
    inputs = iter(["/answer 1 备注:同意 先这样 改别名即可", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    run_repl(ProjectInfo(info.path, "多空格备注书"), store)
    lines = (store.root / ".harness" / "pending.answer").read_text(encoding="utf-8").splitlines()
    assert lines[0] == "1"
    assert lines[1] == "备注:同意 先这样 改别名即可"


# ---------------------------------------------------------------- 终审 Important 修补


class _FakeLoop:
    """repl 级假 AgentLoop：run() 直接把 state 置 done，不触网、不真跑。"""

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def run(self):
        self.kwargs["state"].status = "done"
        return self.kwargs["state"]


def test_run_start_invokes_event_log_rotation(tmp_path, monkeypatch):
    """Important 1：run 起点（drive_run）以会话既有 EventLog 发起一次轮转检查——
    50MB/50k 行阈值不再是死代码。"""
    from harness import session as session_mod
    from harness.session import Components, RunSession

    info = create_project(tmp_path, "轮转书")
    store = ProjectStore(info.path)
    calls = []
    monkeypatch.setattr(EventLog, "rotate_if_needed", lambda self: calls.append(self) or None)
    monkeypatch.setattr(session_mod, "AgentLoop", _FakeLoop)
    sess = RunSession(ProjectInfo(info.path, "轮转书"), store, _settings())
    state = RunState.new("写点啥", ["outline"])
    asyncio.run(drive_run(sess, state, Components(None, None, None)))
    assert calls == [sess.events]  # 用的正是会话里的 EventLog 实例，未另起第二个


def test_resume_replay_starts_at_last_run_boundary(tmp_path):
    """Important 5：跨 run 边界不回放——/resume 只重建最后一个 run_started 之后的事件，
    上个 run 的 brief/步骤不进本 run 记忆的 decided/近况。"""
    from harness.session import RunSession

    info = create_project(tmp_path, "边界书")
    store = ProjectStore(info.path)
    sess = RunSession(ProjectInfo(info.path, "边界书"), store, _settings())
    sess.events.append("run_started", {"raw": "第一轮的 brief", "deliverables": ["outline"]})
    sess.events.append("agent_step", {"thought": "run1 的想法", "action": "plan_arcs", "args": {}})
    sess.events.append("done", {"summary": "run1 完成"})
    sess.events.append("run_started", {"raw": "第二轮的 brief", "deliverables": ["outline"]})
    sess.events.append("agent_step", {"thought": "run2 的想法", "action": "plan_arcs", "args": {}})
    window = sess.replay_events()
    assert all(e["kind"] != "run_started" or e["payload"]["raw"] == "第二轮的 brief"
               for e in window)                    # 窗口里没有第一轮 run_started
    mem = WorkMemory.rebuild(window)
    assert any("第二轮的 brief" in d for d in mem.decided)
    assert not any("第一轮" in d for d in mem.decided)
    assert any("run2 的想法" in v for _, v in mem.detail)
    assert not any("run1" in v for _, v in mem.detail)


def test_brief_without_provider_leaves_no_phantom_run_state(tmp_path, monkeypatch):
    """Important 7：**没跑过**就不留下 state.json / run_started 这种幻影 run。

    P14 之后这条**与 TTY 无关**（非 TTY 也能跑 loop 了）：判据是「provider 没配 →
    _make_components 返回 None → 压根没跑」。
    """
    info = create_project(tmp_path, "幻影书")
    store = ProjectStore(info.path)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    inputs = iter(["写点什么", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    # **不配 provider** → 主循环起不来 → 不得留下幻影 run
    no_provider = Settings(library_root=Path("x"), providers={}, default_provider="",
                            default_tier="standard", web_verify=True)
    run_repl(ProjectInfo(info.path, "幻影书"), store, settings=no_provider)
    assert not (store.root / ".harness" / "state.json").exists()
    events = EventLog(store.root)
    assert events.tail(100) == []  # 无 run_started 事件落盘

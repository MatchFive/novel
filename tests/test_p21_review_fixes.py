"""P21：独立复审（P16–P20）发现的缺陷修复——**逐条钉死**。

复审报告：`d:/tmp/p1620_review.md`（2026-11-17，外部 agent）。
本文件覆盖复审点名的**测试盲区**：

- **P16-1** 多个后台 run 并发（共用 state/memory/events）→ 单 run 守卫；
- **P16-2** ``EventLog.append`` 无锁 → 并发 append **seq 重复、行丢失**；
- **P16-3** EOF//quit 的 join **无限等** → 审批没人答时进程永不退出；
- **P16-4** ``/pause``、``/abort`` 对后台 run **不生效**（run 继续写盘）；
- **P18-1** 第 5 节的值含**换行** → 行式解析**截断**；
- **P18-2** R1 不要求章号严格递增 → **同章两条**造出假回退；
- **P20-1** 否定/开区间范围附「请只写此范围内的内容」→ **与原文语义相反**。

（P17-1/2/3 的用例在 ``tests/agent/test_loop.py`` 与 ``tests/tools/test_registry.py``。）
"""
from __future__ import annotations

import io
import json
import threading

from harness.audit.rules import check_monotonic
from harness.memory.store import MemoryStore
from harness.projects import create_project
from harness.run.events import EventLog
from harness.run.scope import BriefScope, range_unfold_reason
from harness.session import Components
from harness.store.project import ProjectStore

# ------------------------------------------------------------------ P16-2：events 并发

def test_eventlog_concurrent_appends_keep_all_lines_and_unique_seq(tmp_path):
    """**P16-2**：并发 append → **一条不丢 + seq 不重复**。

    旧行为：「读最后一个 seq → +1 → 追加」不是原子的 → 8 线程×40 条 → 303 条落盘、
    **197 个重复 seq** → 事件流的「seq 单调递增」契约（附录 E.2）失效 → 续跑回放、
    审计留痕、误删恢复全部退化。
    """
    log = EventLog(tmp_path)
    threads, per = 8, 40

    def worker() -> None:
        for i in range(per):
            log.append("tool_result", {"i": i})

    ts = [threading.Thread(target=worker) for _ in range(threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    events = log.tail(threads * per * 2)
    assert len(events) == threads * per, "有事件丢了"
    seqs = [e["seq"] for e in events]
    assert len(set(seqs)) == len(seqs), "seq 有重复"
    assert sorted(seqs) == list(range(1, threads * per + 1))


def test_eventlog_rotation_keeps_seq_monotonic(tmp_path):
    """轮转后 seq 仍然接着走（轮转与 append 同一把锁 → 不丢事件）。"""
    log = EventLog(tmp_path)
    log.append("a", {})
    log.append("b", {})
    log.rotate_if_needed()
    log.append("c", {})
    events = log.tail(10)
    assert [e["seq"] for e in events] == [1, 2, 3]


# ------------------------------------------------------------------ P16-1/3/4：REPL 后台 run

class _PipeStdin(io.StringIO):
    def isatty(self) -> bool:
        return False


def _session(tmp_path, name="复审书"):
    from harness.config import ProviderConfig, Settings
    from harness.session import RunSession as _Session

    info = create_project(tmp_path, name)
    store = ProjectStore(info.path)
    settings = Settings(library_root=tmp_path / "lib",
                        providers={"test": ProviderConfig(base_url="https://api.test",
                                                          api_key="k", lite="m",
                                                          standard="m", pro="m")},
                        default_provider="test", default_tier="standard",
                        web_verify=True)
    return _Session(info, store, settings), store


def test_p21_second_task_refused_while_background_run_alive(tmp_path, monkeypatch, capsys):
    """**P16-1**：已有活着的后台 run → **不起第二个 run**（本地同时只跑一个员工任务）。

    旧行为：`_run_or_background` 只 append 线程 → 两个 run 共用 `sess.state`/`sess.memory`/
    events/store → 状态被覆盖、写盘交错（设计 §2.1 明写「本地同时只跑一个员工任务」）。
    """
    from harness.repl import _submit_brief

    sess, _store = _session(tmp_path)
    started = threading.Event()
    release = threading.Event()
    runs = {"n": 0}

    async def fake_run_agent(session, state, comp, *, intake=None, on_note=None):
        runs["n"] += 1
        started.set()
        release.wait(5)

    monkeypatch.setattr("harness.session.RunSession.components",
                    lambda self: Components(object(), None, None))
    monkeypatch.setattr("harness.repl.drive_run", fake_run_agent)
    monkeypatch.setattr("sys.stdin", _PipeStdin(""))

    _submit_brief(sess, "写个仙侠", ["outline"], user_specified=False)
    assert started.wait(timeout=2)
    _submit_brief(sess, "换个题材", ["outline"], user_specified=False)   # 应被拒

    out = capsys.readouterr().out
    assert runs["n"] == 1, "起了第二个 run（共用 state/memory → 状态互相覆盖）"
    assert "同时只跑一个员工任务" in out

    release.set()
    from harness.repl import _join_background
    _join_background(timeout=5)


def test_p21_join_is_bounded_when_run_never_finishes(tmp_path, monkeypatch, capsys):
    """**P16-3**：EOF//quit 的 join **有上限** → 不会因为「审批没人答」而挂死。

    旧行为：``_join_background(timeout=None)`` → ``thread.join(None)`` → 永久阻塞 →
    ``printf '/task …\\n' | harness <项目>`` 的进程**永不退出**（daemon 线程救不了
    主线程的 join）。
    """
    from harness.repl import _join_background

    _sess, store = _session(tmp_path, "挂死书")
    blocker = threading.Event()

    def _never_end():
        blocker.wait(30)

    thread = threading.Thread(target=_never_end, daemon=True)
    thread.start()
    from harness.repl import _RUNNER
    _RUNNER.threads.append(thread)

    import time
    t0 = time.monotonic()
    _join_background(timeout=0.2, pending_dir=store.root / ".harness")
    elapsed = time.monotonic() - t0

    assert elapsed < 5, f"join 等了 {elapsed:.1f}s（上限没生效？）"
    out = capsys.readouterr().out
    assert "未收尾" in out and "pending.answer" in out
    blocker.set()


def test_p21_abort_stops_background_run(tmp_path, monkeypatch):
    """**P16-4**：``/abort`` 之后**后台 run 真的停**（不再调工具/写盘）。

    旧行为：``/abort`` 只改 ``state.status``，而 AgentLoop **从不回看** status →
    「已终止」的 run 照样继续跑，最后还可能 finish 写回 done。
    """
    from harness.repl import _cmd_abort

    sess, store = _session(tmp_path, "终止书")
    state = RunStateFactory(sess)
    sess.state = state

    # 起一个「会一直跑」的 loop（真 AgentLoop + 假 LLM）
    from harness.agent.loop import AgentLoop
    from harness.config import ProviderConfig, Settings
    from harness.prompts.loader import PromptLoader
    from harness.run.memory import WorkMemory
    from harness.tools import readonly_registry

    class _LLM:
        provider = ProviderConfig(base_url="https://api.test", api_key="k",
                                  lite="m", standard="m", pro="m")

        async def complete(self, messages, **kwargs):
            class _C:
                text = json.dumps({"thought": "t", "action": "get_project_state",
                                   "args": {}}, ensure_ascii=False)
                usage = None
                tool_calls = None
            return _C()

        async def aclose(self):
            pass

    settings = Settings(library_root=tmp_path / "lib",
                        providers={"test": _LLM.provider},
                        default_provider="test", default_tier="standard",
                        web_verify=True)
    registry = readonly_registry(store)
    loop = AgentLoop(store=store, state=state, events=EventLog(store.root),
                     memory=WorkMemory(), registry=registry,
                     ui=object(), llm=_LLM(),
                     prompts=PromptLoader(home_dir=store.root),
                     deliverables=["outline"], settings=settings)

    def _abort_soon():
        threading.Event().wait(0.2)
        _cmd_abort(sess)

    threading.Thread(target=_abort_soon, daemon=True).start()
    result = _run(loop)

    assert result.status == "aborted", f"run 没被 /abort 停住（status={result.status}）"
    assert state.stats.get("total_steps", 0) < 60, "abort 后还在无限跑"


def RunStateFactory(sess):
    from harness.run.state import RunState
    state = RunState.new("写个仙侠", ["outline"])
    state.save(sess.store.root)
    return state


def _run(loop):
    import asyncio
    return asyncio.run(loop.run())


def test_p21_abort_during_intake_prevents_run(tmp_path, monkeypatch, capsys):
    """需求解析期间 `/abort` 到达 → **不起 ReAct 循环**（「已终止」必须是真的）。

    P21 补：解析（LLM 调用，几秒）期间用户敲 `/pause`/`/abort` → 状态被改 →
    若解析完照常起循环 → 用户看到「已终止」但 run 还在跑（**观测不实**）。
    """
    import asyncio

    from harness.run.state import RunState
    from harness.session import Components, drive_run

    sess, store = _session(tmp_path, "解析期终止书")
    state = RunState.new("写个仙侠", ["outline"])
    state.save(store.root)
    sess.state = state

    async def fake_intake(**kwargs):
        state.status = "aborted"      # 模拟解析期间 /abort 到达
        return None, "解析完成"

    monkeypatch.setattr("harness.run.intake.run_intake", fake_intake)

    started = []

    class _FakeLoop:
        def __init__(self, **kwargs):
            pass

        async def run(self):
            started.append(1)

    monkeypatch.setattr("harness.session.AgentLoop", _FakeLoop)

    asyncio.run(drive_run(sess, state, Components(object(), object(), object()),
                          intake=("写个仙侠", ["outline"], False), on_note=print))

    assert not started, "解析期间被终止了，却还是起了 ReAct 循环"
    assert "需求解析期间被终止" in capsys.readouterr().out


# ------------------------------------------------------------------ P18-1：值含换行

def test_p21_memory_value_with_newline_is_single_line(tmp_path):
    """**P18-1**：模型给出**含换行**的值 → 写盘**压成一行**、读回**不截断**。

    旧行为：值里的 ``\\n`` 直接写进**行式**记忆文件 → 解析只取第一行 → 后半截**丢失**
    （第 5 节的 level 甚至可能被截成 "9"）→ 历史被静默改写。
    """
    mem = MemoryStore(tmp_path)
    mem.write_chapter_memory(1, "起程", {
        "events": [], "character_events": [], "info_updates": [], "promise_updates": [],
        "state_updates": [{"character": "甲", "field": "notes", "value": "第一行\n第二行"}],
    })

    text = (tmp_path / "memory" / "0001.md").read_text(encoding="utf-8")
    assert "第一行 第二行" in text
    data = mem.read_chapter_memory(1)
    assert data["state_updates"] == [
        {"character": "甲", "field": "notes", "value": "第一行 第二行"}]


def test_p21_state_md_value_with_newline_is_single_line(tmp_path):
    """state.md 的值同样**压成一行**（行式格式 → 换行会错档/截断）。"""
    mem = MemoryStore(tmp_path)
    mem.apply_state_updates({"characters": [
        {"name": "甲", "notes": "左肩\n右臂", "location": "东\n城"}]}, seq=1)
    text = (mem.root / "memory" / "state.md").read_text(encoding="utf-8")
    assert "左肩 右臂" in text and "东 城" in text
    entry = mem.read_state()["characters"]["甲"]
    assert entry["notes"] == "左肩 右臂"


# ------------------------------------------------------------------ P18-2：R1 同章两条

def test_p21_r1_ignores_same_chapter_pair():
    """**P18-2**：同一章里同一角色同一字段两条 → **不报**（那是章内不一致，不是跨章回退）。

    旧行为：只按 (角色, 字段) 取相邻上一条 → 同章的 9→7 被当成「第2章为9，第2章回退为7」
    → **假回退**，噪音淹没用户。
    """
    findings = check_monotonic(
        [{"character": "甲", "field": "level", "value": "9", "chapter": 2},
         {"character": "甲", "field": "level", "value": "7", "chapter": 2}], [])
    assert findings == []


def test_p21_r1_still_reports_cross_chapter_regression():
    """跨章回退**照报**（别把真问题一起放过）。"""
    findings = check_monotonic(
        [{"character": "甲", "field": "level", "value": "9", "chapter": 1},
         {"character": "甲", "field": "level", "value": "7", "chapter": 2}], [])
    assert len(findings) == 1
    assert findings[0].chapter == 2


def test_p21_r1_same_chapter_uses_last_value_for_next_chapter():
    """同章多条 → 以**最后一条**为准（与下一章比较时用最后那条）。"""
    findings = check_monotonic(
        [{"character": "甲", "field": "level", "value": "9", "chapter": 2},
         {"character": "甲", "field": "level", "value": "7", "chapter": 2},
         {"character": "甲", "field": "level", "value": "8", "chapter": 3}], [])
    assert findings == [], "同章去重后不该报（7→8 是上升）"


# ------------------------------------------------------------------ P20-1：说明不与原文相反

def test_p21_negation_range_note_does_not_invert_meaning():
    """**P20-1**：「不写第一卷」是**排除** → 说明**不得**变成「请只写此范围内的内容」。

    旧行为：折算不出卷区间就一律附「请只写此范围内的内容」→ 与原文**语义相反**
    （模型会读成「只写不写第一卷」）。
    """
    out = BriefScope(write_target="不写第一卷").block(for_writing=True)
    assert "正文范围：不写第一卷" in out
    assert "排除语义" in out
    assert "不按范围拦截" in out          # P27 起措辞统一为「范围」（卷或章）
    assert "请只写此范围内的内容" not in out
    assert "按上述范围原文自行取舍" in out


def test_p21_open_end_range_note_does_not_invert_meaning():
    """「从第二卷开始」是**开区间** → 说明**不得**变成「只写第二卷」。"""
    out = BriefScope(write_target="从第二卷开始写").block(for_writing=True)
    assert "开区间" in out
    assert "请只写此范围内的内容" not in out


def test_p21_chapter_granular_reason_is_specific():
    """章粒度 → 原因说清「是章粒度」（不是笼统的「无法解析」）。"""
    assert "章粒度" in range_unfold_reason("从第二章写到第五章")
    assert range_unfold_reason("第一卷到第三卷") == ""      # 能折算 → 无原因

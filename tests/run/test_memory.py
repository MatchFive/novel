from harness.run.constants import WORK_MEMORY_REPLAY
from harness.run.events import EventLog
from harness.run.memory import WorkMemory


def test_rebuild_from_replay_tail(tmp_path):
    log = EventLog(tmp_path)
    log.append("run_started", {"raw": "写卷骨架"})
    log.append("agent_step", {"thought": "先规划", "action": "plan_arcs", "args": {}})
    log.append("tool_result", {"tool": "plan_arcs", "observation": "3 卷"})
    log.append("user_choice", {"question": "继续？", "picked": ["1"]})
    mem = WorkMemory.rebuild(log.tail(WORK_MEMORY_REPLAY * 2))
    assert any("写卷骨架" in d for d in mem.decided)
    assert any("plan_arcs" in k for k, _ in mem.detail)
    assert any("继续？" in d for d in mem.decided)


def test_detail_overflow_folds_oldest_into_summary():
    mem = WorkMemory()
    for i in range(25):
        mem.record({"kind": "agent_step", "payload": {"thought": f"s{i}"}})
    assert len(mem.detail) == 20
    assert any("s0" in s for s in mem.summary)


def test_render_omits_empty_sections():
    mem = WorkMemory()
    mem.record({"kind": "run_started", "payload": {"raw": "x"}})
    out = mem.render()
    assert "【已定】" in out and "【近况】" not in out

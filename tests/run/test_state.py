from harness.run.state import RunState


def test_new_run_state_is_planning_with_zeroed_stats():
    s = RunState.new("写三卷大纲", ["outline"])
    assert s.status == "planning" and s.stats["step"] == 0
    assert s.brief["deliverables"] == ["outline"] and s.brief["plan_arcs"] is None


def test_save_load_round_trip(tmp_path):
    s = RunState.new("r", [])
    s.save(tmp_path)
    s2 = RunState.load(tmp_path)
    assert s2.status == "planning" and s2.stats["retry_queue"] == []


def test_load_returns_none_when_missing(tmp_path):
    assert RunState.load(tmp_path) is None


def test_terminal_states(tmp_path):
    s = RunState.new("r", [])
    assert not s.is_terminal()
    s.status = "done"; assert s.is_terminal()
    s.status = "paused"; assert not s.is_terminal()


def test_initial_defaults_match_e3_sample():
    s = RunState.new("r", [])
    assert s.stats["step_kinds"] == {} and s.brief["constraints"] == []
    assert s.stats["budget_windows"] == 0 and s.stats["step_plan"] == []


def test_load_returns_none_on_corrupt_state(tmp_path):
    harness = tmp_path / ".harness"
    harness.mkdir()
    (harness / "state.json").write_bytes(b"not-json{{{}\xff\xfe")
    assert RunState.load(tmp_path) is None

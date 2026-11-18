from harness.run.events import EventLog


def test_append_assigns_monotonic_seq_and_ts(tmp_path):
    log = EventLog(tmp_path)
    e1 = log.append("run_started", {"raw": "x"})
    e2 = log.append("agent_step", {"thought": "t"})
    assert e1["seq"] == 1 and e2["seq"] == 2
    assert e1["kind"] == "run_started" and "ts" in e1


def test_tail_skips_corrupt_lines(tmp_path):
    log = EventLog(tmp_path)
    log.append("a", {}); log.append("b", {})
    with open(tmp_path / ".harness" / "events.log", "a", encoding="utf-8") as f:
        f.write("not-json{{{}\n")
    log.append("c", {})
    kinds = [e["kind"] for e in log.tail(3)]
    assert kinds == ["a", "b", "c"]  # 坏行被跳过


def test_tail_skips_valid_json_non_dict_and_seq_recovery(tmp_path):
    """合法 JSON 标量行（手改 events.log 写入 123）：tail 跳过、append 照常续 seq、
    rebuild 不崩在 event.get("kind")。"""
    from harness.run.memory import WorkMemory

    log = EventLog(tmp_path)
    log.append("agent_step", {"thought": "t1"})
    with open(tmp_path / ".harness" / "events.log", "a", encoding="utf-8") as f:
        f.write("123\n")  # 合法 JSON 但非 dict
    log.append("agent_step", {"thought": "t2"})
    thoughts = [e["payload"]["thought"] for e in log.tail(10)]
    assert thoughts == ["t1", "t2"]             # 标量行被跳过
    assert log.append("agent_step", {"thought": "t3"})["seq"] == 3  # seq 未被标量行打断
    mem = WorkMemory.rebuild(log.tail(10))
    assert len(mem.detail) == 3                 # rebuild 未抛 AttributeError


def test_tail_replay_rebuilds_work_memory_source(tmp_path):
    log = EventLog(tmp_path)
    for i in range(5):
        log.append("agent_step", {"thought": f"t{i}"})
    assert [e["seq"] for e in log.tail(2)] == [4, 5]


def test_rotation_archives_gz(tmp_path, monkeypatch):
    log = EventLog(tmp_path)
    log.append("a", {})
    # 直接调 rotate_if_needed 前把阈值 monkeypatch 小
    monkeypatch.setattr("harness.run.constants.EVENTS_ROTATE_LINES", 1)
    archived = log.rotate_if_needed()
    assert archived is not None and archived.suffix == ".gz"
    assert not (tmp_path / ".harness" / "events.log").exists() or \
        (tmp_path / ".harness" / "events.log").read_text(encoding="utf-8") == ""
    log.append("b", {})
    assert log.tail(1)[0]["kind"] == "b"  # seq 重新从 1 起算可接受


def test_rotation_same_month_keeps_both_archives(tmp_path, monkeypatch):
    """同月两次轮转不再互相覆盖：归档名带时间戳后缀，两份 gz 都在。"""
    log = EventLog(tmp_path)
    monkeypatch.setattr("harness.run.constants.EVENTS_ROTATE_LINES", 1)
    log.append("a", {})
    first = log.rotate_if_needed()
    log.append("b", {})
    second = log.rotate_if_needed()
    assert first is not None and second is not None
    assert first != second                    # 时间戳后缀消歧
    assert first.exists() and second.exists()  # 旧的归档未被 clobber

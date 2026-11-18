"""EventLog.read_new：字节 offset 增量读（Task 9 SSE 轮询的廉价增量读契约）。"""
import json

from harness.run.events import EventLog


def test_read_new_from_zero(tmp_path):
    log = EventLog(tmp_path)
    log.append("run_started", {"raw": "x"})
    log.append("agent_step", {"thought": "t"})
    events, new_offset = log.read_new(0)
    assert [e["kind"] for e in events] == ["run_started", "agent_step"]
    assert new_offset > 0


def test_read_new_incremental(tmp_path):
    log = EventLog(tmp_path)
    log.append("a", {})
    events1, off1 = log.read_new(0)
    assert [e["kind"] for e in events1] == ["a"]
    log.append("b", {})
    log.append("c", {})
    events2, off2 = log.read_new(off1)
    assert [e["kind"] for e in events2] == ["b", "c"]  # 二次调用只给新行
    assert off2 > off1                                  # offset 前进


def test_read_new_partial_tail_line(tmp_path):
    log = EventLog(tmp_path)
    log.append("a", {})
    with open(log.path, "a", encoding="utf-8") as f:
        f.write('{"seq": 99, "kind": "half"')  # 无换行：半行
    events, off = log.read_new(0)
    assert [e["kind"] for e in events] == ["a"]  # 尾行没写完不被消费
    path_bytes = len(log.path.read_bytes())
    assert off < path_bytes                       # offset 停在换行处，不吞半行


def test_read_new_bad_line_skipped(tmp_path):
    log = EventLog(tmp_path)
    log.append("a", {})
    with open(log.path, "a", encoding="utf-8") as f:
        f.write("not-json{{{}\n123\n")  # 坏行 + 合法 JSON 非 dict（同 tail 口径）
    log.append("b", {})
    events, off = log.read_new(0)
    assert [e["kind"] for e in events] == ["a", "b"]  # 坏行静默跳过
    assert off == len(log.path.read_bytes())          # 跳过的行仍计入 offset 前进（不死循环）
    # 再次同 offset 读：无新内容 → 空
    assert log.read_new(off) == ([], off)


def test_read_new_rotation_restarts(tmp_path):
    log = EventLog(tmp_path)
    log.append("a", {"pad": "x" * 200})  # 让旧 offset 大于新文件大小（触发 st_size < offset 重扫）
    _, off = log.read_new(0)
    log.path.unlink()  # 轮转删了活动文件
    assert log.read_new(off) == ([], 0)  # 文件不存在 → ([], 0)
    log.append("b", {})  # seq 从 1 重起
    events, off2 = log.read_new(off)
    # stat().st_size < offset → 从 0 重扫，拿到新文件内容
    assert [e["kind"] for e in events] == ["b"]
    assert off2 == len(log.path.read_bytes())
    assert json.loads(log.path.read_text(encoding="utf-8").splitlines()[0])["seq"] == 1

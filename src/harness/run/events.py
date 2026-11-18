"""events.log：追加/回放/轮转（JSONL，一行一事件）。

事件格式：`{"seq": N, "kind": "...", "payload": {...}, "ts": "ISO8601"}`。
活动文件位于 `<root>/.harness/events.log`；超阈值轮转为同目录 `events-YYYYMM-<时间戳>.log.gz`。
seq 恢复容忍坏尾行：活动文件不存在 → 1；最后可解析行 seq + 1；
尾行损坏 → 回退扫描前值；扫不到 → 1。

**线程安全**（2026-11-17 独立复审 P16-2）：P16 之后 run 在**后台线程**里跑，REPL 主线程
也在 append（「/pause」「/abort」、第二个 run 的 `run_started`）→ 「读最后一个 seq → +1 →
追加」**不是原子的** → 并发时 seq 重复、行丢失（实测 8 线程×40 条 → 303 条落盘、
**197 个重复 seq**）→ 事件流的「seq 单调递增」契约（附录 E.2）失效 → 续跑回放、
审计留痕、误删恢复全部退化。→ **按路径加进程内锁**，「分配 seq + 追加」同一临界区。
"""

import gzip
import json
import threading
from datetime import UTC, datetime
from pathlib import Path

from harness.run import constants

EVENTS_DIR = ".harness"
EVENTS_FILE = "events.log"

#: **按路径**的写锁（进程内）——同一 events.log 的所有 EventLog 实例共用一把锁。
#: （同一个项目可能有多份 EventLog 对象：REPL 会话一份、run 里另一份。）
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    """取某个 events.log 的写锁（按绝对路径；不存在则建）。"""
    key = str(Path(path).resolve())
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[key] = lock
        return lock


class EventLog:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.dir = self.root / EVENTS_DIR
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / EVENTS_FILE

    # ------------------------------------------------------------------ 写

    def append(self, kind: str, payload: dict) -> dict:
        """追加一行事件，返回含 seq/ts 的完整事件 dict。

        **分配 seq 与写盘在同一临界区**（否则并发 append → seq 重复、行丢失）。
        """
        with _lock_for(self.path):
            event = {
                "seq": self._next_seq(),
                "kind": kind,
                "payload": payload,
                "ts": datetime.now(UTC).isoformat(),
            }
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def _next_seq(self) -> int:
        last = self._last_seq()
        return 1 if last is None else last + 1

    def _last_seq(self) -> int | None:
        """活动文件最后一个可解析事件的 seq；尾行损坏时回退扫描前值。"""
        if not self.path.exists():
            return None
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        last_seq = None
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue  # 坏行跳过，继续向前找
            if not isinstance(obj, dict):
                continue  # 合法 JSON 但非 dict（如手改的标量 123）：无 seq 语义，跳过
            seq = obj.get("seq")
            if isinstance(seq, int) and not isinstance(seq, bool):
                last_seq = seq
                break
        return last_seq

    # ------------------------------------------------------------------ 读

    def read_new(self, offset: int) -> tuple[list[dict], int]:
        """从字节 offset 增量读 (新事件列表, 新 offset)。
        - offset=0 从头；只返回**完整行**（尾行没写完不消费）；
        - 坏行/非 dict 行静默跳过（同 tail 口径）；
        - 文件不存在 → ([], 0)；文件比 offset 小（轮转删了）→ 从 0 重扫。"""
        if not self.path.exists():
            return [], 0
        if self.path.stat().st_size < offset:
            offset = 0
        with open(self.path, "rb") as f:
            f.seek(offset)
            data = f.read()
        head, sep, _tail = data.rpartition(b"\n")  # 最后一个 \n 之后的尾巴不消费
        if not sep:
            return [], offset
        consumed = offset + len(head) + 1  # 跳过的坏行也计入（否则死循环）
        events = []
        for raw in head.split(b"\n"):
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue  # 坏行静默跳过（同 tail 口径）
            if not isinstance(obj, dict):
                continue  # 合法 JSON 标量/数组：非事件形态，跳过
            events.append(obj)
        return events, consumed

    def tail(self, n: int) -> list[dict]:
        """活动文件最后 n 个事件；坏行静默跳过，不影响 seq 连续性。"""
        events = []
        for line in self._scan_lines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue  # 坏行静默跳过
            if not isinstance(obj, dict):
                continue  # 合法 JSON 标量/数组：非事件形态，跳过（防 rebuild 崩在 .get）
            events.append(obj)
        return events[-n:] if n > 0 else []

    def _scan_lines(self) -> list[str]:
        if not self.path.exists():
            return []
        text = self.path.read_text(encoding="utf-8")
        if len(text) > constants.SCAN_CAP:
            half = constants.SCAN_CAP // 2
            text = text[:half] + "\n...\n" + text[-half:]
        return text.splitlines()

    # ------------------------------------------------------------------ 轮转

    def rotate_if_needed(self) -> Path | None:
        """行数或字节超阈值 → gzip 归档到 events-YYYYMM-<时间戳>.log.gz 并删活动文件。

        归档名带秒级+微秒时间戳：同月两次轮转不再互相覆盖（旧实现同名 clobber）。
        **与 append 同一把锁**（否则轮转与并发 append 交错 → 丢事件）。
        """
        with _lock_for(self.path):
            if not self.path.exists():
                return None
            lines = 0
            with open(self.path, encoding="utf-8") as f:
                for _ in f:
                    lines += 1
            size = self.path.stat().st_size
            if lines < constants.EVENTS_ROTATE_LINES and size < constants.EVENTS_ROTATE_BYTES:
                return None
            yyyymm = datetime.now(UTC).strftime("%Y%m")
            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
            archive = self.dir / f"events-{yyyymm}-{stamp}.log.gz"
            # Windows 时钟滴答 ~15.6ms：连续两次轮转可能落同一微秒戳，重打戳消歧（同月不互覆约定）
            while archive.exists():
                stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
                archive = self.dir / f"events-{yyyymm}-{stamp}.log.gz"
            with open(self.path, "rb") as f_in, gzip.open(archive, "wb") as f_out:
                f_out.writelines(f_in)
            self.path.unlink()
            return archive

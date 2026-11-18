"""SSE 事件流（Task 9）：``GET /api/project/{id}/stream?after=<seq>``。

帧格式（逐字）：

- 首帧 ``event: hello\\ndata: {"project","last_seq"}\\n\\n``；
- 每条事件 ``event: event\\ndata: {seq,kind,payload,ts}\\n\\n``；
- 心跳 ``: ping\\n\\n``（SSE 注释行）。

``after`` 重连：首循环前 ``read_new(0)`` 全扫一次，把 ``seq>after`` 的历史补发并把
offset 定位到日志末尾（**每连接只全扫一次**；稳态循环都是 ``read_new(offset)`` 增量读，
不受 ``SCAN_CAP`` 限——字节 offset 直接 seek，超阈值的长日志也能完整补发）。

客户端断开：starlette 关闭生成器（yield 点抛 ``GeneratorExit``），``finally`` 收尾。
心跳以 poll_interval 累进近似：每 heartbeat 秒一条 ``: ping``。
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

from harness.run.approval import parse_pending_md
from harness.run.events import EventLog
from harness.store.project import ProjectStore

_PING = ": ping\n\n"


def _frame(name: str, data: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _is_seq(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _poll_pending(path: Path) -> tuple[tuple[int, int] | None, dict | None]:
    """pending.md 现值。

    不存在/读失败 → ``(None, None)``；存在但无头/坏格式 → ``(sig, None)``——存在性以
    sig 为准（不误发 cleared），等它变成可解析再播报。sig = ``(mtime_ns, size)``：
    换一个 pending（重写）sig 变 → 重新播报。
    """
    try:
        st = path.stat()
    except OSError:
        return None, None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return (st.st_mtime_ns, st.st_size), None
    return (st.st_mtime_ns, st.st_size), parse_pending_md(text)


async def event_stream(store: ProjectStore, *, after: int, poll_interval: float,
                       heartbeat: float) -> AsyncIterator[str]:
    """首帧 event: hello / data: {"project","last_seq"}；
    然后周期循环：read_new(offset) 有新行 → 每条一行 `event: event\\ndata: {seq,kind,payload,ts}`；
    每 heartbeat 秒一条 `: ping` 注释；after 重连：首循环前先把 seq>after 的历史补发
    （从 0 扫一次定位 offset——每连接一次，稳态不再全扫）。"""
    log = EventLog(store.root)

    # after 重连定位：全扫一次（每连接一次），补发 seq>after，offset 直接到末尾。
    backlog, offset = log.read_new(0)
    replay = [e for e in backlog if _is_seq(e.get("seq")) and e["seq"] > after]
    last_seq = max((e["seq"] for e in backlog if _is_seq(e.get("seq"))), default=0)

    yield _frame("hello", {"project": store.root.name, "last_seq": last_seq})
    for event in replay:
        yield _frame("event", event)

    # pending 状态机（Task 11）：每轮主循环查 pending.md mtime/存在性。
    # 出现（或换了一个，sig 变）且未播报 → approval_pending；已播报后两文件消失
    # （run 消费答案清盘）→ approval_cleared（前端据此把占位切「已答」）。
    pending_path = store.root / ".harness" / "pending.md"
    announced: tuple[int, int] | None = None

    beat = 0.0
    try:
        while True:
            events, offset = log.read_new(offset)
            for event in events:
                yield _frame("event", event)
            sig, payload = _poll_pending(pending_path)
            if sig is not None and payload is not None and sig != announced:
                yield _frame("approval_pending", payload)
                announced = sig
            elif sig is None and announced is not None:
                yield _frame("approval_cleared", {})
                announced = None
            await asyncio.sleep(poll_interval)
            beat += poll_interval
            if beat >= heartbeat:
                yield _PING
                beat = 0.0
    finally:
        # 客户端断开 → 生成器被关闭（GeneratorExit 在 yield 点抛出）。文件型 EventLog
        # 无打开句柄需要释放；保留 finally 作为断开收尾点（后续接入清理逻辑挂这里）。
        pass

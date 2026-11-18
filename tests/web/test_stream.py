"""Task 9：SSE 事件流 GET /api/project/{id}/stream?after=<seq>。

帧契约（逐字）：首帧 ``event: hello\\ndata: {"project","last_seq"}\\n\\n``；
每条事件 ``event: event\\ndata: {seq,kind,payload,ts}\\n\\n``；心跳 ``: ping\\n\\n``。

**为什么不走 TestClient 流式读**：starlette 0.46.2 的 TestClient transport 会把响应体
**整个缓冲完**才返回（``portal.call(self.app, ...)`` 等 ASGI app 跑完），无限 SSE 流
永远跑不完 → ``client.stream()`` 连响应头都拿不到（已用最小 repro 证实）。因此：
语义测试直接驱动 ``event_stream`` 异步生成器（asyncio.run + wait_for 兜底超时）；
HTTP 层（路由注册 / 参数传递 / media_type）用 monkeypatch 换成有限生成器走 TestClient。
"""

import asyncio
import contextlib
import json

import pytest
from fastapi.testclient import TestClient

from harness.config import Settings
from harness.run.events import EventLog
from harness.store.project import ProjectStore
from harness.web.stream import event_stream


@pytest.fixture
def settings(tmp_path):
    return Settings(
        library_root=tmp_path / "novels",
        providers={},
        default_provider="",
        default_tier="standard",
        web_verify=True,
    )


@pytest.fixture
def client(settings):
    from harness.web.app import create_app

    # 心跳 / 轮询都压到 0.01s：心跳测试不等真 25s。
    return TestClient(create_app(settings, poll_interval=0.01, heartbeat=0.01))


@pytest.fixture
def fx(client, settings):
    """建项目「流书」，返回 (ProjectStore, EventLog) 供测试预写事件。"""
    resp = client.post("/api/projects", json={"title": "流书"})
    assert resp.status_code == 200
    store = ProjectStore(settings.library_root / "流书")
    return store, EventLog(store.root)


def _collect(store, *, after=0, until, poll_interval=0.01, heartbeat=0.01,
             limit=100, timeout=5.0):
    """驱动 event_stream 收帧，直到 until(frames) 为真；收不到 → 超时失败。

    收尾走 ``gen.aclose()``——即客户端断开时 starlette 关闭生成器的同一条 finally 路径。
    """
    async def run():
        frames = []
        gen = event_stream(store, after=after, poll_interval=poll_interval,
                           heartbeat=heartbeat)
        try:
            async for chunk in gen:
                frames.append(chunk)
                if until(frames) or len(frames) >= limit:
                    break
        finally:
            with contextlib.suppress(BaseException):
                await gen.aclose()
        return frames

    return asyncio.run(asyncio.wait_for(run(), timeout=timeout))


def _parse(chunk):
    """'event: X\\ndata: {...}\\n\\n' → (name, data dict)；': ping\\n\\n' → (None, None)。"""
    name, data = None, None
    for line in chunk.splitlines():
        if line.startswith("event: "):
            name = line[len("event: "):]
        elif line.startswith("data: "):
            data = json.loads(line[len("data: "):])
    return name, data


def test_stream_hello_and_events(client, fx):
    store, log = fx
    log.append("run_started", {"n": 1})
    log.append("step_done", {"n": 2})

    frames = _collect(store, until=lambda fs: len(fs) >= 3)

    hello, ev1, ev2 = frames[0], frames[1], frames[2]
    assert _parse(hello) == ("hello", {"project": "流书", "last_seq": 2})
    name, data = _parse(ev1)
    assert name == "event" and data["seq"] == 1 and data["kind"] == "run_started"
    name, data = _parse(ev2)
    assert name == "event" and data["seq"] == 2 and data["kind"] == "step_done"


def test_stream_after_replays_tail(client, fx):
    store, log = fx
    first = log.append("run_started", {"n": 1})
    log.append("step_done", {"n": 2})

    frames = _collect(store, after=first["seq"], until=lambda fs: len(fs) >= 2)

    assert _parse(frames[0]) == ("hello", {"project": "流书", "last_seq": 2})
    replayed = [f for f in frames if _parse(f)[0] == "event"]
    assert [_parse(f)[1]["seq"] for f in replayed] == [2]  # 只补发 seq>after 的第 2 条


def test_stream_heartbeat(client, fx):
    store, _log = fx
    frames = _collect(store, until=lambda fs: any(f == ": ping\n\n" for f in fs))
    assert ": ping\n\n" in frames


def test_stream_route(client, fx, monkeypatch):
    """HTTP 层：路由注册、after 查询参数、app.state 的 poll_interval/heartbeat 传递。

    TestClient 无法消费无限流（见模块 docstring），换成有限生成器验证装配。
    """
    captured = {}

    async def finite(store, *, after, poll_interval, heartbeat):
        captured.update(after=after, poll_interval=poll_interval, heartbeat=heartbeat)
        yield "event: hello\ndata: {}\n\n"

    monkeypatch.setattr("harness.web.app.event_stream", finite)
    resp = client.get("/api/project/流书/stream?after=7")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.text.startswith("event: hello")
    # 查询参数 + create_app(poll_interval=0.01, heartbeat=0.01) 原样透传进生成器
    assert captured == {"after": 7, "poll_interval": 0.01, "heartbeat": 0.01}

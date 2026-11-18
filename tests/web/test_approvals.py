"""Task 11：审批 API（GET pending 现值 / POST answer）+ SSE approval_pending/approval_cleared。

- 应答 API 与 repl /answer **同一 write_answer**：校验失败 400、pending 已被答/不存在 409。
- SSE：pending.md 出现且未播报 → ``approval_pending``（data {question,options,count,multi}）；
  已被答（pending.md 消失）→ ``approval_cleared``。

帧收集沿用 test_stream 的驱动方式（TestClient 缓冲无限流，见该模块 docstring）。
"""
from __future__ import annotations

import asyncio
import contextlib
import json

import pytest
from fastapi.testclient import TestClient

from harness.config import Settings
from harness.run.approval import _render_pending
from harness.store.project import ProjectStore

PENDING = _render_pending("改吗？", ["改", "不改"], multi=False)


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

    return TestClient(create_app(settings, poll_interval=0.01, heartbeat=0.01))


@pytest.fixture
def fx(client, settings):
    """建项目「批书」，返回 ProjectStore（.harness 目录已就绪）。"""
    resp = client.post("/api/projects", json={"title": "批书"})
    assert resp.status_code == 200
    store = ProjectStore(settings.library_root / "批书")
    (store.root / ".harness").mkdir(parents=True, exist_ok=True)
    return store


def _url(store: ProjectStore, tail: str) -> str:
    return f"/api/project/{store.root.name}/approvals/{tail}"


def _collect(store, *, until, limit=100, timeout=5.0):
    """驱动 event_stream 收帧，直到 until(frames) 为真（同 test_stream._collect）。"""
    from harness.web.stream import event_stream

    async def run():
        frames = []
        gen = event_stream(store, after=0, poll_interval=0.01, heartbeat=0.01)
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


# ------------------------------------------------------------------ 应答 API


def test_answer_endpoint_400_on_invalid(client, fx):
    """越界选项号 → 400 {"error": ...}，且**绝不写** pending.answer（Review Focus #2）。"""
    (fx.root / ".harness" / "pending.md").write_text(PENDING, encoding="utf-8")
    resp = client.post(_url(fx, "answer"), json={"indices": [9], "note": ""})
    assert resp.status_code == 400
    assert "error" in resp.json()
    assert not (fx.root / ".harness" / "pending.answer").exists()


def test_answer_endpoint_ok_then_pending_missing_409(client, fx):
    """合法应答 → 200 {"ok": true} 且写盘格式与 repl 一致；答完再问 → 409。"""
    pending_path = fx.root / ".harness" / "pending.md"
    pending_path.write_text(PENDING, encoding="utf-8")
    resp = client.post(_url(fx, "answer"), json={"indices": [1], "note": "同意"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    lines = (fx.root / ".harness" / "pending.answer").read_text(encoding="utf-8").splitlines()
    assert lines[0] == "1" and lines[1] == "备注:同意"
    # run 侧消费答案后清掉两个文件 → 再答 = pending 已被答/不存在 → 409
    pending_path.unlink()
    resp = client.post(_url(fx, "answer"), json={"indices": [1], "note": ""})
    assert resp.status_code == 409


def test_pending_endpoint_reports_current_value(client, fx):
    """GET pending：有 → pending:true + 解析字段；无 → {"pending": false}。"""
    resp = client.get(_url(fx, "pending"))
    assert resp.status_code == 200
    assert resp.json() == {"pending": False}
    (fx.root / ".harness" / "pending.md").write_text(PENDING, encoding="utf-8")
    body = client.get(_url(fx, "pending")).json()
    assert body["pending"] is True
    assert body["question"] == "改吗？"
    assert body["options"] == ["改", "不改"]
    assert body["count"] == 2 and body["multi"] is False


# ------------------------------------------------------------------ SSE pending 状态机


def test_sse_emits_approval_pending(client, fx):
    """流已起来之后 pending.md 才出现 → 主循环轮询发现 → 播报 approval_pending。"""
    pending_path = fx.root / ".harness" / "pending.md"
    state = {"written": False}

    def until(frames):
        # hello 已出（流进入稳态）后再落 pending.md：播报只能靠轮询，不能靠启动快照。
        if not state["written"] and any(_parse(f)[0] == "hello" for f in frames):
            pending_path.write_text(PENDING, encoding="utf-8")
            state["written"] = True
        return any(_parse(f)[0] == "approval_pending" for f in frames)

    frames = _collect(fx, until=until)
    _name, data = next(_parse(f) for f in frames if _parse(f)[0] == "approval_pending")
    assert data == {"question": "改吗？", "options": ["改", "不改"], "count": 2, "multi": False}


def test_sse_emits_approval_cleared(client, fx):
    """已播报的 pending 被答掉（pending.md 消失）→ 播报警 approval_cleared 复位状态机。"""
    pending_path = fx.root / ".harness" / "pending.md"
    pending_path.write_text(PENDING, encoding="utf-8")
    state = {"announced": False}

    def until(frames):
        if not state["announced"] and any(_parse(f)[0] == "approval_pending" for f in frames):
            state["announced"] = True
            pending_path.unlink()  # run 侧消费答案后清掉两个文件
        return state["announced"] and any(_parse(f)[0] == "approval_cleared" for f in frames)

    frames = _collect(fx, until=until)
    _name, data = next(_parse(f) for f in frames if _parse(f)[0] == "approval_cleared")
    assert data == {}

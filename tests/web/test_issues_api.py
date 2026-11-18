"""Task 14：问题台账处置——resolve/ignore + 「交给 AI 修」触发。

契约（brief 逐字）：

- ``POST /api/project/{id}/issues/{id}/resolve`` body ``{"status": "resolved"|"ignored"}`` → 200；
- ``POST /api/project/{id}/issues/{id}/fix`` → 202 ``{"accepted": true}``；
  ``components()`` 缺（未配置 provider）→ 409（绝不硬跑，issue 保持 pending）；
  否则 ``asyncio.create_task`` 跑 ``harness.audit.fix.fix_issue``
  （``emit=session.events.append``，审批确认卡走文件协议由前端弹窗应答）。

``test_fix_kicks_off_task`` 的时序：fix 路由 202 应答后修复在后台 task 里跑，
spy 用 ``threading.Event`` 等它真正被调用（TestClient 的 loop 在另一个线程里）。
"""

import threading

import pytest
from fastapi.testclient import TestClient

from harness.config import ProviderConfig, Settings
from harness.run.issues import IssuesStore


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

    return TestClient(create_app(settings))


@pytest.fixture
def fx(client):
    """建一个项目，返回其项目 id（文件夹名）。"""
    resp = client.post("/api/projects", json={"title": "台账书"})
    assert resp.status_code == 200
    return resp.json()["id"]


def _enable_provider(client):
    """给 app 的 settings 挂一个 dummy provider（components() 因而非 None；不联网）。"""
    s = client.app.state.settings
    s.providers["dummy"] = ProviderConfig(
        base_url="http://127.0.0.1:9/v1", api_key="x", lite="m", standard="m", pro="m")
    s.default_provider = "dummy"


def test_resolve_and_ignore(client, fx):
    root = client.app.state.settings.library_root / fx
    store = IssuesStore(root)
    a = store.add("canon", "标题甲", "详情甲", quote="原文甲")
    b = store.add("canon", "标题乙", "详情乙", quote="原文乙")

    resp = client.post(f"/api/project/{fx}/issues/{a.id}/resolve", json={"status": "resolved"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "resolved"
    assert IssuesStore(root).one(a.id).status == "resolved"

    resp = client.post(f"/api/project/{fx}/issues/{b.id}/resolve", json={"status": "ignored"})
    assert resp.status_code == 200
    assert IssuesStore(root).one(b.id).status == "ignored"
    # 两条都已不在 pending 列表
    assert [i.id for i in IssuesStore(root).list("pending")] == []


def test_resolve_unknown_id_404(client, fx):
    resp = client.post(f"/api/project/{fx}/issues/9999/resolve", json={"status": "resolved"})
    assert resp.status_code == 404


def test_fix_requires_provider(client, fx):
    """未配置 provider → components() 为 None → 409，issue 保持 pending（不硬跑）。"""
    root = client.app.state.settings.library_root / fx
    issue = IssuesStore(root).add("canon", "待修", "详情", quote="原文")

    resp = client.post(f"/api/project/{fx}/issues/{issue.id}/fix")

    assert resp.status_code == 409
    assert "error" in resp.json()
    assert IssuesStore(root).one(issue.id).status == "pending"


def test_fix_unknown_id_404(client, fx):
    _enable_provider(client)
    resp = client.post(f"/api/project/{fx}/issues/9999/fix")
    assert resp.status_code == 404


def test_fix_busy_409(client, fx, monkeypatch):
    """有 run 在跑（runner.alive()）→ 409 busy，fix_issue 未被调起（issue 保持 pending）。"""
    _enable_provider(client)
    root = client.app.state.settings.library_root / fx
    issue = IssuesStore(root).add("canon", "待修", "详情", quote="原文")

    called = threading.Event()

    async def fake_fix_issue(**kwargs):
        called.set()
        return "不应被调起"

    monkeypatch.setattr("harness.audit.fix.fix_issue", fake_fix_issue)
    monkeypatch.setattr(client.app.state.runner, "alive", lambda: True)

    resp = client.post(f"/api/project/{fx}/issues/{issue.id}/fix")

    assert resp.status_code == 409
    assert resp.json() == {"error": "busy"}
    assert not called.wait(timeout=1)
    assert IssuesStore(root).one(issue.id).status == "pending"


def test_fix_kicks_off_task(client, fx, monkeypatch):
    """provider 在 → 202 accepted，fix_issue 被 asyncio.create_task 调起（spy 实证）。"""
    _enable_provider(client)
    root = client.app.state.settings.library_root / fx
    issue = IssuesStore(root).add("canon", "待修", "详情", quote="原文片段")

    called = threading.Event()
    seen: dict = {}

    async def fake_fix_issue(**kwargs):
        seen.update(kwargs)
        called.set()
        return "已修改第1章并标记 resolved，记忆已重提取"

    monkeypatch.setattr("harness.audit.fix.fix_issue", fake_fix_issue)

    resp = client.post(f"/api/project/{fx}/issues/{issue.id}/fix")

    assert resp.status_code == 202
    assert resp.json() == {"accepted": True}
    # 后台 task 真正跑起来（TestClient 的 loop 在另一线程，给它一点时间）
    assert called.wait(timeout=10)
    assert seen["issue"].id == issue.id
    assert seen["store"].root == root
    assert callable(seen["emit"])
    assert seen["ui"] is not None
    # 修复结果不落在台账文件里（状态翻转由 fix_issue 内部完成）；此处保持 pending 即可
    assert IssuesStore(root).one(issue.id).status == "pending"


def test_fix_in_flight_blocks_task_resume_and_second_fix(client, fx, monkeypatch):
    """终审 Finding ②：fix 在飞（app.state.fix_projects 含本项目 = pending.md 单槽被占）
    → 起 run / 续跑 / 二次 fix 一律 409 busy（不抢槽、不共用 pending.answer）；
    槽位释放后两边恢复 200。

    槽位直接占（与路由 ``create_task`` 前 add 的是同一只集合）——TestClient 每个请求
    一个 task group，响应完成即取消后台 task，真实「跨请求的 fix 在飞」无法在其上模拟
    （生产 uvicorn 无此限制）；生命周期见 ``test_fix_slot_lifecycle_add_then_discard``。
    """
    import time

    _enable_provider(client)
    root = client.app.state.settings.library_root / fx
    store = IssuesStore(root)
    issue_a = store.add("canon", "甲待修", "详情甲", quote="原文甲")
    issue_b = store.add("canon", "乙待修", "详情乙", quote="原文乙")

    monkeypatch.setattr(client.app.state.runner, "launch", lambda *a, **k: None)

    # 先起一个 run（planning 非终态，给 resume 一个可作用对象）
    resp = client.post(f"/api/project/{fx}/task", json={"raw": "x", "deliverables": ["outline"]})
    assert resp.status_code == 200

    # 模拟 fix 在飞：占住单槽（修复前：三个入口都不查槽，直接放行 → 两个 waiter 共享
    # 一份 pending.answer，第二个 ask 覆写问题、双方消费同一答案 → 静默错答）
    client.app.state.fix_projects.add(fx)
    try:
        resp = client.post(f"/api/project/{fx}/task",
                           json={"raw": "y", "deliverables": ["outline"]})
        assert resp.status_code == 409
        assert resp.json() == {"error": "busy"}
        resp = client.post(f"/api/project/{fx}/issues/{issue_a.id}/fix")
        assert resp.status_code == 409
        assert resp.json() == {"error": "busy"}
        assert IssuesStore(root).one(issue_a.id).status == "pending"   # 未起幻影修复
        resp = client.post(f"/api/project/{fx}/commands/resume")
        assert resp.status_code == 409
        assert resp.json() == {"error": "busy"}
    finally:
        client.app.state.fix_projects.discard(fx)

    # 槽位释放（fix 结束）→ 起 run / 续跑恢复 200
    for _ in range(1000):
        if fx not in client.app.state.fix_projects:
            break
        time.sleep(0.01)
    assert fx not in client.app.state.fix_projects
    resp = client.post(f"/api/project/{fx}/task", json={"raw": "z", "deliverables": ["outline"]})
    assert resp.status_code == 200
    resp = client.post(f"/api/project/{fx}/commands/resume")
    assert resp.status_code == 200
    # issue_b 全程未被动过
    assert IssuesStore(root).one(issue_b.id).status == "pending"


def test_fix_slot_lifecycle_add_then_discard(client, fx, monkeypatch):
    """槽位生命周期：fix 路由在 create_task **之前** add；任务结束（含异常/取消）后
    finally discard——绝不把项目永久锁成 409 busy（终审 Finding ② 的另一半）。"""
    import time

    _enable_provider(client)
    root = client.app.state.settings.library_root / fx
    issue = IssuesStore(root).add("canon", "待修", "详情", quote="原文")

    class _SpySet(set):
        def __init__(self):
            super().__init__()
            self.events: list[tuple[str, str]] = []

        def add(self, x):
            self.events.append(("add", x))
            super().add(x)

        def discard(self, x):
            self.events.append(("discard", x))
            super().discard(x)

    client.app.state.fix_projects = _SpySet()

    called = threading.Event()

    async def fake_fix_issue(**kwargs):
        called.set()
        return "已修复"

    monkeypatch.setattr("harness.audit.fix.fix_issue", fake_fix_issue)

    resp = client.post(f"/api/project/{fx}/issues/{issue.id}/fix")
    assert resp.status_code == 202
    assert called.wait(timeout=10)
    # TestClient 每请求一个 task group：响应完成时后台 task 被取消 → finally 照样释放槽位
    for _ in range(1000):
        if fx not in client.app.state.fix_projects:
            break
        time.sleep(0.01)
    assert fx not in client.app.state.fix_projects
    spy = client.app.state.fix_projects
    assert ("add", fx) in spy.events and ("discard", fx) in spy.events
    assert spy.events.index(("add", fx)) < spy.events.index(("discard", fx))

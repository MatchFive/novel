"""Task 5：起 run / pause / resume / abort API（AsyncRunner 与 AgentLoop 同事件循环）。"""

import pytest
from fastapi.testclient import TestClient

from harness.config import ProviderConfig, Settings
from harness.run.events import EventLog
from harness.run.state import RunState


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
    resp = client.post("/api/projects", json={"title": "运行书"})
    assert resp.status_code == 200
    return resp.json()["id"]


def _enable_provider(client):
    """给 app 的 settings 挂一个 dummy provider（components() 因而非 None；不联网）。"""
    s = client.app.state.settings
    s.providers["dummy"] = ProviderConfig(
        base_url="http://127.0.0.1:9/v1", api_key="x", lite="m", standard="m", pro="m")
    s.default_provider = "dummy"


def _events(root):
    return EventLog(root).tail(100)


def test_task_starts_run(client, fx, monkeypatch):
    _enable_provider(client)
    calls = []
    monkeypatch.setattr(client.app.state.runner, "launch",
                        lambda *a, **k: calls.append((a, k)))

    resp = client.post(f"/api/project/{fx}/task",
                       json={"raw": "写前三章", "deliverables": ["outline"]})
    assert resp.status_code == 200
    assert resp.json() == {"status": "planning"}

    root = client.app.state.settings.library_root / fx
    # state.json 落盘
    state = RunState.load(root)
    assert state is not None and state.status == "planning"
    assert state.brief["raw"] == "写前三章"
    assert state.brief["deliverables"] == ["outline"]
    # run_started 事件落盘
    assert any(e["kind"] == "run_started" for e in _events(root))
    # runner.launch 被调（web 显式给出 deliverables → user_specified=True）
    assert len(calls) == 1
    _args, kwargs = calls[0]
    assert kwargs["intake"] == ("写前三章", ["outline"], True)


def test_task_busy_409(client, fx, monkeypatch):
    monkeypatch.setattr(client.app.state.runner, "alive", lambda: True)

    resp = client.post(f"/api/project/{fx}/task",
                       json={"raw": "x", "deliverables": ["outline"]})
    assert resp.status_code == 409
    assert resp.json() == {"error": "busy"}
    # 守卫先行：连 state.json 都不落（不留幻影 run）
    root = client.app.state.settings.library_root / fx
    assert not (root / ".harness" / "state.json").exists()


def test_task_no_provider_409(client, fx):
    resp = client.post(f"/api/project/{fx}/task",
                       json={"raw": "x", "deliverables": ["outline"]})
    assert resp.status_code == 409
    assert "error" in resp.json()
    # 不落幻影 run
    root = client.app.state.settings.library_root / fx
    assert not (root / ".harness" / "state.json").exists()


def test_pause_abort_commands(client, fx, monkeypatch):
    _enable_provider(client)
    monkeypatch.setattr(client.app.state.runner, "launch", lambda *a, **k: None)
    root = client.app.state.settings.library_root / fx
    resp = client.post(f"/api/project/{fx}/task",
                       json={"raw": "x", "deliverables": ["outline"]})
    assert resp.status_code == 200

    # pause：状态翻转 + paused 事件落盘
    resp = client.post(f"/api/project/{fx}/commands/pause")
    assert resp.status_code == 200
    assert resp.json() == {"status": "paused"}
    assert RunState.load(root).status == "paused"
    paused = [e for e in _events(root) if e["kind"] == "paused"]
    assert paused and paused[-1]["payload"]["reason"] == "web pause"

    # abort：状态翻转 + aborted 事件落盘
    resp = client.post(f"/api/project/{fx}/commands/abort")
    assert resp.status_code == 200
    assert resp.json() == {"status": "aborted"}
    assert RunState.load(root).status == "aborted"
    assert any(e["kind"] == "aborted" for e in _events(root))

    # 未知命令名 → 404
    assert client.post(f"/api/project/{fx}/commands/nope").status_code == 404


def test_repl_pause_uses_shared(tmp_path, monkeypatch, capsys):
    """repl 的 ``_cmd_pause`` 委托 ``session.pause_run``（spy），不再自己改状态。"""
    from harness import repl
    from harness.projects import create_project
    from harness.session import RunSession
    from harness.store.project import ProjectStore

    settings = Settings(library_root=tmp_path, providers={}, default_provider="",
                        default_tier="standard", web_verify=True)
    info = create_project(tmp_path, "委托书")
    sess = RunSession(info, ProjectStore(info.path), settings)
    sess.new_run("写开篇", ["outline"])          # planning：非终态，可暂停

    spy = []
    monkeypatch.setattr(repl, "pause_run", lambda s, reason: spy.append((s, reason)))
    repl._cmd_pause(sess)

    assert spy == [(sess, "user /pause")]
    out = capsys.readouterr().out
    assert "已暂停（/resume 续跑，/abort 终止）。" in out

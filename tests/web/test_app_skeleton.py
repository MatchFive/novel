"""Task 4：web 骨架——书架 / state / events 只读 API。"""

import pytest
from fastapi.testclient import TestClient

from harness.config import Settings
from harness.run.events import EventLog


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


def _create(client, title="测试书"):
    resp = client.post("/api/projects", json={"title": title})
    assert resp.status_code == 200
    return resp.json()


def test_projects_empty(tmp_path, client):
    resp = client.get("/api/projects")
    assert resp.status_code == 200
    assert resp.json() == {"projects": []}


def test_create_project(tmp_path, client, settings):
    body = _create(client, "我的书")
    assert body["id"] == "我的书" and body["title"] == "我的书"
    # 落盘
    assert (settings.library_root / "我的书" / "README.md").exists()
    # GET 列表可见
    resp = client.get("/api/projects")
    assert resp.json() == {"projects": [{"id": "我的书", "title": "我的书"}]}


@pytest.mark.parametrize("title", ["", "   \t\n  "])
def test_create_project_blank_title_422(client, settings, title):
    resp = client.post("/api/projects", json={"title": title})
    assert resp.status_code == 422
    # 不落盘：library_root 下无新文件夹
    root = settings.library_root
    assert not root.exists() or not any(root.iterdir())


def test_state_counts(tmp_path, client, settings):
    from harness.run.issues import IssuesStore
    from harness.store.project import ChapterDoc, ProjectStore

    _create(client, "我的书")
    root = settings.library_root / "我的书"
    store = ProjectStore(root)
    store.write_chapter(ChapterDoc(seq=1, meta={"seq": 1, "title": "第一章", "status": "done"}, body="x"))
    store.write_chapter(ChapterDoc(seq=2, meta={"seq": 2, "title": "第二章", "status": "draft"}, body="y"))
    IssuesStore(root).add("test", "t", "d")

    resp = client.get("/api/project/我的书/state")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] is None and body["brief"] is None
    assert body["running"] is False
    counts = body["counts"]
    assert counts["chapters"] == 2
    assert counts["prose"] == 1
    assert counts["issues_pending"] == 1
    assert counts["volumes"] == 0
    assert counts["plots"] == 0
    assert counts["characters_pending"] == 0
    assert counts["memory"] == 0
    assert counts["events_last_seq"] == 0


def test_unknown_project_404(client):
    assert client.get("/api/project/不存在/state").status_code == 404
    assert client.get("/api/project/不存在/events").status_code == 404


def test_events_after_filter(tmp_path, client, settings):
    _create(client, "我的书")
    root = settings.library_root / "我的书"
    log = EventLog(root)
    log.append("run_started", {"n": 1})
    log.append("step_done", {"n": 2})
    log.append("run_finished", {"n": 3})

    resp = client.get("/api/project/我的书/events?after=1")
    assert resp.status_code == 200
    body = resp.json()
    assert [e["seq"] for e in body["events"]] == [2, 3]
    assert body["last_seq"] == 3

    # 不带 after：全量
    assert len(client.get("/api/project/我的书/events").json()["events"]) == 3

"""StaticFiles 托管冒烟（G5）：GET / 200/404、API 不被 html catch-all 吞掉。

dist 不入库，三个分支全部 monkeypatch ``_web_dist`` 到确定性状态，
无论本地是否构建过 web/dist 都能跑。
"""

import pytest
from fastapi.testclient import TestClient

from harness.config import Settings


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
def dist_client(settings, tmp_path, monkeypatch):
    """假 dist：tmp_path 下含 index.html 的目录。"""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>SPA</html>", encoding="utf-8")
    monkeypatch.setattr("harness.web.app._web_dist", lambda: dist)
    from harness.web.app import create_app

    return TestClient(create_app(settings))


def test_root_404_without_dist(settings, monkeypatch):
    """dist 不存在（开发模式 / 未构建）：不挂 StaticFiles，GET / 走 FastAPI 默认 404。"""
    monkeypatch.setattr("harness.web.app._web_dist", lambda: None)
    from harness.web.app import create_app

    client = TestClient(create_app(settings))
    assert client.get("/").status_code == 404


def test_root_200_with_dist(dist_client):
    """dist 存在：html=True → GET / 直接出单页应用的 index.html。"""
    resp = dist_client.get("/")
    assert resp.status_code == 200
    assert "SPA" in resp.text


def test_api_routes_not_swallowed_by_static_mount(dist_client):
    """StaticFiles 挂在 / 之后：/api/* 仍由 API 路由优先匹配，返回 JSON 而非 html。"""
    resp = dist_client.get("/api/projects")
    assert resp.status_code == 200
    assert resp.json() == {"projects": []}
    assert resp.headers["content-type"].startswith("application/json")

"""生成流水线相关设置项：content_rating / chapter_target_words。"""
from __future__ import annotations

import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.database import create_all, engine


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module", autouse=True)
async def setup_db():
    await create_all()
    yield


@pytest.fixture(autouse=True)
async def cleanup_tables():
    yield
    async with engine.begin() as conn:
        await conn.exec_driver_sql("DELETE FROM user_settings;")


@pytest.mark.anyio
async def test_settings_defaults_include_generation_fields():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/api/settings")
        assert r.status_code == 200
        body = r.json()
        assert body["content_rating"] == "standard"
        assert body["chapter_target_words"] == 2500


@pytest.mark.anyio
async def test_update_content_rating_and_target_words():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.put("/api/settings", json={"content_rating": "strict", "chapter_target_words": 1500})
        assert r.status_code == 200
        assert r.json()["content_rating"] == "strict"
        assert r.json()["chapter_target_words"] == 1500


@pytest.mark.anyio
async def test_invalid_rating_rejected():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.put("/api/settings", json={"content_rating": "xxx"})
        assert r.status_code == 422


@pytest.mark.anyio
async def test_target_words_clamped():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.put("/api/settings", json={"chapter_target_words": 100})
        assert r.status_code == 200
        assert r.json()["chapter_target_words"] == 1000

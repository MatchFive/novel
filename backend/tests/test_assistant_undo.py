"""章节自动生成变更的 undo / undoable。"""
from __future__ import annotations

import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.database import create_all, engine, AsyncSessionLocal
from app.models import LongChangeRecord


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
        for table in ("long_change_records", "long_chapters", "projects"):
            await conn.exec_driver_sql(f"DELETE FROM {table};")


async def _make_project_and_chapter(ac) -> tuple[str, str]:
    r = await ac.post("/api/projects", json={"type": "long", "title": "t", "description": ""})
    pid = r.json()["id"]
    r = await ac.post("/api/long/chapters", json={
        "project_id": pid, "title": "第一章", "content": "旧正文", "order": 0,
    })
    cid = r.json()["id"]
    return pid, cid


async def _seed_auto_record(pid: str, cid: str, before: dict):
    async with AsyncSessionLocal() as db:
        db.add(LongChangeRecord(
            project_id=pid, entity_type="chapter", entity_id=cid,
            before=before, after={"content": "新正文"}, status="applied", source="auto",
        ))
        await db.commit()


@pytest.mark.anyio
async def test_undo_restores_previous_content():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        pid, cid = await _make_project_and_chapter(ac)
        await _seed_auto_record(pid, cid, {"title": "第一章", "content": "旧正文", "status": "draft"})
        # 模拟自动应用后的状态
        await ac.put(f"/api/long/chapters/{cid}", json={"content": "新正文", "status": "generated"})

        r = await ac.get(f"/api/assistant/undoable/{cid}")
        assert r.json()["undoable"] is True

        r = await ac.post("/api/assistant/undo", json={
            "project_id": pid, "entity_type": "chapter", "entity_id": cid,
        })
        assert r.status_code == 200
        assert r.json()["ok"] is True

        r = await ac.get(f"/api/long/chapters/detail/{cid}")
        assert r.json()["content"] == "旧正文"

        # 再撤一次：无可撤销记录
        r = await ac.post("/api/assistant/undo", json={
            "project_id": pid, "entity_type": "chapter", "entity_id": cid,
        })
        assert r.json()["ok"] is False
        r = await ac.get(f"/api/assistant/undoable/{cid}")
        assert r.json()["undoable"] is False


@pytest.mark.anyio
async def test_undo_without_record_returns_not_ok():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        pid, cid = await _make_project_and_chapter(ac)
        r = await ac.post("/api/assistant/undo", json={
            "project_id": pid, "entity_type": "chapter", "entity_id": cid,
        })
        assert r.status_code == 200
        assert r.json()["ok"] is False

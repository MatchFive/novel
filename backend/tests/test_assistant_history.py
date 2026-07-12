import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.database import create_all, engine


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module", autouse=True)
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: None)
    await create_all()
    yield


@pytest.mark.anyio
async def test_chat_persists_messages():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # ensure project exists
        r = await ac.post("/api/projects", json={"type": "long", "title": "test", "description": ""})
        assert r.status_code == 200
        pid = r.json()["id"]

        r = await ac.post("/api/assistant/chat", json={"project_id": pid, "message": "hello"})
        assert r.status_code == 200
        body = r.json()
        assert "message_id" in body
        assert body["ok"] is True

        r = await ac.get(f"/api/assistant/session/{pid}/history")
        assert r.status_code == 200
        msgs = r.json()["messages"]
        assert any(m["role"] == "user" for m in msgs)
        assert any(m["role"] == "assistant" for m in msgs)

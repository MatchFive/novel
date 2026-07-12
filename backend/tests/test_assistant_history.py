import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, patch
from app.main import app
from app.database import create_all, engine, AsyncSessionLocal
from app.models import AssistantMessage


@pytest.fixture(autouse=True)
async def cleanup_tables():
    yield
    async with engine.begin() as conn:
        for table in (
            "assistant_messages",
            "assistant_sessions",
            "long_change_records",
            "long_characters",
            "long_outlines",
            "long_foreshadows",
            "long_world_settings",
            "long_plot_nodes",
            "long_chapters",
            "projects",
        ):
            await conn.exec_driver_sql(f"DELETE FROM {table};")

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
@patch("app.api.assistant.LLMClient")
async def test_chat_persists_messages(mock_llm_client):
    mock_llm = mock_llm_client.return_value
    mock_llm.parse_llm_json = AsyncMock(return_value={"intent": "test", "tasks": []})
    mock_llm.chat = AsyncMock(return_value="summary")

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


@pytest.mark.anyio
async def test_stage_change():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post("/api/projects", json={"type": "long", "title": "stage", "description": ""})
        pid = r.json()["id"]
        r = await ac.get(f"/api/assistant/session/{pid}/history")
        session_id = r.json()["session_id"]

        record = {
            "id": "test-record-1",
            "project_id": pid,
            "action": "update",
            "entity_type": "character",
            "entity_id": "char-1",
            "before": {"name": "Alice"},
            "after": {"name": "Alice2"},
            "requires_confirmation": True,
        }
        r = await ac.post("/api/assistant/stage", json={"session_id": session_id, "change_record": record})
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert any(rec["id"] == "test-record-1" for rec in r.json()["staged_changes"])

        # Re-read the session history and verify the staged record persisted.
        r = await ac.get(f"/api/assistant/session/{pid}/history")
        assert r.status_code == 200
        assert any(rec["id"] == "test-record-1" for rec in r.json()["staged_changes"])


@pytest.mark.anyio
async def test_confirm_updates_message_metadata():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post("/api/projects", json={"type": "long", "title": "confirm", "description": ""})
        pid = r.json()["id"]
        r = await ac.get(f"/api/assistant/session/{pid}/history")
        session_id = r.json()["session_id"]

        record = {
            "id": "confirm-record-1",
            "project_id": pid,
            "action": "add",
            "entity_type": "character",
            "entity_id": None,
            "before": None,
            "after": {"name": "Bob"},
            "requires_confirmation": True,
        }
        r = await ac.post("/api/assistant/stage", json={"session_id": session_id, "change_record": record})
        assert r.status_code == 200

        async with AsyncSessionLocal() as db:
            msg = AssistantMessage(session_id=session_id, role="assistant", content="summary", metadata_={})
            db.add(msg)
            await db.commit()

        r = await ac.post("/api/assistant/confirm", json={"session_id": session_id})
        assert r.status_code == 200

        r = await ac.get(f"/api/assistant/session/{pid}/history")
        assert r.status_code == 200
        assistant_messages = [m for m in r.json()["messages"] if m["role"] == "assistant"]
        assert assistant_messages
        latest = assistant_messages[-1]
        assert latest["metadata"].get("status") == "applied"
        assert latest["metadata"].get("applied_count") == 1


@pytest.mark.anyio
async def test_reject_updates_message_metadata():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post("/api/projects", json={"type": "long", "title": "reject", "description": ""})
        pid = r.json()["id"]
        r = await ac.get(f"/api/assistant/session/{pid}/history")
        session_id = r.json()["session_id"]

        record = {
            "id": "reject-record-1",
            "project_id": pid,
            "action": "update",
            "entity_type": "character",
            "entity_id": "char-1",
            "before": {"name": "Alice"},
            "after": {"name": "Alice2"},
            "requires_confirmation": True,
        }
        r = await ac.post("/api/assistant/stage", json={"session_id": session_id, "change_record": record})
        assert r.status_code == 200

        async with AsyncSessionLocal() as db:
            msg = AssistantMessage(session_id=session_id, role="assistant", content="summary", metadata_={})
            db.add(msg)
            await db.commit()

        r = await ac.post("/api/assistant/reject", json={"session_id": session_id})
        assert r.status_code == 200
        assert r.json().get("rejected_count") == 1

        r = await ac.get(f"/api/assistant/session/{pid}/history")
        assert r.status_code == 200
        assistant_messages = [m for m in r.json()["messages"] if m["role"] == "assistant"]
        assert assistant_messages
        latest = assistant_messages[-1]
        assert latest["metadata"].get("status") == "rejected"
        assert latest["metadata"].get("rejected_count") == 1


@pytest.mark.anyio
async def test_confirm_partial_status():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post("/api/projects", json={"type": "long", "title": "partial", "description": ""})
        pid = r.json()["id"]
        r = await ac.get(f"/api/assistant/session/{pid}/history")
        session_id = r.json()["session_id"]

        valid_record = {
            "id": "partial-record-1",
            "project_id": pid,
            "action": "add",
            "entity_type": "character",
            "entity_id": None,
            "before": None,
            "after": {"name": "Carol"},
            "requires_confirmation": True,
        }
        invalid_record = {
            "id": "partial-record-2",
            "project_id": pid,
            "action": "update",
            "entity_type": "character",
            "entity_id": "non-existent-id",
            "before": {"name": "Diana"},
            "after": {"name": "Diana2"},
            "requires_confirmation": True,
        }
        for record in (valid_record, invalid_record):
            r = await ac.post("/api/assistant/stage", json={"session_id": session_id, "change_record": record})
            assert r.status_code == 200

        async with AsyncSessionLocal() as db:
            msg = AssistantMessage(session_id=session_id, role="assistant", content="summary", metadata_={})
            db.add(msg)
            await db.commit()

        r = await ac.post("/api/assistant/confirm", json={"session_id": session_id})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert body.get("errors")

        r = await ac.get(f"/api/assistant/session/{pid}/history")
        assert r.status_code == 200
        assistant_messages = [m for m in r.json()["messages"] if m["role"] == "assistant"]
        assert assistant_messages
        latest = assistant_messages[-1]
        assert latest["metadata"].get("status") == "partial"
        assert latest["metadata"].get("applied_count") == 1
        assert latest["metadata"].get("error_count") == 1


"""Tests for the harness commit node."""
from __future__ import annotations

import pytest

from app.agents.harness.models import HarnessStage, WorkerResult
from app.agents.harness.nodes.commit import commit_state
from app.agents.harness.state import ChangeRecord, HarnessState
from app.database import AsyncSessionLocal, create_all, engine
from app.models import LongChangeRecord, Project
from app import repositories as repo


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
        for table in (
            "long_change_records",
            "long_chapters",
            "projects",
        ):
            await conn.exec_driver_sql(f"DELETE FROM {table};")


async def _make_project(db) -> str:
    p = Project(type="long", title="Commit Test", description="")
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p.id


async def _make_chapter(db, project_id: str) -> str:
    row = await repo.create_chapter(
        db,
        {
            "project_id": project_id,
            "title": "第一章",
            "order": 1,
            "content": "original content",
            "detailed_outline": "original outline",
        },
    )
    return row["id"]


def _make_state(project_id: str, records: list[ChangeRecord]) -> HarnessState:
    return HarnessState(project_id=project_id, session_id="s1", change_records=records)


@pytest.mark.anyio
async def test_commit_auto_applies_chapter_content_update():
    async with AsyncSessionLocal() as db:
        pid = await _make_project(db)
        chid = await _make_chapter(db, pid)

        records = [
            ChangeRecord(
                id="cr_1",
                project_id=pid,
                action="update",
                entity_type="chapter",
                entity_id=chid,
                after={"content": "new content"},
                stage="chapter_text",
            ),
        ]
        state = _make_state(pid, records)
        state.results["chapter_text"] = WorkerResult(
            worker="chapter_text", task_id="t1", notes=["note 1"], stage="chapter_text"
        )

        new_state = await commit_state(state, db, is_global=False)

        assert new_state.stage == HarnessStage.DONE
        assert len(new_state.auto_applied) == 1
        assert new_state.auto_applied[0]["change_id"] == "cr_1"
        assert new_state.auto_applied[0]["fields"] == ["content"]
        assert new_state.auto_applied[0]["notes"] == ["note 1"]
        assert len(new_state.staged_records) == 0

        chapter = await repo.get_chapter(db, chid)
        assert chapter["content"] == "new content"

        audit = await db.execute(
            LongChangeRecord.__table__.select().where(
                LongChangeRecord.entity_id == chid,
                LongChangeRecord.source == "auto",
            )
        )
        audit_rows = audit.all()
        assert len(audit_rows) == 1
        assert audit_rows[0]._mapping["before"]["content"] == "original content"
        assert audit_rows[0]._mapping["after"]["content"] == "new content"


@pytest.mark.anyio
async def test_commit_stages_chapter_status_only_update():
    async with AsyncSessionLocal() as db:
        pid = await _make_project(db)
        chid = await _make_chapter(db, pid)

        records = [
            ChangeRecord(
                id="cr_2",
                project_id=pid,
                action="update",
                entity_type="chapter",
                entity_id=chid,
                after={"status": "completed"},
                stage="chapter_text",
            ),
        ]
        state = _make_state(pid, records)

        new_state = await commit_state(state, db, is_global=False)

        assert new_state.stage == HarnessStage.DONE
        assert len(new_state.auto_applied) == 0
        assert len(new_state.staged_records) == 1
        assert new_state.staged_records[0].id == "cr_2"

        chapter = await repo.get_chapter(db, chid)
        assert chapter["status"] != "completed"


@pytest.mark.anyio
async def test_commit_global_session_stages_all_records():
    records = [
        ChangeRecord(
            id="cr_3",
            project_id="p_global",
            action="update",
            entity_type="chapter",
            entity_id="ch1",
            after={"content": "global content"},
            stage="chapter_text",
        ),
        ChangeRecord(
            id="cr_4",
            project_id="p_global",
            action="add",
            entity_type="character",
            after={"name": "Alice"},
            stage="character",
        ),
    ]
    state = _make_state("p_global", records)

    async with AsyncSessionLocal() as db:
        new_state = await commit_state(state, db, is_global=True)

    assert new_state.stage == HarnessStage.DONE
    assert len(new_state.auto_applied) == 0
    assert len(new_state.staged_records) == 2


@pytest.mark.anyio
async def test_commit_non_chapter_records_are_staged():
    async with AsyncSessionLocal() as db:
        pid = await _make_project(db)
        records = [
            ChangeRecord(
                id="cr_5",
                project_id=pid,
                action="add",
                entity_type="character",
                after={"name": "Bob"},
                stage="character",
            ),
        ]
        state = _make_state(pid, records)

        new_state = await commit_state(state, db, is_global=False)

        assert new_state.stage == HarnessStage.DONE
        assert len(new_state.auto_applied) == 0
        assert len(new_state.staged_records) == 1
        assert new_state.staged_records[0].id == "cr_5"


@pytest.mark.anyio
async def test_commit_auto_apply_failure_downgrades_to_staged():
    async with AsyncSessionLocal() as db:
        pid = await _make_project(db)
        records = [
            ChangeRecord(
                id="cr_6",
                project_id=pid,
                action="update",
                entity_type="chapter",
                entity_id="non-existent-chapter-id",
                after={"content": "new content"},
                stage="chapter_text",
            ),
        ]
        state = _make_state(pid, records)

        new_state = await commit_state(state, db, is_global=False)

        assert new_state.stage == HarnessStage.DONE
        assert len(new_state.auto_applied) == 0
        assert len(new_state.staged_records) == 1
        assert new_state.staged_records[0].id == "cr_6"


@pytest.mark.anyio
async def test_commit_auto_applies_multiple_chapter_fields():
    async with AsyncSessionLocal() as db:
        pid = await _make_project(db)
        chid = await _make_chapter(db, pid)

        records = [
            ChangeRecord(
                id="cr_7",
                project_id=pid,
                action="update",
                entity_type="chapter",
                entity_id=chid,
                after={
                    "content": "new content",
                    "detailed_outline": "new outline",
                    "status": "completed",
                },
                stage="chapter_text",
            ),
        ]
        state = _make_state(pid, records)

        new_state = await commit_state(state, db, is_global=False)

        assert new_state.stage == HarnessStage.DONE
        assert len(new_state.auto_applied) == 1
        assert set(new_state.auto_applied[0]["fields"]) == {"content", "detailed_outline", "status"}
        assert len(new_state.staged_records) == 0

        chapter = await repo.get_chapter(db, chid)
        assert chapter["content"] == "new content"
        assert chapter["detailed_outline"] == "new outline"
        assert chapter["status"] == "completed"

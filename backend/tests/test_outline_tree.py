"""outline 树层级约束与 temp_id 映射测试。"""
from __future__ import annotations

import pytest

from app import repositories as repo
from app.core.errors import AppError
from app.database import AsyncSessionLocal, create_all, engine
from app.models import AssistantSession, Project
from app.services.change_apply import (
    _validate_outline_change,
    apply_change,
    confirm_session,
)


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
            "long_outlines",
            "assistant_sessions",
            "projects",
        ):
            await conn.exec_driver_sql(f"DELETE FROM {table};")


async def _make_project(db) -> str:
    p = Project(type="long", title="t", description="")
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p.id


@pytest.mark.anyio
async def test_broad_cannot_have_parent():
    async with AsyncSessionLocal() as db:
        pid = await _make_project(db)
        with pytest.raises(AppError) as exc:
            await _validate_outline_change(
                db, pid, "add", None, {"type": "broad", "parent_id": "x"}
            )
        assert exc.value.code == "INVALID_HIERARCHY"


@pytest.mark.anyio
async def test_period_requires_broad_parent():
    async with AsyncSessionLocal() as db:
        pid = await _make_project(db)
        with pytest.raises(AppError) as exc:
            await _validate_outline_change(
                db, pid, "add", None, {"type": "period", "parent_id": None}
            )
        assert exc.value.code == "INVALID_HIERARCHY"


@pytest.mark.anyio
async def test_volume_requires_period_parent():
    async with AsyncSessionLocal() as db:
        pid = await _make_project(db)
        broad = await repo.create_outline(
            db, {"project_id": pid, "type": "broad", "title": "总纲"}
        )
        with pytest.raises(AppError) as exc:
            await _validate_outline_change(
                db, pid, "add", None, {"type": "volume", "parent_id": broad["id"]}
            )
        assert exc.value.code == "INVALID_HIERARCHY"


@pytest.mark.anyio
async def test_parent_from_different_project_rejected():
    async with AsyncSessionLocal() as db:
        pid_a = await _make_project(db)
        pid_b = await _make_project(db)
        broad_in_a = await repo.create_outline(
            db, {"project_id": pid_a, "type": "broad", "title": "A 项目总纲"}
        )
        with pytest.raises(AppError) as exc:
            await _validate_outline_change(
                db, pid_b, "add", None, {"type": "period", "parent_id": broad_in_a["id"]}
            )
        assert exc.value.code == "INVALID_HIERARCHY"
        assert "父节点不属于当前项目" in exc.value.message


@pytest.mark.anyio
async def test_period_parent_must_be_broad():
    async with AsyncSessionLocal() as db:
        pid = await _make_project(db)
        period = await repo.create_outline(
            db, {"project_id": pid, "type": "period", "title": "时期"}
        )
        with pytest.raises(AppError) as exc:
            await _validate_outline_change(
                db, pid, "add", None, {"type": "period", "parent_id": period["id"]}
            )
        assert exc.value.code == "INVALID_HIERARCHY"


@pytest.mark.anyio
async def test_cyclic_hierarchy_rejected():
    async with AsyncSessionLocal() as db:
        pid = await _make_project(db)
        broad = await repo.create_outline(
            db, {"project_id": pid, "type": "broad", "title": "总纲"}
        )
        period = await repo.create_outline(
            db,
            {
                "project_id": pid,
                "type": "period",
                "parent_id": broad["id"],
                "title": "时期",
            },
        )
        # 试图把 broad 移动到其后代 period 下，形成循环
        with pytest.raises(AppError) as exc:
            await _validate_outline_change(
                db,
                pid,
                "update",
                broad["id"],
                {"type": "volume", "parent_id": period["id"]},
            )
        assert exc.value.code == "CYCLIC_HIERARCHY"


@pytest.mark.anyio
async def test_delete_node_with_children_fails():
    async with AsyncSessionLocal() as db:
        pid = await _make_project(db)
        broad = await repo.create_outline(
            db, {"project_id": pid, "type": "broad", "title": "总纲"}
        )
        await repo.create_outline(
            db,
            {
                "project_id": pid,
                "type": "period",
                "parent_id": broad["id"],
                "title": "时期",
            },
        )
        with pytest.raises(AppError) as exc:
            await _validate_outline_change(db, pid, "delete", broad["id"], {})
        assert exc.value.code == "HAS_CHILDREN"


@pytest.mark.anyio
async def test_chapter_start_greater_than_end_fails():
    async with AsyncSessionLocal() as db:
        pid = await _make_project(db)
        period = await repo.create_outline(
            db, {"project_id": pid, "type": "period", "title": "时期"}
        )
        with pytest.raises(AppError) as exc:
            await _validate_outline_change(
                db,
                pid,
                "add",
                None,
                {
                    "type": "volume",
                    "parent_id": period["id"],
                    "chapter_start": 5,
                    "chapter_end": 3,
                },
            )
        assert exc.value.code == "INVALID_RANGE"


@pytest.mark.anyio
async def test_non_volume_cannot_set_chapter_range():
    async with AsyncSessionLocal() as db:
        pid = await _make_project(db)
        with pytest.raises(AppError) as exc:
            await _validate_outline_change(
                db,
                pid,
                "add",
                None,
                {"type": "broad", "chapter_start": 1, "chapter_end": 5},
            )
        assert exc.value.code == "INVALID_RANGE"


@pytest.mark.anyio
async def test_volume_can_set_valid_chapter_range():
    async with AsyncSessionLocal() as db:
        pid = await _make_project(db)
        period = await repo.create_outline(
            db, {"project_id": pid, "type": "period", "title": "时期"}
        )
        # 不应抛出异常
        await _validate_outline_change(
            db,
            pid,
            "add",
            None,
            {
                "type": "volume",
                "parent_id": period["id"],
                "chapter_start": 1,
                "chapter_end": 5,
            },
        )


@pytest.mark.anyio
async def test_apply_change_persists_chapter_range():
    async with AsyncSessionLocal() as db:
        pid = await _make_project(db)
        period = await repo.create_outline(
            db, {"project_id": pid, "type": "period", "title": "时期"}
        )
        r = await apply_change(
            db,
            pid,
            {
                "entity_type": "outline",
                "action": "add",
                "after": {
                    "type": "volume",
                    "parent_id": period["id"],
                    "title": "第一卷",
                    "chapter_start": 1,
                    "chapter_end": 10,
                },
            },
        )
        assert r["ok"] is True
        row = await repo.get_outline(db, r["entity_id"])
        assert row.chapter_start == 1
        assert row.chapter_end == 10


@pytest.mark.anyio
async def test_confirm_session_resolves_temp_parent():
    async with AsyncSessionLocal() as db:
        pid = await _make_project(db)
        sess = AssistantSession(
            project_id=pid,
            title="test",
            staged_changes=[
                {
                    "id": "c1",
                    "entity_type": "outline",
                    "action": "add",
                    "temp_id": "temp:broad1",
                    "after": {"type": "broad", "title": "总纲"},
                },
                {
                    "id": "c2",
                    "entity_type": "outline",
                    "action": "add",
                    "temp_id": "temp:period1",
                    "after": {
                        "type": "period",
                        "parent_id": "temp:broad1",
                        "title": "时期",
                    },
                },
                {
                    "id": "c3",
                    "entity_type": "outline",
                    "action": "add",
                    "temp_id": "temp:volume1",
                    "after": {
                        "type": "volume",
                        "parent_id": "temp:period1",
                        "title": "第一卷",
                        "chapter_start": 1,
                        "chapter_end": 3,
                    },
                },
            ],
        )
        db.add(sess)
        await db.commit()
        await db.refresh(sess)

        result = await confirm_session(db, sess.id)
        assert result["ok"] is True
        assert len(result["errors"]) == 0
        assert len(result["applied"]) == 3

        outlines = await repo.list_outlines(db, pid)
        by_title = {o["title"]: o for o in outlines}
        assert by_title["时期"]["parent_id"] == by_title["总纲"]["id"]
        assert by_title["第一卷"]["parent_id"] == by_title["时期"]["id"]


@pytest.mark.anyio
async def test_confirm_session_reports_parent_failed_for_missing_temp():
    async with AsyncSessionLocal() as db:
        pid = await _make_project(db)
        sess = AssistantSession(
            project_id=pid,
            title="test",
            staged_changes=[
                {
                    "id": "c1",
                    "entity_type": "outline",
                    "action": "add",
                    "after": {
                        "type": "period",
                        "parent_id": "temp:missing",
                        "title": "孤儿时期",
                    },
                },
            ],
        )
        db.add(sess)
        await db.commit()
        await db.refresh(sess)

        result = await confirm_session(db, sess.id)
        assert result["ok"] is False
        assert len(result["errors"]) == 1
        assert result["errors"][0]["code"] == "PARENT_FAILED"
        assert len(result["applied"]) == 0

        await db.refresh(sess)
        assert len(sess.staged_changes) == 1

"""测试 change_apply 新增变更动作：partial_update、append、patch。"""
from __future__ import annotations

import pytest

from app.core.errors import AppError
from app.database import AsyncSessionLocal, create_all, engine
from app import repositories as repo
from app.models import Project
from app.services.change_apply import apply_change


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
            "long_characters",
            "long_chapters",
            "projects",
        ):
            try:
                await conn.exec_driver_sql(f"DELETE FROM {table};")
            except Exception:
                pass


async def _make_project(db) -> str:
    p = Project(type="long", title="t", description="")
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p.id


@pytest.mark.anyio
async def test_partial_update_preserves_omitted_fields_and_filters_none():
    async with AsyncSessionLocal() as db:
        pid = await _make_project(db)
        char = await repo.create_character(
            db, {"project_id": pid, "name": "Alice", "traits": "沉稳", "ability": "剑术"}
        )

        r = await apply_change(db, pid, {
            "entity_type": "character",
            "action": "partial_update",
            "entity_id": char["id"],
            "after": {"traits": "冷静", "ability": None, "name": None},
        })
        assert r["ok"] is True

        updated = await repo.get_character(db, char["id"])
        assert updated.traits == "冷静"
        assert updated.ability == "剑术"
        assert updated.name == "Alice"


@pytest.mark.anyio
async def test_append_concatenates_string_fields():
    async with AsyncSessionLocal() as db:
        pid = await _make_project(db)
        ch = await repo.create_chapter(
            db, {"project_id": pid, "order": 0, "title": "起", "content": "开篇 "}
        )

        r = await apply_change(db, pid, {
            "entity_type": "chapter",
            "action": "append",
            "entity_id": ch["id"],
            "after": {"content": "风雨欲来"},
        })
        assert r["ok"] is True

        updated = await repo.get_chapter(db, ch["id"])
        assert updated["content"] == "开篇 风雨欲来"


@pytest.mark.anyio
async def test_patch_replaces_unique_match():
    async with AsyncSessionLocal() as db:
        pid = await _make_project(db)
        ch = await repo.create_chapter(
            db, {"project_id": pid, "order": 0, "title": "起", "content": "the quick brown fox"}
        )

        r = await apply_change(db, pid, {
            "entity_type": "chapter",
            "action": "patch",
            "entity_id": ch["id"],
            "after": {"search": "brown", "replace": "red"},
        })
        assert r["ok"] is True

        updated = await repo.get_chapter(db, ch["id"])
        assert updated["content"] == "the quick red fox"


@pytest.mark.anyio
async def test_patch_not_found_raises_patch_not_found():
    async with AsyncSessionLocal() as db:
        pid = await _make_project(db)
        ch = await repo.create_chapter(
            db, {"project_id": pid, "order": 0, "title": "起", "content": "the quick brown fox"}
        )

        with pytest.raises(AppError) as exc_info:
            await apply_change(db, pid, {
                "entity_type": "chapter",
                "action": "patch",
                "entity_id": ch["id"],
                "after": {"search": "purple", "replace": "red"},
            })
        assert exc_info.value.code == "PATCH_NOT_FOUND"


@pytest.mark.anyio
async def test_patch_ambiguous_raises_patch_ambiguous():
    async with AsyncSessionLocal() as db:
        pid = await _make_project(db)
        ch = await repo.create_chapter(
            db, {"project_id": pid, "order": 0, "title": "起", "content": "foo bar foo bar"}
        )

        with pytest.raises(AppError) as exc_info:
            await apply_change(db, pid, {
                "entity_type": "chapter",
                "action": "patch",
                "entity_id": ch["id"],
                "after": {"search": "foo", "replace": "baz"},
            })
        assert exc_info.value.code == "PATCH_AMBIGUOUS"

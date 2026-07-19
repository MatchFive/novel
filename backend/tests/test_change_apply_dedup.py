"""确认添加去重：同名实体自动合并更新，world 完全相同则跳过。"""
from __future__ import annotations

import pytest

from app.database import create_all, engine, AsyncSessionLocal
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
            "long_world_settings",
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
async def test_add_duplicate_character_merges_into_update():
    async with AsyncSessionLocal() as db:
        pid = await _make_project(db)
        await repo.create_character(db, {"project_id": pid, "name": "张三", "traits": "沉稳"})

        r = await apply_change(db, pid, {
            "entity_type": "character", "action": "add",
            "after": {"name": "张三 ", "ability": "剑术"},
        })

        assert r["ok"] is True
        assert r.get("merged_into")
        chars = await repo.list_characters(db, pid)
        assert len(chars) == 1
        assert chars[0]["ability"] == "剑术"   # 非空字段覆盖
        assert chars[0]["traits"] == "沉稳"    # 未提供的字段保留


@pytest.mark.anyio
async def test_add_character_case_insensitive_merge():
    async with AsyncSessionLocal() as db:
        pid = await _make_project(db)
        await repo.create_character(db, {"project_id": pid, "name": "Alice"})
        r = await apply_change(db, pid, {
            "entity_type": "character", "action": "add",
            "after": {"name": "alice", "traits": "冷静"},
        })
        assert r.get("merged_into")
        assert len(await repo.list_characters(db, pid)) == 1


@pytest.mark.anyio
async def test_add_new_character_not_merged():
    async with AsyncSessionLocal() as db:
        pid = await _make_project(db)
        await repo.create_character(db, {"project_id": pid, "name": "张三"})
        r = await apply_change(db, pid, {
            "entity_type": "character", "action": "add",
            "after": {"name": "李四"},
        })
        assert r["ok"] is True
        assert "merged_into" not in r
        assert len(await repo.list_characters(db, pid)) == 2


@pytest.mark.anyio
async def test_add_world_exact_duplicate_skipped():
    async with AsyncSessionLocal() as db:
        pid = await _make_project(db)
        await repo.create_world(db, {"project_id": pid, "category": "地理", "content": "大陆分为五洲"})
        r = await apply_change(db, pid, {
            "entity_type": "world", "action": "add",
            "after": {"category": "地理", "content": "大陆分为五洲"},
        })
        assert r["ok"] is True
        assert r.get("skipped_duplicate") is True
        assert len(await repo.list_world(db, pid)) == 1


@pytest.mark.anyio
async def test_add_duplicate_with_empty_fields_skipped():
    """after 全为空字段时视为无操作跳过，不覆盖已有数据。"""
    async with AsyncSessionLocal() as db:
        pid = await _make_project(db)
        await repo.create_character(db, {"project_id": pid, "name": "张三", "traits": "沉稳"})
        r = await apply_change(db, pid, {
            "entity_type": "character", "action": "add",
            "after": {"name": "张三", "traits": "", "ability": None},
        })
        assert r.get("skipped_duplicate") is True
        chars = await repo.list_characters(db, pid)
        assert chars[0]["traits"] == "沉稳"

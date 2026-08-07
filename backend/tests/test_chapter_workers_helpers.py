"""章节生成 helper 纯函数测试。"""
from __future__ import annotations

import pytest

from app.agents.harness.workers._chapter_utils import (
    chapter_summaries_chain,
    generation_settings,
    volume_outline_text,
)
from app.database import AsyncSessionLocal, create_all, engine
from app.models import Project, UserSetting


def _ch(order, title, outline="", content=""):
    return {"id": f"id{order}", "order": order, "title": title,
            "detailed_outline": outline, "content": content}


def test_chain_includes_only_previous_chapters():
    chapters = [
        _ch(0, "起", outline="开局设定"),
        _ch(1, "承", outline="主角出山"),
        _ch(2, "转", outline="当前章"),
    ]
    chain = chapter_summaries_chain(chapters[2], chapters)
    assert "开局设定" in chain
    assert "主角出山" in chain
    assert "当前章" not in chain


def test_chain_falls_back_to_content_and_capped():
    chapters = [_ch(i, f"第{i}章", content="x" * 300) for i in range(20)]
    target = _ch(20, "当前", outline="o")
    chapters.append(target)
    chain = chapter_summaries_chain(chapters[20], chapters, limit=500)
    assert len(chain) <= 500
    assert chain != "（无）"


def test_chain_empty_when_first_chapter():
    chapters = [_ch(0, "唯一章", outline="大纲")]
    assert chapter_summaries_chain(chapters[0], chapters) == "（无）"


def test_volume_outline_text_hits_range():
    outlines = [
        {"id": "p1", "type": "period", "title": "开荒期", "content": "概述"},
        {"id": "v1", "type": "volume", "parent_id": "p1", "title": "第1卷", "content": "卷内容", "chapter_start": 1, "chapter_end": 10},
    ]
    assert "卷内容" in volume_outline_text(outlines, 0)
    assert "时期《开荒期》" in volume_outline_text(outlines, 0)


def test_volume_outline_text_misses_range():
    outlines = [
        {"id": "p1", "type": "period", "title": "开荒期", "content": "概述"},
        {"id": "v1", "type": "volume", "parent_id": "p1", "title": "第1卷", "content": "卷内容", "chapter_start": 1, "chapter_end": 10},
    ]
    result = volume_outline_text(outlines, 15)
    assert "未找到本卷大纲" in result
    assert "开荒期" in result


def test_volume_outline_text_no_period_fallback():
    outlines = [
        {"id": "v1", "type": "volume", "title": "第1卷", "content": "卷内容", "chapter_start": 1, "chapter_end": 10},
    ]
    result = volume_outline_text(outlines, 15)
    assert result == "（暂无卷大纲）"


def test_volume_outline_text_skips_volume_without_range():
    outlines = [
        {"id": "p1", "type": "period", "title": "开荒期", "content": "概述"},
        {"id": "v1", "type": "volume", "parent_id": "p1", "title": "第1卷", "content": "卷内容"},
    ]
    result = volume_outline_text(outlines, 0)
    assert "未找到本卷大纲" in result
    assert "开荒期" in result


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def setup_db():
    await create_all()
    yield


@pytest.fixture
async def cleanup_tables(setup_db):
    yield
    async with engine.begin() as conn:
        for table in ("projects", "user_settings"):
            try:
                await conn.exec_driver_sql(f"DELETE FROM {table};")
            except Exception:
                pass


@pytest.mark.anyio
async def test_generation_settings_uses_project_config(setup_db, cleanup_tables):
    async with AsyncSessionLocal() as db:
        s = UserSetting(content_rating="strict", chapter_target_words=1500)
        db.add(s)
        p = Project(
            type="long",
            title="t",
            generation_config={"chapter_target_words": 3000, "content_rating": "loose"},
        )
        db.add(p)
        await db.commit()
        await db.refresh(p)

        target, rating = await generation_settings(db, p.id)
        assert target == 3000
        assert rating == "loose"


@pytest.mark.anyio
async def test_generation_settings_falls_back_to_user_setting(setup_db, cleanup_tables):
    async with AsyncSessionLocal() as db:
        s = UserSetting(content_rating="strict", chapter_target_words=1500)
        db.add(s)
        p = Project(type="long", title="t", generation_config={})
        db.add(p)
        await db.commit()
        await db.refresh(p)

        target, rating = await generation_settings(db, p.id)
        assert target == 1500
        assert rating == "strict"


@pytest.mark.anyio
async def test_generation_settings_falls_back_to_defaults(setup_db, cleanup_tables):
    async with AsyncSessionLocal() as db:
        p = Project(type="long", title="t", generation_config={})
        db.add(p)
        await db.commit()
        await db.refresh(p)

        target, rating = await generation_settings(db, p.id)
        assert target == 2500
        assert rating == "standard"


@pytest.mark.anyio
async def test_generation_settings_without_project_id_uses_user_setting(setup_db, cleanup_tables):
    async with AsyncSessionLocal() as db:
        s = UserSetting(content_rating="loose", chapter_target_words=4000)
        db.add(s)
        await db.commit()

        target, rating = await generation_settings(db)
        assert target == 4000
        assert rating == "loose"

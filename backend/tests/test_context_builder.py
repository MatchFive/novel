"""ContextBuilder 单元测试。"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.agents.harness.context_builder import (
    ContextBuilder,
    _extract_keywords,
    _score_entity,
    build_entities_from_context,
)


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


def test_extract_keywords_filters_stopwords_and_short_tokens():
    text = "我要修改刘修的能力和性格，让他变得更强。"
    result = _extract_keywords(text)
    assert "刘修" in result
    assert "能力" in result
    assert "性格" in result
    assert "的" not in result
    assert "我" not in result


def test_extract_keywords_keeps_english_and_numbers():
    text = "AB计划 300金币"
    result = _extract_keywords(text)
    assert "ab" in result
    assert "300" in result


def test_score_entity_weights_name_higher():
    entity = {"id": "c1", "name": "刘修", "traits": "穿越者", "ability": "剑术"}
    keywords = ["刘修"]
    score = _score_entity(entity, keywords)
    assert score >= 3


def test_build_entities_from_context_maps_plural_keys():
    context = {
        "characters": [{"id": "c1", "name": "刘修"}],
        "outlines": [{"id": "o1", "title": "总纲"}],
        "foreshadows": [{"id": "f1", "title": "伏笔"}],
        "chapters": [{"id": "ch1", "title": "第一章"}],
        "plot": [{"id": "p1", "title": "剧情"}],
        "world": [{"id": "w1", "category": "设定"}],
    }
    entities = build_entities_from_context(context)
    assert entities["character"][0]["name"] == "刘修"
    assert entities["outline"][0]["title"] == "总纲"
    assert entities["foreshadow"][0]["title"] == "伏笔"
    assert entities["chapter"][0]["title"] == "第一章"
    assert entities["plot"][0]["title"] == "剧情"
    assert entities["world"][0]["category"] == "设定"


@pytest.mark.anyio
async def test_build_normalizes_plural_entity_keys():
    """直接传入 context（复数键名）时，ContextBuilder 应能正确识别实体。"""
    db = AsyncMock()
    context = {
        "characters": [{"id": "c1", "name": "奇迹女神（伊维娜）", "traits": "女神", "ability": "神明级", "status": "alive"}],
        "outlines": [],
        "foreshadows": [],
        "chapters": [],
        "plot": [],
        "world": [],
    }
    builder = ContextBuilder(db, entities=context)
    result = await builder.build("完善奇迹女神设定", focus_entity_type="outline")
    assert "相关角色" in result
    assert "奇迹女神（伊维娜）" in result


@pytest.mark.anyio
async def test_build_returns_formatted_context():
    db = AsyncMock()
    llm = AsyncMock()

    builder = ContextBuilder(db, llm)
    with patch.object(
        builder, "_fetch_entities", new=AsyncMock(return_value={
            "character": [
                {"id": "c1", "name": "刘修", "traits": "穿越者", "ability": "剑术", "status": "活着"},
            ],
            "outline": [],
            "plot": [],
            "foreshadow": [],
            "world": [],
            "chapter": [],
        })
    ):
        result = await builder.build("完善刘修")

    assert "相关角色" in result
    assert "刘修" in result
    assert "穿越者" in result


@pytest.mark.anyio
async def test_build_returns_empty_when_no_matching_keywords():
    """当查询与任何实体都不匹配时，相关上下文为空。"""
    db = AsyncMock()
    llm = AsyncMock()

    builder = ContextBuilder(db, llm)
    with patch.object(
        builder, "_fetch_entities", new=AsyncMock(return_value={
            "character": [
                {"id": "c1", "name": "刘修", "traits": "穿越者", "ability": "剑术", "status": "活着"},
            ],
            "outline": [],
            "plot": [],
            "foreshadow": [],
            "world": [],
            "chapter": [],
        })
    ):
        result = await builder.build("生成大纲")

    assert "相关角色" not in result
    assert result == ""


@pytest.mark.anyio
async def test_build_with_focus_entity_returns_related():
    db = AsyncMock()
    llm = AsyncMock()

    builder = ContextBuilder(db, llm)
    with patch.object(
        builder, "_fetch_entities", new=AsyncMock(return_value={
            "character": [
                {"id": "c1", "name": "刘修", "traits": "穿越者", "ability": "剑术", "status": "活着"},
            ],
            "outline": [],
            "plot": [],
            "foreshadow": [],
            "world": [],
            "chapter": [],
        })
    ):
        result = await builder.build("围绕刘修生成大纲", focus_entity_type="character", focus_entity_id="c1")

    assert "相关角色" in result
    assert "c1" in result

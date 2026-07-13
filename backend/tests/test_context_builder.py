"""ContextBuilder 单元测试。"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.agents.harness.context_builder import (
    ContextBuilder,
    _extract_keywords,
    _score_entity,
    _coarse_filter,
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


def test_coarse_filter_returns_top_scored():
    entities = [
        {"id": "c1", "name": "刘修", "traits": "穿越者"},
        {"id": "c2", "name": "张三", "traits": "路人"},
        {"id": "c3", "name": "李四", "traits": "与刘修相关"},
    ]
    keywords = ["刘修"]
    result = _coarse_filter(entities, keywords, top_n=2)
    assert len(result) == 2
    assert result[0]["id"] == "c1"


def test_coarse_filter_fallback_when_no_keywords():
    entities = [
        {"id": "c1", "name": "刘修"},
        {"id": "c2", "name": "张三"},
    ]
    result = _coarse_filter(entities, [], top_n=2)
    assert result == entities[:2]


@pytest.mark.anyio
async def test_build_returns_formatted_context():
    db = AsyncMock()
    llm = AsyncMock()
    llm.chat.return_value = '{"character": ["c1"], "outline": []}'

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
        })
    ):
        result = await builder.build("p1", "完善刘修")

    assert "相关角色" in result
    assert "刘修" in result
    assert "穿越者" in result


@pytest.mark.anyio
async def test_build_fallback_to_coarse_top_when_llm_returns_invalid():
    db = AsyncMock()
    llm = AsyncMock()
    llm.chat.return_value = "not json"

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
        })
    ):
        result = await builder.build("p1", "完善刘修")

    assert "相关角色" in result
    assert "c1" in result


@pytest.mark.anyio
async def test_build_guards_non_list_selection_values():
    db = AsyncMock()
    llm = AsyncMock()
    # LLM returns a string instead of a list for character ids
    llm.chat.return_value = '{"character": "c1", "outline": []}'

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
        })
    ):
        result = await builder.build("p1", "完善刘修")

    assert "相关角色" in result
    assert "c1" in result

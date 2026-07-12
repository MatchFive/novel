"""Worker tool-calling loop 测试。"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.harness.workers import CharacterWorker


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_worker_tool_loop_uses_user_role_for_tool_results():
    """自定义 TOOL_CALL 协议不应生成 role=tool 消息，避免 OpenAI/DeepSeek 400。"""
    llm = AsyncMock()
    llm.chat.side_effect = [
        'TOOL_CALL:{"name":"read_characters","arguments":{"project_id":"p1"}}',
        json.dumps({"changes": []}),
    ]

    with patch("app.agents.harness.worker_base.call_tool", new=AsyncMock(return_value=[{"id": "c1"}])):
        worker = CharacterWorker(db=AsyncMock(), llm=llm, recursive_limit=5)
        await worker.run("设计一个主角", {})

    assert llm.chat.call_count >= 2

    for call in llm.chat.call_args_list:
        messages = call.kwargs.get("messages") or call.args[0]
        for msg in messages:
            assert msg.get("role") != "tool", (
                f"发现 role=tool 的消息，会导致 DeepSeek/OpenAI 返回 400: {msg}"
            )


@pytest.mark.anyio
async def test_worker_parses_final_json_into_changes():
    """LLM 返回的 JSON 建议应被解析为 dict，以便 aggregator 提取 changes。"""
    llm = AsyncMock()
    llm.chat.return_value = json.dumps({
        "changes": [
            {
                "action": "add",
                "entity_id": None,
                "fields": {"name": "刘修", "traits": "穿越者"},
            }
        ]
    })

    with patch("app.agents.harness.worker_base.call_tool", new=AsyncMock()):
        worker = CharacterWorker(db=AsyncMock(), llm=llm, recursive_limit=5)
        result = await worker.run("设计一个主角", {})

    assert result.get("changes") == [
        {
            "action": "add",
            "entity_id": None,
            "fields": {"name": "刘修", "traits": "穿越者"},
        }
    ]
    assert "raw" not in result


def test_parse_final_extracts_json_from_markdown_explanation():
    """_parse_final 应能从说明文字 + 代码块的组合中提取 JSON。"""
    worker = CharacterWorker(db=AsyncMock(), llm=AsyncMock(), recursive_limit=1)
    text = (
        "**角色名称**：刘修\n\n"
        "```json\n"
        '{"changes": [{"action": "add", "entity_id": null, "fields": {"name": "刘修"}}]}\n'
        "```"
    )
    assert worker._parse_final(text) == {
        "changes": [{"action": "add", "entity_id": None, "fields": {"name": "刘修"}}]
    }


def test_parse_final_extracts_json_from_inline_braces():
    """没有代码块时，应尝试截取第一个 {...} 子串。"""
    worker = CharacterWorker(db=AsyncMock(), llm=AsyncMock(), recursive_limit=1)
    text = '这是说明 {"changes": []} 后面还有内容'
    assert worker._parse_final(text) == {"changes": []}


def test_parse_final_handles_plain_json():
    """纯 JSON 字符串应直接解析。"""
    worker = CharacterWorker(db=AsyncMock(), llm=AsyncMock(), recursive_limit=1)
    assert worker._parse_final('{"changes": []}') == {"changes": []}


def test_parse_final_handles_json_array():
    """JSON 数组应包装为 changes。"""
    worker = CharacterWorker(db=AsyncMock(), llm=AsyncMock(), recursive_limit=1)
    assert worker._parse_final('[{"action": "add"}]') == {"changes": [{"action": "add"}]}


def test_parse_final_falls_back_to_raw_for_plain_text():
    """非 JSON 文本应回退为 raw。"""
    worker = CharacterWorker(db=AsyncMock(), llm=AsyncMock(), recursive_limit=1)
    assert worker._parse_final("直接输出一些文本") == {"raw": "直接输出一些文本"}


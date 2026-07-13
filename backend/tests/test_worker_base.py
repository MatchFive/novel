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


@pytest.mark.anyio
async def test_worker_prompt_includes_existing_characters_and_update_rule():
    """CharacterWorker 应在 prompt 中携带现有角色，并明确要求同名时更新而非新增。"""
    llm = AsyncMock()
    llm.chat.return_value = json.dumps({"changes": []})

    worker = CharacterWorker(db=AsyncMock(), llm=llm, recursive_limit=1)
    context = {"characters": [{"id": "c1", "name": "刘修", "traits": "穿越者"}]}
    await worker.run("完善刘修的设定", context)

    first_call = llm.chat.call_args_list[0]
    messages = first_call.kwargs.get("messages") or first_call.args[0]
    system_msg = messages[0]["content"]
    user_msg = messages[-1]["content"]

    assert "刘修" in user_msg
    assert "c1" in user_msg
    assert "action='update'" in system_msg
    assert "不要创建与现有角色同名的重复角色" in system_msg


@pytest.mark.anyio
async def test_worker_uses_context_builder_when_project_id_present():
    """CharacterWorker 在 context 包含 project_id 时应调用 ContextBuilder 并展示相关上下文。"""
    llm = AsyncMock()
    llm.chat.return_value = json.dumps({"changes": []})

    worker = CharacterWorker(db=AsyncMock(), llm=llm, recursive_limit=1)
    with patch("app.agents.harness.workers.ContextBuilder") as MockBuilder:
        MockBuilder.return_value.build = AsyncMock(
            return_value="## 相关角色\n- [c2]\n  name: 刘修\n  traits: 穿越者"
        )
        context = {"project_id": "p1", "characters": [{"id": "c1", "name": "刘修"}]}
        await worker.run("完善刘修的设定", context)

    first_call = llm.chat.call_args_list[0]
    messages = first_call.kwargs.get("messages") or first_call.args[0]
    user_msg = messages[-1]["content"]

    assert "相关角色" in user_msg
    assert "刘修" in user_msg
    MockBuilder.assert_called_once()
    _, kwargs = MockBuilder.call_args
    assert kwargs.get("entities") is context
    MockBuilder.return_value.build.assert_awaited_once_with("完善刘修的设定", "character")


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


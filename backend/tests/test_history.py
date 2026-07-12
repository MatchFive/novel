from unittest.mock import AsyncMock

import pytest

from app.agents.harness.history import (
    append_summary,
    build_history_context,
    build_messages,
    should_summarize,
    summarize_messages,
)
from app.models import AssistantMessage, AssistantSession, UserSetting


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


def test_build_history_context_includes_summaries_and_recent_without_system_input():
    session = AssistantSession(
        id="s1",
        project_id="p1",
        summaries=[{"turn_range": "1-2", "summary": "earlier summary"}],
        message_count=2,
    )
    messages = [
        AssistantMessage(session_id="s1", role="user", content="hi"),
        AssistantMessage(session_id="s1", role="assistant", content="hello"),
    ]
    settings = UserSetting(assistant_max_summaries=5)

    context = build_history_context(session, messages, settings)

    assert len(context) == 3
    assert context[0]["role"] == "user"
    assert "历史摘要" in context[0]["content"]
    assert "earlier summary" in context[0]["content"]
    assert context[1] == {"role": "user", "content": "hi"}
    assert context[2] == {"role": "assistant", "content": "hello"}
    # 不应包含 system 或当前输入
    for msg in context:
        assert msg["role"] != "system"
        assert msg["content"] != "now"


def test_build_messages_includes_summaries_and_recent():
    session = AssistantSession(
        id="s1", project_id="p1", summaries=[{"turn_range": "1-2", "summary": " earlier"}], message_count=2
    )
    messages = [
        AssistantMessage(session_id="s1", role="user", content="hi"),
        AssistantMessage(session_id="s1", role="assistant", content="hello"),
    ]
    settings = UserSetting(assistant_max_summaries=5)
    msgs = build_messages("sys", session, messages, "now", settings)
    assert msgs[0] == {"role": "system", "content": "sys"}
    assert msgs[1]["role"] == "user" and "历史摘要" in msgs[1]["content"]
    assert msgs[2]["role"] == "user" and msgs[2]["content"] == "hi"
    assert msgs[3]["role"] == "assistant"
    assert msgs[-1]["content"] == "now"


def test_should_summarize_at_threshold():
    session = AssistantSession(message_count=40)
    settings = UserSetting(assistant_summary_threshold=20)
    assert should_summarize(session, settings) is True


def test_should_not_summarize_below_threshold():
    session = AssistantSession(message_count=2)
    settings = UserSetting(assistant_summary_threshold=20)
    assert should_summarize(session, settings) is False


@pytest.mark.anyio
async def test_summarize_messages_trims_to_max_length():
    llm = AsyncMock()
    llm.chat = AsyncMock(return_value="x" * 2000)
    messages = [AssistantMessage(session_id="s1", role="user", content="hi")]
    settings = UserSetting(assistant_summary_max_length=300)
    result = await summarize_messages(messages, settings, llm)
    assert len(result) == 300


def test_append_summary_resets_message_count_and_appends_turn_range():
    session = AssistantSession(
        id="s1",
        project_id="p1",
        summaries=[{"turn_range": "1-2", "summary": "first"}],
        message_count=4,
    )
    messages = [
        AssistantMessage(session_id="s1", role="user", content="a"),
        AssistantMessage(session_id="s1", role="assistant", content="b"),
        AssistantMessage(session_id="s1", role="user", content="c"),
        AssistantMessage(session_id="s1", role="assistant", content="d"),
    ]
    settings = UserSetting(assistant_max_summaries=5)
    append_summary(session, messages, "second summary", settings)

    assert session.message_count == 0
    assert len(session.summaries) == 2
    assert session.summaries[-1]["turn_range"] == "3-4"
    assert session.summaries[-1]["summary"] == "second summary"


def test_append_summary_caps_to_max_summaries_keeps_most_recent():
    session = AssistantSession(
        id="s1",
        project_id="p1",
        summaries=[
            {"turn_range": "1-1", "summary": "one"},
            {"turn_range": "2-2", "summary": "two"},
        ],
        message_count=2,
    )
    messages = [
        AssistantMessage(session_id="s1", role="user", content="a"),
        AssistantMessage(session_id="s1", role="assistant", content="b"),
    ]
    settings = UserSetting(assistant_max_summaries=2)
    append_summary(session, messages, "three", settings)

    assert session.message_count == 0
    assert len(session.summaries) == 2
    assert session.summaries[0]["summary"] == "two"
    assert session.summaries[1]["summary"] == "three"
    assert session.summaries[1]["turn_range"] == "3-3"
    assert not any(s["summary"] == "one" for s in session.summaries)


def test_build_history_context_respects_max_summaries():
    session = AssistantSession(
        id="s1",
        summaries=[
            {"turn_range": "1-1", "summary": "one"},
            {"turn_range": "2-2", "summary": "two"},
            {"turn_range": "3-3", "summary": "three"},
        ],
        message_count=0,
    )
    settings = UserSetting(assistant_max_summaries=2)
    context = build_history_context(session, [], settings)
    assert len(context) == 2
    assert "one" in context[0]["content"]
    assert "three" not in context[0]["content"]
    assert "three" not in context[1]["content"]

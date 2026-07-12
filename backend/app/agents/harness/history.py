"""助手多轮历史上下文组装与摘要生成。"""
from __future__ import annotations

from app.models import AssistantMessage, AssistantSession, UserSetting


def _max_summaries(settings: UserSetting) -> int:
    """返回最大保留摘要数，显式设置的 0 是合法值。"""
    return settings.assistant_max_summaries if settings.assistant_max_summaries is not None else 5


def build_history_context(
    session: AssistantSession,
    messages: list[AssistantMessage],
    settings: UserSetting,
) -> list[dict[str, str]]:
    """返回历史摘要 + 最近具体消息（不含 system prompt 与当前输入）。"""
    out: list[dict[str, str]] = []

    max_summaries = _max_summaries(settings)
    if max_summaries == 0:
        summaries = []
    else:
        summaries = (session.summaries or [])[-max_summaries:]
    for i, s in enumerate(summaries):
        out.append({
            "role": "user",
            "content": f"[历史摘要 {i + 1}（{s.get('turn_range', '未知范围')}）]\n{s.get('summary', '')}",
        })

    recent_count = _recent_message_count(session, settings)
    recent = messages[-recent_count:] if recent_count > 0 else []
    for m in recent:
        out.append({"role": m.role, "content": m.content})

    return out


def build_messages(
    system_prompt: str,
    session: AssistantSession,
    messages: list[AssistantMessage],
    user_input: str,
    settings: UserSetting,
) -> list[dict[str, str]]:
    """为 LLM 组装完整 messages：system + 历史上下文 + 当前输入。"""
    return [
        {"role": "system", "content": system_prompt},
        *build_history_context(session, messages, settings),
        {"role": "user", "content": user_input},
    ]


def _recent_message_count(session: AssistantSession, settings: UserSetting) -> int:
    """返回自上次压缩以来应保留的具体消息条数。"""
    # message_count 是 user+assistant 总条数；保留这些条
    return max(0, session.message_count or 0)


def should_summarize(session: AssistantSession, settings: UserSetting) -> bool:
    """是否已累积到需要压缩的阈值。threshold 单位是'轮'，每轮 2 条消息。"""
    threshold = max(1, settings.assistant_summary_threshold or 20)
    return (session.message_count or 0) >= threshold * 2


async def summarize_messages(
    messages: list[AssistantMessage],
    settings: UserSetting,
    llm,
) -> str:
    """调用 LLM 把最近一轮对话压缩成摘要。"""
    lines = []
    for m in messages:
        prefix = "用户" if m.role == "user" else "助手"
        lines.append(f"{prefix}: {m.content}")
    prompt = (
        "请把以下对话总结为一段简洁摘要，保留用户的创作意图、关键指令和已确认的变更。"
        "该摘要仅用于后续对话上下文，不对用户显示。\n\n"
        + "\n".join(lines)
    )
    summary = await llm.chat([{"role": "user", "content": prompt}])
    max_len = max(100, settings.assistant_summary_max_length or 1000)
    return summary[:max_len]


def append_summary(
    session: AssistantSession,
    messages: list[AssistantMessage],
    summary_text: str,
    settings: UserSetting,
) -> None:
    """把新生成的摘要加入 session，并清理超出限制的摘要。"""
    summaries = list(session.summaries or [])
    total_turns = len(messages) // 2
    start_turn = 1
    if summaries:
        # 简单按轮数累加
        last_range = summaries[-1].get("turn_range", "1-1")
        last_end = int(last_range.split("-")[-1])
        start_turn = last_end + 1
    end_turn = start_turn + total_turns - 1
    summaries.append({
        "turn_range": f"{start_turn}-{end_turn}",
        "summary": summary_text,
    })
    max_summaries = _max_summaries(settings)
    if max_summaries == 0:
        session.message_count = 0
        return
    session.summaries = summaries[-max_summaries:]
    session.message_count = 0

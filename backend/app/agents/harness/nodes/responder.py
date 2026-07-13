"""responder：LLM 汇总变更摘要 + 前端预览。"""
from __future__ import annotations

from app.agents.harness.state import ChangeRecord
from app.core.llm_client import LLMClient

RESPONDER_PROMPT = """你是小说创作助手的回答生成器。下面是一组由 Worker 建议的变更记录（尚未落库）。
请用简洁中文向用户说明：将发生哪些改动、涉及哪些实体、建议确认或调整。不要编造未列出的内容。"""

GLOBAL_RESPONDER_PROMPT = """你是小说创作助手。当前没有加载任何项目上下文，请根据用户输入直接进行通用对话或创作建议，
不要生成任何需要落库的变更记录。"""


def render_records(records: list[ChangeRecord]) -> str:
    lines = []
    for r in records:
        verb = {"add": "新增", "update": "更新", "delete": "删除"}.get(r.action, r.action)
        lines.append(f"- [{verb}] {r.entity_type} {r.entity_id or '(新)'}：{list((r.after or {}).keys())}")
    return "\n".join(lines) or "（无变更）"


async def respond(llm: LLMClient, records: list[ChangeRecord], user_input: str = "", history_context: list[dict] | None = None, system_prompt: str | None = None) -> str:
    listing = render_records(records)
    msgs = [{"role": "system", "content": system_prompt or RESPONDER_PROMPT}]
    if history_context:
        msgs.extend(history_context)
    msgs.append({"role": "user", "content": f"用户输入：{user_input}\n变更清单：\n{listing}"})
    try:
        return await llm.chat(msgs)
    except Exception:
        return "已生成以下变更建议，请在确认后应用：\n" + listing

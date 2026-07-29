"""responder：LLM 汇总变更摘要 + 前端预览。"""
from __future__ import annotations

import json

from app.agents.harness.models import HarnessStage
from app.agents.harness.state import ChangeRecord, HarnessState
from app.core.errors import AppError


RESPONDER_PROMPT = """你是小说创作助手的回答生成器。下面是一组由 Worker 建议的变更记录（尚未落库）以及当前项目上下文。
请用简洁中文向用户说明：将发生哪些改动、涉及哪些实体、建议确认或调整。不要编造未列出的内容。
如果用户只是询问项目现有内容（如角色、大纲、设定等），请根据【项目上下文】直接回答，不要生成变更。"""

GLOBAL_RESPONDER_PROMPT = """你是小说创作助手。当前没有加载任何项目上下文，请根据用户输入直接进行通用对话或创作建议，
不要生成任何需要落库的变更记录。"""


def render_records(records: list[ChangeRecord]) -> str:
    lines = []
    for r in records:
        verb = {"add": "新增", "update": "更新", "delete": "删除"}.get(r.action, r.action)
        lines.append(f"- [{verb}] {r.entity_type} {r.entity_id or '(新)'}：{list((r.after or {}).keys())}")
    return "\n".join(lines) or "（无变更）"


def _render_context(context: dict | None) -> str:
    """把项目上下文中的实体列表渲染为 responder 可读的摘要。"""
    if not context:
        return "（无项目上下文）"

    sections = []
    characters = context.get("characters") or []
    if characters:
        sections.append("【角色】")
        for c in characters:
            sections.append(f"- {c.get('name')} (id={c.get('id')})")

    outlines = context.get("outlines") or []
    if outlines:
        sections.append("【大纲】")
        for o in outlines:
            sections.append(f"- {o.get('title')} (id={o.get('id')})")

    worlds = context.get("world") or []
    if worlds:
        sections.append("【世界观】")
        for w in worlds:
            sections.append(f"- {w.get('category')} (id={w.get('id')})")

    plots = context.get("plot") or []
    if plots:
        sections.append("【剧情节点】")
        for p in plots:
            sections.append(f"- {p.get('title')} (id={p.get('id')})")

    foreshadows = context.get("foreshadows") or []
    if foreshadows:
        sections.append("【伏笔】")
        for f in foreshadows:
            sections.append(f"- {f.get('title')} (id={f.get('id')})")

    chapters = context.get("chapters") or []
    if chapters:
        sections.append("【章节】")
        for ch in chapters:
            sections.append(f"- {ch.get('title')} (id={ch.get('id')})")

    return "\n".join(sections) or "（项目上下文为空）"


def _render_worker_results(worker_results: list[dict]) -> str:
    """把 Worker 的原始结果渲染给 responder，便于其理解用户问了什么。"""
    lines = []
    for res in worker_results:
        worker = res.get("worker", "unknown")
        stage = res.get("stage", "")
        changes = res.get("changes") or []
        raw = res.get("raw", "")
        lines.append(f"- Worker: {worker} (stage={stage})")
        if changes:
            lines.append(f"  changes: {json.dumps(changes, ensure_ascii=False)}")
        if raw:
            lines.append(f"  raw: {raw[:500]}")
    return "\n".join(lines) or "（无 Worker 输出）"


async def respond(
    llm,
    records: list[ChangeRecord],
    user_input: str = "",
    history_context: list[dict] | None = None,
    system_prompt: str | None = None,
    context: dict | None = None,
    worker_results: list[dict] | None = None,
) -> str:
    listing = render_records(records)
    context_text = _render_context(context)
    worker_text = _render_worker_results(worker_results or [])
    msgs = [{"role": "system", "content": system_prompt or RESPONDER_PROMPT}]
    if history_context:
        msgs.extend(history_context)
    msgs.append({"role": "user", "content": (
        f"用户输入：{user_input}\n\n"
        f"变更清单：\n{listing}\n\n"
        f"项目上下文：\n{context_text}\n\n"
        f"Worker 原始输出：\n{worker_text}"
    )})
    try:
        return await llm.chat(msgs)
    except AppError:
        # 配置类错误（如缺 API key）上抛，不伪装成生成失败
        raise
    except Exception:
        return "已生成以下变更建议，请在确认后应用：\n" + listing


async def respond_state(state: HarnessState, llm) -> HarnessState:
    worker_results = [
        {
            "worker": r.worker,
            "stage": r.stage,
            "changes": r.changes,
            "notes": r.notes,
        }
        for r in state.results.values()
    ]
    summary = await respond(
        llm,
        state.change_records,
        user_input=state.user_input,
        history_context=None,  # history handled separately
        system_prompt=GLOBAL_RESPONDER_PROMPT if not state.project_id else None,
        context=state.context.entities if state.project_id else None,
        worker_results=worker_results,
    )
    state.summary = summary
    state.stage = HarnessStage.COMMIT
    return state

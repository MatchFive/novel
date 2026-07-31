from __future__ import annotations

import json
import logging
import uuid

from app import repositories as repo
from app.agents.workflows.registry import register_step

logger = logging.getLogger(__name__)


FORESHADOW_AUDIT_PROMPT = """你是小说伏笔审计员。请检查以下未回收伏笔，判断它们是否应在已有章节中回收、标记为 abandoned 或继续 pending。
只输出 JSON：
{
  "report": [
    {"foreshadow_id": "...", "suggestion": "...", "proposed_state": "pending|revealed|abandoned"}
  ],
  "change_records": [
    {"action": "update", "entity_id": "...", "after": {"state": "revealed"}}
  ]
}"""


@register_step
async def load_data(ctx):
    project_id = ctx.project_id
    if not project_id:
        raise ValueError("foreshadow_audit workflow requires project_id")
    foreshadows = await repo.list_foreshadows(ctx.db, project_id)
    chapters = await repo.list_chapters(ctx.db, project_id)
    ctx.outputs["load_data"] = {"foreshadows": foreshadows, "chapters": chapters}
    pending = [f for f in foreshadows if f.get("state") == "pending"]
    return {"pending_count": len(pending)}


@register_step
async def audit(ctx):
    data = ctx.outputs["load_data"]
    pending = [f for f in data["foreshadows"] if f.get("state") == "pending"]
    if not pending:
        return {"report": [], "messages": ["没有待回收伏笔"]}

    llm = await ctx.llm_factory("medium")
    payload = json.dumps(
        {"pending": pending, "chapters": data["chapters"]},
        ensure_ascii=False,
        indent=2,
    )
    messages = [{"role": "user", "content": FORESHADOW_AUDIT_PROMPT + "\n\n" + payload}]
    try:
        raw = await llm.parse_llm_json(messages)
    except Exception as exc:
        return {"report": [], "messages": [f"审计失败：{exc}"]}

    if not isinstance(raw, dict):
        raw = {}
    report = raw.get("report") or []
    for rec in raw.get("change_records") or []:
        if not isinstance(rec, dict):
            continue
        rec.setdefault("id", f"cr_{uuid.uuid4().hex[:12]}")
        rec.setdefault("project_id", ctx.project_id)
        rec.setdefault("action", "update")
        rec.setdefault("entity_type", "foreshadow")
        rec.setdefault("requires_confirmation", True)
        after = rec.setdefault("after", {})
        if after.get("state") not in {"pending", "revealed", "abandoned"}:
            after["state"] = "pending"
        ctx.change_records.append(rec)
    return {"report": report, "messages": [f"审计完成，发现 {len(report)} 条建议"]}

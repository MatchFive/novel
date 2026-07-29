"""Commit node: auto-apply chapter changes and stage remaining records."""
from __future__ import annotations

import logging

from app import repositories as repo
from app.agents.harness.models import HarnessStage
from app.agents.harness.state import ChangeRecord, HarnessState
from app.models import LongChangeRecord
from app.services.change_apply import apply_change

logger = logging.getLogger(__name__)

_CHAPTER_AUTO_FIELDS = {"content", "detailed_outline", "status"}


def _is_chapter_auto_apply(record: ChangeRecord) -> bool:
    keys = set((record.after or {}).keys())
    return (
        record.entity_type == "chapter"
        and record.action == "update"
        and bool(record.entity_id)
        and keys <= _CHAPTER_AUTO_FIELDS
        and bool(keys & {"content", "detailed_outline"})
    )


async def commit_state(state: HarnessState, db, is_global: bool) -> HarnessState:
    notes_by_stage = {
        r.stage: r.notes
        for r in state.results.values()
        if r.notes
    }
    auto_applied: list[dict] = []
    staged_records: list[ChangeRecord] = []

    for r in state.change_records:
        if not is_global and _is_chapter_auto_apply(r):
            try:
                before_row = await repo.get_chapter(db, r.entity_id)
                before = _row_to_dict(before_row)
                await apply_change(db, state.project_id, r.model_dump())
            except Exception:
                logger.exception("自动应用章节变更失败，降级为待确认")
                await db.rollback()
                staged_records.append(r)
                continue
            try:
                db.add(LongChangeRecord(
                    project_id=state.project_id,
                    entity_type="chapter",
                    entity_id=r.entity_id,
                    before=before,
                    after=r.after,
                    status="applied",
                    source="auto",
                ))
                await db.commit()
            except Exception:
                logger.exception("自动应用审计记录写入失败（变更已应用）")
                await db.rollback()
            auto_applied.append({
                "change_id": r.id,
                "entity_id": r.entity_id,
                "entity_type": "chapter",
                "fields": list((r.after or {}).keys()),
                "notes": notes_by_stage.get(r.stage) or [],
            })
        else:
            staged_records.append(r)

    state.auto_applied = auto_applied
    # Note: actual staging to AssistantSession happens in assistant.py to keep node stateless
    state.staged_records = staged_records
    state.stage = HarnessStage.DONE
    return state


def _row_to_dict(row) -> dict | None:
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}

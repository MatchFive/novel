"""aggregator：Worker 结果 -> ChangeRecord[]（稳定 id + 实体类型 + before/after）。"""
from __future__ import annotations

import json
from typing import Any

from app.agents.harness.state import ChangeRecord, make_change


# 实体类型映射（worker 产出 -> 数据表实体）
_WORKER_ENTITY = {
    "character": "character",
    "world": "world",
    "outline": "outline",
    "plot": "plot",
    "foreshadow": "foreshadow",
}


def aggregate(project_id: str, worker_results: list[dict]) -> list[ChangeRecord]:
    records: list[ChangeRecord] = []
    for res in worker_results:
        worker = res.get("worker")
        entity_type = _WORKER_ENTITY.get(worker, worker or "unknown")
        changes = res.get("changes") or []
        if isinstance(changes, str):
            try:
                changes = json.loads(changes)
            except Exception:
                changes = []
        for ch in changes:
            action = ch.get("action", "add")
            fields = ch.get("fields", {})
            entity_id = ch.get("entity_id")
            records.append(make_change(
                project_id=project_id,
                action=action,
                entity_type=entity_type,
                after=fields,
                entity_id=entity_id,
                before=ch.get("before"),
            ))
    return records

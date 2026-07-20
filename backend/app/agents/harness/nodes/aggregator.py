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
    "broad_outline": "outline",
    "plot": "plot",
    "plot_nodes": "plot",
    "foreshadow": "foreshadow",
    "chapter_outline": "chapter",
    "chapter_text": "chapter",
}


def aggregate(project_id: str, worker_results: list[dict]) -> list[ChangeRecord]:
    records: list[ChangeRecord] = []
    for res in worker_results:
        worker = res.get("worker")
        default_entity_type = _WORKER_ENTITY.get(worker, worker or "unknown")
        changes = res.get("changes") or []
        stage = res.get("stage", "")
        if isinstance(changes, str):
            try:
                changes = json.loads(changes)
            except Exception:
                changes = []
        for ch in changes:
            action = ch.get("action", "add")
            fields = ch.get("fields", {})
            entity_id = ch.get("entity_id")
            entity_type = ch.get("entity_type")
            if entity_type is None:
                if worker == "assignment":
                    entity_type = "plot" if "chapter_id" in fields else "chapter"
                else:
                    entity_type = default_entity_type
            records.append(make_change(
                project_id=project_id,
                action=action,
                entity_type=entity_type,
                after=fields,
                entity_id=entity_id,
                before=ch.get("before"),
                stage=stage,
                temp_id=ch.get("temp_id"),
            ))
    return records

"""Aggregator node: WorkerResult -> ChangeRecord[]."""
from __future__ import annotations

import json
from typing import Any

from app.agents.harness.models import HarnessStage, WorkerResult
from app.agents.harness.state import ChangeRecord, HarnessState, make_change


_WORKER_ENTITY = {
    "character": "character",
    "world": "world",
    "outline": "outline",
    "broad_outline": "outline",
    "outline_split": "outline",
    "plot": "plot",
    "plot_nodes": "plot",
    "foreshadow": "foreshadow",
    "chapter_outline": "chapter",
    "chapter_text": "chapter",
    "assignment": "chapter",
}


def aggregate_state(state: HarnessState) -> HarnessState:
    state.change_records = _aggregate_results(state.project_id or "", state.results)
    state.stage = HarnessStage.RESPOND
    return state


def _aggregate_results(project_id: str, results: dict[str, WorkerResult]) -> list[ChangeRecord]:
    records: list[ChangeRecord] = []
    for task_id, res in results.items():
        worker = res.worker
        default_entity_type = _WORKER_ENTITY.get(worker, worker or "unknown")
        changes = res.changes
        stage = res.stage or worker
        if isinstance(changes, str):
            try:
                changes = json.loads(changes)
            except Exception:
                changes = []
        for ch in changes:
            if not isinstance(ch, dict):
                continue
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


def aggregate(project_id: str, worker_results: list[dict]) -> list[ChangeRecord]:
    """Legacy aggregator interface for callers still passing list[dict]."""
    mapped: dict[str, WorkerResult] = {}
    for i, res in enumerate(worker_results):
        worker = res.get("worker", "unknown")
        mapped[str(i)] = WorkerResult.from_raw(
            worker=worker,
            task_id=str(i),
            raw=res,
        )
    return _aggregate_results(project_id, mapped)

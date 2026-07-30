"""Harness runtime data models."""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class Task(BaseModel):
    id: str
    worker: str
    goal: str
    input_artifacts: dict[str, str] = {}
    output_artifacts: list[str] = []
    deps: list[str] = []
    meta: dict[str, Any] = {}


class ExecutionPlan(BaseModel):
    intent: str
    tasks: list[Task] = []
    global_context: dict[str, Any] = {}


class WorkerResult(BaseModel):
    worker: str = ""
    task_id: str = ""
    status: str = "completed"  # completed | error
    summary: str = ""
    changes: list[dict] = []
    artifacts: dict[str, Any] = {}
    notes: list[str] = []
    error: str | None = None
    stage: str = ""

    @classmethod
    def from_raw(cls, worker: str, task_id: str, raw: dict) -> "WorkerResult":
        changes = raw.get("changes") or []
        if isinstance(changes, str):
            import json
            try:
                changes = json.loads(changes)
            except Exception:
                changes = []
        return cls(
            worker=worker,
            task_id=task_id,
            status="error" if raw.get("error") else "completed",
            summary=raw.get("summary", ""),
            changes=changes,
            artifacts=raw.get("artifacts", {}),
            notes=raw.get("notes", []),
            error=raw.get("error"),
            stage=raw.get("stage", worker),
        )


class HarnessContext(BaseModel):
    project_id: str | None = None
    user_input: str = ""
    project_summary: str = ""
    entities: dict[str, list[dict]] = Field(default_factory=dict)
    session_context: dict[str, Any] = Field(default_factory=dict)
    artifacts: dict[str, dict[str, Any]] = Field(default_factory=dict)

    def entity_list(self, entity_type: str) -> list[dict]:
        return self.entities.get(entity_type, [])


class HarnessError(BaseModel):
    stage: str
    message: str
    details: dict[str, Any] = {}


class WorkerMetadata(BaseModel):
    model_config = {"protected_namespaces": ()}

    worker_name: str
    description: str
    system_prompt: str
    tools: list[str]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    model_level: str = "default"
    temperature: float = 0.7
    timeout: float = 60.0
    recursive_limit: int | None = None
    skills: list[str] = Field(default_factory=list)
    rag_skills: list[str] = Field(default_factory=list)


class HarnessStage(str, Enum):
    INIT = "init"
    ANALYZE = "analyze"
    PLAN = "plan"
    EXECUTE = "execute"
    AGGREGATE = "aggregate"
    RESPOND = "respond"
    COMMIT = "commit"
    DONE = "done"
    ERROR = "error"

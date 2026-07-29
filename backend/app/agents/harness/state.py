"""Harness 状态定义与 ChangeRecord reducer。"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class ChangeRecord(BaseModel):
    id: str
    project_id: str
    action: str  # add | update | delete
    entity_type: str  # character | foreshadow | outline | world | plot
    entity_id: Optional[str] = None
    before: Optional[dict] = None
    after: Optional[dict] = None
    temp_id: Optional[str] = None
    requires_confirmation: bool = True
    stage: str = ""


from app.agents.harness.models import (
    ExecutionPlan,
    HarnessContext,
    HarnessError,
    HarnessStage,
    WorkerResult,
)


class HarnessState(BaseModel):
    project_id: str | None = None
    session_id: str = ""
    user_input: str = ""
    stage: HarnessStage = HarnessStage.INIT
    context: HarnessContext = Field(default_factory=HarnessContext)
    plan: ExecutionPlan | None = None
    results: dict[str, WorkerResult] = Field(default_factory=dict)
    change_records: list[ChangeRecord] = Field(default_factory=list)
    staged_records: list[ChangeRecord] = Field(default_factory=list)
    summary: str = ""
    error: HarnessError | None = None
    auto_applied: list[dict] = Field(default_factory=list)

    def add_change(self, cr: ChangeRecord) -> None:
        self.change_records.append(cr)


def make_change(
    project_id: str,
    action: str,
    entity_type: str,
    after: dict,
    entity_id: Optional[str] = None,
    before: Optional[dict] = None,
    stage: Optional[str] = None,
    temp_id: Optional[str] = None,
) -> ChangeRecord:
    import uuid
    return ChangeRecord(
        id=f"cr_{uuid.uuid4().hex[:12]}",
        project_id=project_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before=before,
        after=after,
        stage=stage or "",
        temp_id=temp_id,
    )

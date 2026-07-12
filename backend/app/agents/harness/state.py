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
    requires_confirmation: bool = True


class HarnessState(BaseModel):
    project_id: str
    user_input: str = ""
    context: dict = Field(default_factory=dict)        # 前置取数结果
    execution_plan: dict = Field(default_factory=dict) # supervisor 输出
    worker_results: list = Field(default_factory=list)
    change_records: list[ChangeRecord] = Field(default_factory=list)
    summary: str = ""
    stage: str = "init"  # init | analyze | dispatch | collect | aggregate | respond

    def add_change(self, cr: ChangeRecord) -> None:
        self.change_records.append(cr)


def make_change(
    project_id: str,
    action: str,
    entity_type: str,
    after: dict,
    entity_id: Optional[str] = None,
    before: Optional[dict] = None,
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
    )

from __future__ import annotations

from typing import Any, Awaitable, Callable

from pydantic import BaseModel, ConfigDict, Field


class WorkflowStep(BaseModel):
    name: str
    fn: str
    depends_on: list[str] = Field(default_factory=list)
    condition: str | None = None


class WorkflowDefinition(BaseModel):
    name: str
    description: str
    steps: list[WorkflowStep] = Field(default_factory=list)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)


class WorkflowContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    db: Any
    llm_factory: Callable[[str | None], Awaitable[Any]]
    project_id: str | None = None
    chapter_id: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    change_records: list[dict] = Field(default_factory=list)
    messages: list[str] = Field(default_factory=list)


class WorkflowRunResult(BaseModel):
    workflow_name: str
    status: str  # completed | partial | failed
    outputs: dict[str, Any]
    change_records: list[dict]
    messages: list[str]
    session_id: str | None = None

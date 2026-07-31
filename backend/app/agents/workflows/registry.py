from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Awaitable, Callable

from app.agents.workflows.models import WorkflowContext, WorkflowDefinition

logger = logging.getLogger(__name__)

_STEP_REGISTRY: dict[str, Callable[[WorkflowContext], Awaitable[dict]]] = {}


def register_step(
    fn: Callable[[WorkflowContext], Awaitable[dict]],
) -> Callable[[WorkflowContext], Awaitable[dict]]:
    """Register a step function as '<module_name>.<function_name>'."""
    module_name = fn.__module__.split(".")[-1]
    name = f"{module_name}.{fn.__name__}"
    _STEP_REGISTRY[name] = fn
    return fn


def get_step(fn_path: str) -> Callable[[WorkflowContext], Awaitable[dict]]:
    if fn_path not in _STEP_REGISTRY:
        raise ValueError(f"Unknown workflow step: {fn_path}")
    return _STEP_REGISTRY[fn_path]


def load_workflow_definition(name: str) -> WorkflowDefinition:
    path = Path(__file__).parent / "configs" / f"{name}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    definition = WorkflowDefinition(**raw)
    for step in definition.steps:
        get_step(step.fn)  # Validate early.
    return definition


def list_workflows() -> list[WorkflowDefinition]:
    configs: list[WorkflowDefinition] = []
    config_dir = Path(__file__).parent / "configs"
    for path in sorted(config_dir.glob("*.json")):
        configs.append(load_workflow_definition(path.stem))
    return configs

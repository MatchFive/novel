from __future__ import annotations

import logging
from typing import Any

from app.agents.workflows.models import WorkflowContext, WorkflowDefinition, WorkflowRunResult
from app.agents.workflows.registry import get_step

logger = logging.getLogger(__name__)


def _eval_condition(condition: str, context: WorkflowContext) -> bool:
    """Evaluate a simple workflow condition against inputs/outputs.

    Conditions are expected to be simple boolean expressions referencing
    ``inputs`` and ``outputs`` (e.g. ``outputs.extract.drafts``).
    """
    namespace: dict[str, Any] = {
        "inputs": context.inputs,
        "outputs": context.outputs,
    }
    try:
        # Restrict builtins to avoid arbitrary code execution.
        return bool(eval(condition, {"__builtins__": {}}, namespace))  # noqa: S307
    except Exception as exc:  # pragma: no cover
        logger.warning("Condition %r evaluation failed: %s", condition, exc)
        return False


def _topological_sort(steps: list[Any]) -> list[Any]:
    """Return steps ordered by dependencies."""
    by_name = {step.name: step for step in steps}
    visited: set[str] = set()
    order: list[Any] = []

    def visit(name: str) -> None:
        if name in visited:
            return
        step = by_name[name]
        for dep in step.depends_on:
            visit(dep)
        visited.add(name)
        order.append(step)

    for step in steps:
        visit(step.name)
    return order


async def run_workflow(
    definition: WorkflowDefinition, context: WorkflowContext
) -> WorkflowRunResult:
    """Execute a workflow definition against the provided context."""
    status = "completed"

    for step in _topological_sort(definition.steps):
        if step.condition and not _eval_condition(step.condition, context):
            logger.info("Skipping step %s due to condition %r", step.name, step.condition)
            continue

        fn = get_step(step.fn)
        try:
            result = await fn(context)
        except Exception as exc:  # pragma: no cover
            logger.exception("Workflow step %s failed", step.name)
            status = "failed"
            context.messages.append(f"Step {step.name} failed: {exc}")
            break

        if not isinstance(result, dict):
            result = {"result": result}

        context.outputs[step.name] = result
        context.change_records.extend(result.get("change_records", []))
        context.messages.extend(result.get("messages", []))

    return WorkflowRunResult(
        workflow_name=definition.name,
        status=status,
        outputs=context.outputs,
        change_records=context.change_records,
        messages=context.messages,
        session_id=None,
    )

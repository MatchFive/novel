from __future__ import annotations

import logging

from app.agents.workflows.models import (
    WorkflowContext,
    WorkflowDefinition,
    WorkflowRunResult,
    WorkflowStep,
)
from app.agents.workflows.registry import get_step

logger = logging.getLogger(__name__)


def _topological_sort(steps: list[WorkflowStep]) -> list[WorkflowStep]:
    by_name = {s.name: s for s in steps}
    visited: set[str] = set()
    order: list[WorkflowStep] = []

    def visit(name: str) -> None:
        if name in visited:
            return
        visited.add(name)
        for dep in by_name[name].depends_on:
            visit(dep)
        order.append(by_name[name])

    for step in steps:
        visit(step.name)
    return order


def _eval_condition(condition: str | None, ctx: WorkflowContext) -> bool:
    if not condition:
        return True
    try:
        return bool(
            eval(condition, {"__builtins__": {}}, {"inputs": ctx.inputs, "outputs": ctx.outputs})
        )
    except Exception:
        logger.warning("Workflow condition eval failed: %s", condition)
        return False


async def run_workflow(
    definition: WorkflowDefinition,
    ctx: WorkflowContext,
) -> WorkflowRunResult:
    messages: list[str] = []
    completed: set[str] = set()
    failed: set[str] = set()

    for step in _topological_sort(definition.steps):
        if step.name in completed or step.name in failed:
            continue
        if any(dep in failed for dep in step.depends_on):
            failed.add(step.name)
            messages.append(f"跳过 {step.name}：上游步骤失败")
            continue
        if not _eval_condition(step.condition, ctx):
            messages.append(f"跳过 {step.name}：条件不满足")
            continue

        fn = get_step(step.fn)
        try:
            out = await fn(ctx)
        except Exception as exc:
            logger.exception("Workflow step %s failed", step.name)
            failed.add(step.name)
            messages.append(f"{step.name} 失败：{exc}")
            continue

        ctx.outputs[step.name] = out if isinstance(out, dict) else {"value": out}
        completed.add(step.name)
        if isinstance(out, dict):
            ctx.change_records.extend(out.get("change_records") or [])
            messages.extend(out.get("messages") or [])

    status = "completed"
    if failed:
        status = "failed" if not completed else "partial"

    return WorkflowRunResult(
        workflow_name=definition.name,
        status=status,
        outputs=ctx.outputs,
        change_records=ctx.change_records,
        messages=messages,
    )

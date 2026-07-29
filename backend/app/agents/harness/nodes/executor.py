"""Executor node: run the ExecutionPlan through DagExecutor."""
from __future__ import annotations

from app.agents.harness.dag_executor import DagExecutor
from app.agents.harness.models import HarnessStage
from app.agents.harness.state import HarnessState
from app.agents.harness.worker_manager import WorkerManager


async def executor(
    state: HarnessState,
    db,
    llm_factory,
    recursive_limit: int,
    history_context: list[dict] | None = None,
) -> HarnessState:
    manager = WorkerManager()
    dag = DagExecutor(manager, db, llm_factory, recursive_limit, history_context=history_context)
    state.results = await dag.execute(state.plan, state.context)
    state.stage = HarnessStage.AGGREGATE
    return state

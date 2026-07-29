"""DAG Executor: run tasks in topological order with parallelism."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.agents.harness.models import ExecutionPlan, HarnessContext, Task, WorkerResult
from app.agents.harness.worker_base import WorkerBase
from app.agents.harness.worker_manager import WorkerManager

logger = logging.getLogger(__name__)


class DagExecutor:
    def __init__(
        self,
        manager: WorkerManager,
        db,
        llm_factory,
        recursive_limit: int,
        history_context: list[dict] | None = None,
    ):
        self.manager = manager
        self.db = db
        self.llm_factory = llm_factory
        self.recursive_limit = recursive_limit
        self.history_context = history_context or []

    async def execute(
        self,
        plan: ExecutionPlan,
        context: HarnessContext,
    ) -> dict[str, WorkerResult]:
        results: dict[str, WorkerResult] = {}
        tasks_by_id = {t.id: t for t in plan.tasks}
        remaining = set(tasks_by_id.keys())
        completed: set[str] = set()
        failed: set[str] = set()

        while remaining:
            ready = [
                tid for tid in remaining
                if all(dep in completed for dep in tasks_by_id[tid].deps)
                and not any(dep in failed for dep in tasks_by_id[tid].deps)
            ]
            if not ready:
                # Cyclic dependency or all remaining blocked by failure
                for tid in remaining:
                    task = tasks_by_id[tid]
                    if any(dep in failed for dep in task.deps):
                        results[tid] = WorkerResult(
                            worker=task.worker,
                            task_id=tid,
                            status="skipped",
                            error="上游任务失败",
                        )
                    else:
                        results[tid] = WorkerResult(
                            worker=task.worker,
                            task_id=tid,
                            status="error",
                            error="依赖循环或无法调度",
                        )
                break

            coros = [self._run_task(tasks_by_id[tid], context) for tid in ready]
            batch_results = await asyncio.gather(*coros, return_exceptions=True)

            for tid, res in zip(ready, batch_results):
                remaining.discard(tid)
                if isinstance(res, Exception):
                    logger.exception("Task %s failed", tid)
                    results[tid] = WorkerResult(
                        worker=tasks_by_id[tid].worker,
                        task_id=tid,
                        status="error",
                        error=str(res),
                    )
                    failed.add(tid)
                else:
                    results[tid] = res
                    if res.status == "error":
                        failed.add(tid)
                    else:
                        completed.add(tid)
                        context.artifacts[tid] = res.artifacts

        return results

    async def _run_task(self, task: Task, context: HarnessContext) -> WorkerResult:
        worker_cls = self.manager.get_worker_class(task.worker)
        metadata = self.manager.get_metadata(task.worker)
        llm = await self.llm_factory(metadata.model_level)
        worker = worker_cls(self.db, llm, self.recursive_limit, metadata=metadata, timeout=metadata.timeout)

        # Resolve input artifacts from upstream task results
        input_artifacts: dict[str, Any] = {}
        for key, upstream_id in task.input_artifacts.items():
            input_artifacts[key] = context.artifacts.get(upstream_id, {})

        raw = await worker.run(task, context, history_context=self.history_context)
        result = WorkerResult.from_raw(task.worker, task.id, raw)
        return result

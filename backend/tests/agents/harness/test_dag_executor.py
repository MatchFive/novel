# tests/agents/harness/test_dag_executor.py
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from app.agents.harness.dag_executor import DagExecutor
from app.agents.harness.models import ExecutionPlan, HarnessContext, Task, WorkerResult
from app.agents.harness.worker_manager import WorkerManager


class TestDagExecutor(unittest.IsolatedAsyncioTestCase):
    async def test_sequential_dependency(self):
        manager = MagicMock(spec=WorkerManager)
        manager.get_worker_class.return_value = MagicMock()
        manager.get_metadata.return_value = MagicMock(model_level="default", timeout=60.0, recursive_limit=None)

        async def llm_factory(level):
            return MagicMock()

        worker_instance = MagicMock()
        worker_instance.run = AsyncMock(side_effect=[
            {"summary": "task1", "changes": [], "artifacts": {"x": 1}},
            {"summary": "task2", "changes": [], "artifacts": {}},
        ])
        manager.get_worker_class.return_value = lambda *args, **kwargs: worker_instance

        plan = ExecutionPlan(
            intent="test",
            tasks=[
                Task(id="t1", worker="character", goal="g1"),
                Task(id="t2", worker="outline", goal="g2", deps=["t1"]),
            ],
        )
        context = HarnessContext()
        executor = DagExecutor(manager, MagicMock(), llm_factory, 8)
        results = await executor.execute(plan, context)

        self.assertEqual(results["t1"].status, "completed")
        self.assertEqual(results["t2"].status, "completed")
        self.assertEqual(context.artifacts["t1"], {"x": 1})

    async def test_parallel_independent_tasks(self):
        manager = MagicMock(spec=WorkerManager)
        manager.get_worker_class.return_value = MagicMock()
        manager.get_metadata.return_value = MagicMock(model_level="default", timeout=60.0, recursive_limit=None)

        async def llm_factory(level):
            return MagicMock()

        worker_instance = MagicMock()
        worker_instance.run = AsyncMock(side_effect=[
            {"summary": "a", "changes": []},
            {"summary": "b", "changes": []},
        ])
        manager.get_worker_class.return_value = lambda *args, **kwargs: worker_instance

        plan = ExecutionPlan(
            intent="test",
            tasks=[
                Task(id="a", worker="character", goal="g"),
                Task(id="b", worker="world", goal="g"),
            ],
        )
        context = HarnessContext()
        executor = DagExecutor(manager, MagicMock(), llm_factory, 8)
        results = await executor.execute(plan, context)

        self.assertEqual(results["a"].status, "completed")
        self.assertEqual(results["b"].status, "completed")


if __name__ == "__main__":
    unittest.main()

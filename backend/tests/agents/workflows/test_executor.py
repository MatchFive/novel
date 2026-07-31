import unittest
from unittest.mock import AsyncMock

from app.agents.workflows.executor import run_workflow
from app.agents.workflows.models import WorkflowContext, WorkflowDefinition, WorkflowStep
from app.agents.workflows.registry import _STEP_REGISTRY


class TestWorkflowExecutor(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        async def load(ctx: WorkflowContext):
            return {"value": ctx.inputs.get("x", 0)}

        async def double(ctx: WorkflowContext):
            return {"value": ctx.outputs["load"]["value"] * 2}

        _STEP_REGISTRY["test.load"] = load
        _STEP_REGISTRY["test.double"] = double

    async def test_linear_steps(self):
        definition = WorkflowDefinition(
            name="linear",
            description="",
            steps=[
                WorkflowStep(name="load", fn="test.load"),
                WorkflowStep(name="double", fn="test.double", depends_on=["load"]),
            ],
        )
        ctx = WorkflowContext(
            db=None,
            llm_factory=AsyncMock(),
            inputs={"x": 3},
        )
        result = await run_workflow(definition, ctx)
        self.assertEqual(result.status, "completed")
        self.assertEqual(ctx.outputs["double"]["value"], 6)

    async def test_failure_skips_downstream(self):
        async def fail(ctx: WorkflowContext):
            raise RuntimeError("boom")

        async def downstream(ctx: WorkflowContext):
            return {"value": 1}

        _STEP_REGISTRY["test.fail"] = fail
        _STEP_REGISTRY["test.downstream"] = downstream

        definition = WorkflowDefinition(
            name="failing",
            description="",
            steps=[
                WorkflowStep(name="fail", fn="test.fail"),
                WorkflowStep(name="downstream", fn="test.downstream", depends_on=["fail"]),
            ],
        )
        ctx = WorkflowContext(db=None, llm_factory=AsyncMock())
        result = await run_workflow(definition, ctx)
        self.assertEqual(result.status, "failed")
        self.assertNotIn("downstream", ctx.outputs)


if __name__ == "__main__":
    unittest.main()

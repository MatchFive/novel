import unittest
from unittest.mock import AsyncMock

from app.agents.workflows.executor import run_workflow
from app.agents.workflows.models import WorkflowContext, WorkflowDefinition, WorkflowStep
from app.agents.workflows.registry import _STEP_REGISTRY


class TestWorkflowExecutor(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._registry_backup = dict(_STEP_REGISTRY)

        async def load(ctx: WorkflowContext):
            return {"value": ctx.inputs.get("x", 0)}

        async def double(ctx: WorkflowContext):
            return {"value": ctx.outputs["load"]["value"] * 2}

        _STEP_REGISTRY["test.load"] = load
        _STEP_REGISTRY["test.double"] = double

    async def asyncTearDown(self):
        _STEP_REGISTRY.clear()
        _STEP_REGISTRY.update(self._registry_backup)

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

    async def test_partial_execution(self):
        async def ok(ctx: WorkflowContext):
            return {"value": 1}

        async def fail(ctx: WorkflowContext):
            raise RuntimeError("boom")

        _STEP_REGISTRY["test.ok"] = ok
        _STEP_REGISTRY["test.fail"] = fail

        definition = WorkflowDefinition(
            name="partial",
            description="",
            steps=[
                WorkflowStep(name="ok", fn="test.ok"),
                WorkflowStep(name="fail", fn="test.fail"),
            ],
        )
        ctx = WorkflowContext(db=None, llm_factory=AsyncMock())
        result = await run_workflow(definition, ctx)
        self.assertEqual(result.status, "partial")
        self.assertIn("ok", ctx.outputs)
        self.assertNotIn("fail", ctx.outputs)

    async def test_conditional_step_skipped(self):
        async def produce(ctx: WorkflowContext):
            return {"skip": True}

        async def conditional(ctx: WorkflowContext):
            return {"value": 42}

        _STEP_REGISTRY["test.produce"] = produce
        _STEP_REGISTRY["test.conditional"] = conditional

        definition = WorkflowDefinition(
            name="conditional",
            description="",
            steps=[
                WorkflowStep(name="produce", fn="test.produce"),
                WorkflowStep(
                    name="conditional",
                    fn="test.conditional",
                    depends_on=["produce"],
                    condition="outputs['produce']['skip'] is False",
                ),
            ],
        )
        ctx = WorkflowContext(db=None, llm_factory=AsyncMock())
        result = await run_workflow(definition, ctx)
        self.assertEqual(result.status, "completed")
        self.assertNotIn("conditional", ctx.outputs)
        self.assertTrue(any("跳过 conditional" in m for m in result.messages))

    async def test_builtin_memory_condition_syntax(self):
        """Built-in memory_update condition must use bracket syntax on dict outputs."""
        from app.agents.workflows.executor import _eval_condition
        from app.agents.workflows.registry import load_workflow_definition

        definition = load_workflow_definition("memory_update")
        apply_step = next(s for s in definition.steps if s.name == "apply")
        self.assertTrue(apply_step.condition)

        outputs = {"extract": {"drafts": [{"content": "x"}]}}
        self.assertTrue(
            _eval_condition(apply_step.condition, WorkflowContext(db=None, llm_factory=AsyncMock(), inputs={"auto_apply": True}, outputs=outputs))
        )
        self.assertFalse(
            _eval_condition(apply_step.condition, WorkflowContext(db=None, llm_factory=AsyncMock(), inputs={"auto_apply": False}, outputs=outputs))
        )
        self.assertFalse(
            _eval_condition(apply_step.condition, WorkflowContext(db=None, llm_factory=AsyncMock(), inputs={"auto_apply": True}, outputs={"extract": {"drafts": []}}))
        )


if __name__ == "__main__":
    unittest.main()

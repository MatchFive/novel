import unittest

import app.agents.workflows  # noqa: F401  # registers built-in workflow steps
from app.agents.workflows.models import WorkflowContext
from app.agents.workflows.registry import _STEP_REGISTRY, get_step, list_workflows, load_workflow_definition, register_step


class TestWorkflowRegistry(unittest.TestCase):
    def setUp(self):
        self._registry_backup = dict(_STEP_REGISTRY)

    def tearDown(self):
        _STEP_REGISTRY.clear()
        _STEP_REGISTRY.update(self._registry_backup)

    def test_register_and_get(self):
        async def dummy_step(ctx: WorkflowContext):
            return {"ok": True}

        registered = register_step(dummy_step)
        self.assertIs(registered, dummy_step)
        self.assertIn("test_registry.dummy_step", _STEP_REGISTRY)
        self.assertIs(get_step("test_registry.dummy_step"), dummy_step)

    def test_load_workflow_definition(self):
        definition = load_workflow_definition("memory_update")
        self.assertEqual(definition.name, "memory_update")
        self.assertTrue(len(definition.steps) >= 2)
        step_names = {step.name for step in definition.steps}
        self.assertIn("extract", step_names)
        self.assertIn("build_changes", step_names)

    def test_list_workflows(self):
        workflows = list_workflows()
        names = {w.name for w in workflows}
        expected = {
            "memory_update",
            "chapter_generation",
            "foreshadow_audit",
            "world_consistency",
        }
        self.assertTrue(expected.issubset(names))


if __name__ == "__main__":
    unittest.main()

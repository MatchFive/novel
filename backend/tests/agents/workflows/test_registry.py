import unittest
from unittest.mock import MagicMock, patch

from app.agents.workflows.models import WorkflowContext
from app.agents.workflows.registry import _STEP_REGISTRY, get_step, register_step


class TestWorkflowRegistry(unittest.TestCase):
    def test_register_and_get(self):
        async def dummy_step(ctx: WorkflowContext):
            return {"ok": True}

        registered = register_step(dummy_step)
        self.assertIs(registered, dummy_step)
        self.assertIn("test_registry.dummy_step", _STEP_REGISTRY)
        self.assertIs(get_step("test_registry.dummy_step"), dummy_step)


if __name__ == "__main__":
    unittest.main()

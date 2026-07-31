import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.workflows.memory_update import apply_changes, extract_drafts
from app.agents.workflows.models import WorkflowContext


class TestMemoryUpdateWorkflow(unittest.IsolatedAsyncioTestCase):
    async def test_extract_step(self):
        ctx = WorkflowContext(
            db=MagicMock(),
            llm_factory=AsyncMock(),
            chapter_id="c1",
        )
        with patch(
            "app.agents.workflows.memory_update.extract_memory_drafts",
            new=AsyncMock(return_value={"drafts": [{"id": "d1"}], "grouped_by_character": {}}),
        ):
            out = await extract_drafts(ctx)
        self.assertEqual(out["drafts"][0]["id"], "d1")

    async def test_apply_step_respects_auto_apply(self):
        ctx = WorkflowContext(
            db=MagicMock(),
            llm_factory=AsyncMock(),
            chapter_id="c1",
            inputs={"auto_apply": False},
        )
        out = await apply_changes(ctx)
        self.assertIsNone(out["applied"])


if __name__ == "__main__":
    unittest.main()

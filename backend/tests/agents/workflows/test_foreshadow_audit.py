import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.workflows.foreshadow_audit import load_data
from app.agents.workflows.models import WorkflowContext


class TestForeshadowAuditWorkflow(unittest.IsolatedAsyncioTestCase):
    async def test_load_data(self):
        ctx = WorkflowContext(
            db=MagicMock(),
            llm_factory=AsyncMock(),
            project_id="p1",
        )
        with patch(
            "app.agents.workflows.foreshadow_audit.repo.list_foreshadows",
            new=AsyncMock(return_value=[{"id": "f1", "state": "pending"}]),
        ), patch(
            "app.agents.workflows.foreshadow_audit.repo.list_chapters",
            new=AsyncMock(return_value=[]),
        ):
            out = await load_data(ctx)
        self.assertEqual(out["pending_count"], 1)


if __name__ == "__main__":
    unittest.main()

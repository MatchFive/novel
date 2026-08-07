import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.workflows.chapter_generation import load_context
from app.agents.workflows.models import WorkflowContext


class TestChapterGenerationWorkflow(unittest.IsolatedAsyncioTestCase):
    async def test_load_context(self):
        ctx = WorkflowContext(
            db=AsyncMock(),
            llm_factory=AsyncMock(),
            project_id="p1",
            inputs={"chapter_id": "c1"},
        )
        ctx.db.get = AsyncMock(return_value=None)
        with patch("app.agents.workflows.chapter_generation.repo.get_chapter", new=AsyncMock(return_value={"id": "c1", "order": 0})) as m_chapter, \
             patch("app.agents.workflows.chapter_generation.repo.list_chapters", new=AsyncMock(return_value=[])) as m_chapters, \
             patch("app.agents.workflows.chapter_generation.repo.list_outlines", new=AsyncMock(return_value=[])) as m_outlines, \
             patch("app.agents.workflows.chapter_generation.repo.list_characters", new=AsyncMock(return_value=[])) as m_chars, \
             patch("app.agents.workflows.chapter_generation.repo.list_world", new=AsyncMock(return_value=[])) as m_world, \
             patch("app.agents.workflows.chapter_generation.repo.list_plot", new=AsyncMock(return_value=[])) as m_plot, \
             patch("app.agents.workflows.chapter_generation.repo.list_foreshadows", new=AsyncMock(return_value=[])) as m_foreshadows, \
             patch("app.agents.workflows.chapter_generation.generation_settings", new=AsyncMock(return_value=(2500, "standard"))) as m_settings, \
             patch("app.agents.workflows.chapter_generation.character_memories_for_chapter", new=AsyncMock(return_value={})):
            out = await load_context(ctx)
        self.assertEqual(out["chapter_id"], "c1")
        m_chapter.assert_awaited_once()
        m_settings.assert_awaited_once_with(ctx.db, "p1")


if __name__ == "__main__":
    unittest.main()

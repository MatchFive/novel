import unittest
from unittest.mock import AsyncMock, MagicMock

from app.agents.harness.models import HarnessContext, Task, WorkerMetadata
from app.agents.harness.worker_base import WorkerBase


class TestWorkerBaseSkillInjection(unittest.IsolatedAsyncioTestCase):
    async def test_run_injects_inline_skill(self):
        metadata = WorkerMetadata(
            worker_name="character",
            description="test",
            system_prompt="You are a test worker.",
            tools=[],
            input_schema={},
            output_schema={},
            skills=["character_arc"],
            rag_skills=[],
        )

        db = MagicMock()
        llm = MagicMock()
        worker = WorkerBase(db, llm, 8, metadata=metadata, timeout=60.0)

        captured_prompts = {}

        async def fake_tool_loop(system_prompt, user_prompt, extra_tools=None, history_context=None):
            captured_prompts["system"] = system_prompt
            captured_prompts["user"] = user_prompt
            return {"summary": "ok", "changes": []}

        worker._tool_loop = fake_tool_loop

        task = Task(id="t1", worker="character", goal="设计主角")
        context = HarnessContext()
        result = await worker.run(task, context)

        self.assertIn("【创作方法论参考】", captured_prompts["system"])
        self.assertIn("character_arc", captured_prompts["system"])
        self.assertIn("主角塑造", captured_prompts["system"])
        self.assertEqual(result["summary"], "ok")


if __name__ == "__main__":
    unittest.main()

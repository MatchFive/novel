import unittest
from unittest.mock import AsyncMock

from app.agents.harness.nodes.reflection import reflect


class TestReflection(unittest.IsolatedAsyncioTestCase):
    async def test_reflect_returns_result(self):
        llm = AsyncMock()
        llm.parse_llm_json = AsyncMock(
            return_value={"reflection_text": "用户希望保留现有关系", "rules": ["不要修改角色关系"]}
        )
        result = await reflect(
            user_input="改一下主角性格",
            execution_plan={"intent": "修改主角", "tasks": []},
            original_changes=[{"action": "update", "entity_type": "character", "after": {"traits": "勇敢"}}],
            final_changes=[{"action": "update", "entity_type": "character", "after": {"traits": "勇敢"}}],
            feedback="confirm",
            llm=llm,
        )
        self.assertIn("保留", result.reflection_text)
        self.assertTrue(result.rules)

    async def test_reflect_fallback_on_bad_json(self):
        llm = AsyncMock()
        llm.parse_llm_json = AsyncMock(return_value="not json")
        result = await reflect(
            user_input="test",
            execution_plan=None,
            original_changes=[],
            final_changes=[],
            feedback="reject",
            llm=llm,
        )
        self.assertTrue(result.reflection_text)
        self.assertEqual(result.rules, [])


if __name__ == "__main__":
    unittest.main()

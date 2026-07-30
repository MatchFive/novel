import unittest
from unittest.mock import AsyncMock, MagicMock

from app.api.assistant import _last_plan_turn


class TestLastPlanTurn(unittest.IsolatedAsyncioTestCase):
    async def test_returns_message_with_plan(self):
        db = MagicMock()
        msg_with_plan = MagicMock()
        msg_with_plan.metadata_ = {"execution_plan": {"intent": "test"}}
        msg_without_plan = MagicMock()
        msg_without_plan.metadata_ = {"intent": "test"}
        execute_result = MagicMock()
        execute_result.scalars.return_value.all.return_value = [msg_with_plan, msg_without_plan]
        db.execute = AsyncMock(return_value=execute_result)

        result = await _last_plan_turn(db, "s1")
        self.assertIs(result, msg_with_plan)


if __name__ == "__main__":
    unittest.main()

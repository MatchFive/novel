import unittest
from unittest.mock import AsyncMock, MagicMock

import numpy as np

from app.agents.harness.experience_retrieval import retrieve_project_experiences


class TestExperienceRetrieval(unittest.IsolatedAsyncioTestCase):
    async def test_retrieve_filters_by_threshold(self):
        db = MagicMock()
        row = MagicMock()
        row.reflection_text = "用户不喜欢改关系"
        row.rules = ["不要改关系"]
        row.experience_type = "failure"
        emb = np.array([1.0, 0.0], dtype=np.float32)
        emb = emb / np.linalg.norm(emb)
        row.embedding = emb.tobytes()
        execute_result = MagicMock()
        execute_result.scalars.return_value.all.return_value = [row]
        db.execute = AsyncMock(return_value=execute_result)

        results = await retrieve_project_experiences(
            db, "p1", [1.0, 0.0], top_k=3, threshold=0.7
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["experience_type"], "failure")


if __name__ == "__main__":
    unittest.main()

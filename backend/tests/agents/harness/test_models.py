import unittest

from app.agents.harness.models import ExecutionPlan, HarnessContext, Task, WorkerResult


class TestModels(unittest.TestCase):
    def test_task_defaults(self):
        t = Task(id="t1", worker="character", goal="add hero")
        self.assertEqual(t.deps, [])
        self.assertEqual(t.input_artifacts, {})

    def test_worker_result_from_raw_list(self):
        r = WorkerResult.from_raw("character", "t1", {"changes": [{"action": "add", "fields": {"name": "A"}}]})
        self.assertEqual(r.status, "completed")
        self.assertEqual(len(r.changes), 1)

    def test_harness_context_entities(self):
        ctx = HarnessContext(entities={"characters": [{"id": "1", "name": "A"}]})
        self.assertEqual(ctx.entity_list("characters")[0]["name"], "A")


if __name__ == "__main__":
    unittest.main()

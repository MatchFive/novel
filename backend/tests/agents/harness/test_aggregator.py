import unittest

from app.agents.harness.models import HarnessStage, WorkerResult
from app.agents.harness.nodes.aggregator import (
    aggregate,
    aggregate_state,
    _aggregate_results,
)
from app.agents.harness.state import HarnessState


class TestAggregator(unittest.TestCase):
    def test_aggregate_state_populates_change_records_and_stage(self):
        results = {
            "t1": WorkerResult(
                worker="character",
                task_id="t1",
                changes=[{"action": "add", "fields": {"name": "Alice"}}],
            ),
        }
        state = HarnessState(project_id="p1", results=results)
        new_state = aggregate_state(state)

        self.assertEqual(new_state.stage, HarnessStage.RESPOND)
        self.assertEqual(len(new_state.change_records), 1)
        record = new_state.change_records[0]
        self.assertEqual(record.project_id, "p1")
        self.assertEqual(record.entity_type, "character")
        self.assertEqual(record.action, "add")
        self.assertEqual(record.after, {"name": "Alice"})

    def test_aggregate_results_assignment_chapter_id_in_fields_maps_to_plot(self):
        results = {
            "t1": WorkerResult(
                worker="assignment",
                task_id="t1",
                changes=[{"action": "add", "fields": {"chapter_id": "ch1"}}],
            ),
        }
        records = _aggregate_results("p1", results)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].entity_type, "plot")

    def test_aggregate_results_assignment_without_chapter_id_maps_to_chapter(self):
        results = {
            "t1": WorkerResult(
                worker="assignment",
                task_id="t1",
                changes=[{"action": "add", "fields": {"title": "Intro"}}],
            ),
        }
        records = _aggregate_results("p1", results)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].entity_type, "chapter")

    def test_aggregate_legacy_wrapper_parses_string_changes(self):
        """Regression guard: JSON-string changes must be parsed, not rejected."""
        worker_results = [
            {
                "worker": "character",
                "changes": '[{"action": "add", "fields": {"name": "Bob"}}]',
            }
        ]
        records = aggregate("p1", worker_results)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].entity_type, "character")
        self.assertEqual(records[0].after, {"name": "Bob"})

    def test_aggregate_legacy_wrapper_ignores_invalid_string_changes(self):
        worker_results = [
            {
                "worker": "character",
                "changes": "not valid json",
            }
        ]
        records = aggregate("p1", worker_results)
        self.assertEqual(records, [])

    def test_aggregate_legacy_wrapper_preserves_stage(self):
        worker_results = [
            {
                "worker": "outline",
                "stage": "custom_stage",
                "changes": [{"action": "add", "fields": {"title": "Arc"}}],
            }
        ]
        records = aggregate("p1", worker_results)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].stage, "custom_stage")


if __name__ == "__main__":
    unittest.main()

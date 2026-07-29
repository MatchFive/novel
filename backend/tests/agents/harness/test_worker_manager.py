# tests/agents/harness/test_worker_manager.py
import unittest

from app.agents.harness.worker_manager import WorkerManager


class TestWorkerManager(unittest.TestCase):
    def test_manager_loads_all_workers(self):
        manager = WorkerManager()
        names = manager.available_workers()
        self.assertIn("character", names)
        self.assertIn("outline", names)
        self.assertIn("chapter_text", names)

    def test_metadata_has_required_fields(self):
        manager = WorkerManager()
        for metadata in manager.list_workers():
            self.assertTrue(metadata.worker_name)
            self.assertTrue(metadata.description)
            self.assertTrue(metadata.system_prompt)
            self.assertIsInstance(metadata.tools, list)

    def test_resolve_overriding_class(self):
        manager = WorkerManager()
        cls = manager.get_worker_class("character")
        self.assertEqual(cls.__name__, "CharacterWorker")


if __name__ == "__main__":
    unittest.main()

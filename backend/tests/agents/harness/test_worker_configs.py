import json
import unittest
from pathlib import Path

CONFIG_DIR = Path(__file__).parent.parent.parent.parent / "app" / "agents" / "harness" / "workers" / "configs"


class TestWorkerConfigs(unittest.TestCase):
    def test_all_configs_are_valid_json(self):
        paths = sorted(CONFIG_DIR.glob("*.json"))
        self.assertEqual(len(paths), 11, "expected 11 worker configs")
        for path in paths:
            with self.subTest(config=path.name):
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertIn("worker_name", data)
                self.assertIn("system_prompt", data)
                self.assertIsInstance(data["tools"], list)


if __name__ == "__main__":
    unittest.main()

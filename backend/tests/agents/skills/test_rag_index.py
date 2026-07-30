import unittest

from app.agents.skills.rag.index import _parse_frontmatter


class TestRagIndex(unittest.TestCase):
    def test_parse_frontmatter(self):
        text = "---\nskill_name: wangwenclub_case\ntopic: plot\n---\n\nBody content"
        meta, body = _parse_frontmatter(text)
        self.assertEqual(meta["skill_name"], "wangwenclub_case")
        self.assertEqual(meta["topic"], "plot")
        self.assertEqual(body, "Body content")

    def test_parse_without_frontmatter(self):
        text = "Just body content"
        meta, body = _parse_frontmatter(text)
        self.assertEqual(meta, {})
        self.assertEqual(body, text)

    def test_chunk_files_exist(self):
        from pathlib import Path
        chunks_dir = Path(__file__).parent.parent.parent.parent / "app" / "agents" / "skills" / "rag" / "chunks" / "wangwenclub"
        self.assertTrue(chunks_dir.exists())
        self.assertGreaterEqual(len(list(chunks_dir.glob("*.md"))), 5)


if __name__ == "__main__":
    unittest.main()

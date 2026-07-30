import unittest

from app.agents.skills.models import SkillConfig
from app.agents.skills.skill_manager import SkillManager


class TestSkillManager(unittest.TestCase):
    def test_manager_loads_inline_skills(self):
        manager = SkillManager()
        cfg = manager.get_skill("plot_design")
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg.type, "inline")
        self.assertIn("outline", cfg.triggers)

    def test_manager_loads_rag_skills(self):
        manager = SkillManager()
        cfg = manager.get_skill("wangwenclub_case")
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg.type, "rag")
        self.assertEqual(cfg.top_k, 3)

    def test_get_skills_for_worker_by_trigger(self):
        manager = SkillManager()
        results = manager.get_skills_for_worker("character")
        names = [cfg.skill_name for cfg, _ in results]
        self.assertIn("character_arc", names)

    def test_get_skills_for_worker_by_explicit_list(self):
        manager = SkillManager()
        results = manager.get_skills_for_worker(
            "outline", worker_skills=["plot_design"]
        )
        names = [cfg.skill_name for cfg, _ in results]
        self.assertEqual(names, ["plot_design"])

    def test_get_skills_for_worker_returns_content(self):
        manager = SkillManager()
        results = manager.get_skills_for_worker("character")
        self.assertTrue(results)
        for cfg, content in results:
            self.assertTrue(content.strip())

    def test_priority_ordering(self):
        manager = SkillManager()
        results = manager.get_skills_for_worker("chapter_text")
        priorities = [cfg.priority for cfg, _ in results]
        self.assertEqual(priorities, sorted(priorities))


if __name__ == "__main__":
    unittest.main()

"""M4b 测试：AI 指令执行 / 教程知识库 / 提示词管理（不调用 LLM）。运行：python tests/test_m4b.py"""
from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import load_config  # noqa: E402
from app.db.migrations import seed_defaults  # noqa: E402
from app.db.session import Database  # noqa: E402
from app.services.chapter_service import ChapterService  # noqa: E402
from app.services.character_service import CharacterService  # noqa: E402
from app.services.command_service import CommandService  # noqa: E402
from app.services.handbook_service import HandbookService  # noqa: E402
from app.services.outline_service import OutlineService  # noqa: E402
from app.services.project_service import ProjectService  # noqa: E402
from app.services.prompt_service import PromptService  # noqa: E402
from app.services.world_service import WorldService  # noqa: E402


def main() -> int:
    try:
        cfg = load_config()
        db = Database(Path(tempfile.mkdtemp()) / "m4b.db")
        db.init_schema()
        seed_defaults(db)

        project = ProjectService(db).create(name="m4b", genre="玄幻", logline="故事")
        ws = WorldService(db)
        entry = ws.create_entry(project_id=project.id, category_id=None, title="星火城",
                                content="东方大陆第一城。", importance=4)
        hero = CharacterService(db).create(project_id=project.id, name="林晚",
                                           role_type="protagonist",
                                           profile={"personality": "外冷内热"})
        arc = OutlineService(db).create(project_id=project.id, title="第一卷")
        chs = ChapterService(db)
        ch = chs.create(project_id=project.id, arc_id=arc.id, title="第一章",
                        objective="林晚进城", pov_char_id=hero.id)
        chs.set_beats(ch.id, [{"pov": "林晚", "location": "星火城", "event": "初见城主"}])

        svc = CommandService(db, None)

        # ---- 指令执行：world / character / chapter / beat ----
        plan = {
            "impact_summary": "测试",
            "changes": [
                {"domain": "world", "target": "星火城",
                 "before": "东方大陆第一城", "after": "东方大陆第一雄城，城墙黑曜石"},
                {"domain": "character", "target": "林晚", "field": "profile.personality",
                 "before": "外冷内热", "after": "外冷内热、重情义"},
                {"domain": "chapter", "target": "第一章", "field": "objective",
                 "before": "", "after": "林晚在星火城拜见城主"},
                {"domain": "beat", "target": "第一章", "index": 0,
                 "before": "", "after": "林晚与城主在城楼上密谈"},
            ],
        }
        result = svc.apply_plan(project.id, plan, {0, 1, 2, 3})
        assert result["applied"] == 4, result

        e = ws.get_entry(entry.id)
        assert "黑曜石" in e.content
        c = CharacterService(db).get(hero.id)
        assert c.profile["personality"] == "外冷内热、重情义"
        ch2 = chs.get(ch.id)
        assert "拜见城主" in ch2.objective
        assert ch2.beats_json[0]["event"] == "林晚与城主在城楼上密谈"
        # 跳过不存在的实体
        bad = svc.apply_plan(project.id, {"changes": [
            {"domain": "world", "target": "不存在的地方", "after": "x"}]}, {0})
        assert bad["applied"] == 0 and bad["skipped"]
        print("CommandService apply OK (world/character/chapter/beat + skip-missing)")

        # ---- 教程知识库：导入切片 + 检索 ----
        hb = HandbookService(db)
        doc = hb.import_text("网文正文技巧", "正文",
                             "第一章 节奏控制\n快节奏意味着每章都有事件推进。\n\n"
                             "第二章 对话技巧\n对话要符合人物性格。\n\n"
                             "第三章 环境描写\n环境描写不宜过长。", project.id)
        assert doc.id
        chunks = hb.search_chunks("节奏", category="正文")
        assert chunks and "节奏" in chunks[0].content
        docs = hb.list_docs(project.id)
        assert any("网文正文技巧" in (d.title or "") for d in docs), "导入的教程应在列表中"
        # 内置教程（seed 导入的"创作指南"）也应在
        assert any("创作指南" in (d.title or "") for d in docs), "内置教程应已导入"
        print("Handbook OK (import -> auto-split -> FTS search + builtin tutorial)")

        # ---- 提示词管理：版本 +1 / 恢复默认 ----
        ps = PromptService(db)
        tpl = ps.get("task.draft_continue")
        v0 = tpl.version
        ps.update_template("task.draft_continue", tpl.template + "\n# 自定义补充")
        assert ps.get("task.draft_continue").version == v0 + 1
        restored = ps.restore_default("task.draft_continue")
        assert restored and "# 自定义补充" not in restored
        assert ps.get("task.draft_continue").version == v0 + 2
        print("PromptService OK (versioning + restore default)")

        print("ALL M4b TESTS PASSED")
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

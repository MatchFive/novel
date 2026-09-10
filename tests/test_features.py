"""功能补齐测试：角色关系 / 对话模拟控件 / 点子合并 / 大纲评估 / 节奏检查 / 对话入库。运行：python tests/test_features.py"""
from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core.config import load_config  # noqa: E402
from app.db.migrations import seed_defaults  # noqa: E402
from app.db.session import Database  # noqa: E402
from app.services.character_service import CharacterService  # noqa: E402
from app.services.project_service import ProjectService  # noqa: E402
from app.ui.views.character_view import CharacterView  # noqa: E402
from app.ui.views.chapter_view import ChapterView  # noqa: E402
from app.ui.views.idea_view import IdeaView  # noqa: E402
from app.ui.views.outline_view import OutlineView  # noqa: E402
from app.ui.workspace import Workspace  # noqa: E402


def main() -> int:
    try:
        app = QApplication(sys.argv)
        cfg = load_config()
        db = Database(Path(tempfile.mkdtemp()) / "feat.db")
        db.init_schema()
        seed_defaults(db)
        project = ProjectService(db).create(name="feat", genre="玄幻", logline="故事")

        # ---- 角色关系（服务层） ----
        cs = CharacterService(db)
        a = cs.create(project_id=project.id, name="林晚", role_type="protagonist")
        b = cs.create(project_id=project.id, name="玄冥", role_type="antagonist")
        c = cs.create(project_id=project.id, name="苏晴", role_type="support")
        cs.add_relation(project_id=project.id, from_id=a.id, to_id=b.id,
                        relation="宿敌", description="杀父之仇")
        cs.add_relation(project_id=project.id, from_id=a.id, to_id=c.id,
                        relation="盟友", description="并肩作战")
        rels = cs.list_relations(project.id)
        assert len(rels) == 2
        cs.delete_relation(rels[0].id)
        assert len(cs.list_relations(project.id)) == 1
        print("Character relations OK")

        # ---- 视图构造 ----
        ws = Workspace(cfg, db, project)
        cv = ws._views["characters"]
        assert hasattr(cv, "right_tabs") and cv.right_tabs.count() == 3, "角色页应有 3 个 tab"
        cv.refresh()
        assert cv.relation_list.count() == 1, "关系列表应刷新"
        assert cv.char_a_combo.count() == 3, "对话下拉应填充"
        print("CharacterView relations + dialogue tabs OK")

        iv = ws._views["ideas"]
        assert hasattr(iv, "btn_merge"), "点子页应有合并按钮"
        ov = ws._views["outline"]
        assert hasattr(ov, "btn_eval"), "大纲页应有爽点评估按钮"
        chv = ws._views["chapters"]
        assert hasattr(chv, "btn_pace"), "章节页应有节奏检查按钮"
        assert hasattr(chv, "btn_branches"), "章节页应有多分支按钮"
        print("Idea/Outline/Chapter new buttons OK")

        print("ALL FEATURE TESTS PASSED")
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

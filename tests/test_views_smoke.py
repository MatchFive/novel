"""视图冒烟测试：IdeaView / WorldView CRUD（M1 验收）。运行：python tests/test_views_smoke.py"""
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
from app.services.project_service import ProjectService  # noqa: E402
from app.ui.views.idea_view import IdeaView  # noqa: E402
from app.ui.views.world_view import WorldView  # noqa: E402
from app.ui.workspace import Workspace  # noqa: E402


def main() -> int:
    app = QApplication(sys.argv)
    for run in range(3):
        try:
            cfg = load_config()
            db = Database(Path(tempfile.mkdtemp()) / "g.db")
            db.init_schema()
            seed_defaults(db)
            project = ProjectService(db).create(name="demo", genre="fantasy", logline="logline")
            ws = Workspace(cfg, db, project)

            iv = IdeaView(ws)
            iv.refresh()
            idea = iv.service.create(project_id=project.id, title="t", summary="s")
            iv.refresh()
            iv._current_id = idea.id
            iv.title_edit.setText("u")
            iv.summary_edit.setPlainText("x")
            iv._save()
            saved = iv.service.get(idea.id)
            assert saved.title == "u", f"title={saved.title}"

            wv = WorldView(ws)
            wv.refresh()
            n = wv.cat_list.count()
            # 8 个默认分类 + 视图顶部的"全部"项
            assert n == 9, f"categories={n}"
            # 视图的"新增条目"归属当前选中分类；此处验证 DB 层写入 + 视图刷新不崩溃
            entry = wv.service.create_entry(project_id=project.id, category_id=wv._current_category_id,
                                            title="RuleA", content="c", canonical=True)
            wv._load_entries()
            assert wv.entry_list.count() >= 1, wv.entry_list.count()
            db_entries = wv.service.list_entries(project.id, None)
            assert any(e.id == entry.id for e in db_entries), "entry not persisted"
            print(f"run {run}: OK")
        except Exception:
            traceback.print_exc()
            return 1
    print("3/3 PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

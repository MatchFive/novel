"""M1 视图冒烟测试：角色 / 大纲 / 章节（含细纲、字数统计）。运行：python tests/test_m1_views.py"""
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
from app.ui.views.character_view import CharacterView  # noqa: E402
from app.ui.views.chapter_view import ChapterView  # noqa: E402
from app.ui.views.outline_view import OutlineView  # noqa: E402
from app.ui.workspace import Workspace  # noqa: E402


def main() -> int:
    app = QApplication(sys.argv)
    try:
        cfg = load_config()
        db = Database(Path(tempfile.mkdtemp()) / "m1.db")
        db.init_schema()
        seed_defaults(db)
        project = ProjectService(db).create(name="m1", genre="fantasy", logline="logline")
        ws = Workspace(cfg, db, project)

        # ---- 角色 ----
        cv = CharacterView(ws)
        cv.refresh()
        c = cv.service.create(project_id=project.id, name="林晚", role_type="protagonist",
                              profile={"personality": "外冷内热", "motivation": "寻回记忆"})
        cv.refresh()
        # 列表按重要程度分组：角色条目数 = 数据条数（分组标题不带 data）
        char_items = [cv.char_list.item(i) for i in range(cv.char_list.count())
                      if cv.char_list.item(i).data(0x0100) is not None]
        assert len(char_items) == 1
        cv._current_id = c.id
        cv._save()
        saved = cv.service.get(c.id)
        assert saved.profile["personality"] == "外冷内热"
        print("CharacterView OK")

        # ---- 大纲 ----
        ov = OutlineView(ws)
        ov.refresh()
        arc = ov.service.create(project_id=project.id, title="第一卷",
                                goal="觉醒血脉", conflict="宗门追杀", resolution="逃离宗门")
        ov.refresh()
        assert ov.arc_list.count() == 1
        ov._current_id = arc.id
        ov._save()
        print("OutlineView OK")

        # ---- 章节 ----
        chv = ChapterView(ws)
        chv.refresh()
        ch = chv.service.create(project_id=project.id, arc_id=arc.id, title="第一章",
                                objective="主角觉醒")
        chv.refresh()
        # 章节树：卷节点下应有 1 个章节子节点
        assert chv.table.topLevelItemCount() == 1
        assert chv.table.topLevelItem(0).childCount() == 1
        chv._current_chapter_id = ch.id
        chv.editor.setPlainText("这是一段正文内容，用于测试字数统计。")
        chv._save_draft()
        updated = chv.service.get(ch.id)
        assert updated.word_count == len("这是一段正文内容，用于测试字数统计。"), updated.word_count
        # 细纲节拍
        chv.service.set_beats(ch.id, [{"pov": "林晚", "location": "山谷", "event": "血脉觉醒"}])
        chv._load_beats()
        assert chv.beat_list.count() == 1
        assert chv.service.get_beats(ch.id)[0]["event"] == "血脉觉醒"
        print("ChapterView OK (draft word_count + beats)")

        # ---- 生成配置 ----
        panel = chv.config_panel
        panel.target_words.setValue(4500)
        cur = panel.current()
        assert cur["target_words"] == 4500
        assert cur["quality_mode"] in ("draft", "refined")
        print("GenConfigPanel OK")

        print("ALL M1 VIEW TESTS PASSED")
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""细纲手动编辑测试：双击载入→修改→保存生效 + 场景/钩子等字段可编辑（不调 LLM）。
运行：python tests/test_beats_edit.py"""
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
from app.services.chapter_service import ChapterService  # noqa: E402
from app.services.project_service import ProjectService  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402


def main() -> int:
    try:
        app = QApplication(sys.argv)
        cfg = load_config()
        db = Database(Path(tempfile.mkdtemp()) / "be.db")
        db.init_schema()
        seed_defaults(db)
        p = ProjectService(db).create(name="be", genre="玄幻", logline="l")
        chs = ChapterService(db)
        ch = chs.create(project_id=p.id, title="第一章", objective="目标")
        chs.set_beats(ch.id, [
            {"pov": "刘修", "location": "家中", "event": "查分狂喜"},
            {"pov": "刘修", "location": "卧室", "event": "疲惫入睡"},
        ])
        chs.update(ch.id, outline_json={"scene": "家中", "ending_hook": "意识被抽离"})

        w = MainWindow(cfg, db)
        w.show()
        w._load_project(p.id)
        view = w.workspace._views["chapters"]
        view._current_chapter_id = ch.id
        view._load_beats()

        # 加载后：列表带序号前缀、字段区有内容
        assert view.beat_list.count() == 2
        assert view.beat_list.item(0).text().startswith("1. ")
        assert "场景：家中" in view.outline_edit.toPlainText()
        assert "结尾钩子：意识被抽离" in view.outline_edit.toPlainText()

        # ---- 双击第二条 → 修改 → 保存：必须写回（本次 bug 的回归） ----
        item1 = view.beat_list.item(1)
        view._edit_beat(item1)
        view.beat_edit.setPlainText("2. 刘修｜卧室｜发现身边躺了个大姐姐")
        view._save_beats()
        beats = chs.get_beats(ch.id)
        assert beats[1]["event"] == "发现身边躺了个大姐姐", beats
        assert beats[1]["pov"] == "刘修", beats  # 序号前缀不能污染 pov
        print("edit-beat-save OK")

        # ---- 全新内容（未双击载入）→ 保存即新增 ----
        view._editing_row = None
        view.beat_edit.setPlainText("刘修｜房间｜女神像开口说话")
        view._save_beats()
        beats = chs.get_beats(ch.id)
        assert len(beats) == 3 and beats[2]["event"] == "女神像开口说话", beats
        print("append-new-beat OK")

        # ---- 字段区编辑：场景/钩子/出场人物解析入库 ----
        view.outline_edit.setPlainText(
            "场景：异世界·贵族卧室\n"
            "出场：刘修（主角）；伊维娜（奇迹女神）\n"
            "对话要点：你捡到本神了，凡人。\n"
            "爽点：高考超常发挥\n"
            "结尾钩子：女神睁眼")
        view.beat_edit.clear()
        view._save_beats()
        ch2 = chs.get(ch.id)
        assert ch2.outline_json["scene"] == "异世界·贵族卧室"
        assert ch2.outline_json["ending_hook"] == "女神睁眼"
        assert ch2.outline_json["characters"][1] == {"name": "伊维娜", "role": "奇迹女神"}
        assert ch2.outline_json["dialogue_hooks"] == ["你捡到本神了，凡人。"]
        print("outline fields edit OK")

        # ---- 状态自动流转：写入正文后 empty → draft ----
        chs.update(ch.id, content="正文内容……")
        assert chs.get(ch.id).content_status == "draft", "有正文后状态应自动变 draft"
        # 显式指定状态时不被覆盖
        chs.update(ch.id, content="更多正文", content_status="polished")
        assert chs.get(ch.id).content_status == "polished"
        print("content_status auto-transition OK")

        print("ALL BEATS EDIT TESTS PASSED")
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""计划卡片渲染测试：变更项含显式 null（after: null）时卡片必须正常弹出（不静默崩溃）。
运行：python tests/test_plan_card.py"""
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
from app.ui.main_window import MainWindow  # noqa: E402


def main() -> int:
    try:
        app = QApplication(sys.argv)
        cfg = load_config()
        db = Database(Path(tempfile.mkdtemp()) / "pc.db")
        db.init_schema()
        seed_defaults(db)
        project = ProjectService(db).create(name="pc", genre="玄幻", logline="l")

        w = MainWindow(cfg, db)
        w.show()
        w._load_project(project.id)
        panel = w.chat_panel

        # 模拟 deepseek 风格输出：显式 null 字段（历史上导致 None[:40] 静默崩卡片）
        plan = {
            "impact_summary": "测试计划",
            "changes": [
                {"domain": "chapter", "action": "add", "target": "第一章",
                 "field": None, "before": None, "after": None,
                 "beats": [{"event": "查分"}], "note": None},
                {"domain": "world", "action": "update", "target": "设定A",
                 "before": None, "after": "新内容", "note": None},
                {"domain": "chapter", "action": "delete", "target": "第二章",
                 "after": None, "note": None},
            ],
        }
        panel._on_plan_ready(plan)  # 不应抛异常
        assert panel.plan_frame.isVisible(), "计划卡片应弹出"
        assert panel.plan_list.count() == 3
        text0 = panel.plan_list.item(0).text()
        assert "第一章" in text0 and "1 个节拍" in text0, text0
        print("plan card with null fields OK")

        # 空计划：提示而非崩溃
        panel._on_plan_ready({"changes": []})
        print("empty plan OK")

        print("ALL PLAN CARD TESTS PASSED")
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

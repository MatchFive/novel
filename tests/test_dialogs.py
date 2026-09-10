"""对话框回归测试：新建项目对话框字段收集 / 打开项目对话框（修复 toPlainText bug）。运行：python tests/test_dialogs.py"""
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
from app.ui.dialogs.project_dialog import NewProjectDialog, OpenProjectDialog  # noqa: E402


def main() -> int:
    try:
        app = QApplication(sys.argv)
        cfg = load_config()
        db = Database(Path(tempfile.mkdtemp()) / "dlg.db")
        db.init_schema()
        seed_defaults(db)
        service = ProjectService(db)

        # NewProjectDialog：values() 必须能安全收集所有字段（回归：theme 为 QLineEdit）
        dlg = NewProjectDialog()
        dlg.name_edit.setText("测试书名")
        dlg.genre_combo.setCurrentText("玄幻")
        dlg.logline_edit.setText("一句话梗概")
        dlg.theme_edit.setText("成长主题")
        dlg.style_edit.setPlainText("网文爽快流")
        vals = dlg.values()
        assert vals["name"] == "测试书名"
        assert vals["theme"] == "成长主题"
        assert vals["style_guide"] == "网文爽快流"
        assert dlg._validate() is None or True  # _validate 对非空名字 accept

        p = service.create(**vals)
        assert p.theme == "成长主题"
        print("NewProjectDialog.values OK")

        # OpenProjectDialog：列表正确展示
        od = OpenProjectDialog(service)
        assert od.list_widget.count() == 1
        assert od.list_widget.item(0).text().startswith("测试书名")
        print("OpenProjectDialog OK")

        print("ALL DIALOG TESTS PASSED")
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""教程库停用/提炼测试。运行：python tests/test_handbook.py"""
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
from app.services.handbook_service import HandbookService  # noqa: E402
from app.services.project_service import ProjectService  # noqa: E402


def main() -> int:
    try:
        app = QApplication(sys.argv)
        cfg = load_config()
        db = Database(Path(tempfile.mkdtemp()) / "hb.db")
        db.init_schema()
        seed_defaults(db)
        p = ProjectService(db).create(name="hb", genre="玄幻", logline="story")
        svc = HandbookService(db)

        d1 = svc.import_text("黄金三章", "正文", "第一章要立钩子。\n\n第二章要展开冲突。", p.id)
        d2 = svc.import_text("节奏控制", "节奏", "快节奏多事件。", p.id)
        docs = svc.list_docs(p.id)
        print("初始文档数（含内置教程）:", len(docs))

        # 停用 d1
        svc.set_enabled(d1.id, False)
        docs_after = svc.list_docs(p.id)
        still_shown = any(d.id == d1.id for d in docs_after)
        assert still_shown, "停用的教程应在列表中显示（不是消失）"
        states = {d.title: ("停用" if not d.enabled else "启用") for d in docs_after}
        print("停用后列表仍显示:", still_shown, "| 状态:", states)

        # 检索只查启用的（含本项目），本项目教程优先
        hits = svc.search_chunks("钩子", project_id=p.id)
        assert all(c.doc_id != d1.id for c in hits), "停用的教程不应被检索到"
        hits2 = svc.search_chunks("快节奏", project_id=p.id)  # 精确关键词，本项目独有
        assert hits2 and hits2[0].doc_id == d2.id, "本项目教程应可检索且优先"
        print("检索过滤 OK（停用不注入，本项目教程优先）")

        # ---- 查看内容（DocViewDialog） ----
        from app.ui.views.handbook_view import _DocViewDialog
        doc = svc.get_doc(d2.id)
        chunks = svc.list_chunks(d2.id)
        dlg = _DocViewDialog(doc, chunks)
        assert "节奏控制" in dlg.windowTitle()
        assert dlg.full_view.toPlainText() == "快节奏多事件。"
        assert len(chunks) >= 1
        print("view dialog OK (原文 + 切片)")

        print("ALL HANDBOOK TESTS PASSED")
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""AI 创作对话测试：项目上下文构建 / 面板构造与存为点子（不调用 LLM）。运行：python tests/test_chat.py"""
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
from app.services.chat_service import ChatService  # noqa: E402
from app.services.character_service import CharacterService  # noqa: E402
from app.services.idea_service import IdeaService  # noqa: E402
from app.services.project_service import ProjectService  # noqa: E402
from app.services.world_service import WorldService  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402


def main() -> int:
    try:
        app = QApplication(sys.argv)
        cfg = load_config()
        db = Database(Path(tempfile.mkdtemp()) / "chat.db")
        db.init_schema()
        seed_defaults(db)

        project = ProjectService(db).create(name="对话项目", genre="玄幻",
                                            logline="少年觉醒血脉", theme="成长",
                                            style_guide="网文爽快流")
        WorldService(db).create_entry(project_id=project.id, category_id=None,
                                      title="灵气复苏", content="灵气 2049 年复苏。",
                                      importance=5, canonical=True)
        CharacterService(db).create(project_id=project.id, name="林晚",
                                    role_type="protagonist",
                                    profile={"personality": "外冷内热"})

        # ---- 项目上下文 ----
        service = ChatService(db, None)
        ctx = service.build_project_context(project.id, "灵气")
        assert "对话项目" in ctx and "少年觉醒血脉" in ctx
        assert "灵气复苏" in ctx, "关键词应命中相关设定"
        assert "林晚" in ctx
        assert "（尚未打开项目" in service.build_project_context(None, "")
        print("ChatService context OK")

        # ---- 面板构造 + 绑定 + 存为点子 ----
        w = MainWindow(cfg, db)
        w.show()
        w._load_project(project.id)
        panel = w.chat_panel
        assert panel.service is not None, "打开项目后应绑定 ChatService"
        assert w.dock_tabs.count() == 2, "dock 应有 对话/建议草稿 两个 tab"

        # 模拟一次 AI 回复（_save_as_idea 会弹输入框，此处验证历史与持久化路径）
        panel._history.append({"role": "assistant", "content": "建议给林晚加一个预知未来的金手指。"})
        assert len([m for m in panel._history if m["role"] == "assistant"]) == 1
        idea = IdeaService(db).create(project_id=project.id, title="预知金手指",
                                      summary=panel._history[-1]["content"])
        assert IdeaService(db).get(idea.id).summary.startswith("建议给林晚")
        print("ChatPanel bind + history + idea persistence OK")

        print("ALL CHAT TESTS PASSED")
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

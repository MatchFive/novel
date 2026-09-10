"""项目工作台：打开项目后的主界面（左侧导航树 + 中央模块视图切换）。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.core.config import AppConfig
from app.db.session import Database
from app.services.llm_service import LLMService
from app.services.project_service import ProjectService

MODULES = [
    ("dashboard", "仪表盘"),
    ("ideas", "点子"),
    ("world", "世界观"),
    ("characters", "角色"),
    ("outline", "大纲"),
    ("chapters", "章节"),
    ("memory", "记忆库"),
    ("handbook", "教程库"),
    ("prompts", "提示词"),
    ("revision", "修订检查"),
    ("logs", "日志"),
]


class Workspace(QWidget):
    """项目工作区：导航树 + 模块视图栈。"""

    def __init__(self, cfg: AppConfig, db: Database, project, main_window=None, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.db = db
        self.project = project
        self.main_window = main_window
        self.project_service = ProjectService(db)
        self.llm_service = LLMService(db, cfg)

        splitter = QSplitter(self)

        # 左侧导航
        self.nav = QListWidget()
        self.nav.setObjectName("nav_list")
        self.nav.setFixedWidth(150)
        for key, label in MODULES:
            item = QListWidgetItem(label)
            item.setData(0x0100, key)  # Qt.UserRole
            self.nav.addItem(item)
        self.nav.currentItemChanged.connect(self._on_nav)

        # 中央模块栈
        self.stack = QStackedWidget()
        self._views: dict[str, QWidget] = {}

        splitter.addWidget(self.nav)
        splitter.addWidget(self.stack)
        splitter.setStretchFactor(1, 1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(splitter)

        self._build_views()
        self.nav.setCurrentRow(0)

    # ---------------- 模块视图 ----------------
    def _build_views(self) -> None:
        from app.ui.views.character_view import CharacterView
        from app.ui.views.chapter_view import ChapterView
        from app.ui.views.dashboard_view import DashboardView
        from app.ui.views.handbook_view import HandbookView
        from app.ui.views.idea_view import IdeaView
        from app.ui.views.log_view import LogView
        from app.ui.views.memory_view import MemoryView
        from app.ui.views.outline_view import OutlineView
        from app.ui.views.prompt_view import PromptView
        from app.ui.views.revision_view import RevisionView
        from app.ui.views.world_view import WorldView

        self.register_view("dashboard", DashboardView(self))
        self.register_view("ideas", IdeaView(self))
        self.register_view("world", WorldView(self))
        self.register_view("characters", CharacterView(self))
        self.register_view("outline", OutlineView(self))
        self.register_view("chapters", ChapterView(self))
        self.register_view("memory", MemoryView(self))
        self.register_view("handbook", HandbookView(self))
        self.register_view("prompts", PromptView(self))
        self.register_view("revision", RevisionView(self))
        self.register_view("logs", LogView(self))

    # ---------------- 草稿应用转发 ----------------
    def apply_suggestion(self) -> None:
        view = self.stack.currentWidget()
        fn = getattr(view, "apply_suggestion", None)
        if fn:
            fn()

    def apply_config(self, cfg) -> None:
        """热更新模型配置：替换 cfg 引用并重建 LLM 客户端（模型设置界面保存后调用）。"""
        from app.llm.client import OpenAICompatClient
        self.cfg = cfg
        self.llm_service.cfg = cfg
        self.llm_service.client = OpenAICompatClient(cfg)

    def refresh_all(self) -> None:
        """刷新所有模块视图（对话执行修改后同步最新数据）。"""
        for view in self._views.values():
            refresh = getattr(view, "refresh", None)
            if refresh:
                try:
                    refresh()
                except Exception:
                    pass

    # ---------------- 模块注册 ----------------
    def register_view(self, key: str, view: QWidget) -> None:
        self._views[key] = view
        self.stack.addWidget(view)

    def _on_nav(self, current: QListWidgetItem | None, _prev=None) -> None:
        if current is None:
            return
        key = current.data(0x0100)
        view = self._views.get(key)
        if view is not None:
            self.stack.setCurrentWidget(view)
            refresh = getattr(view, "refresh", None)
            if refresh:
                refresh()

    def goto(self, key: str) -> None:
        """按模块 key 跳转（仪表盘一键导航用）。"""
        for i in range(self.nav.count()):
            if self.nav.item(i).data(0x0100) == key:
                self.nav.setCurrentRow(i)
                return

    def _placeholder(self, text: str) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        label = QLabel(text)
        label.setStyleSheet("color:#888; font-size:14px;")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(label)
        return w

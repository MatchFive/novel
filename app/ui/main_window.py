"""主窗口骨架（M0）：导航树 + 标签页 + AI 助手面板 + 状态栏。业务视图在 M1 接入。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.config import AppConfig
from app.core.constants import APP_NAME, APP_VERSION, TIER_LITE, TIER_PRO, TIER_STANDARD
from app.db.session import Database
from app.services.project_service import ProjectService


class MainWindow(QMainWindow):
    def __init__(self, cfg: AppConfig, db: Database, project_id: int | None = None):
        super().__init__()
        self.cfg = cfg
        self.db = db
        self.project_id = project_id
        self.project_service = ProjectService(db)

        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.resize(1400, 880)

        self._build_menus()
        self._build_toolbar()
        self._build_central()
        self._build_dock()
        self._build_statusbar()

        if self.project_id is not None:
            self._load_project(self.project_id)

    # ---------------- 构建 ----------------
    def _build_menus(self) -> None:
        bar = self.menuBar()
        m_file = bar.addMenu("文件(&F)")
        act_new = m_file.addAction("新建项目…")
        act_new.triggered.connect(self.on_new_project)
        act_open = m_file.addAction("打开项目…")
        act_open.triggered.connect(self.on_open_project)
        m_file.addSeparator()
        act_backup = m_file.addAction("备份数据库\tCtrl+B")
        act_backup.triggered.connect(self.on_backup)
        act_restore = m_file.addAction("恢复数据库…")
        act_restore.triggered.connect(self.on_restore)
        m_file.addSeparator()
        m_file.addAction("退出").triggered.connect(self.close)

        m_ai = bar.addMenu("AI 工具(&A)")
        act_model = m_ai.addAction("模型与路由设置…")
        act_model.triggered.connect(self.on_model_settings)
        # AI 指令已与「对话」页整合（对话里说修改意图即可触发计划确认执行），不再提供独立入口

        m_help = bar.addMenu("帮助(&H)")
        m_help.addAction("关于").triggered.connect(self.on_about)

    def open_command_dialog(self) -> None:
        if not hasattr(self, "workspace"):
            return
        from app.ui.dialogs.command_dialog import CommandDialog
        dlg = CommandDialog(self.workspace, self)
        dlg.exec()

    # ---------------- 模型设置 ----------------
    def on_model_settings(self) -> None:
        from app.ui.dialogs.model_settings_dialog import ModelSettingsDialog
        dlg = ModelSettingsDialog(self.cfg, self)
        if dlg.exec() and dlg.new_cfg is not None:
            self._apply_config(dlg.new_cfg)

    def _apply_config(self, new_cfg) -> None:
        """热更新模型配置：替换引用并重建 LLM 客户端（无需重启）。"""
        self.cfg = new_cfg
        if hasattr(self, "workspace"):
            self.workspace.apply_config(new_cfg)
        self.status.showMessage("模型配置已保存并即时生效。")

    def _build_toolbar(self) -> None:
        tb = QToolBar("主工具栏", self)
        tb.setMovable(False)
        self.addToolBar(tb)

        tb.addWidget(QLabel(" 档位: "))
        self.tier_combo = QComboBox()
        self.tier_combo.addItem("跟随路由", "")
        self.tier_combo.addItem("Lite", TIER_LITE)
        self.tier_combo.addItem("Standard", TIER_STANDARD)
        self.tier_combo.addItem("Pro", TIER_PRO)
        tb.addWidget(self.tier_combo)
        tb.addSeparator()

        for text in ("AI 生成", "续写", "记忆提取", "一致性检查"):
            act = tb.addAction(text)
            act.setEnabled(False)  # M1 接入

    def _build_central(self) -> None:
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.setCentralWidget(self.tabs)

        # 欢迎页（占位）
        welcome = QWidget()
        lay = QVBoxLayout(welcome)
        label = QLabel("长篇小说 AI 创作工作台\n\n从「文件」菜单新建或打开一个项目开始。")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(label)
        self.tabs.addTab(welcome, "欢迎")

    def _build_dock(self) -> None:
        dock = QDockWidget("AI 助手", self)
        dock.setObjectName("assistant_dock")
        dock.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea | Qt.DockWidgetArea.LeftDockWidgetArea)

        self.dock_tabs = QTabWidget()

        # tab 1：创作对话
        from app.ui.views.chat_panel import ChatPanel
        self.chat_panel = ChatPanel()
        self.dock_tabs.addTab(self.chat_panel, "对话")

        # tab 2：建议草稿
        panel = QWidget()
        lay = QVBoxLayout(panel)
        lay.addWidget(QLabel("生成任务"))
        self.task_label = QLabel("空闲")
        lay.addWidget(self.task_label)
        lay.addWidget(QLabel("建议草稿（预览/编辑后点「应用草稿」写入）"))
        self.suggestion = QPlainTextEdit()
        self.suggestion.setPlaceholderText("AI 生成内容将在此流式上屏……")
        lay.addWidget(self.suggestion)
        row = QHBoxLayout()
        self.btn_apply_suggestion = QPushButton("应用草稿")
        self.btn_apply_suggestion.clicked.connect(self._on_apply_suggestion)
        self.btn_clear_suggestion = QPushButton("清空")
        self.btn_clear_suggestion.clicked.connect(self._clear_current_draft)
        row.addWidget(self.btn_apply_suggestion)
        row.addWidget(self.btn_clear_suggestion)
        lay.addLayout(row)
        lay.addWidget(QLabel("本次注入（设定/记忆/承诺命中）"))
        self.injected_view = QPlainTextEdit()
        self.injected_view.setReadOnly(True)
        self.injected_view.setPlaceholderText("本次生成注入的检索命中项将在此展示……")
        self.injected_view.setMaximumHeight(110)
        lay.addWidget(self.injected_view)
        self.dock_tabs.addTab(panel, "建议草稿")

        # 章节草稿仓库：草稿按章节归属常驻内存（应用/清空/关程序才消失），切章实时切换
        self._chapter_drafts: dict[int, str] = {}
        self._current_draft_chapter: int | None = None

        dock.setWidget(self.dock_tabs)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)

    # ---------------- 建议草稿面板 API ----------------
    def set_task_label(self, text: str) -> None:
        self.task_label.setText(text)

    def clear_suggestion(self) -> None:
        self.suggestion.clear()

    def append_suggestion(self, text: str) -> None:
        self.suggestion.moveCursor(self.suggestion.textCursor().MoveOperation.End)
        self.suggestion.insertPlainText(text)
        self.suggestion.ensureCursorVisible()

    def get_suggestion(self) -> str:
        return self.suggestion.toPlainText()

    # ---------------- 章节草稿仓库 ----------------
    def set_chapter_draft(self, chapter_id: int, text: str) -> None:
        """写入某章节的草稿；若当前正显示该章，同步上屏。"""
        self._chapter_drafts[chapter_id] = text
        if self._current_draft_chapter == chapter_id:
            self.suggestion.setPlainText(text)

    def switch_chapter_draft(self, chapter_id: int | None) -> None:
        """切换章节时：先把当前面板内容（含用户手改）存回上一章，再载入新章的草稿。"""
        if chapter_id == self._current_draft_chapter:
            return
        if self._current_draft_chapter is not None:
            cur = self.suggestion.toPlainText()
            if cur.strip():
                self._chapter_drafts[self._current_draft_chapter] = cur
            else:
                self._chapter_drafts.pop(self._current_draft_chapter, None)
        self._current_draft_chapter = chapter_id
        draft = self._chapter_drafts.get(chapter_id, "") if chapter_id is not None else ""
        self.suggestion.setPlainText(draft)

    def clear_chapter_draft(self, chapter_id: int) -> None:
        """清除某章节的草稿（应用/清空后）；若正显示该章则清空面板。"""
        self._chapter_drafts.pop(chapter_id, None)
        if self._current_draft_chapter == chapter_id:
            self.suggestion.clear()

    def _clear_current_draft(self) -> None:
        """「清空」按钮：清掉当前章节的草稿（仓库+面板）。"""
        self.suggestion.clear()
        if self._current_draft_chapter is not None:
            self._chapter_drafts.pop(self._current_draft_chapter, None)

    def set_injected(self, injected: dict) -> None:
        lines = []
        for key, val in injected.items():
            if key == "kept_blocks":
                continue
            label = {
                "pov_memories": "视点角色经历", "other_char_events": "他人经历",
                "knowledge_state": "知识状态", "related_events": "历史事件",
                "open_promises": "承诺/伏笔", "world_entries": "世界观条目",
            }.get(key, key)
            lines.append(f"{label}: {val} 条")
        self.injected_view.setPlainText("\n".join(lines) if lines else "（无注入）")

    def _on_apply_suggestion(self) -> None:
        if hasattr(self, "workspace") and hasattr(self.workspace, "apply_suggestion"):
            self.workspace.apply_suggestion()

    def _build_statusbar(self) -> None:
        self.status = self.statusBar()
        self.model_label = QLabel("未选择模型")
        self.status.addPermanentWidget(self.model_label)

    # ---------------- 项目 ----------------
    def _load_project(self, project_id: int) -> None:
        self.project_id = project_id
        project = self.project_service.get(project_id)
        if project is None:
            return
        from app.ui.workspace import Workspace
        self.workspace = Workspace(self.cfg, self.db, project, self)
        self.setCentralWidget(self.workspace)
        self.chat_panel.bind_workspace(self.workspace)
        self.setWindowTitle(f"{APP_NAME} — {project.name}")
        self.status.showMessage(f"已打开项目：{project.name}")

    def on_new_project(self) -> None:
        from app.ui.dialogs.project_dialog import NewProjectDialog
        dlg = NewProjectDialog(self)
        if dlg.exec():
            p = self.project_service.create(**dlg.values())
            self._load_project(p.id)

    def on_open_project(self) -> None:
        from app.ui.dialogs.project_dialog import OpenProjectDialog
        dlg = OpenProjectDialog(self.project_service, self)
        if dlg.exec() and dlg.selected_project is not None:
            self._load_project(dlg.selected_project.id)

    def on_about(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.about(self, "关于", f"{APP_NAME} v{APP_VERSION}\n长篇小说 AI 创作工作台")

    # ---------------- 备份 / 恢复 ----------------
    def on_backup(self) -> None:
        from app.db.backup import create_backup
        target = create_backup(self.db.db_path, self.cfg.data_dir)
        self.status.showMessage(f"已备份：{target.name}")

    def on_restore(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        from app.db.backup import list_backups
        backups = list_backups(self.cfg.data_dir)
        if not backups:
            QMessageBox.information(self, "恢复数据库", "还没有备份文件（先用「备份数据库」创建）。")
            return
        from PySide6.QtWidgets import QInputDialog
        names = [f"{b.name}（{b.stat().st_size // 1024} KB）" for b in backups]
        choice, ok = QInputDialog.getItem(
            self, "恢复数据库", "选择要恢复的备份（恢复后需重启应用）：", names, 0, False
        )
        if not ok:
            return
        backup = backups[names.index(choice)]
        ret = QMessageBox.question(
            self, "恢复数据库",
            f"确定用 {backup.name} 覆盖当前数据库吗？\n当前数据将被该备份替换。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        from app.db.backup import restore_backup
        self.db.engine.dispose()  # 释放连接后覆盖
        restore_backup(backup, self.db.db_path)
        QMessageBox.information(
            self, "恢复数据库", "恢复完成。请重启应用以加载恢复后的数据。"
        )

    def _close_tab(self, index: int) -> None:
        self.tabs.removeTab(index)

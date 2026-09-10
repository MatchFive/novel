"""启动对话框：选择最近项目 / 新建 / 打开。"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from app.services.project_service import ProjectService


class StartDialog(QDialog):
    """应用启动时：列出已有项目，可选进入/新建/打开。"""

    def __init__(self, service: ProjectService, parent=None):
        super().__init__(parent)
        self.setWindowTitle("长篇小说 AI 创作工作台")
        self.setMinimumSize(440, 380)
        self.selected_project_id: int | None = None
        self.action = "open"  # open / new

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("选择项目："))

        self.list_widget = QListWidget()
        self.projects = service.list()
        for p in self.projects:
            item = QListWidgetItem(f"{p.name}（{p.genre or '未分类'}）")
            item.setData(0x0100, p.id)  # Qt.UserRole
            self.list_widget.addItem(item)
        if self.projects:
            self.list_widget.setCurrentRow(0)
        lay.addWidget(self.list_widget)

        self.btn_open = QPushButton("打开所选项目")
        self.btn_open.clicked.connect(self._open)
        self.btn_new = QPushButton("新建项目…")
        self.btn_new.clicked.connect(self._new)
        row = QHBoxLayout()
        row.addWidget(self.btn_open)
        row.addWidget(self.btn_new)
        lay.addLayout(row)

        if not self.projects:
            self.btn_open.setEnabled(False)

    def _open(self) -> None:
        row = self.list_widget.currentRow()
        if row < 0:
            return
        self.selected_project_id = self.list_widget.item(row).data(0x0100)
        self.action = "open"
        self.accept()

    def _new(self) -> None:
        self.selected_project_id = None
        self.action = "new"
        self.accept()

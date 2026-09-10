"""项目相关对话框：新建 / 打开。"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QTextEdit,
    QVBoxLayout,
)

from app.services.project_service import ProjectService

GENRES = ["玄幻", "仙侠", "都市", "科幻", "历史", "悬疑", "言情", "武侠", "奇幻", "其他"]


class NewProjectDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("新建项目")
        self.setMinimumWidth(460)

        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("例如：星海拾遗")
        self.genre_combo = QComboBox()
        self.genre_combo.addItems(GENRES)
        self.logline_edit = QLineEdit()
        self.logline_edit.setPlaceholderText("一句话梗概（可留空，之后 AI 帮你补）")
        self.theme_edit = QLineEdit()
        self.theme_edit.setPlaceholderText("主题（可留空）")
        self.style_edit = QTextEdit()
        self.style_edit.setPlaceholderText("文风指南，例如：网文爽快流、多对话、快节奏（可留空）")
        self.style_edit.setMaximumHeight(80)

        form.addRow("书名 *", self.name_edit)
        form.addRow("类型", self.genre_combo)
        form.addRow("一句话梗概", self.logline_edit)
        form.addRow("主题", self.theme_edit)
        form.addRow("文风指南", self.style_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(buttons)

    def _validate(self) -> None:
        if not self.name_edit.text().strip():
            self.name_edit.setFocus()
            return
        self.accept()

    def values(self) -> dict:
        return {
            "name": self.name_edit.text().strip(),
            "genre": self.genre_combo.currentText(),
            "logline": self.logline_edit.text().strip(),
            "theme": self.theme_edit.text().strip(),
            "style_guide": self.style_edit.toPlainText().strip(),
        }


class OpenProjectDialog(QDialog):
    def __init__(self, service: ProjectService, parent=None):
        super().__init__(parent)
        self.setWindowTitle("打开项目")
        self.setMinimumSize(420, 360)
        self.selected_project = None

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

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_open)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _accept_open(self) -> None:
        row = self.list_widget.currentRow()
        if row < 0:
            return
        pid = self.list_widget.item(row).data(0x0100)
        self.selected_project = next(p for p in self.projects if p.id == pid)
        self.accept()

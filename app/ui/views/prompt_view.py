"""提示词管理视图（M4）：三级模板列表 + 内嵌编辑 + 版本历史 + 恢复默认。"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from app.services.prompt_service import PromptService

SCOPE_LABEL = {"global": "全局基础", "project": "作品级", "task": "任务模板"}


class PromptView(QWidget):
    def __init__(self, workspace, parent=None):
        super().__init__(parent)
        self.workspace = workspace
        self.service = PromptService(workspace.db)

        splitter = QSplitter(self)
        self.tpl_list = QListWidget()
        self.tpl_list.currentItemChanged.connect(self._on_select)
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("{{占位符}} 说明：写作时由上下文组装器填充……")
        self.info_label = QLabel("")
        self.info_label.setWordWrap(True)
        self.info_label.setStyleSheet("color:#666;")

        self.btn_save = QPushButton("保存新版本")
        self.btn_save.clicked.connect(self._save)
        self.btn_restore = QPushButton("恢复内置默认")
        self.btn_restore.clicked.connect(self._restore)
        row = QHBoxLayout()
        row.addWidget(self.btn_save)
        row.addWidget(self.btn_restore)

        right = QWidget()
        rlay = QVBoxLayout(right)
        rlay.addWidget(self.info_label)
        rlay.addWidget(QLabel("模板内容"))
        rlay.addWidget(self.editor)
        rlay.addLayout(row)
        right.setMinimumWidth(520)

        splitter.addWidget(self.tpl_list)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        lay = QVBoxLayout(self)
        lay.addWidget(splitter)

    def refresh(self) -> None:
        templates = self.service.list_templates()
        self.tpl_list.clear()
        for t in templates:
            self.tpl_list.addItem(QListWidgetItem(f"[{SCOPE_LABEL.get(t.scope, t.scope)}] {t.key}"))
            self.tpl_list.item(self.tpl_list.count() - 1).setData(0x0100, t.key)
        if templates:
            self.tpl_list.setCurrentRow(0)
        else:
            self.editor.clear()

    def _on_select(self, current: QListWidgetItem | None, _prev=None) -> None:
        if current is None:
            return
        key = current.data(0x0100)
        tpl = self.service.get(key)
        if tpl is None:
            return
        self.editor.setPlainText(tpl.template)
        self.info_label.setText(
            f"{tpl.key}｜版本 v{tpl.version}｜{SCOPE_LABEL.get(tpl.scope, tpl.scope)}｜{tpl.name or ''}"
        )

    def _save(self) -> None:
        item = self.tpl_list.currentItem()
        if item is None:
            return
        key = item.data(0x0100)
        try:
            self.service.update_template(key, self.editor.toPlainText())
        except KeyError:
            QMessageBox.warning(self, "提示", f"模板不存在：{key}")
            return
        self.status_msg(f"模板 {key} 已保存（版本 +1）")
        self.refresh()
        for i in range(self.tpl_list.count()):
            if self.tpl_list.item(i).data(0x0100) == key:
                self.tpl_list.setCurrentRow(i)
                break

    def _restore(self) -> None:
        item = self.tpl_list.currentItem()
        if item is None:
            return
        key = item.data(0x0100)
        text = self.service.restore_default(key)
        if text is None:
            QMessageBox.information(self, "提示", "该模板没有内置默认版本。")
            return
        self.editor.setPlainText(text)
        self.status_msg(f"模板 {key} 已恢复内置默认")
        self.refresh()

    def status_msg(self, text: str) -> None:
        mw = getattr(self.workspace, "main_window", None)
        if mw is not None:
            mw.status.showMessage(text)

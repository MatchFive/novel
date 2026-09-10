"""AI 指令对话框（设计文档 3.11）：输入自然语言指令 → 修改计划逐条勾选 → 确认执行。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from app.services.command_service import CommandService
from app.utils.async_utils import run_async


class CommandDialog(QDialog):
    """全局 AI 指令栏（Ctrl+J）。"""

    def __init__(self, workspace, parent=None):
        super().__init__(parent)
        self.workspace = workspace
        self.service = CommandService(workspace.db, workspace.llm_service)
        self.project_id = workspace.project.id
        self._plan: dict = {}
        self._change_count = 0

        self.setWindowTitle("AI 指令 — 自然语言修改设定/剧情/章节")
        self.setMinimumSize(620, 460)

        lay = QVBoxLayout(self)
        self.input = QLineEdit()
        self.input.setPlaceholderText(
            '例如：把反派的身份改成主角失散多年的哥哥；或 给主角加一个金手指：受伤会愈合但代价是失忆'
        )
        self.input.returnPressed.connect(self._execute)
        self.btn_run = QPushButton("生成修改计划")
        self.btn_run.clicked.connect(self._execute)
        row = QHBoxLayout()
        row.addWidget(self.input, 1)
        row.addWidget(self.btn_run)
        lay.addLayout(row)

        self.impact_label = QLabel("")
        self.impact_label.setWordWrap(True)
        self.impact_label.setStyleSheet("color:#666;")
        lay.addWidget(self.impact_label)

        self.plan_list = QListWidget()
        self.plan_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        lay.addWidget(QLabel("修改计划（勾选要执行的变更项；AI 不会自动写入任何数据）"))
        lay.addWidget(self.plan_list)

        btn_row = QHBoxLayout()
        self.btn_apply = QPushButton("执行勾选的变更")
        self.btn_apply.setEnabled(False)
        self.btn_apply.clicked.connect(self._apply)
        self.btn_close = QPushButton("关闭")
        self.btn_close.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_apply)
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_close)
        lay.addLayout(btn_row)

    # ---------------- 外部调用入口 ----------------
    def run_command(self, command: str) -> None:
        """对话面板调用：预填指令并自动生成修改计划。"""
        self.input.setText(command)
        self._execute()

    # ---------------- 计划 ----------------
    def _execute(self) -> None:
        command = self.input.text().strip()
        if not command:
            return
        self.btn_run.setEnabled(False)
        self.btn_run.setText("分析中…")
        self.plan_list.clear()
        self.impact_label.setText("")

        async def run():
            return await self.service.plan(self.project_id, command)

        run_async(run(), on_done=self._on_plan, on_error=self._on_error)

    def _on_plan(self, plan: dict) -> None:
        self.btn_run.setEnabled(True)
        self.btn_run.setText("生成修改计划")
        self._plan = plan or {}

        clarification = self._plan.get("clarification")
        if clarification:
            QMessageBox.information(self, "需要澄清",
                                    f"指令指代不明，请补充说明：\n{clarification}")
            return

        changes = self._plan.get("changes") or []
        self._change_count = len(changes)
        self.impact_label.setText(
            f"影响范围：{self._plan.get('impact_summary', '')}\n"
            f"共 {self._change_count} 项变更"
            + (f"；新增设定建议 {len(self._plan.get('new_settings') or [])} 项（将进入设定沉淀候选）"
               if self._plan.get("new_settings") else "")
        )
        self.plan_list.clear()
        for i, change in enumerate(changes):
            item = QListWidgetItem(
                f"[{change.get('domain', '?')}] {change.get('target', '')}："
                f"{change.get('before', '')} → {change.get('after', '')}"
                + (f"（{change.get('note', '')}）" if change.get("note") else "")
            )
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(0x0100, i)
            self.plan_list.addItem(item)
        self.btn_apply.setEnabled(self._change_count > 0)

    # ---------------- 执行 ----------------
    def _apply(self) -> None:
        accept: set[int] = set()
        for r in range(self.plan_list.count()):
            item = self.plan_list.item(r)
            if item.checkState() == Qt.CheckState.Checked:
                accept.add(item.data(0x0100))
        if not accept:
            QMessageBox.information(self, "提示", "请至少勾选一项变更。")
            return
        result = self.service.apply_plan(self.project_id, self._plan, accept)
        msg = f"已执行 {result['applied']} 项变更。"
        if result["skipped"]:
            msg += "\n跳过：" + "；".join(result["skipped"])
        msg += "\n\n建议：受影响章节可重新执行「记忆提取」以同步记忆。"
        QMessageBox.information(self, "执行完成", msg)
        self.accept()

    def _on_error(self, exc: Exception) -> None:
        self.btn_run.setEnabled(True)
        self.btn_run.setText("生成修改计划")
        QMessageBox.warning(self, "指令执行失败", str(exc))

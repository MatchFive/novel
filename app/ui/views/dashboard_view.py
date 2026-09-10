"""仪表盘视图：项目进度总览 + 一键跳转。"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services.dashboard_service import DashboardService

JUMP_BUTTONS = [
    ("ideas", "点子", "记录灵感"),
    ("world", "世界观", "搭建设定"),
    ("characters", "角色", "设计角色"),
    ("outline", "大纲", "构建大纲"),
    ("chapters", "章节", "写正文"),
    ("revision", "修订检查", "检查一致性"),
]


class DashboardView(QWidget):
    def __init__(self, workspace, parent=None):
        super().__init__(parent)
        self.workspace = workspace
        self.service = DashboardService(workspace.db)
        self.project_id = workspace.project.id

        lay = QVBoxLayout(self)
        title = QLabel(f"📖 {workspace.project.name}")
        title.setStyleSheet("font-size:18px; font-weight:bold;")
        lay.addWidget(title)

        self.stats_grid = QGridLayout()
        self._value_labels: dict[str, QLabel] = {}
        for i, (key, label) in enumerate([
            ("ideas", "点子"), ("entries", "设定条目"), ("characters", "角色"),
            ("arcs", "卷"), ("chapters", "章节"), ("words", "总字数"),
            ("drafted", "已写章节"), ("extracted", "已提取记忆章节"),
        ]):
            box = QGroupBox(label)
            v = QLabel("0")
            v.setStyleSheet("font-size:20px; font-weight:bold;")
            box.setStyleSheet("QGroupBox{font-size:12px;}")
            inner = QVBoxLayout(box)
            inner.addWidget(v)
            self.stats_grid.addWidget(box, i // 4, i % 4)
            self._value_labels[key] = v
        lay.addLayout(self.stats_grid)

        # Token 用量面板
        usage_box = QGroupBox("Token 用量（按模型档位汇总）")
        ulay = QVBoxLayout(usage_box)
        self.usage_table = QTableWidget(0, 5)
        self.usage_table.setHorizontalHeaderLabels(["厂商", "档位", "调用次数", "输入 tokens", "输出 tokens"])
        self.usage_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.usage_table.setMaximumHeight(160)
        ulay.addWidget(self.usage_table)
        lay.addWidget(usage_box)

        nav_box = QGroupBox("继续创作")
        nlay = QHBoxLayout(nav_box)
        for key, label, hint in JUMP_BUTTONS:
            btn = QPushButton(f"{label}（{hint}）")
            btn.clicked.connect(lambda _=False, k=key: self.workspace.goto(k))
            nlay.addWidget(btn)
        lay.addWidget(nav_box)
        lay.addStretch(1)

    def refresh(self) -> None:
        s = self.service.stats(self.project_id)
        for key, label in self._value_labels.items():
            value = s.get(key, 0)
            if key == "words":
                value = f"{s['words']:,}"
            label.setText(str(value))
        p = self.workspace.project
        if p.logline:
            self.setToolTip(f"一句话梗概：{p.logline}")
        # Token 用量
        usage = self.service.usage(self.project_id)
        self.usage_table.setRowCount(len(usage))
        for r, row in enumerate(usage):
            self.usage_table.setItem(r, 0, QTableWidgetItem(row.get("provider", "-") or "-"))
            self.usage_table.setItem(r, 1, QTableWidgetItem(row.get("tier", "-") or "-"))
            self.usage_table.setItem(r, 2, QTableWidgetItem(str(row.get("calls", 0))))
            self.usage_table.setItem(r, 3, QTableWidgetItem(f"{row.get('in_tokens', 0):,}"))
            self.usage_table.setItem(r, 4, QTableWidgetItem(f"{row.get('out_tokens', 0):,}"))

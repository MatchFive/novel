"""生成配置面板（设计文档 3.14）：作品级生成参数，保存到 project.settings。"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.core.constants import DEFAULT_GEN_CONFIG


class GenerationConfigPanel(QWidget):
    """读取/修改项目的生成配置（target_words/style/pov/pace/quality/tier 等）。"""

    def __init__(self, workspace, parent=None):
        super().__init__(parent)
        self.workspace = workspace
        self._settings = dict(DEFAULT_GEN_CONFIG)
        self._settings.update(workspace.project.settings or {})

        box = QGroupBox("生成配置（影响 AI 生成；可在单次生成时覆盖）")
        form = QFormLayout(box)

        self.target_words = QSpinBox()
        self.target_words.setRange(500, 20000)
        self.target_words.setSingleStep(500)
        self.target_words.setValue(int(self._settings.get("target_words", 3000)))

        self.style_edit = QLineEdit()
        self.style_edit.setPlaceholderText("如：网文爽快流、多对话、快节奏")
        self.style_edit.setText(self._settings.get("style", "") or "")

        self.pov_combo = QComboBox()
        self.pov_combo.addItem("第三人称", "third")
        self.pov_combo.addItem("第一人称", "first")
        idx = self.pov_combo.findData(self._settings.get("pov", "third"))
        self.pov_combo.setCurrentIndex(max(idx, 0))

        self.pace_combo = QComboBox()
        self.pace_combo.addItem("快节奏", "fast")
        self.pace_combo.addItem("适中", "medium")
        self.pace_combo.addItem("慢热", "slow")
        idx = self.pace_combo.findData(self._settings.get("pace", "medium"))
        self.pace_combo.setCurrentIndex(max(idx, 0))

        self.dialogue_combo = QComboBox()
        self.dialogue_combo.addItem("高", "high")
        self.dialogue_combo.addItem("中", "medium")
        self.dialogue_combo.addItem("低", "low")
        idx = self.dialogue_combo.findData(self._settings.get("dialogue_density", "medium"))
        self.dialogue_combo.setCurrentIndex(max(idx, 0))

        self.quality_combo = QComboBox()
        self.quality_combo.addItem("草稿（低成本模型）", "draft")
        self.quality_combo.addItem("精修（强模型）", "refined")
        idx = self.quality_combo.findData(self._settings.get("quality_mode", "draft"))
        self.quality_combo.setCurrentIndex(max(idx, 0))

        self.tier_combo = QComboBox()
        self.tier_combo.addItem("跟随路由", "")
        self.tier_combo.addItem("Lite", "lite")
        self.tier_combo.addItem("Standard", "standard")
        self.tier_combo.addItem("Pro", "pro")
        idx = self.tier_combo.findData(self._settings.get("tier_override", ""))
        self.tier_combo.setCurrentIndex(max(idx, 0))

        self.memory_combo = QComboBox()
        self.memory_combo.addItem("标准", "standard")
        self.memory_combo.addItem("严格", "strict")
        self.memory_combo.addItem("宽松", "loose")
        idx = self.memory_combo.findData(self._settings.get("memory_strength", "standard"))
        self.memory_combo.setCurrentIndex(max(idx, 0))

        self.draft_mode_combo = QComboBox()
        self.draft_mode_combo.addItem("逐节拍（云端模型推荐）", "beats")
        self.draft_mode_combo.addItem("整章单发（本地微调模型推荐）", "whole")
        idx = self.draft_mode_combo.findData(self._settings.get("draft_mode", "beats"))
        self.draft_mode_combo.setCurrentIndex(max(idx, 0))

        form.addRow("每章目标字数", self.target_words)
        form.addRow("行文风格", self.style_edit)
        form.addRow("视角", self.pov_combo)
        form.addRow("节奏", self.pace_combo)
        form.addRow("对话密度", self.dialogue_combo)
        form.addRow("质量模式", self.quality_combo)
        form.addRow("模型档位", self.tier_combo)
        form.addRow("记忆强度", self.memory_combo)
        form.addRow("正文生成方式", self.draft_mode_combo)

        self.btn_save = QPushButton("保存生成配置")
        self.btn_save.clicked.connect(self._save)

        lay = QVBoxLayout(self)
        lay.addWidget(box)
        lay.addWidget(self.btn_save)
        lay.addStretch(1)

    def current(self) -> dict:
        """返回当前面板值（未保存也生效，供单次生成使用）。"""
        return {
            "target_words": self.target_words.value(),
            "style": self.style_edit.text().strip(),
            "pov": self.pov_combo.currentData(),
            "pace": self.pace_combo.currentData(),
            "dialogue_density": self.dialogue_combo.currentData(),
            "quality_mode": self.quality_combo.currentData(),
            "tier_override": self.tier_combo.currentData() or None,
            "memory_strength": self.memory_combo.currentData(),
            "draft_mode": self.draft_mode_combo.currentData(),
        }

    def _save(self) -> None:
        settings = dict(self.workspace.project.settings or {})
        settings.update(self.current())
        from app.services.project_service import ProjectService
        ProjectService(self.workspace.db).update(self.workspace.project.id, settings=settings)
        self.workspace.project.settings = settings
        self.btn_save.setText("已保存 ✓")
        self.btn_save.setEnabled(False)

    def on_saved(self) -> None:
        self.btn_save.setText("保存生成配置")
        self.btn_save.setEnabled(True)

"""世界观视图：分类树 + 条目列表 + 编辑 + AI 骨架生成（M1）。"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from app.services.world_service import WorldService
from app.utils.async_utils import run_async


class WorldView(QWidget):
    def __init__(self, workspace, parent=None):
        super().__init__(parent)
        self.workspace = workspace
        self.service = WorldService(workspace.db)
        self.project_id = workspace.project.id
        self._current_entry_id: int | None = None
        self._current_category_id: int | None = None

        splitter = QSplitter(self)

        # 左：分类 + 条目
        left = QWidget()
        llay = QVBoxLayout(left)
        self.cat_list = QListWidget()
        self.cat_list.currentItemChanged.connect(self._on_category)
        self.entry_list = QListWidget()
        self.entry_list.currentItemChanged.connect(self._on_entry)
        self.btn_add_cat = QPushButton("+ 分类")
        self.btn_add_cat.clicked.connect(self._add_category)
        self.btn_add_entry = QPushButton("+ 条目")
        self.btn_add_entry.clicked.connect(self._add_entry)
        self.btn_skeleton = QPushButton("AI 生成骨架")
        self.btn_skeleton.setObjectName("ai_button")
        self.btn_skeleton.clicked.connect(self._ai_skeleton)
        row = QHBoxLayout()
        row.addWidget(self.btn_add_cat)
        row.addWidget(self.btn_add_entry)
        llay.addWidget(QLabel("分类"))
        llay.addWidget(self.cat_list)
        llay.addLayout(row)
        llay.addWidget(QLabel("条目"))
        llay.addWidget(self.entry_list)
        llay.addWidget(self.btn_skeleton)
        left.setMinimumWidth(240)

        # 右：条目编辑
        right = QWidget()
        rlay = QVBoxLayout(right)
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("条目标题")
        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("标签（逗号分隔）")
        self.content_edit = QPlainTextEdit()
        self.content_edit.setPlaceholderText("设定内容……")
        self.importance_spin = QSpinBox()
        self.importance_spin.setRange(1, 5)
        self.importance_spin.setValue(3)
        self.canonical_check = QCheckBox("铁律（不可违背）")

        self.btn_save = QPushButton("保存")
        self.btn_save.clicked.connect(self._save_entry)
        self.btn_del = QPushButton("删除条目")
        self.btn_del.clicked.connect(self._delete_entry)

        rlay.addWidget(QLabel("标题"))
        rlay.addWidget(self.title_edit)
        rlay.addWidget(QLabel("标签"))
        rlay.addWidget(self.tags_edit)
        rlay.addWidget(QLabel("内容"))
        rlay.addWidget(self.content_edit)
        meta = QHBoxLayout()
        meta.addWidget(QLabel("重要度"))
        meta.addWidget(self.importance_spin)
        meta.addWidget(self.canonical_check)
        meta.addStretch(1)
        rlay.addLayout(meta)
        row2 = QHBoxLayout()
        row2.addWidget(self.btn_save)
        row2.addWidget(self.btn_del)
        rlay.addLayout(row2)
        right.setMinimumWidth(440)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        lay = QVBoxLayout(self)
        lay.addWidget(splitter)

    # ---------------- 数据 ----------------
    def refresh(self) -> None:
        cats = self.service.list_categories(self.project_id)
        self.cat_list.clear()
        all_item = QListWidgetItem("全部")
        all_item.setData(0x0100, None)  # Qt.UserRole：None=不过滤分类
        self.cat_list.addItem(all_item)
        for cat in cats:
            item = QListWidgetItem(cat.name)
            item.setData(0x0100, cat.id)
            self.cat_list.addItem(item)
        self.cat_list.setCurrentRow(0)  # 默认选中"全部"

    def _on_category(self, current: QListWidgetItem | None, _prev=None) -> None:
        # current.data 为 None 时表示"全部"（不过滤）
        self._current_category_id = current.data(0x0100) if current else None
        self._load_entries()

    def _load_entries(self) -> None:
        # _current_category_id 为 None 时 list_entries 返回全部条目（含未分类）
        entries = self.service.list_entries(self.project_id, self._current_category_id)
        self.entry_list.clear()
        for e in entries:
            flag = "◆ " if e.canonical else ""
            item = QListWidgetItem(f"{flag}{e.title}  (重要{e.importance})")
            item.setData(0x0100, e.id)
            self.entry_list.addItem(item)
        if entries:
            self.entry_list.setCurrentRow(0)
        else:
            self._clear_form()

    def _on_entry(self, current: QListWidgetItem | None, _prev=None) -> None:
        if current is None:
            return
        self._current_entry_id = current.data(0x0100)
        e = self.service.get_entry(self._current_entry_id)
        if e is None:
            return
        self.title_edit.setText(e.title)
        self.tags_edit.setText(e.tags or "")
        self.content_edit.setPlainText(e.content)
        self.importance_spin.setValue(e.importance)
        self.canonical_check.setChecked(bool(e.canonical))

    def _clear_form(self) -> None:
        self._current_entry_id = None
        self.title_edit.clear()
        self.tags_edit.clear()
        self.content_edit.clear()
        self.importance_spin.setValue(3)
        self.canonical_check.setChecked(False)

    # ---------------- 操作 ----------------
    def _add_category(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "新建分类", "分类名称：")
        if ok and name.strip():
            self.service.add_category(self.project_id, name.strip())
            self.refresh()

    def _add_entry(self) -> None:
        self.service.create_entry(
            project_id=self.project_id,
            category_id=self._current_category_id,
            title="新设定",
            content="",
        )
        self._load_entries()

    def _save_entry(self) -> None:
        if self._current_entry_id is None:
            self.service.create_entry(
                project_id=self.project_id,
                category_id=self._current_category_id,
                title=self.title_edit.text().strip() or "未命名",
                content=self.content_edit.toPlainText().strip(),
                tags=self.tags_edit.text().strip(),
                importance=self.importance_spin.value(),
                canonical=self.canonical_check.isChecked(),
            )
        else:
            self.service.update_entry(
                self._current_entry_id,
                title=self.title_edit.text().strip() or "未命名",
                content=self.content_edit.toPlainText().strip(),
                tags=self.tags_edit.text().strip(),
                importance=self.importance_spin.value(),
                canonical=self.canonical_check.isChecked(),
            )
        self._load_entries()

    def _delete_entry(self) -> None:
        if self._current_entry_id is not None:
            self.service.delete_entry(self._current_entry_id)
            self._load_entries()

    # ---------------- AI 骨架 ----------------
    def _ai_skeleton(self) -> None:
        p = self.workspace.project
        if not (p.logline or "").strip():
            QMessageBox.information(self, "提示", "请先在「项目信息」中填写一句话梗概，AI 才能生成世界观。")
            return
        self.btn_skeleton.setEnabled(False)
        self.btn_skeleton.setText("生成中…")
        llm = self.workspace.llm_service
        coro = llm.generate_structured(
            project_id=self.project_id,
            module="world",
            prompt_key="task.world_skeleton",
            system_prompt=llm.load_prompt("global.base_prompt"),
            user_prompt=llm.load_prompt(
                "task.world_skeleton", project_id=self.project_id,
                genre=p.genre or "", logline=p.logline or "", idea="",
            ),
        )
        run_async(coro, on_done=self._on_skeleton_done, on_error=self._on_ai_error)

    def _on_skeleton_done(self, data: dict) -> None:
        self.btn_skeleton.setEnabled(True)
        self.btn_skeleton.setText("AI 生成骨架")
        if not isinstance(data, dict):
            return
        created = 0
        for cat in data.get("categories", []) or []:
            cat_name = cat.get("name", "")
            if not cat_name:
                continue
            # 复用同名分类
            cat_id = None
            for i in range(self.cat_list.count()):
                item = self.cat_list.item(i)
                if item.text() == cat_name:
                    cat_id = item.data(0x0100)
                    break
            if cat_id is None:
                c = self.service.add_category(self.project_id, cat_name)
                cat_id = c.id
            for entry in cat.get("entries", []) or []:
                self.service.create_entry(
                    project_id=self.project_id, category_id=cat_id,
                    title=entry.get("title", "未命名"),
                    content=entry.get("content", ""),
                    importance=int(entry.get("importance", 3)),
                )
                created += 1
        self.refresh()
        QMessageBox.information(self, "AI 骨架", f"已生成 {created} 条设定条目。")

    def _on_ai_error(self, exc: Exception) -> None:
        self.btn_skeleton.setEnabled(True)
        self.btn_skeleton.setText("AI 生成骨架")
        QMessageBox.warning(self, "AI 调用失败", str(exc))

"""点子视图：列表 + 编辑 + AI 扩展/评估（M1）。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from app.services.idea_service import IdeaService
from app.utils.async_utils import run_async

IDEA_STATUS = ["draft", "expanded", "selected", "discarded"]


class IdeaView(QWidget):
    def __init__(self, workspace, parent=None):
        super().__init__(parent)
        self.workspace = workspace
        self.service = IdeaService(workspace.db)
        self.project_id = workspace.project.id
        self._current_id: int | None = None

        splitter = QSplitter(self)

        # 左：列表
        left = QWidget()
        llay = QVBoxLayout(left)
        self.status_combo = QComboBox()
        self.status_combo.addItem("全部", "")
        for s in IDEA_STATUS:
            self.status_combo.addItem(s, s)
        self.status_combo.currentIndexChanged.connect(self.refresh)
        self.idea_list = QListWidget()
        self.idea_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.idea_list.currentItemChanged.connect(self._on_select)
        self.btn_new = QPushButton("新建点子")
        self.btn_del = QPushButton("删除")
        self.btn_merge = QPushButton("合并选中")
        self.btn_merge.setToolTip("选中 2~3 个点子，AI 融合成一个统一点子")
        self.btn_new.clicked.connect(self._new_idea)
        self.btn_del.clicked.connect(self._delete_idea)
        self.btn_merge.clicked.connect(self._merge_ideas)
        row = QHBoxLayout()
        row.addWidget(self.btn_new)
        row.addWidget(self.btn_del)
        row.addWidget(self.btn_merge)
        llay.addWidget(self.status_combo)
        llay.addWidget(self.idea_list)
        llay.addLayout(row)
        left.setMinimumWidth(260)

        # 右：编辑表单
        right = QWidget()
        rlay = QVBoxLayout(right)
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("标题")
        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("标签（逗号分隔）")
        self.summary_edit = QPlainTextEdit()
        self.summary_edit.setPlaceholderText("点子内容……")
        self.eval_label = QLabel("")
        self.eval_label.setWordWrap(True)
        self.eval_label.setStyleSheet("color:#666;")

        self.btn_save = QPushButton("保存")
        self.btn_save.clicked.connect(self._save)
        self.btn_expand = QPushButton("AI 扩展")
        self.btn_expand.clicked.connect(self._ai_expand)
        self.btn_eval = QPushButton("AI 评估")
        self.btn_eval.clicked.connect(self._ai_evaluate)
        btn_row = QHBoxLayout()
        btn_row.addWidget(self.btn_save)
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_expand)
        btn_row.addWidget(self.btn_eval)

        rlay.addWidget(QLabel("标题"))
        rlay.addWidget(self.title_edit)
        rlay.addWidget(QLabel("标签"))
        rlay.addWidget(self.tags_edit)
        rlay.addWidget(QLabel("内容"))
        rlay.addWidget(self.summary_edit)
        rlay.addWidget(self.eval_label)
        rlay.addLayout(btn_row)
        right.setMinimumWidth(420)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        lay = QVBoxLayout(self)
        lay.addWidget(splitter)

    # ---------------- 数据 ----------------
    def refresh(self) -> None:
        status = self.status_combo.currentData() or None
        ideas = self.service.list(self.project_id, status)
        self.idea_list.clear()
        for idea in ideas:
            item = QListWidgetItem(f"{idea.title or '(无标题)'}  [{idea.status}]")
            item.setData(0x0100, idea.id)  # Qt.UserRole
            self.idea_list.addItem(item)
        if ideas:
            self.idea_list.setCurrentRow(0)
        else:
            self._clear_form()

    def _on_select(self, current: QListWidgetItem | None, _prev=None) -> None:
        if current is None:
            return
        self._current_id = current.data(0x0100)
        idea = self.service.get(self._current_id)
        if idea is None:
            return
        self.title_edit.setText(idea.title or "")
        self.tags_edit.setText(idea.tags or "")
        self.summary_edit.setPlainText(idea.summary or "")
        self.eval_label.setText(idea.eval_json and f"评估：{idea.eval_json}" or "")

    def _clear_form(self) -> None:
        self._current_id = None
        self.title_edit.clear()
        self.tags_edit.clear()
        self.summary_edit.clear()
        self.eval_label.clear()

    # ---------------- 操作 ----------------
    def _new_idea(self) -> None:
        idea = self.service.create(project_id=self.project_id, title="新点子")
        self.refresh()
        for i in range(self.idea_list.count()):
            if self.idea_list.item(i).data(0x0100) == idea.id:
                self.idea_list.setCurrentRow(i)
                break
        self.title_edit.setFocus()

    def _save(self) -> None:
        if self._current_id is None:
            idea = self.service.create(
                project_id=self.project_id,
                title=self.title_edit.text().strip(),
                tags=self.tags_edit.text().strip(),
                summary=self.summary_edit.toPlainText().strip(),
            )
            self._current_id = idea.id
        else:
            self.service.update(
                self._current_id,
                title=self.title_edit.text().strip(),
                tags=self.tags_edit.text().strip(),
                summary=self.summary_edit.toPlainText().strip(),
            )
        self.refresh()

    def _delete_idea(self) -> None:
        if self._current_id is None:
            return
        self.service.delete(self._current_id)
        self.refresh()

    def _merge_ideas(self) -> None:
        selected = self.idea_list.selectedItems()
        if len(selected) < 2:
            QMessageBox.information(self, "合并点子", "请按住 Ctrl 选中 2~3 个点子。")
            return
        ideas = [self.service.get(it.data(0x0100)) for it in selected]
        ideas = [i for i in ideas if i is not None]
        text = "\n\n".join(f"点子「{i.title or '(无标题)'}」：{i.summary or ''}" for i in ideas)
        llm = self.workspace.llm_service
        self.btn_merge.setEnabled(False)
        self.btn_merge.setText("合并中…")
        coro = llm.generate_structured(
            project_id=self.project_id, module="idea", prompt_key="task.idea_merge",
            system_prompt=llm.load_prompt("global.base_prompt"),
            user_prompt=llm.load_prompt(
                "task.idea_merge", project_id=self.project_id, ideas=text,
            ),
        )
        run_async(coro, on_done=self._on_merge_done, on_error=self._on_ai_error)

    def _on_merge_done(self, data: dict) -> None:
        self.btn_merge.setEnabled(True)
        self.btn_merge.setText("合并选中")
        if not isinstance(data, dict):
            return
        merged = self.service.create(
            project_id=self.project_id,
            title=data.get("title", "合并点子"),
            summary=(data.get("merged_summary", "")
                     + (f"\n\n【核心冲突】{data.get('conflict', '')}" if data.get("conflict") else "")),
            status="expanded",
        )
        self.refresh()
        QMessageBox.information(self, "合并点子", f"已生成合并点子「{merged.title}」。")

    # ---------------- AI ----------------
    def _ai_expand(self) -> None:
        idea_text = self.summary_edit.toPlainText().strip() or self.title_edit.text().strip()
        if not idea_text:
            QMessageBox.information(self, "提示", "请先填写点子内容。")
            return
        self.btn_expand.setEnabled(False)
        self.btn_expand.setText("扩展中…")
        llm = self.workspace.llm_service
        coro = llm.generate_structured(
            project_id=self.project_id,
            module="idea",
            prompt_key="task.idea_expand",
            system_prompt=llm.load_prompt("global.base_prompt"),
            user_prompt=llm.load_prompt(
                "task.idea_expand", project_id=self.project_id,
                idea=idea_text, genre=self.workspace.project.genre or "",
            ),
        )
        run_async(coro, on_done=self._on_expand_done, on_error=self._on_ai_error)

    def _on_expand_done(self, data: dict) -> None:
        self.btn_expand.setEnabled(True)
        self.btn_expand.setText("AI 扩展")
        if not isinstance(data, dict):
            return
        background = data.get("background", "")
        conflict = data.get("conflict", "")
        selling = "；".join(data.get("selling_points", []) or [])
        merged = "\n".join(
            x for x in (
                self.summary_edit.toPlainText().strip(),
                f"【背景】{background}" if background else "",
                f"【核心冲突】{conflict}" if conflict else "",
                f"【卖点】{selling}" if selling else "",
                f"【世界观方向】{'；'.join(data.get('world_directions', []) or [])}",
                f"【角色方向】{'；'.join(data.get('character_directions', []) or [])}",
            ) if x
        )
        self.summary_edit.setPlainText(merged)
        if self._current_id:
            self.service.update(self._current_id, summary=merged, status="expanded")
        QMessageBox.information(self, "AI 扩展", "扩展结果已填入内容区，请审阅后点「保存」。")

    def _ai_evaluate(self) -> None:
        idea_text = self.summary_edit.toPlainText().strip() or self.title_edit.text().strip()
        if not idea_text:
            QMessageBox.information(self, "提示", "请先填写点子内容。")
            return
        self.btn_eval.setEnabled(False)
        self.btn_eval.setText("评估中…")
        llm = self.workspace.llm_service
        coro = llm.generate_structured(
            project_id=self.project_id,
            module="idea",
            prompt_key="task.idea_evaluate",
            system_prompt=llm.load_prompt("global.base_prompt"),
            user_prompt=llm.load_prompt(
                "task.idea_evaluate", project_id=self.project_id, idea=idea_text,
            ),
        )
        run_async(coro, on_done=self._on_eval_done, on_error=self._on_ai_error)

    def _on_eval_done(self, data: dict) -> None:
        self.btn_eval.setEnabled(True)
        self.btn_eval.setText("AI 评估")
        scores = data.get("scores", {}) if isinstance(data, dict) else {}
        text = (
            f"新颖度 {scores.get('novelty', '-')} | 可扩展性 {scores.get('expandability', '-')} "
            f"| 市场 {scores.get('market', '-')} | 长篇潜力 {scores.get('long_form', '-')}\n"
            f"{data.get('comments', '')}"
        )
        self.eval_label.setText(text)
        if self._current_id:
            self.service.update(self._current_id, eval_json=data)

    def _on_ai_error(self, exc: Exception) -> None:
        self.btn_expand.setEnabled(True)
        self.btn_expand.setText("AI 扩展")
        self.btn_eval.setEnabled(True)
        self.btn_eval.setText("AI 评估")
        QMessageBox.warning(self, "AI 调用失败", str(exc))

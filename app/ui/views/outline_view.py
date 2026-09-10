"""大纲视图：卷（Arc）+ 情节（Plot）两级管理 + AI 生成卷级大纲（M1）。

层级：卷大纲 → 情节（一个情节跨多章）→ 章节细纲。
右侧用标签页收纳「卷信息 / 情节」，避免表单堆叠挤压显示空间。
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.services.outline_service import OutlineService
from app.services.plot_service import PlotService
from app.services.world_service import WorldService
from app.utils.async_utils import run_async


class OutlineView(QWidget):
    def __init__(self, workspace, parent=None):
        super().__init__(parent)
        self.workspace = workspace
        self.service = OutlineService(workspace.db)
        self.plot_service = PlotService(workspace.db)
        self.project_id = workspace.project.id
        self._current_id: int | None = None      # 当前卷
        self._current_plot_id: int | None = None  # 当前情节

        splitter = QSplitter(self)

        left = QWidget()
        llay = QVBoxLayout(left)
        self.arc_list = QListWidget()
        self.arc_list.currentItemChanged.connect(self._on_select)
        self.btn_new = QPushButton("新建卷")
        self.btn_new.clicked.connect(self._new)
        self.btn_del = QPushButton("删除")
        self.btn_del.clicked.connect(self._delete)
        row = QHBoxLayout()
        row.addWidget(self.btn_new)
        row.addWidget(self.btn_del)
        llay.addWidget(self.arc_list)
        llay.addLayout(row)
        left.setMinimumWidth(240)

        # 右：标签页（卷信息 / 情节）——避免长表单把显示空间挤没
        self.tabs = QTabWidget()
        self._build_arc_tab()
        self._build_plot_tab()

        splitter.addWidget(left)
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(1, 1)
        lay = QVBoxLayout(self)
        lay.addWidget(splitter)

    # ---------------- 标签页：卷信息 ----------------
    def _build_arc_tab(self) -> None:
        tab = QWidget()
        rlay = QVBoxLayout(tab)
        # 标题单行收纳（标签与输入框同一行，不占整行高度）
        title_row = QHBoxLayout()
        title_row.addWidget(QLabel("标题"))
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("卷标题，如：第一卷 · 觉醒")
        title_row.addWidget(self.title_edit, 1)
        rlay.addLayout(title_row)

        rlay.addWidget(QLabel("目标"))
        self.goal_edit = QPlainTextEdit()
        self.goal_edit.setPlaceholderText("本卷目标")
        self.goal_edit.setMaximumHeight(56)
        rlay.addWidget(self.goal_edit)
        rlay.addWidget(QLabel("核心冲突"))
        self.conflict_edit = QPlainTextEdit()
        self.conflict_edit.setPlaceholderText("核心冲突")
        self.conflict_edit.setMaximumHeight(56)
        rlay.addWidget(self.conflict_edit)
        rlay.addWidget(QLabel("收束"))
        self.resolution_edit = QPlainTextEdit()
        self.resolution_edit.setPlaceholderText("收束方式 / 卷末钩子")
        self.resolution_edit.setMaximumHeight(56)
        rlay.addWidget(self.resolution_edit)
        rlay.addWidget(QLabel("摘要"))
        self.summary_edit = QPlainTextEdit()
        self.summary_edit.setPlaceholderText("卷摘要（可选）")
        rlay.addWidget(self.summary_edit, 1)  # 摘要吃掉剩余空间

        self.btn_save = QPushButton("保存")
        self.btn_save.clicked.connect(self._save)
        self.btn_ai = QPushButton("AI 生成大纲")
        self.btn_ai.setObjectName("ai_button")
        self.btn_ai.clicked.connect(self._ai_generate)
        self.btn_eval = QPushButton("AI 评估爽点")
        self.btn_eval.setObjectName("ai_button")
        self.btn_eval.setToolTip("评估各卷的爽点/泪点/悬念密度并给出调整建议")
        self.btn_eval.clicked.connect(self._ai_evaluate)
        row2 = QHBoxLayout()
        row2.addWidget(self.btn_save)
        row2.addStretch(1)
        row2.addWidget(self.btn_eval)
        row2.addWidget(self.btn_ai)
        rlay.addLayout(row2)
        tab.setMinimumWidth(460)
        self.tabs.addTab(tab, "卷信息")

    # ---------------- 标签页：情节 ----------------
    def _build_plot_tab(self) -> None:
        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.addWidget(QLabel("当前卷的情节线（一个情节跨多个章节；AI 拆章时统一生成）"))

        splitter2 = QSplitter()
        pl = QWidget()
        pll = QVBoxLayout(pl)
        self.plot_list = QListWidget()
        self.plot_list.currentItemChanged.connect(self._on_plot_select)
        pll.addWidget(self.plot_list)
        prow = QHBoxLayout()
        self.btn_plot_new = QPushButton("新建情节")
        self.btn_plot_new.clicked.connect(self._new_plot)
        self.btn_plot_del = QPushButton("删除")
        self.btn_plot_del.clicked.connect(self._delete_plot)
        prow.addWidget(self.btn_plot_new)
        prow.addWidget(self.btn_plot_del)
        pll.addLayout(prow)
        splitter2.addWidget(pl)

        pr = QWidget()
        prl = QVBoxLayout(pr)
        ptitle_row = QHBoxLayout()
        ptitle_row.addWidget(QLabel("标题"))
        self.plot_title = QLineEdit()
        self.plot_title.setPlaceholderText("情节名，如：初到灰石领")
        ptitle_row.addWidget(self.plot_title, 1)
        prl.addLayout(ptitle_row)
        prl.addWidget(QLabel("核心冲突"))
        self.plot_conflict = QPlainTextEdit()
        self.plot_conflict.setMaximumHeight(56)
        prl.addWidget(self.plot_conflict)
        prl.addWidget(QLabel("收束/钩子"))
        self.plot_resolution = QPlainTextEdit()
        self.plot_resolution.setMaximumHeight(56)
        prl.addWidget(self.plot_resolution)
        prl.addWidget(QLabel("概要"))
        self.plot_summary = QPlainTextEdit()
        self.plot_summary.setPlaceholderText("情节概要：发生什么、谁参与、如何推进")
        prl.addWidget(self.plot_summary, 1)
        # 情节节拍线（跨章粗粒度推进点，一行一条，可直接手改）
        prl.addWidget(QLabel("情节节拍线（一行一条：阶段+事件，如「激化：与地头蛇冲突」；随「保存情节」入库）"))
        self.plot_beats = QPlainTextEdit()
        self.plot_beats.setPlaceholderText("开端：……\n激化：……\n爆发：……\n收束：……")
        prl.addWidget(self.plot_beats, 1)
        self.btn_plot_save = QPushButton("保存情节")
        self.btn_plot_save.clicked.connect(self._save_plot)
        prl.addWidget(self.btn_plot_save)
        splitter2.addWidget(pr)
        splitter2.setStretchFactor(1, 2)

        lay.addWidget(splitter2, 1)
        self.tabs.addTab(tab, "情节")

    # ---------------- 数据 ----------------
    def refresh(self) -> None:
        arcs = self.service.list(self.project_id)
        self.arc_list.clear()
        for a in arcs:
            item = QListWidgetItem(f"第{a.seq + 1}卷 · {a.title or '(未命名)'}")
            item.setData(0x0100, a.id)
            self.arc_list.addItem(item)
        if arcs:
            self.arc_list.setCurrentRow(0)
        else:
            self._clear_form()

    def _refresh_plots(self) -> None:
        self.plot_list.clear()
        if self._current_id is None:
            return
        for p in self.plot_service.list_by_arc(self._current_id):
            item = QListWidgetItem(f"情节{p.seq + 1} · {p.title or '(未命名)'}")
            item.setData(0x0100, p.id)
            self.plot_list.addItem(item)

    def _on_select(self, current: QListWidgetItem | None, _prev=None) -> None:
        if current is None:
            return
        self._current_id = current.data(0x0100)
        a = self.service.get(self._current_id)
        if a is None:
            return
        self.title_edit.setText(a.title or "")
        self.goal_edit.setPlainText(a.goal or "")
        self.conflict_edit.setPlainText(a.conflict or "")
        self.resolution_edit.setPlainText(a.resolution or "")
        self.summary_edit.setPlainText(a.summary or "")
        self._current_plot_id = None
        self._refresh_plots()

    def _clear_form(self) -> None:
        self._current_id = None
        self.title_edit.clear()
        for w in (self.goal_edit, self.conflict_edit, self.resolution_edit, self.summary_edit):
            w.clear()
        self.plot_list.clear()

    # ---------------- 卷操作 ----------------
    def _new(self) -> None:
        a = self.service.create(project_id=self.project_id, title="新卷")
        self.refresh()
        for i in range(self.arc_list.count()):
            if self.arc_list.item(i).data(0x0100) == a.id:
                self.arc_list.setCurrentRow(i)
                break
        self.title_edit.setFocus()

    def _save(self) -> None:
        fields = {
            "title": self.title_edit.text().strip(),
            "goal": self.goal_edit.toPlainText().strip(),
            "conflict": self.conflict_edit.toPlainText().strip(),
            "resolution": self.resolution_edit.toPlainText().strip(),
            "summary": self.summary_edit.toPlainText().strip(),
        }
        if self._current_id is None:
            a = self.service.create(project_id=self.project_id, **fields)
            self._current_id = a.id
        else:
            self.service.update(self._current_id, **fields)
        self.refresh()

    def _delete(self) -> None:
        if self._current_id is not None:
            self.service.delete(self._current_id)
            self.refresh()

    # ---------------- 情节操作 ----------------
    def _on_plot_select(self, current: QListWidgetItem | None, _prev=None) -> None:
        if current is None:
            return
        self._current_plot_id = current.data(0x0100)
        p = self.plot_service.get(self._current_plot_id)
        if p is None:
            return
        self.plot_title.setText(p.title or "")
        self.plot_summary.setPlainText(p.summary or "")
        self.plot_conflict.setPlainText(p.conflict or "")
        self.plot_resolution.setPlainText(p.resolution or "")
        # 节拍线：一行一条
        beats = p.beats_json or []
        self.plot_beats.setPlainText("\n".join(
            b.get("event", "") if isinstance(b, dict) else str(b) for b in beats))

    def _plot_beats_from_edit(self) -> list[dict]:
        """把节拍编辑框的每行文本解析为 [{event}]（忽略空行，剥掉行首序号）。"""
        import re
        beats = []
        for line in self.plot_beats.toPlainText().splitlines():
            line = re.sub(r"^\s*\d+[.、．]?\s*", "", line).strip()
            if line:
                beats.append({"event": line})
        return beats

    def _new_plot(self) -> None:
        if self._current_id is None:
            QMessageBox.information(self, "提示", "请先选择/创建一个卷。")
            return
        p = self.plot_service.create(
            project_id=self.project_id, arc_id=self._current_id, title="新情节")
        self._refresh_plots()
        for i in range(self.plot_list.count()):
            if self.plot_list.item(i).data(0x0100) == p.id:
                self.plot_list.setCurrentRow(i)
                break
        self.plot_title.setFocus()

    def _save_plot(self) -> None:
        fields = {
            "title": self.plot_title.text().strip(),
            "summary": self.plot_summary.toPlainText().strip(),
            "conflict": self.plot_conflict.toPlainText().strip(),
            "resolution": self.plot_resolution.toPlainText().strip(),
            "beats_json": self._plot_beats_from_edit(),
        }
        if self._current_plot_id is None:
            if self._current_id is None:
                QMessageBox.information(self, "提示", "请先选择/创建一个卷。")
                return
            p = self.plot_service.create(
                project_id=self.project_id, arc_id=self._current_id, **fields)
            self._current_plot_id = p.id
        else:
            self.plot_service.update(self._current_plot_id, **fields)
        self._refresh_plots()

    def _delete_plot(self) -> None:
        if self._current_plot_id is not None:
            self.plot_service.delete(self._current_plot_id)  # 章节保留但脱离情节
            self._current_plot_id = None
            self._refresh_plots()

    # ---------------- AI ----------------
    def _ai_generate(self) -> None:
        p = self.workspace.project
        llm = self.workspace.llm_service
        entries = WorldService(self.workspace.db).list_entries(self.project_id, None)
        world_text = "\n".join(f"- {e.title}: {e.content[:80]}" for e in entries[:12]) or "（暂无设定）"
        from app.services.character_service import CharacterService
        chars = CharacterService(self.workspace.db).list(self.project_id)
        cast_text = "\n".join(f"- {c.name}（{c.role_type or '?'}）" for c in chars[:8]) or "（暂无角色）"

        self.btn_ai.setEnabled(False)
        self.btn_ai.setText("生成中…")
        coro = llm.generate_structured(
            project_id=self.project_id,
            module="outline",
            prompt_key="task.outline_generate",
            system_prompt=llm.load_prompt("global.base_prompt"),
            user_prompt=llm.load_prompt(
                "task.outline_generate", project_id=self.project_id,
                theme=p.theme or p.logline or "", style_guide=p.style_guide or "",
                world_entries=world_text, cast_cards=cast_text,
            ),
        )
        run_async(coro, on_done=self._on_ai_done, on_error=self._on_ai_error)

    def _on_ai_done(self, data: dict) -> None:
        self.btn_ai.setEnabled(True)
        self.btn_ai.setText("AI 生成大纲")
        if not isinstance(data, dict):
            return
        arcs = data.get("arcs") or []
        if not arcs:
            QMessageBox.warning(self, "AI 大纲", "模型未返回卷列表。")
            return
        existing = self.service.list(self.project_id)
        if existing:
            ret = QMessageBox.question(
                self, "AI 大纲", f"当前已有 {len(existing)} 个卷。追加生成的新卷？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ret != QMessageBox.StandardButton.Yes:
                return
        for arc in arcs:
            self.service.create(
                project_id=self.project_id,
                title=arc.get("title", ""),
                goal=arc.get("goal", ""),
                conflict=arc.get("conflict", ""),
                resolution=arc.get("resolution", ""),
            )
        self.refresh()
        QMessageBox.information(self, "AI 大纲", f"已生成 {len(arcs)} 个卷，请逐卷审阅调整。")

    def _on_ai_error(self, exc: Exception) -> None:
        self.btn_ai.setEnabled(True)
        self.btn_ai.setText("AI 生成大纲")
        self.btn_eval.setEnabled(True)
        self.btn_eval.setText("AI 评估爽点")
        QMessageBox.warning(self, "AI 调用失败", str(exc))

    # ---------------- AI 评估爽点（3.5） ----------------
    def _ai_evaluate(self) -> None:
        arcs = self.service.list(self.project_id)
        if not arcs:
            QMessageBox.information(self, "评估爽点", "请先创建卷。")
            return
        llm = self.workspace.llm_service
        arcs_text = "\n".join(
            f"第{a.seq + 1}卷「{a.title or ''}」：目标={a.goal or ''}；冲突={a.conflict or ''}；收束={a.resolution or ''}"
            for a in arcs
        )
        self.btn_eval.setEnabled(False)
        self.btn_eval.setText("评估中…")
        coro = llm.generate_structured(
            project_id=self.project_id, module="outline", prompt_key="task.outline_evaluate",
            system_prompt=llm.load_prompt("global.base_prompt"),
            user_prompt=llm.load_prompt(
                "task.outline_evaluate", project_id=self.project_id, arcs=arcs_text,
            ),
        )
        run_async(coro, on_done=self._on_evaluate_done, on_error=self._on_ai_error)

    def _on_evaluate_done(self, data: dict) -> None:
        self.btn_eval.setEnabled(True)
        self.btn_eval.setText("AI 评估爽点")
        if not isinstance(data, dict):
            return
        lines = []
        for a in data.get("arcs") or []:
            lines.append(f"「{a.get('title', '')}」强度 {a.get('score', '-')}/10：{a.get('suggestion', '')}")
        overall = data.get("overall", "")
        text = "\n".join(lines) + (f"\n\n总体评价：{overall}" if overall else "")
        QMessageBox.information(self, "大纲爽点评估", text or "模型未返回评估。")

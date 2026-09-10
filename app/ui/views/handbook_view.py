"""教程知识库视图（M4）：导入 / 启用 / 删除 / 查看内容。"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
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

from app.services.handbook_service import CATEGORIES, HandbookService


class HandbookView(QWidget):
    def __init__(self, workspace, parent=None):
        super().__init__(parent)
        self.workspace = workspace
        self.service = HandbookService(workspace.db)
        self.project_id = workspace.project.id

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            "写作教程知识库（正文生成时自动检索注入【写作技巧参考】；"
            "公用=所有小说可用，本项目=仅当前小说；公用教程可「激活到本项目」单独调整）"))

        self.doc_list = QListWidget()
        self.doc_list.itemDoubleClicked.connect(self._view_doc)
        lay.addWidget(self.doc_list)

        row = QHBoxLayout()
        self.btn_view = QPushButton("查看内容")
        self.btn_view.clicked.connect(self._view_current)
        self.btn_import = QPushButton("导入教程文本…")
        self.btn_import.clicked.connect(self._import)
        self.btn_rechunk = QPushButton("重新提炼/切片…")
        self.btn_rechunk.clicked.connect(self._rechunk)
        self.btn_toggle = QPushButton("启用/停用")
        self.btn_toggle.clicked.connect(self._toggle)
        self.btn_delete = QPushButton("删除")
        self.btn_delete.clicked.connect(self._delete)
        row.addWidget(self.btn_view)
        row.addWidget(self.btn_import)
        row.addWidget(self.btn_rechunk)
        row.addWidget(self.btn_toggle)
        row.addWidget(self.btn_delete)
        lay.addLayout(row)

        row2 = QHBoxLayout()
        self.btn_scope = QPushButton("设为公用/本项目")
        self.btn_scope.clicked.connect(self._switch_scope)
        self.btn_activate = QPushButton("激活到本项目")
        self.btn_activate.clicked.connect(self._activate_to_project)
        row2.addWidget(self.btn_scope)
        row2.addWidget(self.btn_activate)
        row2.addStretch(1)
        lay.addLayout(row2)

    def refresh(self) -> None:
        docs = self.service.list_docs(self.project_id)
        self.doc_list.clear()
        if not docs:
            self.doc_list.addItem("（暂无教程——点「导入教程文本…」粘贴写作指南）")
            return
        for d in docs:
            scope = "公用" if d.project_id is None else "本项目"
            state = "启用" if d.enabled else "停用"
            self.doc_list.addItem(
                QListWidgetItem(f"[{scope}][{d.category or '未分类'}][{state}] {d.title}")
            )
            self.doc_list.item(self.doc_list.count() - 1).setData(0x0100, d.id)

    def _import(self) -> None:
        title, ok = QInputDialog.getText(self, "导入教程", "教程标题：")
        if not ok or not title.strip():
            return
        cat, ok2 = QInputDialog.getItem(self, "导入教程", "分类：", CATEGORIES, 0, False)
        if not ok2:
            return
        scope, ok_scope = QInputDialog.getItem(
            self, "导入教程", "归属（公用=所有小说可用；本项目=仅当前小说）：",
            ["公用（所有小说可用）", "仅本项目"], 0, False,
        )
        if not ok_scope:
            return
        scope_project_id = None if scope.startswith("公用") else self.project_id
        content, ok3 = QInputDialog.getMultiLineText(
            self, "导入教程", "粘贴教程内容（默认用 AI 提炼为写作要点，注入更精准）：", ""
        )
        if not ok3 or not content.strip():
            return
        # 询问是否用 AI 提炼
        from PySide6.QtWidgets import QMessageBox as _QMB
        ret = _QMB.question(
            self, "导入教程",
            "是否用 AI 提炼为写作要点？\n\n「是」：提炼要点清单（正文生成时注入更精准，推荐）\n「否」：按原文切片（保留完整原文）",
            _QMB.StandardButton.Yes | _QMB.StandardButton.No,
        )
        if ret == _QMB.StandardButton.Yes:
            self.btn_import.setEnabled(False)
            self.btn_import.setText("提炼中…")

            async def run():
                return await self.service.import_with_extract(
                    title.strip(), cat, content.strip(), scope_project_id,
                    self.workspace.llm_service,
                )

            def done(doc):
                self.btn_import.setEnabled(True)
                self.btn_import.setText("导入教程文本…")
                self.refresh()
                self.status_msg(f"已提炼导入「{doc.title}」")

            from app.utils.async_utils import run_async
            run_async(run(), on_done=done, on_error=self._on_import_error)
        else:
            doc = self.service.import_text(title.strip(), cat, content.strip(), scope_project_id)
            self.refresh()
            self.status_msg(f"已导入教程「{doc.title}」（原文切片）")

    def _on_import_error(self, exc: Exception) -> None:
        self.btn_import.setEnabled(True)
        self.btn_import.setText("导入教程文本…")
        QMessageBox.warning(self, "提炼失败", str(exc))

    # ---------------- 公用库 / 本项目 ----------------
    def _current_doc(self):
        item = self.doc_list.currentItem()
        if item is None or item.data(0x0100) is None:
            return None
        return self.service.get_doc(item.data(0x0100))

    def _switch_scope(self) -> None:
        """公用 ↔ 本项目 互转（移动归属，不复制）。"""
        doc = self._current_doc()
        if doc is None:
            self.status_msg("请先选中一个教程。")
            return
        if doc.project_id is None:
            self.service.set_scope(doc.id, self.project_id)
            self.status_msg(f"「{doc.title}」已转为本项目教程（其他小说不再可见）")
        else:
            self.service.set_scope(doc.id, None)
            self.status_msg(f"「{doc.title}」已设为公用（所有小说可用）")
        self.refresh()

    def _activate_to_project(self) -> None:
        """把公用教程复制一份到本项目（副本可独立启停/删除，不影响公用原件）。"""
        doc = self._current_doc()
        if doc is None:
            self.status_msg("请先选中一个教程。")
            return
        if doc.project_id is not None:
            self.status_msg("该教程已属于本项目（只有公用教程需要激活）。")
            return
        existing = [d for d in self.service.list_docs(self.project_id)
                    if d.project_id == self.project_id and d.title == doc.title]
        if existing:
            self.status_msg(f"「{doc.title}」已在本项目中，无需重复激活。")
            return
        copy = self.service.copy_to_project(doc.id, self.project_id)
        self.refresh()
        self.status_msg(f"已激活「{doc.title}」到本项目（副本可单独停用/调整）")

    # ---------------- 重新提炼/切片（已导入教程的切片质量升级） ----------------
    def _rechunk(self) -> None:
        item = self.doc_list.currentItem()
        if item is None or item.data(0x0100) is None:
            self.status_msg("请先选中一个教程。")
            return
        doc_id = item.data(0x0100)
        from PySide6.QtWidgets import QMessageBox as _QMB
        ret = _QMB.question(
            self, "重新提炼/切片",
            "用哪种方式重建该教程的切片？（原文不变）\n\n"
            "「是」：AI 重新提炼要点（逐条带分类与检索标题，推荐）\n"
            "「否」：按原文重新切片（标题感知，保留完整原文）",
            _QMB.StandardButton.Yes | _QMB.StandardButton.No | _QMB.StandardButton.Cancel,
        )
        if ret == _QMB.StandardButton.Cancel:
            return
        extract = ret == _QMB.StandardButton.Yes
        self.btn_rechunk.setEnabled(False)
        self.btn_rechunk.setText("重建切片中…")

        async def run():
            await self.service.rechunk_doc(
                doc_id, llm=self.workspace.llm_service if extract else None,
                extract=extract,
            )
            return self.service.list_chunks(doc_id)

        def done(chunks) -> None:
            self.btn_rechunk.setEnabled(True)
            self.btn_rechunk.setText("重新提炼/切片…")
            self.refresh()
            self.status_msg(f"切片已重建（{len(chunks)} 块）——双击教程可查看。")

        def err(exc: Exception) -> None:
            self.btn_rechunk.setEnabled(True)
            self.btn_rechunk.setText("重新提炼/切片…")
            QMessageBox.warning(self, "重建切片失败", str(exc))

        from app.utils.async_utils import run_async
        run_async(run(), on_done=done, on_error=err)

    def _toggle(self) -> None:
        item = self.doc_list.currentItem()
        if item is None:
            return
        doc_id = item.data(0x0100)
        if doc_id is not None:
            # 读取当前状态取反
            docs = self.service.list_docs(self.project_id)
            doc = next((d for d in docs if d.id == doc_id), None)
            if doc:
                self.service.set_enabled(doc_id, not doc.enabled)
                self.refresh()

    def _delete(self) -> None:
        item = self.doc_list.currentItem()
        if item is None:
            return
        doc_id = item.data(0x0100)
        if doc_id is not None:
            self.service.delete_doc(doc_id)
            self.refresh()

    def status_msg(self, text: str) -> None:
        mw = getattr(self.workspace, "main_window", None)
        if mw is not None:
            mw.status.showMessage(text)

    # ---------------- 查看内容 ----------------
    def _view_current(self) -> None:
        self._view_doc(self.doc_list.currentItem())

    def _view_doc(self, item) -> None:
        if item is None:
            return
        doc_id = item.data(0x0100)
        if doc_id is None:
            return
        doc = self.service.get_doc(doc_id)
        if doc is None:
            return
        chunks = self.service.list_chunks(doc_id)
        dlg = _DocViewDialog(doc, chunks, self)
        dlg.exec()


class _DocViewDialog(QDialog):
    """教程内容查看对话框：原文 + 切片（逐个预览）。"""

    def __init__(self, doc, chunks: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"教程：{doc.title}")
        self.setMinimumSize(640, 520)

        lay = QVBoxLayout(self)
        scope = "公用" if doc.project_id is None else "本项目"
        state = "启用" if doc.enabled else "停用"
        info = QLabel(
            f"标题：{doc.title}　分类：{doc.category or '未分类'}　范围：{scope}　状态：{state}　切片数：{len(chunks)}"
        )
        info.setStyleSheet("color:#666;")
        lay.addWidget(info)

        self.tabs = QTabWidget()
        # tab1：原文（完整内容）
        self.full_view = QPlainTextEdit()
        self.full_view.setReadOnly(True)
        self.full_view.setPlainText(doc.content or "")
        self.tabs.addTab(self.full_view, "原文")

        # tab2：切片（点击逐个预览）
        chunks_tab = QWidget()
        cl = QVBoxLayout(chunks_tab)
        self.chunk_list = QListWidget()
        for i, c in enumerate(chunks):
            cat = f"[{c.category}] " if getattr(c, "category", None) else ""
            self.chunk_list.addItem(QListWidgetItem(f"{i + 1}. {cat}{c.title or c.content[:30]}"))
            self.chunk_list.item(i).setData(0x0100, i)
        self.chunk_list.currentItemChanged.connect(self._on_chunk)
        self.chunk_view = QPlainTextEdit()
        self.chunk_view.setReadOnly(True)
        splitter = QSplitter()
        splitter.addWidget(self.chunk_list)
        splitter.addWidget(self.chunk_view)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        cl.addWidget(splitter)
        self.tabs.addTab(chunks_tab, f"切片（{len(chunks)}）")

        lay.addWidget(self.tabs)
        self._chunks = chunks
        if chunks:
            self.chunk_list.setCurrentRow(0)

    def _on_chunk(self, current, _prev=None) -> None:
        if current is None:
            return
        idx = current.data(0x0100)
        if idx is not None and idx < len(self._chunks):
            self.chunk_view.setPlainText(self._chunks[idx].content or "")

"""修订检查视图（M4）：一致性扫描（六类问题）+ 设定沉淀候选。"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.services.canon_capture_service import CanonCaptureService
from app.services.check_service import CheckService
from app.services.chapter_service import ChapterService
from app.utils.async_utils import run_async

TYPE_LABEL = {
    "timeline": "时间线", "setting": "设定冲突", "character": "角色行为",
    "memory_conflict": "记忆冲突", "knowledge_leak": "知识泄漏", "promise_break": "承诺未回收",
}


class RevisionView(QWidget):
    def __init__(self, workspace, parent=None):
        super().__init__(parent)
        self.workspace = workspace
        self.project_id = workspace.project.id
        self.check = CheckService(workspace.db, workspace.llm_service)
        self.canon = CanonCaptureService(workspace.db, workspace.llm_service)
        self.chapter_svc = ChapterService(workspace.db)

        self.tabs = QTabWidget()
        self._build_scan_tab()
        self._build_canon_tab()
        lay = QVBoxLayout(self)
        lay.addWidget(self.tabs)

    # ---------------- 一致性扫描 ----------------
    def _build_scan_tab(self) -> None:
        tab = QWidget()
        lay = QVBoxLayout(tab)
        self.scan_progress = QProgressBar()
        self.scan_progress.setVisible(False)
        self.btn_scan = QPushButton("开始全局扫描（六类问题）")
        self.btn_scan.clicked.connect(self._scan)
        self.filter_combo = QPushButton("全部问题")
        lay.addWidget(self.btn_scan)
        lay.addWidget(self.scan_progress)
        self.issue_list = QListWidget()
        self.issue_list.itemDoubleClicked.connect(self._apply_issue)
        lay.addWidget(QLabel("问题清单（双击=标记已处理并复制建议；右键状态操作在下方按钮）"))
        lay.addWidget(self.issue_list)
        row = QHBoxLayout()
        self.btn_resolve = QPushButton("标记已处理")
        self.btn_resolve.clicked.connect(lambda: self._set_status("resolved"))
        self.btn_ignore = QPushButton("忽略")
        self.btn_ignore.clicked.connect(lambda: self._set_status("ignored"))
        row.addWidget(self.btn_resolve)
        row.addWidget(self.btn_ignore)
        lay.addLayout(row)
        self.tabs.addTab(tab, "一致性扫描")

    def refresh(self) -> None:
        self._load_issues()

    def _load_issues(self) -> None:
        issues = self.check.list_issues(self.project_id, "pending")
        self.issue_list.clear()
        if not issues:
            self.issue_list.addItem("（暂无待处理问题——点击「开始全局扫描」）")
            return
        # chapter_id 是数据库主键，不是章节序号——先映射
        from app.services.chapter_service import ChapterService
        chs = {c.id: c for c in ChapterService(self.workspace.db).list(self.project_id)}

        def label(cid):
            if cid is None:
                return "全书"
            c = chs.get(cid)
            return f"第{c.seq + 1}章" if c else "（已删章节）"

        for it in issues:
            ch = label(it.chapter_id)
            label = TYPE_LABEL.get(it.issue_type, it.issue_type)
            self.issue_list.addItem(
                QListWidgetItem(f"[{ch}][{label}] {it.issue}\n   建议：{it.suggestion or ''}")
            )
            item = self.issue_list.item(self.issue_list.count() - 1)
            item.setData(0x0100, it.id)
            item.setData(0x0101, it.suggestion or "")

    def _scan(self) -> None:
        self.btn_scan.setEnabled(False)
        self.scan_progress.setVisible(True)
        self.scan_progress.setRange(0, 0)

        def progress(done: int, total: int) -> None:
            self.scan_progress.setRange(0, total)
            self.scan_progress.setValue(done)

        async def run():
            return await self.check.scan_project(self.project_id, on_progress=progress)

        def done(n: int) -> None:
            self.btn_scan.setEnabled(True)
            self.scan_progress.setVisible(False)
            self._load_issues()
            QMessageBox.information(self, "一致性扫描", f"扫描完成，发现 {n} 个问题。")

        run_async(run(), on_done=done, on_error=self._on_error)

    def _set_status(self, status: str) -> None:
        item = self.issue_list.currentItem()
        if item is None:
            return
        issue_id = item.data(0x0100)
        if issue_id:
            self.check.set_issue_status(issue_id, status)
            self._load_issues()

    def _apply_issue(self, item: QListWidgetItem) -> None:
        """双击：把修改建议复制到剪贴板，便于到章节应用。"""
        suggestion = item.data(0x0101) or ""
        if suggestion:
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(suggestion)
            QMessageBox.information(self, "修改建议", f"建议已复制到剪贴板：\n{suggestion}")
        self._set_status("resolved")

    # ---------------- 设定沉淀 ----------------
    def _build_canon_tab(self) -> None:
        tab = QWidget()
        lay = QVBoxLayout(tab)
        self.btn_extract = QPushButton("从所有已写章节提取新设定")
        self.btn_extract.clicked.connect(self._extract_all)
        self.candidate_list = QListWidget()
        lay.addWidget(self.btn_extract)
        lay.addWidget(QLabel("候选新设定（勾选后接受入库）"))
        lay.addWidget(self.candidate_list)
        row = QHBoxLayout()
        self.btn_accept = QPushButton("接受选中")
        self.btn_accept.clicked.connect(self._accept_selected)
        self.btn_reject = QPushButton("拒绝选中")
        self.btn_reject.clicked.connect(self._reject_selected)
        row.addWidget(self.btn_accept)
        row.addWidget(self.btn_reject)
        lay.addLayout(row)
        self.tabs.addTab(tab, "设定沉淀")

    def _extract_all(self) -> None:
        chapters = self.chapter_svc.list(self.project_id)
        drafts = [c for c in chapters if (c.content or "").strip()]
        if not drafts:
            QMessageBox.information(self, "设定沉淀", "没有已写正文的章节。")
            return
        self.btn_extract.setEnabled(False)
        self.btn_extract.setText("提取中…")

        async def run():
            total = 0
            for c in drafts:
                total += await self.canon.extract_candidates(
                    self.project_id, c.content or "", "draft", c.id,
                )
            return total

        def done(n: int) -> None:
            self.btn_extract.setEnabled(True)
            self.btn_extract.setText("从所有已写章节提取新设定")
            self._load_candidates()
            QMessageBox.information(self, "设定沉淀", f"检测到 {n} 个新设定候选。")

        run_async(run(), on_done=done, on_error=self._on_error)

    def _load_candidates(self) -> None:
        candidates = self.canon.list_pending(self.project_id)
        self.candidate_list.clear()
        if not candidates:
            self.candidate_list.addItem("（暂无待确认候选）")
            return
        for c in candidates:
            item = QListWidgetItem(
                f"[{c.category or '其他'}] {c.title}\n    {c.content or ''}"
            )
            item.setFlags(item.flags())
            item.setData(0x0100, c.id)
            self.candidate_list.addItem(item)

    def _accept_selected(self) -> None:
        item = self.candidate_list.currentItem()
        if item is None:
            return
        cid = item.data(0x0100)
        if cid:
            self.canon.accept(cid)
            self._load_candidates()
            self.status_msg("已加入设定库（可在世界观页编辑）")

    def _reject_selected(self) -> None:
        item = self.candidate_list.currentItem()
        if item is None:
            return
        cid = item.data(0x0100)
        if cid:
            self.canon.reject(cid)
            self._load_candidates()

    # ---------------- 工具 ----------------
    def _on_error(self, exc: Exception) -> None:
        self.btn_scan.setEnabled(True)
        self.btn_extract.setEnabled(True)
        self.btn_extract.setText("从所有已写章节提取新设定")
        self.scan_progress.setVisible(False)
        QMessageBox.warning(self, "操作失败", str(exc))

    def status_msg(self, text: str) -> None:
        mw = getattr(self.workspace, "main_window", None)
        if mw is not None:
            mw.status.showMessage(text)

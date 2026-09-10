"""记忆库视图（M3）：事件日志（全知）/ 角色经历时间线 / 承诺清单 / 信息项 + 修正操作。"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.services.character_service import CharacterService
from app.services.memory_service import MemoryService


class MemoryView(QWidget):
    def __init__(self, workspace, parent=None):
        super().__init__(parent)
        self.workspace = workspace
        self.project_id = workspace.project.id
        self.memory = MemoryService(workspace.db, workspace.llm_service)

        self.tabs = QTabWidget()
        self._build_events_tab()
        self._build_char_tab()
        self._build_promise_tab()

        lay = QVBoxLayout(self)
        lay.addWidget(self.tabs)

    # ---------------- 事件日志（全知视角） ----------------
    def _build_events_tab(self) -> None:
        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.addWidget(QLabel("剧情事件日志（全知视角，按章）"))
        self.event_list = QListWidget()
        lay.addWidget(self.event_list)
        self.btn_del_event = QPushButton("删除选中事件")
        self.btn_del_event.clicked.connect(self._delete_event)
        lay.addWidget(self.btn_del_event)
        self.tabs.addTab(tab, "事件日志")

    # ---------------- 角色经历（限知视角） ----------------
    def _build_char_tab(self) -> None:
        tab = QWidget()
        lay = QVBoxLayout(tab)
        self.char_combo = QComboBox()
        self.char_combo.currentIndexChanged.connect(self._load_char_events)
        self.char_events = QListWidget()
        self.char_events.itemDoubleClicked.connect(self._edit_char_event)
        lay.addWidget(QLabel("角色经历档案（限知视角，双击编辑修正）"))
        lay.addWidget(self.char_combo)
        lay.addWidget(self.char_events)
        self.btn_del_char_event = QPushButton("删除选中经历")
        self.btn_del_char_event.clicked.connect(self._delete_char_event)
        lay.addWidget(self.btn_del_char_event)
        self.tabs.addTab(tab, "角色经历")

    # ---------------- 承诺清单 ----------------
    def _build_promise_tab(self) -> None:
        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.addWidget(QLabel("承诺 / 伏笔 / 悬念跟踪"))
        self.promise_list = QListWidget()
        lay.addWidget(self.promise_list)
        row = QHBoxLayout()
        self.btn_fulfill = QPushButton("标记已回收")
        self.btn_fulfill.clicked.connect(self._fulfill_promise)
        self.btn_drop = QPushButton("标记已弃")
        self.btn_drop.clicked.connect(lambda: self._set_promise_status("dropped"))
        row.addWidget(self.btn_fulfill)
        row.addWidget(self.btn_drop)
        lay.addLayout(row)
        lay.addWidget(QLabel("信息项与知晓状态"))
        self.info_list = QListWidget()
        lay.addWidget(self.info_list)
        self.tabs.addTab(tab, "承诺与信息")

    # ---------------- 刷新 ----------------
    def refresh(self) -> None:
        self._load_events()
        self._load_characters()
        self._load_promises()
        self._load_info()

    def _chapter_label(self, chapter_id: int | None) -> str:
        """chapter_id 是数据库主键（自增），不是章节序号——映射到真实 第N章/标题。"""
        if chapter_id is None:
            return "（未关联章节）"
        from app.services.chapter_service import ChapterService
        chs = {c.id: c for c in ChapterService(self.workspace.db).list(self.project_id)}
        ch = chs.get(chapter_id)
        if ch is None:
            return "（已删章节）"
        return f"第{ch.seq + 1}章「{ch.title or ''}」"

    def _load_events(self) -> None:
        events = self.memory.list_events(self.project_id)
        self.event_list.clear()
        if not events:
            self.event_list.addItem("（暂无事件——在章节页对已写章节点「记忆提取」）")
            return
        for e in events:
            item = QListWidgetItem(f"[{self._chapter_label(e.chapter_id)}] {e.title or ''}｜{e.summary or ''}")
            item.setData(0x0100, e.id)
            self.event_list.addItem(item)

    def _delete_event(self) -> None:
        item = self.event_list.currentItem()
        if item is None:
            return
        self.memory.delete_story_event(item.data(0x0100))
        self._load_events()

    def _load_characters(self) -> None:
        chars = CharacterService(self.workspace.db).list(self.project_id)
        self.char_combo.clear()
        for c in chars:
            self.char_combo.addItem(c.name, c.id)
        if chars:
            self._load_char_events(0)

    def _load_char_events(self, _idx: int = 0) -> None:
        cid = self.char_combo.currentData()
        if cid is None:
            self.char_events.clear()
            self.char_events.addItem("（暂无角色）")
            return
        events = self.memory.list_character_events(cid)
        self.char_events.clear()
        if not events:
            self.char_events.addItem("（该角色暂无经历档案——对包含 TA 的章节执行记忆提取）")
            return
        for e in events:
            parts = [f"[{self._chapter_label(e.chapter_id)}] {e.summary or ''}"]
            if e.quotes:
                parts.append(f"台词：「{e.quotes}」")
            if e.felt:
                parts.append(f"感受：{e.felt}")
            if e.got_lost:
                parts.append(f"得失：{e.got_lost}")
            if e.secrets:
                parts.append(f"秘密：{e.secrets}")
            item = QListWidgetItem("；".join(p for p in parts if p))
            item.setData(0x0100, e.id)
            self.char_events.addItem(item)

    def _edit_char_event(self, item: QListWidgetItem) -> None:
        """双击经历条目：弹编辑对话框修正（记忆修正面板）。"""
        event_id = item.data(0x0100)
        ev = None
        with self.workspace.db.session_ctx() as session:
            from app.db.repositories.memory_repo import CharacterEventRepository
            ev = CharacterEventRepository(session).get(event_id)
        if ev is None:
            return
        dlg = _EventEditDialog(ev, self)
        if dlg.exec():
            self.memory.update_character_event(event_id, **dlg.values())
            self._load_char_events()

    def _delete_char_event(self) -> None:
        item = self.char_events.currentItem()
        if item is None:
            return
        event_id = item.data(0x0100)
        if event_id is not None:
            self.memory.update_character_event(event_id, summary="（已删除）", quotes="")
            self._load_char_events()

    def _load_promises(self) -> None:
        promises = self.memory.list_promises(self.project_id)
        self.promise_list.clear()
        if not promises:
            self.promise_list.addItem("（暂无承诺/伏笔）")
        for p in promises:
            kind = {"promise": "承诺", "foreshadow": "伏笔", "cliffhanger": "悬念"}.get(p.kind, p.kind)
            marker = {"open": "未回收", "pending": "推进中", "fulfilled": "已回收", "dropped": "已弃"}.get(
                p.status, p.status)
            item = QListWidgetItem(f"[{kind}][{marker}] {p.content}")
            item.setData(0x0100, p.id)
            self.promise_list.addItem(item)

    def _fulfill_promise(self) -> None:
        item = self.promise_list.currentItem()
        if item is None:
            return
        from PySide6.QtWidgets import QInputDialog
        resolution, ok = QInputDialog.getText(self, "标记已回收", "回收方式（可留空）：")
        if not ok:
            return
        self.memory.set_promise_status(item.data(0x0100), "fulfilled", resolution.strip())
        self._load_promises()

    def _set_promise_status(self, status: str) -> None:
        item = self.promise_list.currentItem()
        if item is None:
            return
        self.memory.set_promise_status(item.data(0x0100), status)
        self._load_promises()

    def _load_info(self) -> None:
        items = self.memory.list_info_items(self.project_id)
        self.info_list.clear()
        if not items:
            self.info_list.addItem("（暂无信息项/秘密）")
        for it in items:
            self.info_list.addItem(QListWidgetItem(f"[{it.status}] {it.title}: {it.content or ''}"))


class _EventEditDialog(QDialog):
    """角色经历条目编辑对话框（记忆修正）。"""

    def __init__(self, event, parent=None):
        super().__init__(parent)
        self.setWindowTitle("修正角色经历")
        self.setMinimumWidth(480)
        form = QFormLayout(self)
        self.summary = QPlainTextEdit()
        self.summary.setPlainText(event.summary or "")
        self.quotes = QLineEdit(event.quotes or "")
        self.acted = QPlainTextEdit()
        self.acted.setPlainText(event.acted or "")
        self.felt = QPlainTextEdit()
        self.felt.setPlainText(event.felt or "")
        self.got_lost = QPlainTextEdit()
        self.got_lost.setPlainText(event.got_lost or "")
        self.secrets = QPlainTextEdit()
        self.secrets.setPlainText(event.secrets or "")
        for w in (self.acted, self.felt, self.got_lost, self.secrets):
            w.setMaximumHeight(60)
        form.addRow("发生了什么", self.summary)
        form.addRow("关键台词（原文）", self.quotes)
        form.addRow("做过的事", self.acted)
        form.addRow("情绪", self.felt)
        form.addRow("得失", self.got_lost)
        form.addRow("秘密", self.secrets)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self) -> dict:
        return {
            "summary": self.summary.toPlainText().strip(),
            "quotes": self.quotes.text().strip(),
            "acted": self.acted.toPlainText().strip(),
            "felt": self.felt.toPlainText().strip(),
            "got_lost": self.got_lost.toPlainText().strip(),
            "secrets": self.secrets.toPlainText().strip(),
        }

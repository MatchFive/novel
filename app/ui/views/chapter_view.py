"""章节视图：章节列表 + 细纲节拍 + 正文编辑器 + 生成配置 + AI 拆章/细纲/续写（M1）。"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services.chapter_service import ChapterService
from app.services.outline_service import OutlineService
from app.services.plot_service import PlotService
from app.services.world_service import WorldService
from app.ui.views.gen_config_panel import GenerationConfigPanel
from app.utils.async_utils import run_async


class ChapterView(QWidget):
    def __init__(self, workspace, parent=None):
        super().__init__(parent)
        self.workspace = workspace
        self.service = ChapterService(workspace.db)
        self.plot_service = PlotService(workspace.db)
        self.project_id = workspace.project.id
        self._current_chapter_id: int | None = None

        splitter = QSplitter(self)

        # 左：章节树（卷 → 章节；情节作为章节的归属标签列，不占层级）
        left = QWidget()
        llay = QVBoxLayout(left)
        self.table = QTreeWidget()
        self.table.setColumnCount(4)
        self.table.setHeaderLabels(["标题", "情节", "状态", "字数"])
        self.table.setColumnWidth(0, 260)
        self.table.setEditTriggers(QTreeWidget.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        self.table.itemDoubleClicked.connect(self._edit_title_cell)
        self.table.itemChanged.connect(self._on_title_changed)
        self._loading_rows = False
        self.btn_new = QPushButton("新建章节")
        self.btn_new.clicked.connect(self._new_chapter)
        self.btn_split = QPushButton("AI 拆章")
        self.btn_split.clicked.connect(self._ai_split)
        self.btn_del = QPushButton("删除章节")
        self.btn_del.clicked.connect(self._delete_chapter)
        row = QHBoxLayout()
        row.addWidget(self.btn_new)
        row.addWidget(self.btn_split)
        row.addWidget(self.btn_del)
        llay.addWidget(self.table)
        llay.addLayout(row)

        # 批量操作
        batch_row = QHBoxLayout()
        self.btn_batch_summary = QPushButton("批量摘要")
        self.btn_batch_summary.setToolTip("为所有已写章节生成摘要")
        self.btn_batch_summary.clicked.connect(self._batch_summary)
        self.btn_batch_memory = QPushButton("批量记忆")
        self.btn_batch_memory.setToolTip("为所有已写章节执行记忆提取")
        self.btn_batch_memory.clicked.connect(self._batch_memory)
        self.btn_batch_draft = QPushButton("批量初稿")
        self.btn_batch_draft.setToolTip("为所有未写章节生成初稿（按生成配置目标字数）")
        self.btn_batch_draft.clicked.connect(self._batch_draft)
        self.btn_pace = QPushButton("节奏检查")
        self.btn_pace.setToolTip("检查连续章节的冲突/转折/爽点密度并给出建议")
        self.btn_pace.clicked.connect(self._pace_check)
        batch_row.addWidget(self.btn_batch_summary)
        batch_row.addWidget(self.btn_batch_memory)
        batch_row.addWidget(self.btn_batch_draft)
        llay.addLayout(batch_row)
        llay.addWidget(self.btn_pace)
        left.setMinimumWidth(360)

        # 右：标签页（正文 / 细纲 / 配置）
        self.tabs = QTabWidget()
        self._build_draft_tab()
        self._build_beats_tab()
        self.config_panel = GenerationConfigPanel(workspace)
        self.tabs.addTab(self.config_panel, "生成配置")

        splitter.addWidget(left)
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(1, 1)
        lay = QVBoxLayout(self)
        lay.addWidget(splitter)

    # ---------------- 标签页构建 ----------------
    def _build_draft_tab(self) -> None:
        tab = QWidget()
        lay = QVBoxLayout(tab)
        # 章节标题（可手动修改，随「保存正文」一并入库）
        title_row = QHBoxLayout()
        title_row.addWidget(QLabel("章节标题"))
        self.chapter_title_edit = QLineEdit()
        self.chapter_title_edit.setPlaceholderText("可直接修改，随「保存正文」一并保存")
        title_row.addWidget(self.chapter_title_edit, 1)
        lay.addLayout(title_row)
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("本章正文（Markdown）……\n\n光标处点「AI 续写」，内容会流式输出到右侧 AI 助手面板的建议草稿区。")
        self.btn_save_draft = QPushButton("保存正文")
        self.btn_save_draft.clicked.connect(self._save_draft)
        self.btn_continue = QPushButton("AI 续写（光标处）")
        self.btn_continue.setObjectName("ai_button")
        self.btn_continue.clicked.connect(self._ai_continue)
        self.btn_gen_full = QPushButton("AI 生成（整章）")
        self.btn_gen_full.setObjectName("ai_button")
        self.btn_gen_full.setToolTip("按细纲逐节拍生成整章正文（每拍一次写作+审核循环），完成后选择替换/追加")
        self.btn_gen_full.clicked.connect(self._ai_generate_full)
        self.btn_branches = QPushButton("多分支")
        self.btn_branches.setObjectName("ai_button")
        self.btn_branches.setToolTip("为当前节拍生成 2~3 种走向，选择后按该走向续写")
        self.btn_branches.clicked.connect(self._ai_branches)
        self.btn_summary = QPushButton("生成章节摘要")
        self.btn_summary.clicked.connect(self._ai_summary)
        self.btn_memory = QPushButton("记忆提取")
        self.btn_memory.clicked.connect(self._extract_memory)
        self.btn_memory.setToolTip("从本章提取事件/角色经历/知识/承诺（三层记忆，M3）")
        row = QHBoxLayout()
        row.addWidget(self.btn_save_draft)
        row.addWidget(self.btn_summary)
        row.addWidget(self.btn_memory)
        row.addStretch(1)
        row.addWidget(self.btn_gen_full)
        row.addWidget(self.btn_branches)
        row.addWidget(self.btn_continue)
        lay.addWidget(self.editor)
        lay.addLayout(row)
        self.tabs.addTab(tab, "正文")

    def _build_beats_tab(self) -> None:
        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.addWidget(QLabel("细纲字段（可直接编辑，点「保存细纲」一并入库）："))
        self.outline_edit = QPlainTextEdit()  # 场景/出场/对话要点/爽点/结尾钩子（结构化细纲其余字段）
        self.outline_edit.setMaximumHeight(110)
        self.outline_edit.setPlaceholderText(
            "场景：…\n出场：角色名（作用）；角色名2（作用）\n对话要点：…；…\n爽点：…；…\n结尾钩子：…")
        lay.addWidget(self.outline_edit)
        self.beat_list = QListWidget()
        self.beat_list.itemDoubleClicked.connect(self._edit_beat)
        self.beat_edit = QPlainTextEdit()
        self.beat_edit.setPlaceholderText("双击上方节拍载入修改；或直接输入新节拍。点「保存细纲」一并生效")
        self.beat_edit.setMaximumHeight(90)
        self.btn_beat_gen = QPushButton("AI 生成细纲")
        self.btn_beat_gen.clicked.connect(self._ai_beats)
        self.btn_beat_save = QPushButton("保存细纲")
        self.btn_beat_save.clicked.connect(self._save_beats)
        self.btn_beat_del = QPushButton("删除选中节拍")
        self.btn_beat_del.clicked.connect(self._delete_beat)
        row = QHBoxLayout()
        row.addWidget(self.btn_beat_gen)
        row.addWidget(self.btn_beat_save)
        row.addWidget(self.btn_beat_del)
        lay.addWidget(QLabel("细纲节拍（双击载入编辑框 → 修改 →「保存细纲」生效；格式：角色｜地点｜事件）"))
        lay.addWidget(self.beat_list)
        lay.addWidget(self.beat_edit)
        lay.addLayout(row)
        self.tabs.addTab(tab, "细纲")

    # ---------------- 数据 ----------------
    def refresh(self, select_chapter_id: int | None = None) -> None:
        """章节树：卷 → 章节 两级；情节不作为层级，只作为章节的归属标签列。

        重建后恢复选中：优先 select_chapter_id，其次当前章节（保存正文等操作不跳章）。
        """
        from PySide6.QtCore import Qt
        keep_id = select_chapter_id or self._current_chapter_id
        self._loading_rows = True
        self.table.clear()
        arcs = OutlineService(self.workspace.db).list(self.project_id)
        chapters = self.service.list(self.project_id)
        plots = self.plot_service.list_by_project(self.project_id)
        plot_map = {p.id: p for p in plots}

        first_chapter_item: QTreeWidgetItem | None = None
        keep_item: QTreeWidgetItem | None = None

        def _add_chapters(parent, chs):
            nonlocal first_chapter_item, keep_item
            for ch in chs:
                plot = plot_map.get(ch.plot_id)
                item = QTreeWidgetItem([
                    f"第{ch.seq + 1}章 · {ch.title or '(无标题)'}",
                    (plot.title or "") if plot else "",
                    ch.content_status or "empty",
                    str(ch.word_count or 0),
                ])
                item.setData(0, 0x0100, ("chapter", ch.id))
                parent.addChild(item)
                if first_chapter_item is None:
                    first_chapter_item = item
                if keep_id is not None and ch.id == keep_id:
                    keep_item = item

        for arc in arcs:
            arc_item = QTreeWidgetItem([f"第{arc.seq + 1}卷 · {arc.title or '(未命名)'}", "", "", ""])
            arc_item.setFlags(arc_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.table.addTopLevelItem(arc_item)
            _add_chapters(arc_item, [c for c in chapters if c.arc_id == arc.id])
            arc_item.setExpanded(True)
        # 未分卷章节
        orphan = [c for c in chapters if c.arc_id is None]
        if orphan:
            orphan_item = QTreeWidgetItem(["（未分卷）", "", "", ""])
            orphan_item.setFlags(orphan_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.table.addTopLevelItem(orphan_item)
            _add_chapters(orphan_item, orphan)
            orphan_item.setExpanded(True)
        self._loading_rows = False
        target = keep_item or first_chapter_item
        if target is not None:
            self.table.setCurrentItem(target)

    # ---------------- 标题编辑（双击章节标题直接改） ----------------
    _ROLE_EDITING = 0x0101  # 编辑中标记（UserRole+1）

    def _edit_title_cell(self, item, column: int = 0) -> None:
        from PySide6.QtCore import Qt
        if column != 0:
            return
        data = item.data(0, 0x0100)
        if not data or data[0] != "chapter":
            return  # 只有章节节点可编辑标题
        # 编辑时只显示纯标题（不带「第N章 · 」前缀），降低误改前缀的概率
        ch = self.service.get(data[1])
        if ch is None:
            return
        # 注意：setFlags/setText 都会触发 itemChanged，必须全程用 _loading_rows 挡住，
        # 最后再落「编辑中」标记——否则信号噪声会被误判为编辑提交
        self._loading_rows = True
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        item.setText(0, ch.title or "")
        item.setData(0, self._ROLE_EDITING, True)
        self._loading_rows = False
        self.table.editItem(item, 0)

    def _on_title_changed(self, item, column: int = 0) -> None:
        from PySide6.QtCore import Qt
        if getattr(self, "_loading_rows", False) or column != 0:
            return
        if not item.data(0, self._ROLE_EDITING):
            return  # 非编辑提交（refresh 重填、setFlags 噪声等）
        item.setData(0, self._ROLE_EDITING, None)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        data = item.data(0, 0x0100)
        new_title = item.text(0).strip()
        # 兜底：用户保留了「第N章 · 」前缀时剥离之
        import re
        m = re.match(r"^第\d+章\s*·\s*", new_title)
        if m:
            new_title = new_title[m.end():]
        if not new_title or not data or data[0] != "chapter":
            return
        self.service.update(data[1], title=new_title)
        # 恢复树节点的标准显示格式
        ch = self.service.get(data[1])
        if ch is not None:
            self._loading_rows = True
            item.setText(0, f"第{ch.seq + 1}章 · {new_title}")
            self._loading_rows = False
        self.status_msg(f"章节标题已改为「{new_title}」")

    def _on_row_selected(self) -> None:
        items = self.table.selectedItems()
        if not items:
            return
        data = items[0].data(0, 0x0100)
        if not data or data[0] != "chapter":
            return  # 选中卷/情节节点时不切换正文
        self._current_chapter_id = data[1]
        self._load_chapter(self._current_chapter_id)

    def _load_chapter(self, chapter_id: int) -> None:
        ch = self.service.get(chapter_id)
        if ch is None:
            return
        # 切换章节草稿：面板内容存回上一章，载入本章草稿（常驻内存）
        mw = getattr(self.workspace, "main_window", None)
        if mw is not None and hasattr(mw, "switch_chapter_draft"):
            mw.switch_chapter_draft(chapter_id)
        self.chapter_title_edit.setText(ch.title or "")
        self.editor.setPlainText(ch.content or "")
        self._load_beats()

    def _load_beats(self) -> None:
        beats = self.service.get_beats(self._current_chapter_id) if self._current_chapter_id else []
        self.beat_list.clear()
        for i, b in enumerate(beats):
            item = QListWidgetItem(f"{i + 1}. {b.get('pov', '')}｜{b.get('location', '')}｜{b.get('event', '')}")
            item.setData(0x0100, i)
            self.beat_list.addItem(item)
        # 结构化细纲其余字段（场景/出场人物/对话要点/爽点/结尾钩子）
        ch = self.service.get(self._current_chapter_id) if self._current_chapter_id else None
        outline = (ch.outline_json or {}) if ch else {}
        parts = []
        if outline.get("scene"):
            parts.append(f"场景：{outline['scene']}")
        if outline.get("characters"):
            chars = outline["characters"]
            if isinstance(chars, list):
                chars = "；".join(
                    f"{c.get('name', '')}（{c.get('role', '')}）" if isinstance(c, dict) else str(c)
                    for c in chars)
            parts.append(f"出场：{chars}")
        if outline.get("dialogue_hooks"):
            parts.append("对话要点：" + "；".join(str(x) for x in outline["dialogue_hooks"]))
        if outline.get("humor_points"):
            parts.append("爽点：" + "；".join(str(x) for x in outline["humor_points"]))
        if outline.get("ending_hook"):
            parts.append(f"结尾钩子：{outline['ending_hook']}")
        self.outline_edit.setPlainText("\n".join(parts))
        self._editing_row = None

    def _current_chapter(self):
        return self.service.get(self._current_chapter_id) if self._current_chapter_id else None

    # ---------------- 章节操作 ----------------
    def _new_chapter(self) -> None:
        arc_id = None
        arcs = OutlineService(self.workspace.db).list(self.project_id)
        if arcs:
            names = [f"第{a.seq + 1}卷 · {a.title or ''}" for a in arcs]
            choice, ok = QInputDialog.getItem(self, "归属卷", "选择所属卷（可取消=不归属）", names, 0, False)
            if ok and choice:
                arc_id = arcs[names.index(choice)].id
        ch = self.service.create(project_id=self.project_id, arc_id=arc_id, title="新章节")
        self.refresh(select_chapter_id=ch.id)  # 选中新建的章节
        self.tabs.setCurrentIndex(0)

    def _delete_chapter(self) -> None:
        if self._current_chapter_id is not None:
            self.service.delete(self._current_chapter_id)
            self._current_chapter_id = None
            self.editor.clear()
            self.refresh()

    def _save_draft(self) -> None:
        if self._current_chapter_id is None:
            QMessageBox.information(self, "提示", "请先选择或新建章节。")
            return
        new_title = self.chapter_title_edit.text().strip()
        fields: dict = {"content": self.editor.toPlainText()}
        if new_title:
            fields["title"] = new_title
        self.service.update(self._current_chapter_id, **fields)
        self.refresh()
        self.status_msg("正文与标题已保存")

    # ---------------- 细纲操作 ----------------
    def _beats_from_list(self) -> list[dict]:
        import re
        beats = []
        for i in range(self.beat_list.count()):
            text = self.beat_list.item(i).text()
            text = re.sub(r"^\s*\d+\s*[.、]\s*", "", text)  # 剥离列表序号前缀，避免污染 pov
            parts = text.split("｜")
            if len(parts) >= 3:
                beats.append({"pov": parts[0].strip(), "location": parts[1].strip(),
                              "event": "｜".join(p.strip() for p in parts[2:])})
            elif text.strip():
                beats.append({"pov": "", "location": "", "event": text.strip()})
        return beats

    def _save_beats(self) -> None:
        if self._current_chapter_id is None:
            QMessageBox.information(self, "提示", "请先选择章节。")
            return
        # 编辑框内容写回列表（手动修改生效的关键：双击载入 → 修改 → 保存）
        text = self.beat_edit.toPlainText().strip()
        if text:
            row = getattr(self, "_editing_row", None)
            if row is not None and 0 <= row < self.beat_list.count():
                item = self.beat_list.item(row)
                if text != item.text():
                    item.setText(text)  # 修改选中节拍
            elif all(self.beat_list.item(i).text() != text for i in range(self.beat_list.count())):
                self.beat_list.addItem(text)  # 没有双击载入过的全新内容 → 新增节拍
            self._editing_row = None
        self.service.set_beats(self._current_chapter_id, self._beats_from_list())
        # 结构化细纲字段（场景/出场/对话要点/爽点/结尾钩子）一并入库
        outline = self._parse_outline_edit()
        self.service.update(self._current_chapter_id, outline_json=outline or None)
        self._load_beats()
        self.status_msg("细纲已保存（节拍 + 场景/钩子等字段）")

    def _parse_outline_edit(self) -> dict:
        """把可编辑的细纲字段文本解析回结构化 dict（空行/无该字段则忽略）。"""
        import re
        keymap = {"场景": "scene", "出场": "characters", "出场人物": "characters",
                  "对话要点": "dialogue_hooks", "爽点": "humor_points", "爽点设计": "humor_points",
                  "结尾钩子": "ending_hook"}
        out: dict = {}
        for line in self.outline_edit.toPlainText().splitlines():
            if "：" not in line:
                continue
            key, val = line.split("：", 1)
            field = keymap.get(key.strip())
            val = val.strip()
            if not field or not val:
                continue
            if field == "characters":
                chars = []
                for part in val.split("；"):
                    part = part.strip()
                    if not part:
                        continue
                    m = re.match(r"(.+?)（(.*?)）\s*$", part)
                    chars.append({"name": m.group(1).strip(), "role": m.group(2).strip()}
                                 if m else {"name": part, "role": ""})
                if chars:
                    out[field] = chars
            elif field in ("dialogue_hooks", "humor_points"):
                items = [x.strip() for x in val.split("；") if x.strip()]
                if items:
                    out[field] = items
            else:
                out[field] = val
        return out

    def _edit_beat(self, item: QListWidgetItem) -> None:
        self._editing_row = self.beat_list.row(item)  # 记录正在编辑的行，保存时写回
        self.beat_edit.setPlainText(item.text())

    def _delete_beat(self) -> None:
        row = self.beat_list.currentRow()
        if row >= 0:
            self.beat_list.takeItem(row)

    def _add_beat_from_edit(self) -> None:
        text = self.beat_edit.toPlainText().strip()
        if text:
            self.beat_list.addItem(text)

    # ---------------- AI ----------------
    def _ai_split(self) -> None:
        """AI 拆章（整卷规划）：章节标题 + 完整细纲一次产出——先规划剧情弧线，再落实到章节，
        情节先于标题，章节间衔接不撞车。"""
        p = self.workspace.project
        llm = self.workspace.llm_service
        arcs = OutlineService(self.workspace.db).list(self.project_id)
        if not arcs:
            QMessageBox.information(self, "提示", "请先在大纲页创建卷。")
            return
        names = [f"第{a.seq + 1}卷 · {a.title or ''}" for a in arcs]
        choice, ok = QInputDialog.getItem(
            self, "AI 拆章", "选择要拆解的卷（整卷规划，一次产出每章细纲）：", names, 0, False)
        if not ok:
            return
        arc = arcs[names.index(choice)]
        gen = self.config_panel.current()
        self.btn_split.setEnabled(False)
        self.btn_split.setText("情节规划中…")
        coro = OutlineService(self.workspace.db).plan_chapters(
            arc.id, llm, target_words=gen.get("target_words", 3000))
        run_async(coro, on_done=lambda result: self._on_split_done(result, arc.id),
                  on_error=self._on_ai_error)

    def _on_split_done(self, result, arc_id: int | None) -> None:
        from app.services.command_service import CommandService
        from app.services.plot_service import PlotService
        self.btn_split.setEnabled(True)
        self.btn_split.setText("AI 拆章")
        plots, chapters = result if isinstance(result, tuple) else ([], result)
        if not chapters:
            QMessageBox.warning(self, "AI 拆章", "模型未返回章节计划。")
            return
        plot_svc = PlotService(self.workspace.db)
        # 先入库情节线（按标题建索引，章节按 plot_title 挂载；含情节节拍线）
        plot_ids: dict[str, int] = {}
        with self.workspace.db.session_ctx() as session:
            from app.db.unit_of_work import UnitOfWork
            with UnitOfWork(session):
                for p in plots or []:
                    plot = plot_svc.find_or_create(
                        session, self.project_id, arc_id,
                        title=p.get("title", ""), summary=p.get("summary", ""),
                        conflict=p.get("conflict", ""),
                    )
                    if p.get("resolution"):
                        plot.resolution = p["resolution"]
                    beats = PlotService.normalize_beats(p.get("beats"))
                    if beats:
                        plot.beats_json = beats
                    session.flush()
                    plot_ids[(p.get("title") or "").replace(" ", "")] = plot.id
        for c in chapters:
            plot_title = (c.get("plot_title") or "").replace(" ", "")
            plot_id = plot_ids.get(plot_title)
            ch = self.service.create(
                project_id=self.project_id, arc_id=arc_id, plot_id=plot_id,
                title=c.get("title", ""), objective=c.get("objective", ""),
            )
            beats = CommandService._normalize_beats(c.get("plot")) or []
            extras = {k: c[k] for k in CommandService.OUTLINE_FIELDS
                      if c.get(k) not in (None, "", [])}
            self.service.update(ch.id, beats_json=beats,
                                outline_status="done" if beats else "none",
                                outline_json=extras or None)
        self.refresh()
        QMessageBox.information(
            self, "AI 拆章",
            f"已生成 {len(plots or [])} 个情节、{len(chapters)} 章（每章含细纲节拍），"
            "请逐章审阅调整。")

    def _ai_beats(self) -> None:
        ch = self._current_chapter()
        if ch is None:
            QMessageBox.information(self, "提示", "请先选择章节。")
            return
        llm = self.workspace.llm_service
        entries = WorldService(self.workspace.db).list_entries(self.project_id, None)
        world_text = "\n".join(f"- {e.title}: {e.content[:60]}" for e in entries[:8]) or "（暂无设定）"
        # 邻章避让：前后章节的情节线，防止跨章情节撞车
        all_ch = self.service.list(self.project_id)
        idx = next((i for i, c in enumerate(all_ch) if c.id == ch.id), -1)
        neighbor_parts = []
        for j, c in enumerate(all_ch):
            if j == idx or (idx >= 0 and abs(j - idx) > 2):
                continue
            events = "；".join(b.get("event", "") for b in (c.beats_json or [])[:6])
            if events:
                neighbor_parts.append(f"第{c.seq + 1}章「{c.title or ''}」：{events}")
        neighbors = "\n".join(neighbor_parts) or "（暂无相邻章节细纲）"
        arc_goal = ""
        if ch.arc_id:
            arc = OutlineService(self.workspace.db).get(ch.arc_id)
            arc_goal = (arc.goal or "") if arc else ""
        # 情节上下文：本章所属情节的概要/冲突（跨章情节保持连续）
        plot_context = "（未归属情节）"
        if ch.plot_id:
            plot = self.plot_service.get(ch.plot_id)
            if plot:
                plot_context = (f"「{plot.title or ''}」：{plot.summary or ''}"
                                f"（核心冲突：{plot.conflict or '（无）'}；收束：{plot.resolution or '（无）'}）")
        self.btn_beat_gen.setEnabled(False)
        self.btn_beat_gen.setText("生成中…")
        coro = llm.generate_structured(
            project_id=self.project_id,
            module="chapter",
            prompt_key="task.beat_generate",
            system_prompt=llm.load_prompt("global.base_prompt"),
            user_prompt=llm.load_prompt(
                "task.beat_generate", project_id=self.project_id,
                objective=ch.objective or ch.title or "", cast="（默认）",
                arc_goal=arc_goal or "（未设置）", neighbors=neighbors,
                plot_context=plot_context,
                related_entries=world_text,
            ),
        )
        run_async(coro, on_done=self._on_beats_done, on_error=self._on_ai_error)

    def _on_beats_done(self, data: dict) -> None:
        from app.services.command_service import CommandService
        self.btn_beat_gen.setEnabled(True)
        self.btn_beat_gen.setText("AI 生成细纲")
        if not isinstance(data, dict):
            return
        beats = CommandService._normalize_beats(data.get("beats")) or []
        if not beats:
            QMessageBox.warning(self, "AI 细纲", "模型未返回节拍。")
            return
        self.beat_list.clear()
        for b in beats:
            self.beat_list.addItem(
                f"{b.get('pov', '')}｜{b.get('location', '')}｜{b.get('event', '')}"
            )
        if self._current_chapter_id:
            self.service.set_beats(self._current_chapter_id, self._beats_from_list())
            # 结构化细纲其余字段（场景/出场人物/对话要点/爽点/结尾钩子）
            extras = {k: data[k] for k in CommandService.OUTLINE_FIELDS
                      if data.get(k) not in (None, "", [])}
            if extras:
                self.service.update(self._current_chapter_id, outline_json=extras)
            self._load_beats()
        self.status_msg("细纲已生成，可在左侧修改后保存")

    # ---------------- AI 整章生成（按细纲逐节拍 / 整章单发） ----------------
    @staticmethod
    def _merge_segment(full: str, seg: str) -> str:
        """拼接段落并去重：模型续写时把前文重写一遍（细纲→整章的 SFT 模型尤其常见），
        检测尾首重叠与高度重复段，剥掉重复部分。"""
        import difflib
        full, seg = full.strip(), seg.strip()
        if not full or not seg:
            return full or seg
        # ① 尾首重叠（full 结尾 == seg 开头）→ 剥掉重叠
        max_ol = min(len(full), len(seg), 800)
        for k in range(max_ol, 20, -1):
            if full[-k:] == seg[:k]:
                seg = seg[k:]
                break
        # ② 新段开头 300 字已在全文出现过 → 模型把前文重写了一遍，整段丢弃
        probe = seg[:300].strip()
        if probe and probe in full:
            return full
        # ③ 新段与全文末尾等长部分高度相似（>0.75）→ 重复改写，丢弃
        tail = full[-len(seg):] if len(full) >= len(seg) else full
        if tail and len(seg) > 100 and \
                difflib.SequenceMatcher(None, seg, tail).quick_ratio() > 0.75:
            return full
        return (full + "\n\n" + seg).strip()

    async def _generate_full_chapter(self, ch, gen: dict, prev_summaries: str,
                                     on_segment=None, on_progress=None) -> tuple[str, list]:
        """生成整章：逐节拍（每拍一次写作+审核循环，云端模型推荐）或整章单发
        （整份细纲一次生成——匹配「细纲→整章」SFT 微调模型的训练格式，本地模型推荐）。

        on_segment(i, total, phase, full)：逐节拍模式的拍进度；
        on_progress(phase, text)：工作流级进度（整章单发模式的流式上屏/阶段播报）。
        返回 (整章正文, 每拍的 DraftResult 列表)。
        """
        from app.workflows.draft_workflow import DraftWorkflow
        wf = DraftWorkflow(self.workspace.db, self.workspace.llm_service)
        beats = ch.beats_json or []
        if not beats or gen.get("draft_mode") == "whole":
            result = await wf.run(project_id=self.project_id, chapter=ch, gen_config=gen,
                                  recent_summaries=prev_summaries, on_progress=on_progress)
            return result.draft.strip(), [result]
        # 每拍分摊整章目标字数（配置是整章目标，直接传给单拍会超写）
        per_beat = {**gen, "target_words": max(400, int(gen.get("target_words", 3000)) // len(beats))}
        full = ""
        results = []
        for i, b in enumerate(beats, 1):
            if on_segment:
                on_segment(i, len(beats), "start", full)
            result = await wf.run(
                project_id=self.project_id, chapter=ch, gen_config=per_beat,
                target_beat=b.get("event", ""),
                recent_draft=full[-4000:],  # 衔接：上一段的结尾
                recent_summaries=prev_summaries,
                on_progress=on_progress,
            )
            merged = self._merge_segment(full, result.draft)
            if merged == full and result.draft.strip():
                self.status_msg(f"⚠️ 第 {i} 拍输出与前文重复（模型把前文重写了一遍），已丢弃该段")
            full = merged
            results.append(result)
            if on_segment:
                on_segment(i, len(beats), "done", full)
        return full, results

    def _ai_generate_full(self) -> None:
        """整章生成：按细纲逐节拍生成，实时累积到建议草稿区，完成后选择替换/追加正文。"""
        ch = self._current_chapter()
        if ch is None:
            QMessageBox.information(self, "提示", "请先选择章节。")
            return
        if not ch.beats_json:
            QMessageBox.information(self, "提示", "本章还没有细纲——先在「细纲」页生成/填写节拍。")
            return
        mw = self.workspace.main_window
        gen = self.config_panel.current()
        n_beats = len(ch.beats_json)
        all_chapters = self.service.list(self.project_id)
        prev_summaries = "\n".join(
            f"[第{c.seq + 1}章] {c.summary[:300]}"
            for c in all_chapters if c.seq < (ch.seq or 0) and c.summary
        )
        self.btn_gen_full.setEnabled(False)
        self.btn_gen_full.setText("整章生成中…")
        # 不清空建议草稿区——旧内容保留到新生成流式覆盖为止，防止切换章节误丢内容

        def on_segment(i, total, phase, full):
            if phase == "start":
                mw.set_task_label(f"整章生成：节拍 {i}/{total}（写作→审核）")
            else:
                mw.set_chapter_draft(ch.id, full)  # 实时累积可见（按章节归属）

        phase_names = {"writing": "写作中", "review": "审核中", "revising": "修正中"}

        def on_progress(phase, text):
            """工作流级进度：整章单发模式下流式上屏；逐拍模式下播报拍内阶段。"""
            if phase == "writing" and text:
                if not (ch.beats_json and gen.get("draft_mode") != "whole"):
                    mw.set_chapter_draft(ch.id, text)  # 整章单发：流式累积
                mw.set_task_label(f"整章生成：写作中（{len(text)} 字）")
            elif phase in phase_names:
                mw.set_task_label(f"整章生成：{phase_names[phase]}…")

        async def run():
            return await self._generate_full_chapter(ch, gen, prev_summaries,
                                                     on_segment, on_progress)

        def done(payload):
            full, results = payload
            self.btn_gen_full.setEnabled(True)
            self.btn_gen_full.setText("AI 生成（整章）")
            # 先把成稿存进本章草稿保底——无论用户稍后选什么，文本都不会丢
            mw.set_chapter_draft(ch.id, full)
            n_issues = sum(len(r.issues) for r in results if not r.pass_review)
            mw.set_task_label(f"整章生成完成：{len(full)} 字 / {n_beats} 拍"
                              + (f"，审核修正了 {n_issues} 处" if n_issues else "，审核全部通过"))
            # 审核未过但已达修正上限的问题要明确可见（不是悄悄放过）
            unfixed = [i for r in results if not r.pass_review for i in (r.issues or [])]
            if unfixed:
                self.status_msg("⚠️ 已达修正上限，以下问题未修（可人工处理）：\n"
                                + "\n".join(f"· {i.get('issue', '')}" for i in unfixed[:5]))
            box = QMessageBox(self)
            box.setWindowTitle("整章生成完成")
            box.setText(f"已按细纲生成整章（{len(full)} 字 / {n_beats} 拍）。如何写入正文？")
            btn_replace = box.addButton("替换正文", QMessageBox.ButtonRole.AcceptRole)
            btn_append = box.addButton("追加到末尾", QMessageBox.ButtonRole.DestructiveRole)
            box.addButton("只放建议草稿", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            clicked = box.clickedButton()
            if clicked is btn_replace:
                self.editor.setPlainText(full)
                mw.clear_chapter_draft(ch.id)  # 已写入正文，清掉草稿防误重复应用
                self.status_msg("整章已写入正文（替换）——记得点「保存正文」")
            elif clicked is btn_append:
                self.editor.append(full)
                mw.clear_chapter_draft(ch.id)
                self.status_msg("整章已追加到正文末尾——记得点「保存正文」")
            else:
                self.status_msg("整章在右侧建议草稿区，可编辑后点「应用草稿」")

        def err(exc):
            self.btn_gen_full.setEnabled(True)
            self.btn_gen_full.setText("AI 生成（整章）")
            self._on_ai_error(exc)

        run_async(run(), on_done=done, on_error=err)

    # ---------------- AI 续写（LangGraph 工作流：上下文构造→写作→审核→修正） ----------------
    def _ai_continue(self, extra_beat: str | None = None) -> None:
        ch = self._current_chapter()
        if ch is None:
            QMessageBox.information(self, "提示", "请先选择章节。")
            return
        llm = self.workspace.llm_service
        mw = self.workspace.main_window
        gen = self.config_panel.current()
        target_beat = extra_beat or (ch.beats_json[0].get("event", "") if ch.beats_json else "")

        # 前情概览：前 3 章摘要
        all_chapters = self.service.list(self.project_id)
        prev_summaries = "\n".join(
            f"[第{c.seq + 1}章] {c.summary[:300]}"
            for c in all_chapters if c.seq < (ch.seq or 0) and c.summary
        )

        # 不清空建议草稿区——旧内容保留到新生成流式覆盖为止，防止切换章节误丢内容
        mw.set_task_label("工作流：上下文构造 → 写作 → 审核…")

        phase_names = {"writing": "写作中", "review": "审核中", "revising": "修正中"}

        def on_progress(phase, text):
            if phase == "writing" and text:
                mw.set_chapter_draft(ch.id, text)  # 流式上屏（按章节归属）
                mw.set_task_label(f"工作流：写作中（{len(text)} 字）")
            elif phase in phase_names:
                mw.set_task_label(f"工作流：{phase_names[phase]}…")

        async def run():
            from app.workflows.draft_workflow import DraftWorkflow
            wf = DraftWorkflow(self.workspace.db, llm)
            return await wf.run(
                project_id=self.project_id, chapter=ch, gen_config=gen,
                target_beat=target_beat, recent_draft=self.editor.toPlainText()[-4000:],
                recent_summaries=prev_summaries, on_progress=on_progress,
            )

        def done(result):
            mw.set_injected(result.injected)
            mw.set_chapter_draft(ch.id, result.draft)
            # 审核结果反馈
            if result.pass_review:
                note = "审核通过" + (f"（修正 {result.revise_count} 次后通过）" if result.revise_count else "")
            else:
                note = f"审核发现 {len(result.issues)} 个问题（已修正 {result.revise_count} 次）"
            mw.set_task_label(f"工作流完成：{note} — 审阅后点「应用草稿」")
            if result.issues and not result.pass_review:
                issues_text = "\n".join(f"· [{i.get('type','')}] {i.get('issue','')}" for i in result.issues[:5])
                self.status_msg(f"审核发现问题（已尝试修正）：\n{issues_text}")

        run_async(run(), on_done=done, on_error=self._on_ai_error)

    # ---------------- 多分支续写（设计文档 3.8） ----------------
    def _ai_branches(self) -> None:
        ch = self._current_chapter()
        if ch is None:
            QMessageBox.information(self, "提示", "请先选择章节。")
            return
        llm = self.workspace.llm_service
        gen = self.config_panel.current()
        target_beat = ch.beats_json[0].get("event", "") if ch.beats_json else ""

        from app.llm.assembler import ContextAssembler
        assembler = ContextAssembler(self.workspace.db)
        memory = assembler.memory.inject_draft_memory(
            project_id=self.project_id, chapter=ch, pov_char_id=ch.pov_char_id,
            keywords=[target_beat, ch.objective or ""],
            memory_strength=gen.get("memory_strength", "standard"),
        )
        entries = WorldService(self.workspace.db).list_entries(self.project_id, None)
        world_text = "\n".join(f"- {e.title}: {e.content[:80]}" for e in entries[:6]) or "（暂无）"
        from app.services.character_service import CharacterService
        chars = CharacterService(self.workspace.db).list(self.project_id)
        cast_text = "\n".join(f"- {c.name}：{c.profile.get('personality', '')}" for c in chars[:5]) or "（暂无）"

        self.btn_branches.setEnabled(False)
        self.btn_branches.setText("分支生成中…")
        coro = llm.generate_structured(
            project_id=self.project_id, module="draft", prompt_key="task.draft_branches",
            system_prompt=llm.load_prompt("global.base_prompt"),
            user_prompt=llm.load_prompt(
                "task.draft_branches", project_id=self.project_id,
                objective=ch.objective or "", world_entries=world_text,
                cast_cards=cast_text, pov_memories=memory.pov_memories,
                target_beat=target_beat or ch.title or "",
            ),
        )
        run_async(coro, on_done=self._on_branches_done, on_error=self._on_ai_error)

    def _on_branches_done(self, data: dict) -> None:
        self.btn_branches.setEnabled(True)
        self.btn_branches.setText("多分支")
        if not isinstance(data, dict):
            return
        branches = data.get("branches") or []
        if not branches:
            QMessageBox.warning(self, "多分支", "模型未返回走向。")
            return
        choice, ok = QInputDialog.getItem(
            self, "选择走向", "3 种走向（选中后按该走向续写）：", branches, 0, False
        )
        if ok and choice:
            self.status_msg(f"已选走向：{choice}")
            self._ai_continue(extra_beat=choice)

    def _build_draft_prompt(self, ch, recent_draft: str, target_beat_override: str | None = None):
        """组装正文生成 prompt（上下文组装器：检索注入 + 记忆注入 + 预算裁剪）。"""
        from app.llm.assembler import ContextAssembler
        assembler = ContextAssembler(self.workspace.db)
        gen = self.config_panel.current()

        target_beat = target_beat_override or (ch.beats_json[0].get("event", "") if ch.beats_json else "")
        all_chapters = self.service.list(self.project_id)
        prev_summaries = [
            f"[第{c.seq + 1}章] {c.summary[:300]}"
            for c in all_chapters if c.seq < (ch.seq or 0) and c.summary
        ]
        memory = assembler.memory.inject_draft_memory(
            project_id=self.project_id, chapter=ch,
            pov_char_id=ch.pov_char_id,
            keywords=[target_beat, ch.objective or ""],
            memory_strength=gen.get("memory_strength", "standard"),
        )
        result = assembler.build_draft_prompt(
            project_id=self.project_id, chapter=ch, gen_config=gen,
            recent_draft=recent_draft, target_beat=target_beat,
            recent_summaries="\n".join(prev_summaries[-3:]), memory=memory,
        )
        tier_override = gen.get("tier_override")
        if tier_override is None and gen.get("quality_mode") == "refined":
            tier_override = "pro"
        return result, tier_override

    # ---------------- 批量操作（M2 收尾） ----------------
    def _batch_summary(self) -> None:
        targets = [c for c in self.service.list(self.project_id)
                   if (c.content or "").strip() and not (c.summary or "").strip()]
        if not targets:
            QMessageBox.information(self, "批量摘要", "没有需要生成摘要的章节（已写正文且暂无摘要）。")
            return
        self._run_batch("批量摘要", targets,
                        lambda c: self._gen_summary_text(c),
                        lambda c, text: self.service.update(c.id, summary=text))

    def _batch_memory(self) -> None:
        from app.services.memory_service import MemoryService
        svc = MemoryService(self.workspace.db, self.workspace.llm_service)
        targets = [c for c in self.service.list(self.project_id)
                   if (c.content or "").strip() and c.memory_status != "extracted"]
        if not targets:
            QMessageBox.information(self, "批量记忆", "所有已写章节均已提取记忆。")
            return
        self._run_batch("批量记忆", targets,
                        lambda c: svc.extract_for_chapter(c.id, self.project_id),
                        lambda c, _data: None)

    def _batch_draft(self) -> None:
        targets = [c for c in self.service.list(self.project_id)
                   if not (c.content or "").strip()]
        if not targets:
            QMessageBox.information(self, "批量初稿", "没有未写正文的章节。")
            return
        gen = self.config_panel.current()
        target_words = gen.get("target_words", 3000)
        prev_holder = {"text": ""}  # 上一章正文结尾，供下一章衔接

        async def generate(c):
            # 逐节拍生成整章（修复：原来只写第一个节拍）
            all_chapters = self.service.list(self.project_id)
            prev_summaries = "\n".join(
                f"[第{x.seq + 1}章] {x.summary[:300]}"
                for x in all_chapters if x.seq < (c.seq or 0) and x.summary
            )
            full, _ = await self._generate_full_chapter(c, gen, prev_summaries)
            prev_holder["text"] = full
            return full

        self._run_batch("批量初稿", targets, generate,
                        lambda c, text: self.service.update(
                            c.id, content=text, content_status="draft"),
                        expected=f"每章约 {target_words} 字（逐节拍生成，含审核+修正）")

    def _run_batch(self, title: str, chapters: list, gen_fn, save_fn,
                   expected: str = "") -> None:
        """顺序批量执行：gen_fn(ch) -> result，save_fn(ch, result)。"""
        total = len(chapters)
        mw = self.workspace.main_window
        mw.set_task_label(f"{title}：0/{total}")
        self.btn_batch_summary.setEnabled(False)
        self.btn_batch_memory.setEnabled(False)
        self.btn_batch_draft.setEnabled(False)

        async def run():
            done = 0
            for c in chapters:
                result = await gen_fn(c)
                save_fn(c, result)
                done += 1
                mw.set_task_label(f"{title}：{done}/{total}")
                self.status_msg(f"{title}：{done}/{total}（{c.title or c.seq + 1} 章）")
            return done

        def finish(done: int) -> None:
            self.btn_batch_summary.setEnabled(True)
            self.btn_batch_memory.setEnabled(True)
            self.btn_batch_draft.setEnabled(True)
            mw.set_task_label("空闲")
            self.refresh()
            QMessageBox.information(
                self, title,
                f"{title}完成：{done}/{total} 章。{expected}"
                + ("\n\n提示：批量初稿为草稿质量，请逐章审阅润色。" if "初稿" in title else ""))

        run_async(run(), on_done=finish, on_error=self._on_ai_error)

    # ---------------- 章节摘要 ----------------
    async def _gen_summary_text(self, ch) -> str:
        """生成单章摘要文本（供单章按钮与批量摘要复用）。"""
        llm = self.workspace.llm_service
        text = ""
        async for chunk in llm.generate_stream(
            project_id=self.project_id, module="draft",
            prompt_key="task.chapter_summary",
            system_prompt=llm.load_prompt("global.base_prompt"),
            user_prompt=llm.load_prompt(
                "task.chapter_summary", project_id=self.project_id,
                chapter_title=ch.title or f"第{ch.seq + 1}章",
                chapter_content=(ch.content or "")[-8000:],
            ),
        ):
            text += chunk
        return text

    def _ai_summary(self) -> None:
        ch = self._current_chapter()
        if ch is None or not self.editor.toPlainText().strip():
            QMessageBox.information(self, "提示", "请先选择章节并写入正文。")
            return
        llm = self.workspace.llm_service
        mw = self.workspace.main_window
        self.btn_summary.setEnabled(False)
        self.btn_summary.setText("摘要生成中…")
        mw.set_task_label("生成章节摘要…")  # 摘要直接存入章节，不动建议草稿区

        async def run():
            return await self._gen_summary_text(ch)

        def done(summary: str) -> None:
            self.btn_summary.setEnabled(True)
            self.btn_summary.setText("生成章节摘要")
            if self._current_chapter_id and summary.strip():
                self.service.update(self._current_chapter_id, summary=summary.strip())
                mw.set_task_label("摘要已保存到本章")
                self.status_msg("章节摘要已保存（用于前情概览）")

        run_async(run(), on_done=done, on_error=self._on_ai_error)

    # ---------------- 草稿应用 ----------------
    def apply_suggestion(self) -> None:
        """由主窗口「应用草稿」按钮触发：把建议草稿插入光标处。"""
        mw = self.workspace.main_window
        text = mw.get_suggestion()
        if not text.strip():
            self.status_msg("⚠️ 建议草稿区是空的，没有可应用的内容")
            return
        cursor = self.editor.textCursor()
        cursor.insertText(text)
        self.editor.setTextCursor(cursor)
        if self._current_chapter_id and hasattr(mw, "clear_chapter_draft"):
            mw.clear_chapter_draft(self._current_chapter_id)  # 应用后从草稿仓库移除
        else:
            mw.clear_suggestion()
        mw.set_task_label("空闲")
        self.status_msg("草稿已插入正文，记得保存")

    def _on_ai_error(self, exc: Exception) -> None:
        mw = getattr(self.workspace, "main_window", None)
        if mw is not None:
            mw.set_task_label("空闲")
        QMessageBox.warning(self, "AI 调用失败", str(exc))

    # ---------------- 章节节奏检查（3.6） ----------------
    def _pace_check(self) -> None:
        chapters = [c for c in self.service.list(self.project_id) if (c.content or "").strip() or (c.summary or "").strip()]
        if len(chapters) < 2:
            QMessageBox.information(self, "节奏检查", "至少需要 2 章内容。")
            return
        llm = self.workspace.llm_service
        text = "\n".join(
            f"第{c.seq + 1}章「{c.title or ''}」目标：{c.objective or ''}｜摘要：{(c.summary or '')[:120]}"
            for c in chapters
        )
        self.btn_pace.setEnabled(False)
        self.btn_pace.setText("检查中…")
        coro = llm.generate_structured(
            project_id=self.project_id, module="chapter", prompt_key="task.chapter_pace_check",
            system_prompt=llm.load_prompt("global.base_prompt"),
            user_prompt=llm.load_prompt(
                "task.chapter_pace_check", project_id=self.project_id, chapters=text,
            ),
        )
        run_async(coro, on_done=self._on_pace_done, on_error=self._on_ai_error)

    def _on_pace_done(self, data: dict) -> None:
        self.btn_pace.setEnabled(True)
        self.btn_pace.setText("节奏检查")
        if not isinstance(data, dict):
            return
        lines = []
        for w in data.get("warnings") or []:
            lines.append(f"{w.get('chapter', '')}：{w.get('issue', '')} → {w.get('suggestion', '')}")
        overall = data.get("overall", "")
        text = "\n".join(lines) if lines else "未发现明显节奏问题。"
        if overall:
            text += f"\n\n总体：{overall}"
        QMessageBox.information(self, "章节节奏检查", text)

    # ---------------- 记忆提取（M3） ----------------
    def _extract_memory(self) -> None:
        ch = self._current_chapter()
        if ch is None or not self.editor.toPlainText().strip():
            QMessageBox.information(self, "提示", "请先选择章节并保存正文。")
            return
        from app.services.memory_service import MemoryService
        svc = MemoryService(self.workspace.db, self.workspace.llm_service)
        self.btn_memory.setEnabled(False)
        self.btn_memory.setText("提取中…")
        mw = self.workspace.main_window
        mw.set_task_label("记忆提取中…（事件/经历/知识/承诺）")

        async def run():
            return await svc.extract_for_chapter(ch.id, self.project_id)

        def done(data: dict) -> None:
            self.btn_memory.setEnabled(True)
            self.btn_memory.setText("记忆提取")
            mw.set_task_label("空闲")
            counts = {
                "事件": len(data.get("events", []) or []),
                "角色经历": len(data.get("character_events", []) or []),
                "信息项": len(data.get("info_updates", []) or []),
                "承诺": len(data.get("promise_updates", []) or []),
            }
            self.status_msg("记忆提取完成：" + "，".join(f"{k} {v}" for k, v in counts.items()))
            QMessageBox.information(self, "记忆提取",
                                    "完成：" + "；".join(f"{k} {v}" for k, v in counts.items())
                                    + "\n\n可在「记忆库」页审阅；正文生成时将自动注入角色记忆。")

        run_async(run(), on_done=done, on_error=self._on_ai_error)

    def status_msg(self, text: str) -> None:
        mw = getattr(self.workspace, "main_window", None)
        if mw is not None:
            mw.status.showMessage(text)

"""角色视图：列表 + 角色卡编辑 + AI 生成角色（M1）。"""
from __future__ import annotations

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
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.services.character_service import CharacterService
from app.services.world_service import WorldService
from app.utils.async_utils import run_async

# 角色定位：数据库存英文 key，界面显示中文（按重要程度排序分组）
ROLE_TYPES = ["protagonist", "antagonist", "major", "support", "minor"]
ROLE_LABELS = {
    "protagonist": "主角",
    "antagonist": "反派",
    "major": "主要角色",
    "support": "配角",
    "minor": "次要角色",
}
# 列表分组顺序（越重要越靠前）
ROLE_GROUP_ORDER = ["protagonist", "antagonist", "major", "support", "minor", ""]


def role_label(role_type: str | None) -> str:
    return ROLE_LABELS.get(role_type or "", "未分类")


class CharacterView(QWidget):
    def __init__(self, workspace, parent=None):
        super().__init__(parent)
        self.workspace = workspace
        self.service = CharacterService(workspace.db)
        self.project_id = workspace.project.id
        self._current_id: int | None = None

        splitter = QSplitter(self)

        # 左：列表
        left = QWidget()
        llay = QVBoxLayout(left)
        self.char_list = QListWidget()
        self.char_list.currentItemChanged.connect(self._on_select)
        self.btn_new = QPushButton("新建角色")
        self.btn_new.clicked.connect(self._new)
        self.btn_del = QPushButton("删除")
        self.btn_del.clicked.connect(self._delete)
        self.btn_dedupe = QPushButton("清理重名")
        self.btn_dedupe.setToolTip("合并同名的重复角色（保留最早创建的一个）")
        self.btn_dedupe.clicked.connect(self._dedupe)
        row = QHBoxLayout()
        row.addWidget(self.btn_new)
        row.addWidget(self.btn_del)
        row.addWidget(self.btn_dedupe)
        llay.addWidget(self.char_list)
        llay.addLayout(row)
        left.setMinimumWidth(220)

        # 右：角色卡表单
        right = QWidget()
        rlay = QVBoxLayout(right)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("姓名")
        self.aliases_edit = QLineEdit()
        self.aliases_edit.setPlaceholderText("别名（逗号分隔）")
        self.role_combo = QComboBox()
        for rt in ROLE_TYPES:
            self.role_combo.addItem(role_label(rt), rt)  # 显示中文、存英文 key

        self.personality_edit = QPlainTextEdit()
        self.personality_edit.setPlaceholderText("性格（如：外冷内热、腹黑、重情义）")
        self.personality_edit.setMaximumHeight(70)
        self.background_edit = QPlainTextEdit()
        self.background_edit.setPlaceholderText("背景故事")
        self.background_edit.setMaximumHeight(70)
        self.motivation_edit = QPlainTextEdit()
        self.motivation_edit.setPlaceholderText("动机与目标")
        self.motivation_edit.setMaximumHeight(60)
        self.ability_edit = QPlainTextEdit()
        self.ability_edit.setPlaceholderText("能力 / 金手指")
        self.ability_edit.setMaximumHeight(60)
        self.speech_edit = QLineEdit()
        self.speech_edit.setPlaceholderText("说话风格 / 口头禅")
        self.arc_edit = QPlainTextEdit()
        self.arc_edit.setPlaceholderText("成长弧光：起点 → 终点（一行即可）")
        self.arc_edit.setMaximumHeight(60)

        self.btn_save = QPushButton("保存")
        self.btn_save.clicked.connect(self._save)
        self.btn_fix = QPushButton("AI 修复档案")
        self.btn_fix.setToolTip("把性格/背景等字段的长文本重新拆分到正确字段（修复误填的内容）")
        self.btn_fix.clicked.connect(self._ai_fix_profile)
        self.btn_ai = QPushButton("AI 生成角色")
        self.btn_ai.setObjectName("ai_button")
        self.btn_ai.clicked.connect(self._ai_generate)
        row2 = QHBoxLayout()
        row2.addWidget(self.btn_save)
        row2.addWidget(self.btn_fix)
        row2.addStretch(1)
        row2.addWidget(self.btn_ai)

        rlay.addWidget(QLabel("姓名"))
        rlay.addWidget(self.name_edit)
        rlay.addWidget(QLabel("别名"))
        rlay.addWidget(self.aliases_edit)
        rlay.addWidget(QLabel("定位"))
        rlay.addWidget(self.role_combo)
        rlay.addWidget(QLabel("性格"))
        rlay.addWidget(self.personality_edit)
        rlay.addWidget(QLabel("背景故事"))
        rlay.addWidget(self.background_edit)
        rlay.addWidget(QLabel("动机与目标"))
        rlay.addWidget(self.motivation_edit)
        rlay.addWidget(QLabel("能力 / 金手指"))
        rlay.addWidget(self.ability_edit)
        rlay.addWidget(QLabel("说话风格"))
        rlay.addWidget(self.speech_edit)
        rlay.addWidget(QLabel("成长弧光"))
        rlay.addWidget(self.arc_edit)
        rlay.addLayout(row2)
        right.setMinimumWidth(440)

        # 右侧标签页：角色卡 / 关系图 / 对话模拟
        self.right_tabs = QTabWidget()
        self.right_tabs.addTab(right, "角色卡")
        self.right_tabs.addTab(self._build_relations_tab(), "关系图")
        self.right_tabs.addTab(self._build_dialogue_tab(), "对话模拟")

        splitter.addWidget(left)
        splitter.addWidget(self.right_tabs)
        splitter.setStretchFactor(1, 1)
        lay = QVBoxLayout(self)
        lay.addWidget(splitter)

    # ---------------- 数据 ----------------
    def refresh(self) -> None:
        """角色列表：按重要程度分组显示（主角→反派→主要角色→配角→次要角色）。"""
        from PySide6.QtCore import Qt
        chars = self.service.list(self.project_id)
        self.char_list.clear()
        groups: dict[str, list] = {}
        for c in chars:
            groups.setdefault(c.role_type or "", []).append(c)
        for rt in ROLE_GROUP_ORDER:
            members = groups.get(rt)
            if not members:
                continue
            header = QListWidgetItem(f"── {role_label(rt)}（{len(members)}）──")
            header.setFlags(Qt.ItemFlag.NoItemFlags)  # 分组标题不可选
            self.char_list.addItem(header)
            for c in members:
                item = QListWidgetItem(f"　{c.name}")
                item.setData(0x0100, c.id)  # Qt.UserRole
                self.char_list.addItem(item)
        # 选中第一个可选中的角色
        for i in range(self.char_list.count()):
            if self.char_list.item(i).data(0x0100) is not None:
                self.char_list.setCurrentRow(i)
                break
        else:
            self._clear_form()
        self._refresh_relations()
        self._refresh_dialogue_combos()

    def _on_select(self, current: QListWidgetItem | None, _prev=None) -> None:
        if current is None or current.data(0x0100) is None:
            return  # 分组标题不可选
        self._current_id = current.data(0x0100)
        c = self.service.get(self._current_id)
        if c is None:
            return
        profile = c.profile or {}
        self.name_edit.setText(c.name)
        self.aliases_edit.setText(c.aliases or "")
        idx = self.role_combo.findData(c.role_type)
        self.role_combo.setCurrentIndex(max(idx, 0))
        self.personality_edit.setPlainText(profile.get("personality", "") or "")
        self.background_edit.setPlainText(profile.get("background", "") or "")
        self.motivation_edit.setPlainText(profile.get("motivation", "") or "")
        self.ability_edit.setPlainText(profile.get("ability", "") or "")
        self.speech_edit.setText(profile.get("speech_style", "") or "")
        arc = c.arc or {}
        self.arc_edit.setPlainText(
            f"{arc.get('start', '')} → {arc.get('end', '')}" if arc else ""
        )

    def _clear_form(self) -> None:
        self._current_id = None
        for w in (self.name_edit, self.aliases_edit, self.speech_edit):
            w.clear()
        for w in (self.personality_edit, self.background_edit,
                  self.motivation_edit, self.ability_edit, self.arc_edit):
            w.clear()
        self.role_combo.setCurrentIndex(0)

    def _collect(self) -> dict:
        arc_text = self.arc_edit.toPlainText().strip()
        start, _, end = arc_text.partition("→")
        return {
            "name": self.name_edit.text().strip() or "未命名",
            "aliases": self.aliases_edit.text().strip(),
            "role_type": self.role_combo.currentData(),
            "profile": {
                "personality": self.personality_edit.toPlainText().strip(),
                "background": self.background_edit.toPlainText().strip(),
                "motivation": self.motivation_edit.toPlainText().strip(),
                "ability": self.ability_edit.toPlainText().strip(),
                "speech_style": self.speech_edit.text().strip(),
            },
            "arc": {"start": start.strip(), "end": end.strip()},
        }

    # ---------------- 操作 ----------------
    def _new(self) -> None:
        c = self.service.create(project_id=self.project_id, name="新角色")
        self.refresh()
        for i in range(self.char_list.count()):
            if self.char_list.item(i).data(0x0100) == c.id:
                self.char_list.setCurrentRow(i)
                break
        self.name_edit.setFocus()

    def _save(self) -> None:
        data = self._collect()
        if self._current_id is None:
            c = self.service.create(project_id=self.project_id, **data)
            self._current_id = c.id
        else:
            self.service.update(self._current_id, **data)
        self.refresh()

    def _delete(self) -> None:
        if self._current_id is not None:
            self.service.delete(self._current_id)
            self.refresh()

    def _ai_fix_profile(self) -> None:
        """把当前角色的档案文本重新拆分到正确字段（修复误填）。"""
        if self._current_id is None:
            QMessageBox.information(self, "提示", "请先选中一个角色。")
            return
        c = self.service.get(self._current_id)
        if c is None:
            return
        profile = c.profile or {}
        merged = "\n".join(
            f"{k}：{v}" for k, v in profile.items() if v
        )
        if not merged.strip():
            QMessageBox.information(self, "提示", "该角色档案为空，无需修复。")
            return
        llm = self.workspace.llm_service
        self.btn_fix.setEnabled(False)
        self.btn_fix.setText("修复中…")
        coro = llm.generate_structured(
            project_id=self.project_id, module="character", prompt_key="task.character_parse",
            system_prompt=llm.load_prompt("global.base_prompt"),
            user_prompt=llm.load_prompt(
                "task.character_parse", project_id=self.project_id,
                text=merged + f"\n\n【角色名】{c.name}",
            ),
        )
        run_async(coro, on_done=self._on_fix_done, on_error=self._on_ai_error)

    def _on_fix_done(self, data: dict) -> None:
        self.btn_fix.setEnabled(True)
        self.btn_fix.setText("AI 修复档案")
        profile = (data or {}).get("profile") or {}
        if not profile or self._current_id is None:
            QMessageBox.warning(self, "修复", "未能解析出分字段档案。")
            return
        self.service.update(self._current_id, profile=profile)
        self.refresh()
        for i in range(self.char_list.count()):
            if self.char_list.item(i).data(0x0100) == self._current_id:
                self.char_list.setCurrentRow(i)
                break
        QMessageBox.information(self, "修复完成", "已重新拆分到性格/背景/动机/能力/说话风格各字段，请审阅后保存。")

    def _dedupe(self) -> None:
        removed = self.service.dedupe_characters(self.project_id)
        self.refresh()
        QMessageBox.information(
            self, "清理重名角色",
            f"已合并删除 {removed} 个重名角色（保留最早创建的一个）。" if removed else "没有发现重名角色。"
        )

    # ---------------- AI ----------------
    def _ai_generate(self) -> None:
        llm = self.workspace.llm_service
        p = self.workspace.project
        entries = WorldService(self.workspace.db).list_entries(self.project_id, None)
        world_text = "\n".join(f"- {e.title}: {e.content[:100]}" for e in entries[:15]) or "（暂无设定）"
        requirement = self.name_edit.text().strip() or "生成一个主要角色"
        self.btn_ai.setEnabled(False)
        self.btn_ai.setText("生成中…")
        coro = llm.generate_structured(
            project_id=self.project_id,
            module="character",
            prompt_key="task.character_generate",
            system_prompt=llm.load_prompt("global.base_prompt"),
            user_prompt=llm.load_prompt(
                "task.character_generate", project_id=self.project_id,
                genre=p.genre or "", world_entries=world_text, requirement=requirement,
            ),
        )
        run_async(coro, on_done=self._on_ai_done, on_error=self._on_ai_error)

    def _on_ai_done(self, data: dict) -> None:
        self.btn_ai.setEnabled(True)
        self.btn_ai.setText("AI 生成角色")
        if not isinstance(data, dict) or not data.get("name"):
            QMessageBox.warning(self, "AI 生成", "模型未返回有效角色卡。")
            return
        c = self.service.create(
            project_id=self.project_id,
            name=data.get("name", "新角色"),
            role_type=data.get("role_type", "support"),
            aliases=data.get("aliases", ""),
            profile=data.get("profile") or {},
            arc=data.get("arc"),
        )
        self.refresh()
        for i in range(self.char_list.count()):
            if self.char_list.item(i).data(0x0100) == c.id:
                self.char_list.setCurrentRow(i)
                break
        QMessageBox.information(self, "AI 生成角色", f"已生成角色「{c.name}」，请审阅并调整。")

    def _on_ai_error(self, exc: Exception) -> None:
        self.btn_ai.setEnabled(True)
        self.btn_ai.setText("AI 生成角色")
        QMessageBox.warning(self, "AI 调用失败", str(exc))

    # ================= 关系图 =================
    def _build_relations_tab(self) -> QWidget:
        tab = QWidget()
        lay = QVBoxLayout(tab)
        from PySide6.QtWidgets import QGraphicsView
        self.graph_view = QGraphicsView()
        self.graph_view.setMinimumHeight(220)
        lay.addWidget(self.graph_view)
        lay.addWidget(QLabel("关系列表"))
        self.relation_list = QListWidget()
        lay.addWidget(self.relation_list)
        row = QHBoxLayout()
        self.btn_ai_rel = QPushButton("AI 生成关系网")
        self.btn_ai_rel.clicked.connect(self._ai_relations)
        self.btn_add_rel = QPushButton("手动添加关系")
        self.btn_add_rel.clicked.connect(self._add_relation)
        self.btn_del_rel = QPushButton("删除选中关系")
        self.btn_del_rel.clicked.connect(self._delete_relation)
        row.addWidget(self.btn_ai_rel)
        row.addWidget(self.btn_add_rel)
        row.addWidget(self.btn_del_rel)
        lay.addLayout(row)
        return tab

    def _refresh_relations(self) -> None:
        relations = self.service.list_relations(self.project_id)
        chars = {c.id: c for c in self.service.list(self.project_id)}
        self.relation_list.clear()
        for r in relations:
            a = chars.get(r.from_id)
            b = chars.get(r.to_id)
            item = QListWidgetItem(f"{a.name if a else '?'} —[{r.relation}]→ {b.name if b else '?'}：{r.description or ''}")
            item.setData(0x0100, r.id)
            self.relation_list.addItem(item)
        self._draw_relations(relations, chars)

    def _draw_relations(self, relations, chars: dict) -> None:
        import math
        from PySide6.QtGui import QPen
        from PySide6.QtWidgets import QGraphicsScene
        scene = QGraphicsScene(self.graph_view)
        if not chars:
            self.graph_view.setScene(scene)
            return
        ids = list(chars.keys())
        n = len(ids)
        r = 140.0
        pos = {}
        for i, cid in enumerate(ids):
            angle = 2 * math.pi * i / n
            pos[cid] = (r * math.cos(angle), r * math.sin(angle))
        pen = QPen()
        for cid, (x, y) in pos.items():
            scene.addEllipse(x - 40, y - 18, 80, 36, pen)
            scene.addText(chars[cid].name).setPos(x - 35, y - 14)
        for rel in relations:
            a = pos.get(rel.from_id)
            b = pos.get(rel.to_id)
            if a is None or b is None:
                continue
            scene.addLine(a[0], a[1], b[0], b[1], pen)
            mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            scene.addText(rel.relation or "").setPos(mid[0], mid[1])
        self.graph_view.setScene(scene)
        self.graph_view.fitInView(scene.itemsBoundingRect().adjusted(-40, -40, 40, 40))

    def _ai_relations(self) -> None:
        chars = self.service.list(self.project_id)
        if len(chars) < 2:
            QMessageBox.information(self, "关系网", "至少需要 2 个角色。")
            return
        llm = self.workspace.llm_service
        cast_text = "\n".join(
            f"- {c.name}（{c.role_type or '?'}）：{c.profile.get('personality', '')}；动机 {c.profile.get('motivation', '')}"
            for c in chars
        )
        self.btn_ai_rel.setEnabled(False)
        self.btn_ai_rel.setText("生成中…")
        coro = llm.generate_structured(
            project_id=self.project_id, module="character",
            prompt_key="task.character_relations",
            system_prompt=llm.load_prompt("global.base_prompt"),
            user_prompt=llm.load_prompt(
                "task.character_relations", project_id=self.project_id,
                cast_cards=cast_text,
            ),
        )
        run_async(coro, on_done=self._on_relations_done, on_error=self._on_ai_error)

    def _on_relations_done(self, data: dict) -> None:
        self.btn_ai_rel.setEnabled(True)
        self.btn_ai_rel.setText("AI 生成关系网")
        relations = (data or {}).get("relations") or []
        if not relations:
            QMessageBox.warning(self, "关系网", "模型未返回关系。")
            return
        chars = {c.name: c for c in self.service.list(self.project_id)}
        self.service.clear_relations(self.project_id)
        added = 0
        for rel in relations:
            a = chars.get(rel.get("from", ""))
            b = chars.get(rel.get("to", ""))
            if a and b:
                self.service.add_relation(
                    project_id=self.project_id, from_id=a.id, to_id=b.id,
                    relation=rel.get("relation", ""), description=rel.get("description", ""),
                )
                added += 1
        self._refresh_relations()
        QMessageBox.information(self, "关系网", f"已生成 {added} 条关系。")

    def _add_relation(self) -> None:
        chars = self.service.list(self.project_id)
        if len(chars) < 2:
            QMessageBox.information(self, "关系网", "至少需要 2 个角色。")
            return
        from PySide6.QtWidgets import QInputDialog
        names = [c.name for c in chars]
        a, ok = QInputDialog.getItem(self, "添加关系", "角色 A：", names, 0, False)
        if not ok:
            return
        b, ok = QInputDialog.getItem(self, "添加关系", "角色 B：", names, 0, False)
        if not ok:
            return
        rel, ok = QInputDialog.getText(self, "添加关系", "关系类型（如：盟友/宿敌/师徒）：")
        if not ok:
            return
        desc, ok = QInputDialog.getText(self, "添加关系", "一句话描述（可留空）：")
        if not ok:
            return
        ca = next(c for c in chars if c.name == a)
        cb = next(c for c in chars if c.name == b)
        self.service.add_relation(project_id=self.project_id, from_id=ca.id, to_id=cb.id,
                                  relation=rel.strip(), description=desc.strip())
        self._refresh_relations()

    def _delete_relation(self) -> None:
        item = self.relation_list.currentItem()
        if item is None:
            return
        self.service.delete_relation(item.data(0x0100))
        self._refresh_relations()

    # ================= 对话模拟 =================
    def _build_dialogue_tab(self) -> QWidget:
        tab = QWidget()
        lay = QVBoxLayout(tab)
        self.char_a_combo = QComboBox()
        self.char_b_combo = QComboBox()
        row = QHBoxLayout()
        row.addWidget(QLabel("角色 A"))
        row.addWidget(self.char_a_combo)
        row.addWidget(QLabel("角色 B"))
        row.addWidget(self.char_b_combo)
        lay.addLayout(row)
        self.scene_edit = QPlainTextEdit()
        self.scene_edit.setPlaceholderText("场景（如：深夜茶馆，两人为线报交易）")
        self.scene_edit.setMaximumHeight(60)
        self.seed_edit = QLineEdit()
        self.seed_edit.setPlaceholderText("开场台词（可留空，AI 会补全）")
        lay.addWidget(QLabel("场景"))
        lay.addWidget(self.scene_edit)
        lay.addWidget(QLabel("开场台词"))
        lay.addWidget(self.seed_edit)
        self.btn_dialogue = QPushButton("模拟对话")
        self.btn_dialogue.clicked.connect(self._simulate_dialogue)
        lay.addWidget(self.btn_dialogue)
        self.dialogue_view = QPlainTextEdit()
        self.dialogue_view.setReadOnly(True)
        lay.addWidget(self.dialogue_view)
        return tab

    def _refresh_dialogue_combos(self) -> None:
        chars = self.service.list(self.project_id)
        for combo in (self.char_a_combo, self.char_b_combo):
            combo.clear()
            for c in chars:
                combo.addItem(c.name, c.id)
        if len(chars) > 1:
            self.char_b_combo.setCurrentIndex(1)

    def _simulate_dialogue(self) -> None:
        chars = self.service.list(self.project_id)
        aid = self.char_a_combo.currentData()
        bid = self.char_b_combo.currentData()
        if aid is None or bid is None or aid == bid:
            QMessageBox.information(self, "对话模拟", "请选择两个不同的角色。")
            return
        ca = next(c for c in chars if c.id == aid)
        cb = next(c for c in chars if c.id == bid)
        llm = self.workspace.llm_service
        self.btn_dialogue.setEnabled(False)
        self.btn_dialogue.setText("对话中…")
        self.dialogue_view.clear()

        async def run():
            text = ""
            async for chunk in llm.generate_stream(
                project_id=self.project_id, module="character",
                prompt_key="task.character_dialogue",
                system_prompt=llm.load_prompt("global.base_prompt"),
                user_prompt=llm.load_prompt(
                    "task.character_dialogue", project_id=self.project_id,
                    scene=self.scene_edit.toPlainText().strip() or "日常场景",
                    char_a=f"{ca.name}：{ca.profile.get('personality','')}；说话风格：{ca.profile.get('speech_style','')}",
                    char_b=f"{cb.name}：{cb.profile.get('personality','')}；说话风格：{cb.profile.get('speech_style','')}",
                    seed=self.seed_edit.text().strip(),
                    turns=6,
                ),
            ):
                text += chunk
                self.dialogue_view.setPlainText(text)
            return text

        def done(_t):
            self.btn_dialogue.setEnabled(True)
            self.btn_dialogue.setText("模拟对话")

        run_async(run(), on_done=done, on_error=self._on_ai_error)

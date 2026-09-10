"""AI 创作对话面板：连续对话 + 项目上下文 + 对话内确认式修改 + 会话持久化。

- 修改意图（把/改/增/删…）→ 生成修改计划 → 对话面板内确认卡（勾选执行 / 补充说明 / 取消），不打断对话
- 会话长期保存：自动存库，可切换历史会话恢复上下文（类似 Claude Code 的对话保存）
"""
from __future__ import annotations

import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.services.chat_service import ChatService, is_modification_intent
from app.services.command_service import CommandService
from app.utils.async_utils import run_async

MAX_VIEW_CHARS = 120_000


class ChatPanel(QWidget):
    """右侧 AI 助手 dock 的"对话"页；打开项目后绑定 workspace 以携带上下文。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.workspace = None
        self.service: ChatService | None = None
        self.command: CommandService | None = None
        self._history: list[dict] = []
        self._current_session_id: int | None = None
        self._plan: dict = {}
        self._orch = None              # 编排器（interrupt 续跑用）
        self._orch_thread_id: str | None = None  # 图线程 id（checkpointer 断点续跑）
        self._orch_applied: dict | None = None
        self._title_pending_ai: tuple[int, str] | None = None  # (session_id, 首条用户消息)，首轮回复后让 AI 润色标题
        self._plan_running = False      # 防重入：计划分析中
        self._heartbeat_task = None     # 心跳任务（计划超过 1 分钟仍未完成时每分钟播报）

        lay = QVBoxLayout(self)

        # 会话管理行
        sess_row = QHBoxLayout()
        self.session_combo = QComboBox()
        self.session_combo.currentIndexChanged.connect(self._on_session_changed)
        self.btn_new_session = QPushButton("＋ 新对话")
        self.btn_new_session.setObjectName("ai_button")
        self.btn_new_session.clicked.connect(self._new_session)
        sess_row.addWidget(self.session_combo, 1)
        sess_row.addWidget(self.btn_new_session)
        lay.addLayout(sess_row)

        hint = QLabel("💬 聊点子/让 AI 补充设定/剧情；说『把…改成/更新…/…入库/生成…细纲』等可**直接修改项目**（会先给计划让你确认）")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#888;")
        lay.addWidget(hint)

        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(False)
        self.view.setStyleSheet("font-size:13px;")
        lay.addWidget(self.view, 1)

        # ---- 修改计划确认卡片（默认隐藏，不打断对话） ----
        self.plan_frame = QFrame()
        self.plan_frame.setObjectName("plan_card")
        self.plan_frame.setVisible(False)
        self.plan_frame.setStyleSheet(
            "QFrame#plan_card{background:#FDF6E3;border:1px solid #C9A227;border-radius:8px;padding:6px;}")
        pfl = QVBoxLayout(self.plan_frame)
        self.plan_impact = QLabel("")
        self.plan_impact.setWordWrap(True)
        self.plan_impact.setStyleSheet("color:#1F2A44;font-weight:bold;")
        pfl.addWidget(self.plan_impact)
        self.plan_list = QListWidget()
        self.plan_list.setMaximumHeight(130)
        self.plan_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        pfl.addWidget(self.plan_list)
        prow = QHBoxLayout()
        self.btn_plan_apply = QPushButton("✅ 执行勾选的修改")
        self.btn_plan_apply.setObjectName("ai_button")
        self.btn_plan_apply.clicked.connect(self._apply_plan)
        self.btn_plan_more = QPushButton("📝 补充说明")
        self.btn_plan_more.clicked.connect(self._plan_needs_more)
        self.btn_plan_cancel = QPushButton("取消")
        self.btn_plan_cancel.clicked.connect(self._apply_cancel)
        prow.addWidget(self.btn_plan_apply)
        prow.addWidget(self.btn_plan_more)
        prow.addWidget(self.btn_plan_cancel)
        pfl.addLayout(prow)
        lay.addWidget(self.plan_frame)

        self.input = QTextEdit()
        self.input.setPlaceholderText("例如：我有个点子… 或 把第3章的目标改成…（Enter 发送，Shift+Enter 换行）")
        self.input.setMaximumHeight(80)
        self.input.installEventFilter(self)
        lay.addWidget(self.input)

        row = QHBoxLayout()
        self.btn_send = QPushButton("发送")
        self.btn_send.setObjectName("ai_button")
        self.btn_send.clicked.connect(self._send)
        self.btn_idea = QPushButton("存为点子")
        self.btn_idea.clicked.connect(self._save_as_idea)
        self.btn_char = QPushButton("存为角色")
        self.btn_char.clicked.connect(self._save_as_character)
        self.btn_world = QPushButton("存为设定")
        self.btn_world.clicked.connect(self._save_as_world)
        row.addWidget(self.btn_send)
        row.addWidget(self.btn_idea)
        row.addWidget(self.btn_char)
        row.addWidget(self.btn_world)
        lay.addLayout(row)

    # ---------------- 绑定 / 会话 ----------------
    def bind_workspace(self, workspace) -> None:
        self.workspace = workspace
        self.service = ChatService(workspace.db, workspace.llm_service)
        self.command = CommandService(workspace.db, workspace.llm_service)
        self.service.backfill_default_titles(workspace.project.id)  # 历史「新对话」补题
        self._load_sessions()

    def _load_sessions(self, select_id: int | None = None) -> None:
        sessions = self.service.list_sessions(self.workspace.project.id)
        self.session_combo.blockSignals(True)
        self.session_combo.clear()
        for s in sessions:
            self.session_combo.addItem(s.title, s.id)
        if sessions:
            idx = 0
            if select_id is not None:
                for i, s in enumerate(sessions):
                    if s.id == select_id:
                        idx = i
                        break
            self.session_combo.setCurrentIndex(idx)
            self.session_combo.blockSignals(False)
            self._load_session(sessions[idx].id)
        else:
            self.session_combo.blockSignals(False)
            self._new_session()

    def _on_session_changed(self, idx: int) -> None:
        sid = self.session_combo.currentData()
        if sid is not None and sid != self._current_session_id:
            self._load_session(sid)

    def _new_session(self) -> None:
        if self.service is None:
            return
        s = self.service.create_session(self.workspace.project.id, "新对话")
        self._load_sessions(select_id=s.id)

    def _load_session(self, session_id: int) -> None:
        self._current_session_id = session_id
        self._title_pending_ai = None
        msgs = self.service.load_history(session_id)
        self._history = [{"role": m.role, "content": m.content} for m in msgs]
        self.view.clear()
        if not self._history:
            self._append_system("👋 你好！可以聊点子/设定/剧情，或让我直接修改项目（说『把…改成…』即可）。")
        else:
            for m in self._history:
                if m["role"] == "user":
                    self._append_user(m["content"])
                elif m["role"] == "assistant":
                    self._append_ai(m["content"])

    def _save_message(self, role: str, content: str) -> None:
        if self._current_session_id is None or self.service is None:
            return
        self.service.save_message(self._current_session_id, role, content)
        if role == "user":
            # 首条用户消息：立刻把「新对话」改成消息摘要标题，便于区分会话
            new_title = self.service.maybe_set_default_title(self._current_session_id, content)
            if new_title:
                self._update_combo_title(self._current_session_id, new_title)
                self._title_pending_ai = (self._current_session_id, content)

    def _update_combo_title(self, session_id: int, title: str) -> None:
        """就地更新下拉框中某会话的显示标题（不触发重新加载）。"""
        for i in range(self.session_combo.count()):
            if self.session_combo.itemData(i) == session_id:
                self.session_combo.setItemText(i, title)
                break

    def _maybe_ai_title(self, ai_text: str) -> None:
        """首轮回复完成后，用 AI 把消息摘要标题润色为更贴切的主题标题（失败静默保留摘要标题）。"""
        pending = self._title_pending_ai
        if not pending or not ai_text.strip():
            return
        sid, user_text = pending
        if sid != self._current_session_id:
            return
        self._title_pending_ai = None

        async def gen():
            return await self.service.generate_title(
                self.workspace.project.id, user_text, ai_text.strip())

        def done(title) -> None:
            if title:
                self.service.rename_session(sid, title)
                self._update_combo_title(sid, title)

        run_async(gen(), on_done=done, on_error=lambda exc: None)

    # ---------------- 发送 ----------------
    def _send(self) -> None:
        if self.service is None:
            self._append_system("⚠️ 请先打开一个项目。")
            return
        text = self.input.toPlainText().strip()
        if not text:
            return
        self.input.clear()

        # 意图分类：优先走 LLM（可在模型与路由设置中指定本地小模型），失败回退关键词
        self.btn_send.setEnabled(False)
        self.btn_send.setText("识别意图…")

        async def classify():
            return await self.service.classify_intent(self.workspace.project.id, text)

        run_async(classify(),
                  on_done=lambda is_mod: self._dispatch_send(text, bool(is_mod)),
                  on_error=self._on_intent_error(text))

    def _on_intent_error(self, text: str):
        """意图分类异常兜底：回退关键词匹配后继续分发。"""
        def handler(exc):
            self._dispatch_send(text, is_modification_intent(text))
        return handler

    def _dispatch_send(self, text: str, is_mod: bool) -> None:
        self.btn_send.setEnabled(True)
        self.btn_send.setText("发送")
        if is_mod:
            if getattr(self, "_plan_running", False):
                # 防重入：Enter 键可绕过按钮禁用，避免两次并发编排互抢状态/挂死
                self._append_system("⏳ 上一个修改计划还在分析中，请等它出计划卡片（或超时）后再发新指令。")
                return
            self._history.append({"role": "user", "content": text})
            self._append_user(text)
            self._save_message("user", text)
            # 整卷拆章快速路径：「把第一卷拆成章节细纲」→ 整卷规划（标题+细纲一次产出，避免撞车）
            if self._try_arc_split(text):
                return
            # 其余修改指令一律走编排器（LLM 分解+实体 Agent），
            # 不再用关键词快速路径——正则误解语义（如把「设定好角色」当成生成设定）只会添堵
            self._make_plan(text)
            return

        self._history.append({"role": "user", "content": text})
        self._append_user(text)
        self._save_message("user", text)

        self.btn_send.setEnabled(False)
        self.btn_send.setText("思考中…")
        self._append_ai("")

        async def consume():
            buf = ""
            async for chunk in self.service.chat_stream(
                self.workspace.project.id, self._history[:-1], text
            ):
                buf += chunk
                self._append_raw(chunk)
            return buf

        def done(full: str) -> None:
            self.btn_send.setEnabled(True)
            self.btn_send.setText("发送")
            if full.strip():
                self._history.append({"role": "assistant", "content": full.strip()})
                self._save_message("assistant", full.strip())
                self._maybe_ai_title(full)

        run_async(consume(), on_done=done, on_error=self._on_error)

    # ---------------- 修改计划（对话内确认） ----------------
    def _try_arc_split(self, text: str) -> bool:
        """整卷拆章快速路径：「把第一卷拆成章节细纲/拆章」→ 整卷规划。

        返回 True=已处理（生成计划展示）；False=不适用。
        """
        import re
        if not re.search(r"拆章|拆成.{0,6}(章|细纲)|拆分.{0,4}卷|卷.{0,4}拆", text):
            return False
        from app.services.outline_service import OutlineService
        ol = OutlineService(self.workspace.db)
        arcs = ol.list(self.workspace.project.id)
        if not arcs:
            return False
        # 匹配「第N卷」或卷名；默认第一卷
        arc = arcs[0]
        m = re.search(r"第\s*(\d+)\s*卷", text)
        if m and 0 < int(m.group(1)) <= len(arcs):
            arc = arcs[int(m.group(1)) - 1]
        else:
            for a in arcs:
                if a.title and a.title in text:
                    arc = a
                    break
        self._append_system(
            f"📐 整卷拆章：正在把「{arc.title or f'第{arc.seq + 1}卷'}」规划为章节+细纲"
            "（一次统筹整卷剧情，情节先于标题，章节间不撞车）……")
        self.btn_send.setEnabled(False)
        self._plan_begin()  # 快速路径也纳入看门狗/防重入
        self._orch = None              # 本路径不走编排器，清掉可能残留的 interrupt 状态
        self._orch_thread_id = None

        async def run():
            plots, chapters = await ol.plan_chapters(arc.id, self.workspace.llm_service)
            changes = []
            # 情节线先进入计划（确认后由执行端创建并挂载章节）
            for i, p in enumerate(plots or []):
                changes.append({
                    "domain": "plot", "action": "add", "target": p.get("title", ""),
                    "after": p.get("summary", "") or "",
                    "conflict": p.get("conflict", "") or "",
                    "resolution": p.get("resolution", "") or "",
                    "beats": p.get("beats") or [],
                    "arc": arc.title or "",
                    "note": f"情节 {i + 1}/{len(plots)}",
                })
            for c in chapters:
                plot = c.get("plot") or []
                changes.append({
                    "domain": "chapter", "action": "add", "target": c.get("title", ""),
                    "after": c.get("objective", "") or "",
                    "beats": plot,
                    "plot_title": c.get("plot_title", "") or "",
                    "arc": arc.title or "",
                    "outline": {k: c[k] for k in (
                        "style_hint", "scene", "characters", "dialogue_hooks",
                        "humor_points", "ending_hook", "target_words")
                        if c.get(k) not in (None, "", [])},
                    "note": f"整卷拆章（{len(plot)} 个节拍）",
                })
            return {"impact_summary": f"整卷拆章：{len(plots or [])} 个情节 + {len(chapters)} 章（每章含细纲节拍）",
                    "changes": changes, "new_settings": []}

        run_async(run(), on_done=self._on_plan_ready, on_error=self._on_error)
        return True

    def _history_summary(self, limit: int = 4) -> str:
        """对话历史摘要（供角色解析携带上下文）。"""
        parts = []
        for m in self._history[-limit:]:
            role = "我" if m["role"] == "user" else "AI"
            parts.append(f"{role}：{(m['content'] or '')[:120]}")
        return "【对话历史】\n" + "\n".join(parts) if parts else ""

    # ---------------- 计划看门狗与心跳（防"无声卡死"） ----------------
    def _plan_begin(self) -> None:
        """标记计划开始 + 启动心跳（每分钟报一次仍在等待，超时由看门狗兜底）。"""
        self._plan_running = True
        self._start_heartbeat()

    def _plan_end(self) -> None:
        self._plan_running = False
        self._stop_heartbeat()

    def _start_heartbeat(self) -> None:
        import asyncio
        self._stop_heartbeat()

        async def beat():
            minutes = 0
            while True:
                await asyncio.sleep(60)
                minutes += 1
                self._append_system(
                    f"⏳ 仍在等待模型响应……（已 {minutes} 分钟；超长文本生成较慢属正常，"
                    "异常可去「日志」页看最近一次 LLM 调用）")

        self._heartbeat_task = asyncio.get_event_loop().create_task(beat())

    def _stop_heartbeat(self) -> None:
        task = getattr(self, "_heartbeat_task", None)
        if task is not None:
            task.cancel()
            self._heartbeat_task = None

    def _make_plan(self, command: str) -> None:
        self._append_system("🔧 检测到修改意图，正在分析影响范围并生成修改计划……")
        self.btn_send.setEnabled(False)
        self._plan_begin()

        async def run():
            # LangGraph 编排：意图识别 → 任务分解 → 实体 Agent 按顺序执行（携带对话历史理解指代）
            import asyncio
            from app.workflows.agent_orchestrator import Orchestrator
            self._orch = Orchestrator(self.workspace.db, self.workspace.llm_service,
                                      self.workspace.project.id,
                                      on_progress=self._append_system)  # 进度实时播报到对话
            self._orch_thread_id = None
            try:
                # 看门狗：整体 30 分钟无结果才超时（本地部署、超长文本生成可能很慢；
                # 只作最后兜底防止无声挂死，正常流程不会碰到）
                result = await asyncio.wait_for(
                    self._orch.run(command, history=self._history[:-1]), timeout=1800)
                # 编排已正常跑完：无论有没有变更都直接采用它的结论——
                # 不再走无对话历史的兜底（兜底看不见「以上内容」，会产出"指代不明"的蠢澄清）
                if result.interrupted:
                    self._orch_thread_id = result.thread_id
                return result.plan
            except asyncio.TimeoutError:
                self._orch = None
                raise TimeoutError(
                    "编排超时（30 分钟无进展）——可能是模型连接挂起或本地模型服务无响应，"
                    "请检查后重发指令；进度消息停留的那一步就是卡住的位置。")
            except Exception as exc:
                # 编排技术故障才回退单步计划（携带对话历史，避免指代丢失）——异常必须可见，不能静默
                from app.core.logger import get_logger
                get_logger("chat_panel").exception("编排器异常，回退单步计划: %s", exc)
                self._append_system(f"⚠️ 编排器异常（{type(exc).__name__}: {exc}），改用单步计划重试……")
            self._orch = None
            from app.workflows.agent_orchestrator import Orchestrator as _Orch
            history_ctx = _Orch._history_text(self._history[:-1])
            return await self.command.plan(
                self.workspace.project.id,
                command + "\n\n（以下是对话历史，指令中的指代以此为准）" + history_ctx)

        run_async(run(), on_done=self._on_plan_ready, on_error=self._on_error)

    def _on_plan_ready(self, plan: dict) -> None:
        self._plan_end()
        self.btn_send.setEnabled(True)
        self._plan = plan or {}
        clarification = self._plan.get("clarification")
        if clarification:
            # 需要澄清：不弹窗，直接在对话里提问，等待用户回复补充
            self._append_ai(f"我需要先弄清楚一下：{clarification}")
            self._history.append({"role": "assistant", "content": f"需要澄清：{clarification}"})
            self._save_message("assistant", f"需要澄清：{clarification}")
            self.input.setFocus()
            return
        changes = self._plan.get("changes") or []
        if not changes:
            self._append_system("⚠️ 没有识别出可执行的修改项，可以补充说明后再试。")
            return
        self.plan_impact.setText(
            f"将修改 {len(changes)} 项｜影响：{str(self._plan.get('impact_summary') or '')[:80]}"
        )
        self.plan_list.clear()
        ACTION_LABEL = {"add": "＋新增", "update": "✎ 修改", "delete": "✕ 删除"}
        for i, c in enumerate(changes):
            # 模型常输出显式 null（"after": null）——必须 or '' 兜底，否则 None[:40] 静默崩掉卡片
            action = ACTION_LABEL.get(c.get("action") or "update", "✎ 修改")
            target = str(c.get("target") or "")
            after_text = str(c.get("after") or "")
            if not after_text and c.get("beats"):
                after_text = f"（{len(c['beats'])} 个节拍）"
            note = str(c.get("note") or "")
            item = QListWidgetItem(
                f"[{action}] [{c.get('domain') or '?'}] {target}：{after_text[:40]}"
                + (f"（{note}）" if note else "")
            )
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(0x0100, i)
            self.plan_list.addItem(item)
        self.plan_frame.setVisible(True)
        self._append_system("📋 修改计划已生成（见下方卡片）——勾选要执行的项后点「执行」，或继续补充说明。")

    def _apply_plan(self) -> None:
        accept = set()
        for r in range(self.plan_list.count()):
            item = self.plan_list.item(r)
            if item.checkState() == Qt.CheckState.Checked:
                accept.add(item.data(0x0100))
        if not accept:
            self._append_system("⚠️ 请至少勾选一项变更。")
            return
        self.plan_frame.setVisible(False)
        self.btn_plan_apply.setEnabled(False)
        self.btn_plan_apply.setText("执行中…")

        async def run():
            if self._orch is not None and self._orch_thread_id:
                # LangGraph interrupt 续跑：确认后图继续执行 apply 节点
                return await self._orch.resume(self._orch_thread_id, True, list(accept))
            return None

        def done(result) -> None:
            self.btn_plan_apply.setEnabled(True)
            self.btn_plan_apply.setText("✅ 执行勾选的修改")
            if result is not None and result.applied is not None:
                applied = result.applied
            else:
                # 回退路径（快速生成/编排失败）：直接执行
                applied = self.command.apply_plan(self.workspace.project.id, self._plan, accept)
            self._finish_apply(applied)

        run_async(run(), on_done=done, on_error=self._on_apply_error)

    def _apply_cancel(self) -> None:
        """取消计划：编排器 resume 取消。"""
        self.plan_frame.setVisible(False)
        if self._orch is not None and self._orch_thread_id:
            run_async(self._orch.resume(self._orch_thread_id, False, []))
        self._orch = None
        self._orch_thread_id = None

    def _finish_apply(self, applied: dict) -> None:
        # 执行后刷新所有模块页（避免"已执行但页面没看到"）
        if self.workspace is not None:
            self.workspace.refresh_all()
        parts = [f"✅ 已执行 {applied.get('applied', 0)} 项修改"]
        for note in applied.get("applied_notes") or []:
            parts.append(f"   · {note}")
        if applied.get("skipped"):
            parts.append("⏭️ 跳过：" + "；".join(applied["skipped"]))
        parts.append("可在各模块页查看，受影响章节可重新「记忆提取」。")
        self._append_system("\n".join(parts))
        self._save_message("assistant",
                           "（执行修改：" + "；".join(applied.get("applied_notes") or []) + "）")

    def _on_apply_error(self, exc: Exception) -> None:
        self.btn_plan_apply.setEnabled(True)
        self.btn_plan_apply.setText("✅ 执行勾选的修改")
        self._append_system(f"⚠️ 执行失败：{exc}")

    def _plan_needs_more(self) -> None:
        self.plan_frame.setVisible(False)
        self._append_system("📝 请在下面对话框补充说明，我会重新生成修改计划。")
        self.input.setFocus()

    # ---------------- 事件 ----------------
    def eventFilter(self, obj, event) -> bool:
        if obj is self.input and event.type() == event.Type.KeyPress:
            if (event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                    and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier)):
                self._send()
                return True
        return super().eventFilter(obj, event)

    def _on_error(self, exc: Exception) -> None:
        self._plan_end()
        self.btn_send.setEnabled(True)
        self.btn_send.setText("发送")
        self._append_ai(f"⚠️ 调用失败：{exc}")

    # ---------------- 展示 ----------------
    def _append_system(self, text: str) -> None:
        self._append(f'<div style="color:#888; margin:6px 0;">{html.escape(text)}</div>')

    def _append_user(self, text: str) -> None:
        safe = html.escape(text).replace("\n", "<br>")
        self._append(
            '<div style="text-align:right; color:#3B5998; font-size:11px; '
            'margin-top:8px; font-weight:bold;">🧑 我</div>'
            f'<div style="background:#DCEBFE; border:1px solid #B6D0F5; border-radius:10px 10px 2px 10px; '
            f'padding:8px 12px; margin:2px 0 6px 40px; text-align:right; color:#1F2A44;">{safe}</div>'
        )

    def _append_ai(self, text: str) -> None:
        safe = html.escape(text).replace("\n", "<br>")
        self._append(
            '<div style="color:#8A6D1B; font-size:11px; margin-top:8px; font-weight:bold;">🤖 AI 助手</div>'
            f'<div style="background:#FFF8E6; border:1px solid #E8D9A6; border-radius:10px 10px 10px 2px; '
            f'padding:8px 12px; margin:2px 40px 6px 0; color:#3A3524;">{safe}</div>'
        )

    def _append_raw(self, text: str) -> None:
        safe = html.escape(text).replace("\n", "<br>")
        self.view.insertHtml(safe)
        self.view.moveCursor(self.view.textCursor().MoveOperation.End)
        if len(self.view.toPlainText()) > MAX_VIEW_CHARS:
            self.view.clear()

    def _append(self, block: str) -> None:
        self.view.append(block)
        self.view.moveCursor(self.view.textCursor().MoveOperation.End)

    # ---------------- 入库（存为点子/角色/设定） ----------------
    def _last_ai_text(self) -> str:
        ai_msgs = [m for m in self._history if m["role"] == "assistant"]
        return ai_msgs[-1]["content"] if ai_msgs else ""

    def _save_as_idea(self) -> None:
        if self.workspace is None:
            self._append_system("⚠️ 请先打开项目。")
            return
        content = self._last_ai_text()
        if not content:
            self._append_system("⚠️ 还没有 AI 回复可保存。")
            return
        from PySide6.QtWidgets import QInputDialog
        title, ok = QInputDialog.getText(self, "存为点子", "点子标题：", text=content[:30])
        if not ok or not title.strip():
            return
        from app.services.idea_service import IdeaService
        idea = IdeaService(self.workspace.db).create(
            project_id=self.workspace.project.id, title=title.strip(),
            summary=content, status="draft",
        )
        self._append_system(f"✅ 已保存为点子「{idea.title}」，可在「点子」页查看。")

    def _save_as_character(self) -> None:
        text = self._last_ai_text()
        if not text:
            self._append_system("⚠️ 还没有 AI 整理的角色档案可入库。")
            return
        self.btn_char.setEnabled(False)
        self.btn_char.setText("解析入库中…")

        async def run():
            return await self.service.parse_character(self.workspace.project.id, text)

        def done(data: dict) -> None:
            self.btn_char.setEnabled(True)
            self.btn_char.setText("存为角色")
            name = data.get("name")
            if not name:
                self._append_system("⚠️ 未能从档案中解析出角色名。")
                return
            from app.services.character_service import CharacterService
            c = CharacterService(self.workspace.db).create(
                project_id=self.workspace.project.id,
                name=name, role_type=data.get("role_type", "support"),
                aliases=data.get("aliases", ""), profile=data.get("profile") or {},
                arc=data.get("arc"),
            )
            self._append_system(f"✅ 已存入角色「{c.name}」——可在「角色」页查看并编辑。")

        run_async(run(), on_done=done, on_error=self._on_error)

    def _save_as_world(self) -> None:
        text = self._last_ai_text()
        if not text:
            self._append_system("⚠️ 还没有 AI 整理的设定可入库。")
            return
        self.btn_world.setEnabled(False)
        self.btn_world.setText("解析入库中…")

        async def run():
            return await self.service.parse_world_entry(self.workspace.project.id, text)

        def done(data: dict) -> None:
            self.btn_world.setEnabled(True)
            self.btn_world.setText("存为设定")
            title = data.get("title")
            if not title:
                self._append_system("⚠️ 未能从文本中解析出设定标题。")
                return
            from app.services.world_service import WorldService
            e = WorldService(self.workspace.db).create_entry(
                project_id=self.workspace.project.id, category_id=None,
                title=title, content=data.get("content", ""),
                importance=int(data.get("importance", 3)),
            )
            self._append_system(f"✅ 已存入设定「{e.title}」——可在「世界观」页查看并编辑。")

        run_async(run(), on_done=done, on_error=self._on_error)

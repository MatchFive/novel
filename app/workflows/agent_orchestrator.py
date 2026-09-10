"""Agent 编排器（LangGraph）：意图识别 → 任务分解 → 实体 Agent 按顺序执行 → 汇总 → interrupt 确认 → 执行。

设计文档 6.7 的 agent 形态落地 + 三条 LangGraph 核心能力：
  · checkpointer（SQLite）：图状态落盘、断点续跑、历史回放
  · interrupt：汇总计划后暂停等用户确认，确认后 resume 执行（四步循环的框架级实现）
  · 条件边：澄清→结束 / 有任务→执行 / 确认→执行 / 取消→结束
本文件封装 LangGraph 细节，对外暴露 run/resume 统一接口（6.7.4 封装原则）。
"""
from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, field
from typing import Any

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt
from typing_extensions import TypedDict

from app.db.repositories.character_repo import CharacterRepository
from app.db.repositories.chapter_repo import ChapterRepository
from app.db.repositories.world_repo import WorldEntryRepository
from app.db.session import Database
from app.services.llm_service import LLMService

# ---------------------------------------------------------------- 状态
class OrchestratorState(TypedDict, total=False):
    command: str                    # 用户指令
    history: list[dict]             # 对话历史（供编排理解"上文提到"的指代）
    context: str                    # 检索到的项目上下文
    tasks: list[dict]               # 分解出的实体任务 [{entity, action, description, order}]
    results: list[dict]             # 各实体 Agent 产出的变更 [{domain, target, before, after, note}]
    plan: dict                      # 汇总后的修改计划（对齐 CommandService.plan 输出）
    clarification: str              # 需要澄清的问题
    user_decision: bool | None      # interrupt 后用户确认（True=执行 / False=取消）
    applied: dict                   # 执行结果（CommandService.apply_plan 的返回）


@dataclass
class OrchestrationResult:
    plan: dict
    tasks: list[dict] = field(default_factory=list)
    interrupted: bool = False       # True=图已暂停在 interrupt 等用户确认
    thread_id: str = ""             # 断点续跑的线程标识
    applied: dict | None = None     # resume 执行后的结果


# ---------------------------------------------------------------- 实体 Agent
# 实体 Agent 可用的只读查询工具（Agent 自主决定何时调用、查什么）
_TOOL_GUIDE = (
    "\n【数据查询工具】如现有上下文不足以完成高质量变更，可先输出工具调用获取数据（可连续调用多次）：\n"
    "- {\"tool\": \"list_chapters\"}：章节列表（序号/标题/目标/节拍数/状态）\n"
    "- {\"tool\": \"get_chapter\", \"args\": {\"target\": \"第N章或标题\"}}：该章完整目标+细纲节拍+细纲字段+正文字数\n"
    "- {\"tool\": \"list_arcs\"}：卷列表（卷名/目标/冲突）\n"
    "- {\"tool\": \"get_arc\", \"args\": {\"target\": \"第N卷或卷名\"}}：该卷完整目标/冲突/收束/概要\n"
    "- {\"tool\": \"list_plots\"}：情节列表（情节名/所属卷/概要/节拍线）\n"
    "- {\"tool\": \"get_plot\", \"args\": {\"target\": \"情节名或第N情节\"}}：该情节完整概要/冲突/收束/节拍线+旗下章节\n"
    "- {\"tool\": \"list_world\"}：设定条目标题列表\n"
    "- {\"tool\": \"get_world\", \"args\": {\"title\": \"条目标题\"}}：该设定完整内容\n"
    "- {\"tool\": \"list_characters\"}：角色列表（名字/类型）\n"
    "- {\"tool\": \"get_character\", \"args\": {\"name\": \"角色名\"}}：该角色完整档案\n"
    "数据足够后直接输出变更（changes），不要再调工具。"
)


class _EntityAgent:
    """实体 Agent 基类：可自主调用只读工具查询数据库，再产出本域变更项。

    工作循环（最多 4 轮）：模型输出 {"tool": ..., "args": {...}} → 执行工具、结果注入
    上下文继续；输出 {"changes": [...]} → 收敛返回。编排器只给任务意图，
    具体变更内容（字段/取值）由本域 Agent 自查数据后自行决定。
    """

    domain: str = ""
    MAX_TOOL_ROUNDS = 2  # 工具轮次上限（每轮可批量调多个工具；轮次过多会把编排拖到天荒地老）

    def __init__(self, db: Database, llm: LLMService, on_progress=None):
        self.db = db
        self.llm = llm
        self.on_progress = on_progress

    def _instructions(self, task: dict, context: str) -> str:
        """子类返回本域的提示词主体（职责 + 要求 + changes 输出格式）。"""
        raise NotImplementedError

    @staticmethod
    def _extract_tool_calls(data: dict) -> list[dict]:
        """从模型输出提取工具调用：兼容 {"tool": ...} 单条与 {"tool_calls": [...]} 数组。"""
        calls: list[dict] = []
        if data.get("tool"):
            calls.append({"tool": data["tool"], "args": data.get("args") or {}})
        raw = data.get("tool_calls")
        if isinstance(raw, list):
            for tc in raw:
                if not isinstance(tc, dict):
                    continue
                name = tc.get("tool") or tc.get("name")
                if name:
                    calls.append({"tool": name,
                                  "args": tc.get("args") or tc.get("arguments") or {}})
        return calls

    async def handle(self, project_id: int, task: dict, context: str) -> list[dict]:
        """工具循环：先自查数据（可选，最多 MAX_TOOL_ROUNDS 轮），最后一轮强制产出变更。"""
        action = task.get("action", "update")
        full_context = self._context(project_id) + "\n" + (context or "")
        data: dict = {}
        # 轮次 = 工具轮 + 1 个强制产出轮（防止配额全花在查数据上、changes 为空触发无历史的兜底）
        for round_no in range(self.MAX_TOOL_ROUNDS + 1):
            final_round = round_no >= self.MAX_TOOL_ROUNDS
            prompt = self._instructions(task, full_context)
            if final_round:
                prompt += ("\n【现在必须收敛】数据已足够，禁止再调用任何工具，"
                           "请直接输出 changes（没有可执行的变更就输出空数组）。")
            else:
                prompt += _TOOL_GUIDE
            data = await self.llm.generate_structured(
                project_id=project_id, module="command", prompt_key="task.agent_step",
                system_prompt=self.llm.load_prompt("global.base_prompt", project_id),
                user_prompt=prompt,
            )
            if not isinstance(data, dict):
                break
            if final_round:
                break
            calls = self._extract_tool_calls(data)
            if not calls:
                break
            for tc in calls[:3]:  # 单轮最多批量执行 3 个工具
                result = self._run_tool(project_id, str(tc["tool"]), tc.get("args") or {})
                if self.on_progress:
                    self.on_progress(f"🔍 查询 {tc['tool']}（{self.domain} Agent）")
                full_context += f"\n【工具 {tc['tool']} 返回】\n{result}"
        changes = (data or {}).get("changes", []) if isinstance(data, dict) else []
        for c in changes:
            c.setdefault("action", action)
            c["domain"] = self.domain  # 强制本域，防止 LLM 编造不支持的域
        return changes

    # ---------------- 只读工具 ----------------
    def _run_tool(self, project_id: int, name: str, args: dict) -> str:
        """执行只读查询工具，返回文本结果（截断防爆）。"""
        try:
            with self.db.session_ctx() as session:
                return self._dispatch_tool(session, project_id, name, args)
        except Exception as exc:
            return f"工具 {name} 执行失败: {exc}"

    def _dispatch_tool(self, session, project_id: int, name: str, args: dict) -> str:
        import json as _json

        from app.db.repositories.chapter_repo import ArcRepository

        def find_chapter(target: str):
            import re
            chapters = ChapterRepository(session).list_by_project(project_id)
            m = re.search(r"第?\s*(\d+)\s*章?", target or "")
            if m and 0 < int(m.group(1)) <= len(chapters):
                return chapters[int(m.group(1)) - 1]
            t = (target or "").replace(" ", "")
            for c in chapters:
                ct = (c.title or "").replace(" ", "")
                if t and ct and (t in ct or ct in t):
                    return c
            return None

        if name == "list_chapters":
            chapters = ChapterRepository(session).list_by_project(project_id)
            if not chapters:
                return "（当前没有任何章节）"
            return "\n".join(
                f"第{c.seq + 1}章「{c.title or ''}」｜目标：{(c.objective or '')[:50]}"
                f"｜节拍 {len(c.beats_json or [])} 个｜正文 {c.word_count or 0} 字｜{c.content_status}"
                for c in chapters[:30])
        if name == "get_chapter":
            ch = find_chapter(str(args.get("target", "")))
            if ch is None:
                return "（未找到该章节）"
            beats = "\n".join(f"  {i + 1}. {b.get('pov', '')}｜{b.get('location', '')}｜{b.get('event', '')}"
                              for i, b in enumerate(ch.beats_json or [])) or "（无细纲）"
            return (f"第{ch.seq + 1}章「{ch.title or ''}」\n目标：{ch.objective or '（无）'}\n"
                    f"细纲字段：{_json.dumps(ch.outline_json or {}, ensure_ascii=False)[:600]}\n"
                    f"细纲节拍：\n{beats}\n正文：{ch.word_count or 0} 字（{ch.content_status}）\n"
                    f"正文节选：\n{(ch.content or '（无正文）')[:3000]}")
        if name == "list_arcs":
            arcs = ArcRepository(session).list_by_project(project_id)
            if not arcs:
                return "（当前没有任何卷）"
            return "\n".join(f"第{a.seq + 1}卷「{a.title or ''}」｜目标：{(a.goal or '')[:60]}"
                             for a in arcs[:10])
        if name == "list_plots":
            from app.db.repositories.chapter_repo import PlotRepository
            plots = PlotRepository(session).list_by_project(project_id)
            if not plots:
                return "（当前没有任何情节）"
            arcs = ArcRepository(session).list_by_project(project_id)
            lines = []
            for p in plots[:20]:
                arc = next((a for a in arcs if a.id == p.arc_id), None)
                arc_tag = f"属「{arc.title or '第' + str(arc.seq + 1) + '卷'}」" if arc else "未分卷"
                lines.append(f"情节{p.seq + 1}「{p.title or ''}」（{arc_tag}）｜概要：{(p.summary or '')[:60]}")
            return "\n".join(lines)
        if name == "get_plot":
            from app.db.repositories.chapter_repo import PlotRepository
            plots = PlotRepository(session).list_by_project(project_id)
            target = str(args.get("target", ""))
            plot = None
            import re
            m = re.search(r"第?\s*(\d+)\s*(?:个)?情节", target)
            if m and 0 < int(m.group(1)) <= len(plots):
                plot = plots[int(m.group(1)) - 1]
            else:
                t = target.replace(" ", "")
                for p in plots:
                    pt = (p.title or "").replace(" ", "")
                    if t and pt and (t in pt or pt in t):
                        plot = p
                        break
            if plot is None:
                return "（未找到该情节）"
            chs = ChapterRepository(session).list_by_plot(plot.id)
            ch_list = "；".join(f"第{c.seq + 1}章「{c.title or ''}」" for c in chs) or "（无章节）"
            beats = plot.beats_json or []
            beats_text = "\n".join(f"  {i + 1}. {b.get('event', '') if isinstance(b, dict) else b}"
                                   for i, b in enumerate(beats)) or "（无节拍线）"
            return (f"情节{plot.seq + 1}「{plot.title or ''}」\n概要：{plot.summary or ''}\n"
                    f"核心冲突：{plot.conflict or ''}\n收束：{plot.resolution or ''}\n"
                    f"情节节拍线：\n{beats_text}\n旗下章节：{ch_list}")
        if name == "get_arc":
            arcs = ArcRepository(session).list_by_project(project_id)
            target = str(args.get("target", ""))
            arc = None
            import re
            m = re.search(r"第\s*(\d+)\s*卷", target)
            if m and 0 < int(m.group(1)) <= len(arcs):
                arc = arcs[int(m.group(1)) - 1]
            else:
                t = target.replace(" ", "")
                for a in arcs:
                    at = (a.title or "").replace(" ", "")
                    if t and at and (t in at or at in t):
                        arc = a
                        break
            if arc is None:
                return "（未找到该卷）"
            return (f"第{arc.seq + 1}卷「{arc.title or ''}」\n目标：{arc.goal or ''}\n"
                    f"核心冲突：{arc.conflict or ''}\n收束：{arc.resolution or ''}\n"
                    f"概要：{(arc.summary or '')[:800]}")
        if name == "list_world":
            entries = WorldEntryRepository(session).list_by_category(project_id, None)
            if not entries:
                return "（当前没有任何设定条目）"
            return "；".join(e.title for e in entries[:40])
        if name == "get_world":
            title = str(args.get("title", ""))
            entries = WorldEntryRepository(session).list_by_category(project_id, None)
            t = title.replace(" ", "")
            for e in entries:
                et = (e.title or "").replace(" ", "")
                if t and et and (t in et or et in t):
                    return f"「{e.title}」\n{(e.content or '')[:1200]}"
            return "（未找到该设定条目）"
        if name == "list_characters":
            chars = CharacterRepository(session).list_by_role(project_id)
            if not chars:
                return "（当前没有任何角色）"
            return "；".join(f"{c.name}（{c.role_type or '?'}）" for c in chars[:30])
        if name == "get_character":
            cname = str(args.get("name", ""))
            chars = CharacterRepository(session).list_by_role(project_id)
            t = cname.replace(" ", "")
            for c in chars:
                if t and (t in (c.name or "").replace(" ", "") or (c.name or "").replace(" ", "") in t):
                    return f"「{c.name}」（{c.role_type or '?'}）\n{_json.dumps(c.profile or {}, ensure_ascii=False)[:1200]}"
            return "（未找到该角色）"
        return f"未知工具: {name}（可用：list_chapters/get_chapter/list_arcs/get_arc/list_plots/get_plot/list_world/get_world/list_characters/get_character）"

    def _context(self, project_id: int) -> str:
        with self.db.session_ctx() as session:
            chars = CharacterRepository(session).list_by_role(project_id)
            chapters = ChapterRepository(session).list_by_project(project_id)
            entries = WorldEntryRepository(session).list_by_category(project_id, None)
            from app.db.repositories.chapter_repo import ArcRepository, PlotRepository
            arcs = ArcRepository(session).list_by_project(project_id)
            plots = PlotRepository(session).list_by_project(project_id)
        return "\n".join([
            "【角色】" + ("；".join(f"{c.name}({c.role_type or '?'})" for c in chars[:10])
                          if chars else "（当前无角色）"),
            "【卷】" + ("；".join(f"第{a.seq + 1}卷「{a.title or ''}」" for a in arcs[:8])
                        if arcs else "（当前无卷）"),
            "【情节】" + ("；".join(f"情节{p.seq + 1}「{p.title or ''}」" for p in plots[:12])
                          if plots else "（当前无情节）"),
            "【章节】" + ("；".join(f"第{c.seq + 1}章「{c.title or ''}」" for c in chapters[:15])
                          if chapters else "（当前无章节——历史提到的章节已被删除）"),
            "【设定】" + ("；".join(f"{e.title}" for e in entries[:15])
                          if entries else "（当前无设定条目）"),
        ])


class WorldAgent(_EntityAgent):
    domain = "world"

    def _instructions(self, task: dict, context: str) -> str:
        action = task.get("action", "update")
        return (
            f"你只负责【世界观设定】域的修改（本次操作：{action}）。\n【任务】" + task.get("description", "")
            + "\n【相关上下文】\n" + context
            + f"\n要求：target 优先用实际存在的条目标题（不确定就先调 get_world/list_world 查）；"
              "若 action=add，target 是新设定名、after 是完整内容；若 action=delete，target 是待删条目标题。"
            + "\n【输出格式】严格 JSON：{\"changes\": [{\"domain\": \"world\", \"action\": \""
              + action + "\", \"target\": \"条目标题\", \"before\": \"原内容片段\", \"after\": \"新内容\", \"note\": \"...\"}]}"
        )


class CharacterAgent(_EntityAgent):
    domain = "character"

    def _instructions(self, task: dict, context: str) -> str:
        action = task.get("action", "update")
        if action == "add":
            # 新增角色：要求输出结构化 profile（性格/背景/动机/能力/说话风格 分字段）
            return (
                "你只负责【新增角色】。\n【任务】" + task.get("description", "")
                + "\n【相关上下文】\n" + context
                + "\n要求：① target 必须是任务中明确说出的角色名（任务说'奇迹女神'就是奇迹女神，不要写成其他角色名）；"
                  "② 从对话历史中提炼该角色的完整设定；"
                  "③ profile 必须分字段填写——personality（性格特征，简短）、background（背景故事）、"
                  "motivation（动机与目标）、ability（能力/金手指）、speech_style（说话风格/口头禅）；"
                  "④ 不要把背景故事塞到 personality 里。"
                + "\n【输出格式】严格 JSON：{\"changes\": [{\"domain\": \"character\", \"action\": \"add\", "
                  "\"target\": \"角色名\", \"profile\": {\"personality\": \"...\", \"background\": \"...\", "
                  "\"motivation\": \"...\", \"ability\": \"...\", \"speech_style\": \"...\"}, \"note\": \"...\"}]}"
            )
        return (
            f"你只负责【角色】域的修改（本次操作：{action}）。\n【任务】" + task.get("description", "")
            + "\n【相关上下文】\n" + context
            + f"\n要求：target 用实际角色名（不确定就先调 get_character 查）；"
              "字段用 profile.personality / profile.background / profile.motivation / profile.ability / profile.speech_style。"
            + "\n【输出格式】严格 JSON：{\"changes\": [{\"domain\": \"character\", \"action\": \""
              + action + "\", \"target\": \"角色名\", \"field\": \"profile.personality 等\", \"before\": \"...\", \"after\": \"...\", \"note\": \"...\"}]}"
        )


class ChapterAgent(_EntityAgent):
    domain = "chapter"

    def _instructions(self, task: dict, context: str) -> str:
        action = task.get("action", "update")
        return (
            f"你只负责【章节/细纲/正文】域的修改（本次操作：{action}）。\n【任务】" + task.get("description", "")
            + "\n【相关上下文】\n" + context
            + f"\n要求：target 用实际章节（可用'第N章'；不确定章节现状就先调 list_chapters/get_chapter 查）；"
              "若 action=add：target 是从情节内容提炼的章节标题（绝不用「第一章」这类占位名），after 是本章目标；"
              "若任务要求连同细纲一起新增，直接在 change 里带 \"beats\" 数组（[{pov,location,event}…]），"
              "有场景/结尾钩子等则放 \"outline\" 对象——add 会连同细纲一次入库，不要再拆 update。"
              "字段 field 支持：\n"
              "· title（章节标题）/ objective（本章目标）/ seq（序号，after 填阿拉伯数字）——"
              "改标题用 title、改序号用 seq，绝不要把标题或序号写进 objective；\n"
              "· arc（归入某卷，after 填卷名或「第N卷」）/ plot（归入某情节，after 填情节名，会自动归入情节所在卷）——"
              "用户说「把第N章归入某卷/某情节」「未分卷章节归位」时用它；\n"
              "· beats（细纲）——after 填 JSON 对象：{\"scene\": \"主要场景\", \"characters\": [{\"name\": \"...\", \"role\": \"本章作用\"}], "
              "\"plot\": [{\"pov\": \"视点角色\", \"location\": \"地点\", \"event\": \"情节事件\"}], "
              "\"dialogue_hooks\": [\"保留台词\"], \"humor_points\": [\"爽点\"], \"ending_hook\": \"章尾钩子\"}；"
              "用户说'把细纲入库/生成细纲/写入节拍'时用它；\n"
              "· content（章节正文）——after 填正文全文；用户说'把正文/内容入库'时用它。\n"
              "对话历史里讨论好的细纲/正文要完整写进 after，不要只写摘要；"
              "写细纲前先调 get_chapter 看相邻章节已有节拍，情节不得与邻章重复。"
            + "\n【输出格式】严格 JSON：{\"changes\": [{\"domain\": \"chapter\", \"action\": \""
              + action + "\", \"target\": \"章节标题\", \"field\": \"title|objective|beats|content\", \"before\": \"...\", \"after\": \"...\", \"note\": \"...\"}]}"
        )


class OutlineAgent(_EntityAgent):
    """大纲（卷 Arc）域：生成/修改卷级目标与概要——绝不产出章节变更。"""

    domain = "outline"

    def _instructions(self, task: dict, context: str) -> str:
        action = task.get("action", "update")
        return (
            f"你只负责【大纲（卷）】域的修改（本次操作：{action}）。\n【任务】" + task.get("description", "")
            + "\n【相关上下文】\n" + context
            + "\n要求：① 大纲的单位是「卷」，不是章节——绝不产出章节变更（不确定卷的现状先调 list_arcs/get_arc 查）；"
              "② 若 action=add，target 是卷名（如「第一卷 开荒期」），after 是本卷目标"
              "（卷末主角要达成的状态/结果），summary 字段写本卷概要（阶段划分与主线走向）；"
              "③ 若 action=update，target 引用已有的卷（可用「第N卷」或卷名），"
              "field 用 goal|conflict|resolution|summary|title；"
              "④ 若 action=delete，target 是待删卷名。"
            + "\n【输出格式】严格 JSON：{\"changes\": [{\"domain\": \"outline\", \"action\": \""
              + action + "\", \"target\": \"卷名\", \"field\": \"goal|summary|...\", "
              "\"after\": \"新内容\", \"summary\": \"本卷概要（add 时填）\", \"note\": \"...\"}]}"
        )


class PlotAgent(_EntityAgent):
    """情节（Plot）域：情节介于卷与章节之间，一个情节跨多章——绝不产出章节/卷变更。"""

    domain = "plot"

    def _instructions(self, task: dict, context: str) -> str:
        action = task.get("action", "update")
        return (
            f"你只负责【情节】域的修改（本次操作：{action}）。\n【任务】" + task.get("description", "")
            + "\n【相关上下文】\n" + context
            + "\n要求：① 情节是「卷→情节→章节」的中间层，一个情节跨多个章节；"
              "target 用情节名（如「初遇」；用户说「第一卷第一个情节」之类时先调 list_plots 定位）；"
              "② 若 action=add：target=情节名，after=情节概要（summary），"
              "另带 \"conflict\"（核心冲突）/\"resolution\"（收束钩子）/\"arc\"（所属卷名）字段，"
              "\"beats\" 为情节节拍线（字符串数组，跨章粗粒度推进点，如「开端：…」「激化：…」）；"
              "③ 若 action=update：field 用 title|summary|conflict|resolution|beats 之一；"
              "「完善情节介绍」= field=summary 写完整概要，可同时输出多条变更分别写 conflict/resolution/beats；"
              "④ 若 action=delete，target 是待删情节名；"
              "⑤ 情节内容要与已有章节/卷保持一致（不确定就先调 list_plots/get_plot/list_chapters 查）。"
            + "\n【输出格式】严格 JSON：{\"changes\": [{\"domain\": \"plot\", \"action\": \""
              + action + "\", \"target\": \"情节名\", \"field\": \"summary|conflict|resolution|beats|title\", "
              "\"after\": \"新内容\", \"beats\": [\"节拍1\", \"节拍2\"], \"arc\": \"卷名（add 时填）\", \"note\": \"...\"}]}"
        )


AGENTS = {
    "world": WorldAgent,
    "character": CharacterAgent,
    "chapter": ChapterAgent,
    "outline": OutlineAgent,  # 大纲（卷）有独立 agent，不再复用章节逻辑
    "plot": PlotAgent,        # 情节（卷与章节之间的中间层）
    "beat": ChapterAgent,
}


# ---------------------------------------------------------------- 编排器
class Orchestrator:
    """LangGraph 编排：orchestrate → execute（按 order 顺序）→ summarize → interrupt 确认 → 执行。

    三条核心能力：checkpointer（SQLite 落盘/断点续跑/历史回放）、interrupt（确认点）、条件边（分支）。
    """

    MAX_TASKS = 10  # 单次编排任务数上限（防止分解成几十个任务、串行调用拖到卡死感）

    def __init__(self, db: Database, llm: LLMService, project_id: int,
                 state_db_path: str | None = None, on_progress=None):
        self.db = db
        self.llm = llm
        self.project_id = project_id
        self.state_db_path = state_db_path or str(db.db_path.parent / "workflow_state.db")
        self.on_progress = on_progress  # 进度播报回调（UI 注入，如 chat_panel._append_system）

    def _progress(self, text: str) -> None:
        if self.on_progress:
            try:
                self.on_progress(text)
            except Exception:
                pass

    async def _open_graph(self):
        """每次运行新开 checkpointer 连接并编译图；调用方用完后必须 close 连接。

        注意：不能长期持有 aiosqlite 连接——其后台线程是非 daemon 的，
        不关闭会阻止进程退出（主窗口关闭后程序残留）。
        """
        conn = await aiosqlite.connect(self.state_db_path)
        saver = AsyncSqliteSaver(conn)
        await saver.setup()  # 初始化 checkpoint 表（幂等）
        return conn, self._build_graph(saver)

    def _build_graph(self, checkpointer):
        graph = StateGraph(OrchestratorState)
        graph.add_node("orchestrate", self._orchestrate)
        graph.add_node("execute", self._execute_tasks)
        graph.add_node("summarize", self._summarize)
        graph.add_node("confirm", self._confirm)          # interrupt 确认点
        graph.add_node("apply", self._apply)              # 确认后执行
        graph.add_node("cancel", self._cancel)
        graph.set_entry_point("orchestrate")

        # 条件边①：orchestrate 后——有澄清→结束返回提问；有任务→执行
        graph.add_conditional_edges(
            "orchestrate",
            self._route_after_orchestrate,
            {"clarify": END, "execute": "execute"},
        )
        graph.add_edge("execute", "summarize")
        graph.add_edge("summarize", "confirm")
        # 条件边②：confirm 后（interrupt resume）——确认→apply；取消→cancel
        graph.add_conditional_edges(
            "confirm",
            self._route_after_confirm,
            {"apply": "apply", "cancel": "cancel"},
        )
        graph.add_edge("apply", END)
        graph.add_edge("cancel", END)
        return graph.compile(checkpointer=checkpointer)

    # ---------------- 条件路由 ----------------
    @staticmethod
    def _route_after_orchestrate(state: OrchestratorState) -> str:
        return "clarify" if state.get("clarification") else "execute"

    @staticmethod
    def _route_after_confirm(state: OrchestratorState) -> str:
        return "apply" if state.get("user_decision") else "cancel"
        return graph.compile()

    # ---- 节点 ----
    async def _orchestrate(self, state: OrchestratorState) -> OrchestratorState:
        """主 Agent：意图识别 + 任务分解（分解为多实体任务并标注顺序与 add/update/delete）。"""
        context = self._project_context()
        history_text = self._history_text(state.get("history", []))
        full_context = context + history_text  # 实体 Agent 也能看到对话历史（理解"主角=刘修"等指代）
        # 编排前预处理：轻量核对调用，对齐历史提到的实体与数据库现状（如章节已被删除）
        self._progress("🔎 步骤 1/3：核对上下文（对齐指令与数据库现状）……")
        reconcile_text = await self._reconcile(state["command"], context, history_text)
        self._progress("🧩 步骤 2/3：分解任务……")
        data = await self.llm.generate_structured(
            project_id=self.project_id, module="command", prompt_key="task.command_execute",
            system_prompt=self.llm.load_prompt("global.base_prompt", self.project_id),
            user_prompt=(
                "你是任务编排 Agent。把用户指令分解为按顺序执行的实体任务（world/character/chapter/outline/plot）。\n"
                "要求：\n"
                "① 标注 action：update（改已有）/ add（新增）/ delete（删除）——用户说'新增/添加/加个/设定一个'用 add，说'改成/调整'用 update，说'删除/去掉'用 delete；\n"
                "② 区分实体层级：「大纲/卷/卷末目标/本卷主线」用 entity=outline（如「生成第一卷大纲」"
                "「确定第一卷卷末目标」→ outline add，target=卷名）；"
                "「情节/情节线/情节介绍/情节节拍」用 entity=plot——情节介于卷与章节之间、一个情节跨多章"
                "（如「完善第一卷第一个情节『初遇』的介绍」→ plot update，target=初遇；"
                "「新增一个情节『XX』」→ plot add）；"
                "「第N章/本章/章节正文/细纲/节拍」用 entity=chapter（细纲与正文也可以入库）。"
                "特别注意：用户说「生成/写第N章的细纲（节拍）」时，若该章已存在（见【项目上下文】的章节列表），"
                "必须用 action=update 且 description 明确写『用 field=beats 写入本章细纲节拍』——"
                "绝不要把「生成细纲」误拆成 add 章节任务（章节已存在时 add 只会空转合并目标）；"
                "只有章节尚不存在时才 add。\n"
                "【新增章节的关键规则】新增章节与写入其细纲必须合并成**一个** chapter add 任务"
                "（description 写「新增章节并写入细纲」，执行 Agent 会把细纲随 add 一起入库）——"
                "绝不要拆成『新增章节』+『写入细纲』两个任务；"
                "章节标题必须从情节内容提炼（如「这波不亏」），绝不用「第一章/第二章」这类占位名"
                "（占位名会建出没有内容的空壳章节）；\n"
                "③ target 必须是用户指令中**明确说出的实体名**——「生成奇迹女神的角色卡」时 target=奇迹女神，"
                "绝不要把用户提到的角色名替换成对话历史里的其他角色（如把『奇迹女神』错写成主角『刘修』）；"
                "指代词（主角/他/她/这个角色）才用对话历史解析；若是新增，target 用用户说的新实体名；\n"
                "④ order：小的先执行，保证依赖（先改设定再改引用它的章节）；\n"
                "⑤ 【长段设定补充的处理】用户输入一大段设定内容（多个设定点）时，**逐条拆分设定点为 add 任务**："
                "每个设定点拆成一个 world add（target=设定主题名，如'力量体系''第十阶规则''跌境规则'），"
                "涉及角色背景则 character add。例如用户写'十阶神明跌到魔法师第三阶、跌境不损耗权柄、成神不老不死'，"
                "拆成：add 力量体系、add 第十阶成神规则、add 跌境规则。不要对这种输入轻易澄清；\n"
                "⑥ 用户说『把上面/刚才讨论的正文/细纲/设定入库』时，内容来源就是【对话历史】——"
                "直接从历史中拆解提取并生成 add/update 任务（章节的正文用 chapter 域 field=content，"
                "细纲用 field=beats），绝不要反过来向用户索取历史里已有的内容；\n"
                "⑦ 只有当用户意图完全无法判断（既非修改也非新增设定）时，才在 clarification 中提问，tasks 留空；\n"
                "⑧ 【项目上下文】是数据库当前真实状态（唯一事实来源）：历史中提到但其中没有的实体一律视为已删除，"
                "用户要操作它时用 add 重建，不要对它发 update/delete；若给出【上下文核对】结果，以它为准；\n"
                "⑨ 你只负责分解任务意图——description 写清「要做什么」即可，具体字段/内容不要替你编："
                "各实体 Agent 会自行调用工具查询数据库（章节细纲/设定全文/角色档案）后决定变更内容。\n"
                + reconcile_text
                + history_text
                + "\n【用户指令】" + state["command"] + "\n【项目上下文】\n" + context
                + "\n【输出格式】严格 JSON：{\"clarification\": \"\", \"tasks\": [{\"entity\": \"world|character|chapter|outline|plot\", "
                  "\"action\": \"add|update|delete\", \"description\": \"...\", \"target\": \"实际实体名\", \"order\": 1}]}"
            ),
        )
        tasks = sorted((data or {}).get("tasks", []), key=lambda t: t.get("order", 0))
        if len(tasks) > self.MAX_TASKS:
            self._progress(f"⚠️ 分解出 {len(tasks)} 个任务，超出上限，只执行前 {self.MAX_TASKS} 个")
            tasks = tasks[: self.MAX_TASKS]
        if tasks:
            brief = "；".join(f"[{t.get('entity', '?')}] {str(t.get('description', ''))[:24]}"
                              for t in tasks[:5])
            self._progress(f"🧩 已分解 {len(tasks)} 个任务：{brief}"
                           + (" …" if len(tasks) > 5 else ""))
        result = {**state, "context": full_context, "tasks": tasks}
        if (data or {}).get("clarification"):
            result["clarification"] = data["clarification"]
        return result

    # 对话历史携带预算：入库类指令需要从历史里完整提取正文/细纲，
    # 每条消息截 300 字会把长正文截没（编排器误判"没有正文"而反复澄清）
    _HISTORY_MSG_CAP = 3000     # 单条消息最多携带字数
    _HISTORY_TOTAL_CAP = 12000  # 历史总预算（最新优先，超出丢弃更早的）

    @classmethod
    def _history_text(cls, history: list[dict]) -> str:
        """把对话历史格式化为上下文（供编排理解指代、提取已讨论的正文/细纲）。

        最新优先、总量预算控制：长消息（AI 写的正文/细纲）按单条上限保留前段，
        保证最近的讨论内容完整可见。
        """
        if not history:
            return ""
        picked: list[str] = []
        budget = cls._HISTORY_TOTAL_CAP
        for m in reversed(history):
            role = "我" if m.get("role") == "user" else "AI"
            content = (m.get("content") or "")[: cls._HISTORY_MSG_CAP]
            if not content.strip():
                continue
            line = f"{role}：{content}"
            if len(line) > budget:
                if not picked:
                    picked.append(line[:budget])  # 至少保留最新一条（截断）
                break
            picked.append(line)
            budget -= len(line)
            if budget <= 0:
                break
        picked.reverse()
        return "\n【对话历史（上文已讨论的内容，入库指令的内容来源）】\n" + "\n".join(picked)

    async def _execute_tasks(self, state: OrchestratorState) -> OrchestratorState:
        """按顺序调用各实体 Agent 执行（每个 Agent 可读其他实体上下文 + 前面任务的执行结果）。

        修复：后续 Agent 能看到前面 Agent 已生成的变更（保持综合处理的一致性，
        如先新增力量体系设定、再新增基于该设定的角色）。
        """
        results: list[dict] = []
        base_context = state.get("context", "")
        tasks = state.get("tasks", [])
        for i, task in enumerate(tasks, 1):
            agent_cls = AGENTS.get(task.get("entity", ""))
            if agent_cls is None:
                continue
            self._progress(
                f"⚙️ 步骤 3/3（{i}/{len(tasks)}）：处理 [{task.get('entity', '?')}] "
                f"{str(task.get('description', ''))[:40]}……")
            agent = agent_cls(self.db, self.llm, on_progress=self.on_progress)
            # 后续 Agent 的上下文 = 项目静态上下文 + 前面任务已生成的变更
            context = base_context + self._results_context(results)
            changes = await agent.handle(self.project_id, task, context)
            results.extend(changes or [])
        return {**state, "results": results}

    @staticmethod
    def _results_context(results: list[dict]) -> str:
        """把前面任务已生成的变更格式化为上下文（供后续 Agent 参考，保持一致）。"""
        if not results:
            return ""
        lines = ["\n【前面任务已生成的变更（参考以保持设定一致）】"]
        for c in results:
            content = c.get("after") or ""
            if not content and c.get("profile"):
                p = c["profile"]
                content = f"性格:{p.get('personality','')} 背景:{p.get('background','')}"
            lines.append(f"· [{c.get('action','')}/{c.get('domain','')}] {c.get('target','')}: {str(content)[:120]}")
        return "\n".join(lines)

    async def _summarize(self, state: OrchestratorState) -> OrchestratorState:
        """汇总为统一的修改计划（对齐 CommandService.plan 输出格式）。"""
        supported = {"world", "character", "chapter", "outline", "plot", "beat"}
        results = state.get("results", [])
        valid = [c for c in results if c.get("domain") in supported]
        dropped = len(results) - len(valid)
        self._progress(f"📋 汇总完成：{len(valid)} 项变更待确认")
        plan = {
            "impact_summary": f"分解为 {len(state.get('tasks', []))} 个实体任务，按依赖顺序执行"
                              + (f"（{dropped} 项不支持的变更已忽略）" if dropped else ""),
            "changes": valid,
            "new_settings": [],
        }
        if state.get("clarification"):
            plan["clarification"] = state["clarification"]
        return {**state, "plan": plan}

    # ---------------- interrupt 确认点 ----------------
    async def _confirm(self, state: OrchestratorState) -> OrchestratorState:
        """interrupt：暂停图，把修改计划暴露给调用方；用户确认后 resume 继续。"""
        self._progress("⏸️ 计划已生成，正在弹出确认卡片……")
        decision = interrupt({
            "plan": state.get("plan", {}),
            "impact_summary": (state.get("plan") or {}).get("impact_summary", ""),
        })
        # resume 值：{"confirmed": bool, "accept": [索引]}
        if isinstance(decision, dict):
            confirmed = bool(decision.get("confirmed"))
            accept = set(decision.get("accept") or [])
        else:
            confirmed = bool(decision)
            accept = set()
        return {**state, "user_decision": confirmed, "accept_indices": accept}

    async def _apply(self, state: OrchestratorState) -> OrchestratorState:
        """用户确认后：执行确认的变更项（写库）。"""
        from app.services.command_service import CommandService
        svc = CommandService(self.db, self.llm)
        plan = state.get("plan") or {}
        changes = plan.get("changes") or []
        accept = state.get("accept_indices") or set(range(len(changes)))
        result = svc.apply_plan(self.project_id, plan, set(accept))
        return {**state, "applied": result}

    async def _cancel(self, state: OrchestratorState) -> OrchestratorState:
        return {**state, "applied": {"applied": 0, "skipped": [], "cancelled": True}}

    def _project_context(self) -> str:
        """项目现状（数据库当前真实状态）。空列表给显式标记，避免模型分不清「没有」和「已删除」。"""
        with self.db.session_ctx() as session:
            chars = CharacterRepository(session).list_by_role(self.project_id)
            chapters = ChapterRepository(session).list_by_project(self.project_id)
            entries = WorldEntryRepository(session).list_by_category(self.project_id, None)
            from app.db.repositories.chapter_repo import ArcRepository, PlotRepository
            arcs = ArcRepository(session).list_by_project(self.project_id)
            plots = PlotRepository(session).list_by_project(self.project_id)
        arc_map = {a.id: a for a in arcs}
        plot_lines = []
        for p in plots[:12]:
            arc = arc_map.get(p.arc_id)
            arc_tag = f"属「{arc.title or '第' + str(arc.seq + 1) + '卷'}」" if arc else "未分卷"
            plot_lines.append(f"情节{p.seq + 1}「{p.title or ''}」（{arc_tag}）")
        return "\n".join([
            "【卷】"
            + ("；".join(f"第{a.seq + 1}卷「{a.title or ''}」" for a in arcs[:10])
               if arcs else "（当前没有任何卷）"),
            "【情节】"
            + ("；".join(plot_lines)
               if plots else "（当前没有任何情节——用户提到情节时可用 plot add 新建）"),
            "【章节】"
            + ("；".join(f"第{c.seq + 1}章「{c.title or ''}」" for c in chapters[:15])
               if chapters else "（当前没有任何章节——若历史中提到过章节，说明已被删除，需重新新增）"),
            "【角色】"
            + ("；".join(f"{c.name}({c.role_type or '?'})" for c in chars[:10])
               if chars else "（当前没有任何角色）"),
            "【设定】"
            + ("；".join(f"{e.title}" for e in entries[:15])
               if entries else "（当前没有任何设定条目）"),
        ])

    async def _reconcile(self, command: str, context: str, history_text: str) -> str:
        """编排前的轻量核对调用：对齐指令/历史中的实体与数据库现状（失败不影响主流程）。

        走 lite 档，可在「模型与路由设置」里指派给本地模型（如 qwen3-14b）。
        """
        try:
            data = await self.llm.generate_structured(
                project_id=self.project_id, module="command",
                prompt_key="task.context_reconcile",
                system_prompt=self.llm.load_prompt("global.base_prompt", self.project_id),
                user_prompt=self.llm.load_prompt(
                    "task.context_reconcile", project_id=self.project_id,
                    current_state=context, command=command,
                    history=history_text[-3000:] if history_text else "（无）",
                ),
                retries=0,  # 核对是辅助调用，失败直接跳过
            )
        except Exception:
            return ""
        if not isinstance(data, dict) or not data:
            return ""
        parts = ["\n【上下文核对（编排前预处理结果）】"]
        if data.get("missing"):
            parts.append("已不存在（按新增处理或忽略）：" + "、".join(str(x) for x in data["missing"]))
        if data.get("existing"):
            parts.append("确实存在：" + "、".join(str(x) for x in data["existing"]))
        if data.get("resolved"):
            parts.append("指代解析：" + str(data["resolved"]))
        if data.get("note"):
            parts.append("建议：" + str(data["note"]))
        return "\n".join(parts) if len(parts) > 1 else ""

    # ---- 对外接口 ----
    async def run(self, command: str, history: list[dict] | None = None) -> OrchestrationResult:
        """运行编排：到 interrupt 确认点暂停，返回 plan + thread_id（供 resume 续跑）。"""
        conn, graph = await self._open_graph()
        try:
            thread_id = uuid.uuid4().hex
            config = {"configurable": {"thread_id": thread_id}}
            final = await graph.ainvoke(
                {"command": command, "history": history or []}, config,
            )
            # 检查是否暂停在 interrupt（confirm 节点）
            state = await graph.aget_state(config)
            interrupted = bool(state.next)  # next 非空 = 暂停在 interrupt
            # plan 优先从 checkpoint 快照取（interrupt 时 ainvoke 的返回形态随版本不同可能不含 state）
            values = getattr(state, "values", None) or {}
            plan = values.get("plan") or (final.get("plan", {}) if final else {})
            # 条件边 clarify → END 时未经过 summarize，plan 为空——从 state 读 clarification 补入
            if not plan and final and final.get("clarification"):
                plan = {"clarification": final["clarification"], "changes": []}
            return OrchestrationResult(
                plan=plan,
                tasks=final.get("tasks", []) if final else [],
                interrupted=interrupted, thread_id=thread_id,
            )
        finally:
            await conn.close()  # interrupt 暂停状态已落盘 sqlite，关连接不影响 resume

    async def resume(self, thread_id: str, confirmed: bool,
                     accept_indices: list[int] | None = None) -> OrchestrationResult:
        """interrupt 后续跑：confirmed=True 执行勾选的变更，False 取消。checkpointer 恢复图状态。"""
        conn, graph = await self._open_graph()
        try:
            config = {"configurable": {"thread_id": thread_id}}
            decision = {"confirmed": confirmed, "accept": accept_indices or []}
            final = await graph.ainvoke(Command(resume=decision), config)
            state = await graph.aget_state(config)
            return OrchestrationResult(
                plan=(final or {}).get("plan", {}),
                tasks=(final or {}).get("tasks", []),
                interrupted=bool(state.next), thread_id=thread_id,
                applied=(final or {}).get("applied"),
            )
        finally:
            await conn.close()

    # ---- 历史回放（checkpointer 白送的能力） ----
    async def get_history(self, thread_id: str) -> list[dict]:
        """返回该线程的图状态历史（节点执行轨迹，断点续跑/回放用）。"""
        conn, graph = await self._open_graph()
        try:
            config = {"configurable": {"thread_id": thread_id}}
            history = []
            async for snapshot in graph.aget_state_history(config):
                history.append({
                    "next": list(snapshot.next) if snapshot.next else [],
                    "step": snapshot.metadata.get("step") if snapshot.metadata else None,
                })
            return history
        finally:
            await conn.close()

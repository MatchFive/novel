"""员工 ReAct 主循环（设计 §1.2 + 附录 C.1）：步决策 → 门禁 → 执行 → 观测回注。

循环契约：MAX_STEPS 硬上限；每轮重取 ``allowed_for_run``（deliverables 运行中可变）；
prompt = global.base_prompt + task.auto_agent_step（内嵌工具签名/brief/digest/memory）；
LLM 走 pro 档模型、temperature=0.3；白名单门 → 冷却门 → 执行；观测 trim 后落
tool_result 事件；连续失败≥2 进冷却，瞬时失败加 ``FAILED-TRANSIENT:`` 前缀且不计熔断；
noop_streak≥6 / token 预算超线 → paused；finish → done（P1 简化：不做 task_target_done
LLM 判定，归 P2/P3——计划明示偏离）；每步 state.save。

**Task 11 接缝（头号约束）**：plan_arcs / edit_by_command 的注册 handler 是同步
签名、内部 ``asyncio.run`` 驱动——在运行的事件循环里经 registry.execute 调它们会
RuntimeError。本 loop 对这两个工具**直接 await 模块级协程**（harness.tools 导出），
其余只读工具走 registry（同步）。P2 的 master_plan / plan_chapters 同样经此 await
分支（即便 registry 已注册也按名字路由到 await，registry 同步边界的 no-op emit 只是
防御性兜底）；emit 直接绑 loop 已持有的 ``self.events.append``（EventLog.append 的
(kind, payload) 签名即 EmitFn，不新增构造参数）；两工具返回 ToolResult，loop 归一
(text, usage) 后工具 usage 与决策 usage 同账累加，预算判定吃合计。
"""
from __future__ import annotations

import asyncio
import json
import logging

from harness.agent.steps import StepParseError, _iter_json_objects, parse_step, trim_observation
from harness.config import Settings
from harness.llm.client import CompletionUsage, OpenAICompatClient
from harness.llm.policy import resolve_max_tokens
from harness.prompts.loader import PromptLoader
from harness.reasons import reason_label
from harness.run import constants
from harness.run.approval import ApprovalCancelled, ApprovalUI, ask_ui_async
from harness.run.digest import progress_digest
from harness.run.events import EventLog
from harness.run.memory import WorkMemory
from harness.run.state import RunState
from harness.run.whitelist import allowed_for_run
from harness.store.project import ProjectStore
from harness.tools import (
    check_plots,
    edit_by_command,
    extract_memory,
    global_check,
    master_plan,
    plan_arcs,
    plan_chapters,
    web_search,
    write_plot,
)
from harness.tools.characters import (
    build_characters,
    delete_character,
    draft_character,
    update_character,
)
from harness.tools.registry import ToolRegistry

__all__ = ["AgentLoop"]

log = logging.getLogger("agent.loop")

# 流程控制类动作：不进 registry，由 loop 内置语义处理（白名单仍管它们）。
_FLOW_ACTIONS = frozenset(
    {"finish", "pause", "ask_user", "set_step_plan", "complete_step", "request_plan_approval"}
)

# TRANSIENT_MARKERS 补遗：工具层「失败（truncated）：…」的英文分类词（constants 钉死不动）。
_EXTRA_TRANSIENT_MARKERS = ("truncated", "starved")
#: **无进展**观测的前缀（设计 §1.2 的「NOPROGRESS: 也计次」口径）——
#: 0 产出的拒绝用它标记：判 noop（不涨有效步、涨 noop_streak），**不进**失败账。
_NOPROGRESS_MARK = "NOPROGRESS:"

#: P32/A8c：edit_by_command 全部提议越界时的观测首行（F1 修法 c 的触发形状）。
#: 「提议内容与现状一致」「已取消」**不算**——前者不是选错工具，后者是人取消。
_OUT_OF_SCOPE_ZERO_CHANGE = "edit_by_command：无范围内文件可应用（零变更）"

#: 同类工具改试提示：观测保留原文，末尾追加一段给模型下一步的指路——把
#: 「升级成 ask_user 要拍板」变成「改试 write_plot」。
_ZERO_CHANGE_SIBLING_HINT = ("（同类工具提示：edit_by_command 只管 world.md / 角色卡 / "
                             "卷骨架 / 情节——要动的若是章节正文/章卡，请改试 "
                             "write_plot（args: {plot_id, seq_from?, seq_to?}）。）")


def _zero_change_sibling_hint(obs: str) -> str:
    """零变更撞墙 → 追加同类工具改试提示；其余观测逐字不动。"""
    head = obs.splitlines()[0] if obs else ""
    if head.startswith(_OUT_OF_SCOPE_ZERO_CHANGE):
        return obs.rstrip("\n") + "\n" + _ZERO_CHANGE_SIBLING_HINT
    return obs


#: 逐工具结果的**严重度**序（failed 最重 → ok 最轻）。
#: 同一工具在并行批里出现多次时取最重的那个（P21，独立复审 P17-2）。
_OUTCOME_RANK = {"ok": 0, "noop": 1, "transient": 2, "failed": 3}


def _worst_outcome(old: str | None, new: str) -> str:
    """两个逐工具结果里取**更重**的那个（``old`` 为 None 时就是 ``new``）。"""
    if old is None:
        return new
    if _OUTCOME_RANK.get(new, 0) > _OUTCOME_RANK.get(old, 0):
        return new
    return old

# 签名兜底表：registry handler 多为闭包无 docstring；白名单内工具一律给一行签名
# （参数形态以 Task 10/11 工具实现为准）。
_FALLBACK_SIG = {
    "plan_arcs": "plan_arcs(args: {instruction}) -> 规划全书卷骨架并写盘（只产卷骨架，不产情节）",
    "master_plan": "master_plan(args: {vol?, instruction?}) -> 逐卷情节规划并写盘"
                   "（master_plan 段二，并发生成；先 plan_arcs 产卷骨架）",
    "plan_chapters": "plan_chapters(args: {plot_id, target_words?}) -> 情节拆章三步+自查并写盘"
                     "（plot_id 形如 \"1-2\"；先 master_plan 产情节）",
    "edit_by_command": "edit_by_command(args: {instruction}) -> 轻量修改 world.md/角色卡/卷骨架/情节"
                       "（diff 确认门后写盘；**不含章节标题与正文**——正文用 write_plot）",
    "write_plot": "write_plot(args: {plot_id, seq_from?, seq_to?}) -> 逐章写章节正文"
                  "（写完的章 status=done；逐拍流式+审核+修正+去味；跳过已完成章，失败停在该章；"
                  "申请写范围外的卷时会先征求用户放行）",
    "extract_memory": "extract_memory(args: {chapter_id}) -> 对指定章重做记忆提取"
                      "（正文被人工改过后用；幂等先清后建）",
    "list_skills": "list_skills() -> 列出可用的 skill（项目级 + 用户级，项目级覆盖同名）",
    "read_skill": "read_skill(args: {name}) -> 读入一个 skill 的 SKILL.md 全文",
    "web_search": "web_search(args: {query, rounds?}) -> 联网核查事实（多条用 ; 分隔）；"
                  "未启用/未配置时明确回绝，不得凭记忆编造",
    "check_plots": "check_plots(args: {plot_ids?}) -> 情节级验收：检查一批情节的完成度/一致性/"
                   "与目标对齐，逐条判定验收清单；产出 verdict（pass/fix/replan）与问题清单，"
                   "问题落 issues 台账（带 quote，可定位到章）",
    "global_check": "global_check(args: {chapter_ids?, editor_mode?}) -> 【收尾前必做】全书级验收："
                    "抽样正文，检查效果与目标达成（目标/限制/人物/结构），逐条判定验收清单；"
                    "问题落 issues 台账并可逐章打回重写；传 editor_mode=\"on\" 用责编签约视角"
                    "（黄金三章/追读）验收",
    "draft_character": "draft_character(args: {name, role_type, requirement?}) -> 起草一张角色卡草稿"
                       "（返回卡片 JSON，人确认后才落卡）",
    "update_character": "update_character(args: {name, new_name?, role_type?, aliases?, add_aliases?,"
                        " remove_aliases?, profile?, set_profile?, arc?, delete?}) -> 修角色卡"
                        "（profile 只补空缺、set_profile 按字段覆盖；delete=true 删卡；改名/升降主角"
                        "自动维护主角名单并重提记忆）",
    "delete_character": "delete_character(args: {name?, names?, all?}) -> 删角色卡"
                        "（逐项隔离；all=true 必须同传 confirm: true；连带主角名单与记忆重提）",
    "build_characters": "build_characters(args: {}) -> 自动建卡司：按世界观规划完整名册"
                        "（撞既有名/别名自动跳过；主角名额满则超额降级重要配角）→ 逐卡起草"
                        "→ 人确认后落卡并重提记忆",
    "finish": "finish(args: {summary?}) -> 任务完成，结束本 run",
    "pause": "pause() -> 暂停 run（可 /resume 续跑）",
    "ask_user": "ask_user(args: {question, options[]}) -> 需要用户拍板的选择（2~5 个候选）",
    "set_step_plan": "set_step_plan(args: {steps[]}) -> 把任务拆成可核对步骤（≤12 个）",
    "complete_step": "complete_step() -> 标记当前计划步骤完成并推进",
    "request_plan_approval": "request_plan_approval() -> 写正文前的规划确认门",
}


class AgentLoop:
    """员工 ReAct 主循环。构造注入全部依赖（测试可整体替换 llm/ui/registry）。"""

    def __init__(self, *, store: ProjectStore, state: RunState, events: EventLog,
                 memory: WorkMemory, registry: ToolRegistry, ui: ApprovalUI,
                 llm: OpenAICompatClient, prompts: PromptLoader,
                 deliverables: list[str], settings: Settings) -> None:
        self.store = store
        self.state = state
        self.events = events
        self.memory = memory
        self.registry = registry
        self.ui = ui
        self.llm = llm
        self.prompts = prompts
        self.deliverables = list(deliverables)
        self.settings = settings

    # ------------------------------------------------------------------ 主循环

    async def run(self) -> RunState:
        # **已被 /abort 终止的 run 不再起跑**（2026-11-17 独立复审 P16-4）：
        # aborted 是**终态**（/resume 也不会续它）→ 跑起来等于把用户的「终止」吃掉。
        # （注意：paused **不是**终态——/resume 正是拿 paused 的 state 续跑，
        #  所以这里只拦 aborted。）
        if self.state.status == "aborted":
            return self.state
        self.state.status = "running"
        self._save()
        noop_streak = 0
        # **熔断按工具分桶**（设计 §1.2：「**同工具**连续失败≥2 进冷却」）：
        # 原先是**全局**计数器 → A 失败一次 + B 失败一次就把 **B** 冷却了（B 只失败过一次），
        # 且 A 的失败会「连坐」到毫不相干的工具上。
        fail_streak: dict[str, int] = {}   # action → 该工具连续失败次数
        cooled: dict[str, int] = {}  # action → 冷却解禁步号
        try:
            for step in range(1, constants.MAX_STEPS + 1):
                # **每步查一次外部叫停**（P16-4）：后台 run 跑着的时候，REPL 仍能收
                # `/pause`、`/abort` → 它们改的是**同一个** state 对象 → run 必须看见。
                # （此前 loop 只在开头设 running、**从不**回看 status → 「已终止」的 run
                #  照样继续调工具、写章，最后还可能 finish 写回 done。）
                # 判据是「状态**不再是 running**」——loop 自己只在开头设 running，
                # 所以状态一旦变了，就是**外部**（/pause、/abort）改的。
                if self.state.status != "running":
                    self._record_result(
                        "loop", f"run 被外部终止（status={self.state.status}）")
                    self._save()
                    return self.state
                self.state.stats["step"] = step
                self.state.stats["total_steps"] = int(self.state.stats.get("total_steps", 0)) + 1
                # deliverables 运行中可变：每轮重取白名单（fail-closed，未知值响亮失败）
                allowed = allowed_for_run(list(self.state.brief.get("deliverables")
                                                or self.deliverables))

                # ---- LLM 步决策
                try:
                    completion = await self._complete(allowed)
                except Exception as exc:  # noqa: BLE001 - 失败分类归 loop（熔断/瞬时语义）
                    obs, kind = self._llm_error_obs(exc)
                    noop_streak += 1
                    if kind == "failed":
                        fail_streak["llm"] = fail_streak.get("llm", 0) + 1
                    self._record_result("llm", obs)
                    if noop_streak >= constants.MAX_NOOP_STREAK:
                        return self._pause("noop_streak", noop_streak)
                    self._save()
                    continue
                if completion.usage is not None:
                    self.state.stats["token_used"] = int(self.state.stats.get("token_used", 0)) \
                        + completion.usage.prompt_tokens + completion.usage.completion_tokens
                paused = self._check_budget()
                if paused is not None:
                    return paused
                response = {"content": completion.text}
                tool_calls = getattr(completion, "tool_calls", None)  # 原生 tool_call 协议注入缝
                if tool_calls:
                    response["tool_calls"] = tool_calls

                # ---- 解析步决策
                try:
                    decision = parse_step(response)
                except StepParseError as exc:
                    self._record_result("parse", f"步决策解析失败：{exc}")
                    noop_streak += 1
                    if noop_streak >= constants.MAX_NOOP_STREAK:
                        return self._pause("noop_streak", noop_streak)
                    self._save()
                    continue
                self._record_step(decision)

                action = decision.action.strip()
                if not action:
                    self._record_result("", "空动作（action 为空）→ NOPROGRESS")
                    noop_streak += 1
                    if noop_streak >= constants.MAX_NOOP_STREAK:
                        return self._pause("noop_streak", noop_streak)
                    self._save()
                    continue
                self.state.stats["step_kinds"][action] = \
                    int(self.state.stats["step_kinds"].get(action, 0)) + 1

                # ---- 白名单门
                if action not in allowed:
                    hint = "、".join(sorted(allowed)) or "（空）"
                    self._record_result(action, f"该动作不在本任务白名单：{action}（本任务可用：{hint}）")
                    noop_streak += 1
                    if noop_streak >= constants.MAX_NOOP_STREAK:
                        return self._pause("noop_streak", noop_streak)
                    self._save()
                    continue

                # ---- 冷却门
                if action in cooled and step < cooled[action]:
                    self._record_result(
                        action, f"{action} 冷却中（连续失败≥2）：第 {cooled[action]} 步后可重试")
                    noop_streak += 1
                    if noop_streak >= constants.MAX_NOOP_STREAK:
                        return self._pause("noop_streak", noop_streak)
                    self._save()
                    continue

                # ---- 终态动作
                if action == "finish":
                    self.state.status = "done"
                    self.state.stats["effective_steps"] = \
                        int(self.state.stats.get("effective_steps", 0)) + 1
                    self.events.append("done", {"summary": str(decision.args.get("summary") or "")})
                    self._save()
                    return self.state
                if action == "pause":
                    return self._pause("agent_pause")

                # ---- 执行
                recorded = False
                tool_usage: CompletionUsage | None = None
                per_tool: dict[str, str] = {}   # 逐工具分类（熔断账按工具分桶）
                if action in ("ask_user", "set_step_plan", "complete_step", "request_plan_approval"):
                    obs, outcome = await self._flow_action(action, decision.args)
                    per_tool = {action: outcome}
                elif action in ("plan_arcs", "edit_by_command", "master_plan", "plan_chapters",
                                "write_plot", "extract_memory", "web_search",
                                "check_plots", "global_check",
                                "draft_character", "update_character",
                                "delete_character", "build_characters"):
                    obs, outcome, tool_usage = await self._async_tool(action, decision.args)
                    obs = _zero_change_sibling_hint(obs)   # P32/A8c：撞「零变更」指路同类工具
                    per_tool = {action: outcome}
                elif self.registry.has(action):
                    obs, outcome, recorded, per_tool = await self._sync_tool(
                        action, decision.args, response, allowed, cooled=cooled, step=step)
                else:
                    self._record_result(action, f"未知工具：{action}（签名见【可用工具】）")
                    noop_streak += 1
                    if noop_streak >= constants.MAX_NOOP_STREAK:
                        return self._pause("noop_streak", noop_streak)
                    self._save()
                    continue

                # ---- 观测分类：瞬时标记前缀化且不计熔断
                if outcome == "transient":
                    obs = "FAILED-TRANSIENT: " + obs
                if outcome == "ok":
                    self.state.stats["effective_steps"] = \
                        int(self.state.stats.get("effective_steps", 0)) + 1
                    noop_streak = 0
                elif outcome == "transient":
                    noop_streak += 1  # 瞬时失败不计熔断，但算无进展
                else:  # failed / noop
                    noop_streak += 1
                # ---- **逐工具记账**（成功清账 / 失败计账，**与整批 outcome 解耦**）
                # P21（独立复审 P17-1）：此前「成功清账」写在 `outcome == "ok"` 分支里
                # → 混合批（一个成功 + 一个失败）整体判 failed → **成功那个工具的账不清**
                # → 它下次单独失败一次就被冷却（**误冷却**）。
                # → 现在按**每个工具自己的**结果记账：ok → 清账；failed → 计账。
                for tool, oc in per_tool.items():
                    if oc == "ok":
                        fail_streak.pop(tool, None)   # 该工具成功 → 它的连续失败清零
                    elif oc == "failed":
                        # **失败记到真正失败的那个工具**（2026-11-15 独立审核 L2）：
                        # 并行批次里是**逐条**分类出来的失败者；单发路径就是 action 自己。
                        # → 冷却对了对象，没失败的工具不会被误冷却。
                        fail_streak[tool] = fail_streak.get(tool, 0) + 1
                        if fail_streak[tool] >= 2:
                            cooled[tool] = step + constants.COOLDOWN_STEPS
                            fail_streak[tool] = 0
                if not recorded:
                    self._record_result(action, obs)
                # P2：工具 usage 与决策 usage 同账——预算判定吃 step 合计（含本步工具），
                # 在 step 末扩一行检查（决策 usage 的既有检查在步首，两处口径同一 helper）。
                if tool_usage is not None:
                    self.state.stats["token_used"] = \
                        int(self.state.stats.get("token_used", 0)) \
                        + tool_usage.prompt_tokens + tool_usage.completion_tokens
                paused = self._check_budget()
                if paused is not None:
                    return paused
                if noop_streak >= constants.MAX_NOOP_STREAK:
                    return self._pause("noop_streak", noop_streak)
                self._save()
            # MAX_STEPS 耗尽（硬上限）
            return self._pause("max_steps", constants.MAX_STEPS)
        except (KeyboardInterrupt, asyncio.CancelledError):
            # Ctrl+C = /pause 语义（CLI 层捕获）：挂起落盘，可 /resume 续跑
            if self.state.status not in ("done", "aborted"):
                self.state.status = "paused"
                self.events.append("paused", {"reason": "interrupted"})
                self._save()
            return self.state
        finally:
            # stream close 权归 loop：run 结束（含异常路径）统一关闭（所有权在调用方，见 llm/client.py）
            try:
                await self.llm.aclose()
            except Exception:
                log.warning("llm 关闭失败", exc_info=True)

    # ------------------------------------------------------------------ LLM

    async def _complete(self, allowed):
        prompt = self._build_prompt(allowed)
        return await self.llm.complete(
            [{"role": "user", "content": prompt}],
            model=self.llm.provider.pro,  # pro 档模型（client 由 default_provider 配置构造）
            temperature=0.3,
            max_tokens=resolve_max_tokens(len(prompt)),
        )

    def _build_prompt(self, allowed) -> str:
        base = self.prompts.get("global.base_prompt").rstrip()
        step_tpl = self.prompts.render(
            "task.auto_agent_step",
            tools="\n".join(self._tool_signatures(allowed)),
            brief=str(self.state.brief.get("raw") or ""),
            progress=progress_digest(self.store),
            memory=self.memory.render(),
            suggested=self._suggested_note(),
        )
        return base + "\n\n" + step_tpl

    def _suggested_note(self) -> str:
        """**A4**：模型推断的交付物 → 步决策提示词里的**行动参考注记**（引导「做什么」）。

        仅当 ``suggested_deliverables`` 非空且 ≠ 授权 deliverables 时渲染；注记明写
        「可用工具与边界仍以 brief.deliverables 为准」——**权限面（能做什么）仍只由
        用户定**（P10 复审 D2），推断不扩权（白名单 fail-closed 不变）。空串 →
        模板 ``{{suggested}}`` 贴段尾，字节零变化。
        """
        suggested = [str(x) for x in (self.state.brief.get("suggested_deliverables") or [])
                     if str(x).strip()]
        authorized = [str(x) for x in (self.state.brief.get("deliverables")
                                       or self.deliverables)]
        if not suggested or suggested == authorized:
            return ""
        return ("（模型推断的交付物：" + "、".join(suggested)
                + "——仅作行动参考：可优先朝这些产物组织推进，finish 时说明与"
                  "授权面的差距；**本 run 可用工具与边界仍以 brief.deliverables 为准**，"
                  "推断不改变权限面。）")

    def _tool_signatures(self, allowed) -> list[str]:
        names = (set(self.registry.names) | _FLOW_ACTIONS) & set(allowed)
        lines = []
        for name in sorted(names):
            doc = self.registry.doc_of(name)
            lines.append(f"- {doc or _FALLBACK_SIG.get(name, f'{name}(args: dict)')}")
        return lines or ["- （无可用工具）"]

    @staticmethod
    def _llm_error_obs(exc) -> tuple[str, str]:
        text = str(exc)
        transient = bool(getattr(exc, "transient", False)) or \
            any(m in text for m in constants.TRANSIENT_MARKERS)
        head = f"LLM 调用失败：{type(exc).__name__}: {text}"
        return (f"FAILED-TRANSIENT: {head}", "transient") if transient else (f"FAILED: {head}", "failed")

    # ------------------------------------------------------------------ 执行分发

    async def _async_tool(self, action: str, args: dict) -> tuple[str, str, CompletionUsage | None]:
        """LLM 重型工具（P1 plan_arcs/edit_by_command + P2 master_plan/plan_chapters +
        P3 write_plot + P5 extract_memory + P6 check_plots/global_check）：直接 await
        模块级协程（Task 11 接缝，按名字路由——即便 registry 已注册也走本分支）。

        注册 handler 是同步签名、内部 asyncio.run——事件循环内经 registry.execute 调它们会
        RuntimeError；这里绕开 registry 走异步核心，emit 绑 loop 已持有的 events.append。
        返回 (观测, 分类, 工具 usage)：这些工具全部返回 ToolResult（P1 已于 Task 8 迁移），
        解包 (text, usage) 后工具 usage 与决策 usage 同账累加。
        """
        try:
            if action == "master_plan":
                result = await master_plan(self.store, self.llm, self.prompts, self.settings,
                                           self.events.append, args)
            elif action == "plan_chapters":
                result = await plan_chapters(self.store, self.llm, self.prompts, self.settings,
                                             self.events.append, args)
            elif action == "write_plot":
                result = await write_plot(self.store, self.llm, self.prompts, self.settings,
                                          self.events.append, args, ui=self.ui)
            elif action == "plan_arcs":
                result = await plan_arcs(self.store, self.llm, self.prompts, self.settings, args)
            elif action == "extract_memory":
                result = await extract_memory(self.store, self.llm, self.prompts,
                                              self.settings, args)
            elif action == "web_search":
                result = await web_search(self.store, self.llm, self.prompts,
                                          self.settings, args)
            elif action == "check_plots":
                result = await check_plots(self.store, self.llm, self.prompts,
                                           self.settings, args)
            elif action == "global_check":
                result = await global_check(self.store, self.llm, self.prompts,
                                            self.settings, args)
            elif action == "draft_character":
                # P40c：四角色工具核心均为协程，必须在本循环 await——走 _sync_tool 的
                # to_thread → _run_sync 会让 LLM client（连接池属本循环）跨循环复用，
                # 调用挂死至 600s 超时（实跑复现）。与 write_plot 同纪律：按名字走 await。
                result = await draft_character(self.store, self.llm, self.prompts,
                                               self.settings, args)
            elif action == "update_character":
                result = await update_character(self.store, self.llm, self.prompts,
                                                self.settings, args)
            elif action == "delete_character":
                result = await delete_character(self.store, self.llm, self.prompts,
                                                self.settings, args)
            elif action == "build_characters":
                result = await build_characters(self.store, self.llm, self.prompts,
                                                self.settings, self.ui, args)
            else:
                result = await edit_by_command(self.store, self.llm, self.prompts, self.ui, args,
                                              emit=self.events.append, settings=self.settings)
        except Exception as exc:  # noqa: BLE001 - 工具异常归 loop 分类
            return f"FAILED: {type(exc).__name__}: {exc}", "failed", None
        obs, usage = result.text, result.usage
        return obs, self._classify(obs), usage

    async def _sync_tool(self, action, args, response, allowed, *, cooled,
                          step) -> tuple[str, str, bool, dict[str, str]]:
        """只读/同步工具：一步多调（全只读）走 execute_parallel 并逐调用落事件；否则单发 execute。

        并行批同样过冷却门：批内被冷却的调用逐条拒判（观测与单动作冷却门一致），
        拒判按 noop 语义计、不涨 fail_streak——否则刚熔断的工具可被塞进多调用批次立即复活。

        返回 (观测, 分类, 是否已落事件, **逐工具分类表** tool→outcome)——
        并行路径**逐条**分类，失败**记到那个工具**头上（2026-11-15 独立审核 L2：
        此前整批只算一个 outcome，熔断账记在**第一个**工具名下 → 真正失败的那个没被冷却、
        没失败的那个被误冷却）。

        **registry 调用走 ``asyncio.to_thread``**（2026-11-15 独立审核 L3，P4 I-1 纪律）：
        这一支全是**只读**工具，但 ``search_chapters`` 每次调用都要建**全书内存索引**
        （读 300 个章文件 + trigram 分词 + 建倒排）→ 同步跑在事件循环上会让主循环**假死数秒**。
        （同一件事在 ``tools/writing.py`` 里已用 ``asyncio.to_thread``。）
        """
        try:
            calls = self._parallel_calls(response, allowed)
            if calls is not None:
                blocked = [(n, a) for n, a in calls if n in cooled and step < cooled[n]]
                live = [(n, a) for n, a in calls if not (n in cooled and step < cooled[n])]
                for name, _args in blocked:
                    self._record_result(
                        name, f"{name} 冷却中（连续失败≥2）：第 {cooled[name]} 步后可重试")
                results = await asyncio.to_thread(
                    self.registry.execute_parallel, live, allowed=allowed) if live else []
                per_tool: dict[str, str] = {}
                outcomes: list[str] = []
                for (name, _args), ob in zip(live, results, strict=True):
                    self._record_result(name, ob)
                    oc = self._classify(ob)
                    # **同一工具在批内出现多次 → 取最「重」的那个结果**
                    # （P21，独立复审 P17-2）：原先是 `per_tool[name] = oc`（后者覆盖前者）
                    # → 批内 [A 失败, A 成功] 会把失败**覆盖成成功** → 失败**不被归因**
                    # → 归因结果取决于调用顺序。→ failed > transient > noop > ok。
                    per_tool[name] = _worst_outcome(per_tool.get(name), oc)
                    outcomes.append(oc)
                parts = list(results)
                if blocked:
                    names = "、".join(n for n, _ in blocked)
                    parts.insert(0, f"{names} 冷却中（并行批内拒判）")
                joined = "\n".join(parts)
                # **整批的分类 = 各条里最「重」的那个**（failed > transient > ok）
                if "failed" in outcomes:
                    overall = "failed"
                elif blocked:
                    # 批内有被冷却的调用 → 这一步**没有实质进展**（noop），
                    # 但批内**真跑了的**调用的失败仍要归因（上面 failed 分支优先）。
                    overall = "noop"
                elif "transient" in outcomes:
                    overall = "transient"
                else:
                    overall = self._classify(joined)
                return joined, overall, True, per_tool
            obs = await asyncio.to_thread(
                self.registry.execute, action, args, allowed=allowed)
        except Exception as exc:  # noqa: BLE001 - handler 异常不吞，归 loop 分类
            # **观测里不带异常原文**（P21，独立复审 P17-3）：原文含瞬时词（「连接被拒绝」
            # 「超时」）→ 这条**确定性失败**会被 `_classify` 翻成 transient → 永远进不了
            # 失败账 → 死循环重试同一个坏工具；原文也可能带路径等内部细节。
            # → 观测给**确定性标签**，异常类型与原文进日志。
            log.warning("工具 %s 抛异常：%s: %s", action, type(exc).__name__, exc)
            obs = f"FAILED: {action} {reason_label('api:error')}"
            return obs, "failed", False, {action: "failed"}
        outcome = self._classify(obs)
        return obs, outcome, False, {action: outcome}

    def _parallel_calls(self, response, allowed) -> list[tuple[str, dict]] | None:
        """一步多调：>1 个 tool_calls 或 content JSON 的 actions 数组，且全部「已注册 +
        只读 + 白名单内」才走 execute_parallel；否则返回 None（单发第一个）。"""
        raw_calls: list[tuple[str, dict]] = []
        tool_calls = response.get("tool_calls")
        if isinstance(tool_calls, list) and len(tool_calls) > 1:
            for call in tool_calls:
                try:
                    name = call["name"]
                    args = call.get("arguments") or "{}"
                    raw_calls.append((name, json.loads(args) if isinstance(args, str) else dict(args)))
                except (KeyError, TypeError, ValueError):
                    return None
        else:
            for obj in _iter_json_objects(response.get("content") or ""):
                actions = obj.get("actions")
                if not isinstance(actions, list):
                    continue
                for item in actions:
                    if not (isinstance(item, dict) and isinstance(item.get("action"), str)):
                        return None
                    args = item.get("args")
                    raw_calls.append((item["action"], dict(args) if isinstance(args, dict) else {}))
                break
        if len(raw_calls) < 2:
            return None
        if all(self.registry.has(n) and self.registry.is_read_only(n) and n in allowed
               for n, _ in raw_calls):
            return raw_calls
        return None

    @staticmethod
    def _classify(obs: str) -> str:
        """观测 → 失败分类（transient / failed / ok）。

        **瞬时词只扫「状态行」（第一行）**（2026-11-15 独立审核 D4）：
        ``TRANSIENT_MARKERS`` 含「**连接**」这种日常用词——原先扫**整条**观测，
        于是**载荷**里的普通词会误判：一次**成功**的 web_search 因结论里写
        「这条驿道**连接**南北两州」被判 transient → 该步不算有效步、白烧 noop 槽、
        连续几次即 ``paused(noop_streak)``。

        保持既有语义不变：
        - 状态行里出现瞬时词（如「连接超时，请重试」）→ 仍判 transient；
        - **失败**观测（``FAILED…`` / 「失败（…）」）的**细节**里出现瞬时词 → 仍判 transient
          （原来的行为，保留）；
        - **成功**观测的载荷 → 不再参与瞬时词扫描。
        """
        # TRANSIENT_MARKERS 是计划钉死的常量（timeout/超时/连接/截断…），但工具层失败串
        # （outline.py/modify.py「失败（{cls}）：…」）的英文分类词 truncated/starved 一个也
        # 匹配不到——而截断恰是设计点名的瞬时类。constants 不动，英文词表在 loop 侧补齐。
        markers = constants.TRANSIENT_MARKERS + _EXTRA_TRANSIENT_MARKERS
        head = obs.splitlines()[0] if obs else ""
        if any(m in head for m in markers):
            return "transient"
        if obs.startswith("FAILED") or "失败（" in head:
            if any(m in obs for m in markers):
                return "transient"
            return "failed"
        # **NOPROGRESS**（独立复审 M3）：0 产出的拒绝（如 P12 的写作范围闸）→
        # 判 **noop**：不���有效步、**要**涨 noop_streak（连续无进展 → 暂停），
        # 且**不进**按工具分桶的失败账（否则会把工具冷却掉 → 合法目标也调不了）。
        if head.startswith(_NOPROGRESS_MARK):
            return "noop"
        # **P32/A8c**：edit_by_command 全部提议落在适用范围外（模型拿它改章节正文/章卡）
        # → 同为 0 产出拒绝（M3 同口径：不算有效步、不进失败账）；观测另附同类工具
        # 改试提示（``_zero_change_sibling_hint``），模型下一步据此改试 write_plot。
        if head.startswith(_OUT_OF_SCOPE_ZERO_CHANGE):
            return "noop"
        return "ok"

    # ------------------------------------------------------------------ 流程动作

    async def _flow_action(self, action: str, args: dict) -> tuple[str, str]:
        """流程控制动作（内置语义，不进 registry）。async：ask_user / request_plan_approval
        的审批等待经 ``ask_ui_async``——web 文件协议用 asyncio.sleep 轮询，不占死事件循环
        （2026-11-18 验收 Critical ①）；repl 终端路径仍同步调用，行为逐字不变。"""
        if action == "ask_user":
            return await self._ask_user(args)
        if action == "set_step_plan":
            steps = [str(s).strip() for s in (args.get("steps") or []) if str(s).strip()]
            if not steps:
                return "set_step_plan 需要 args.steps（非空步骤列表）", "noop"
            plan = [{"text": s, "done": False} for s in steps[:constants.STEP_PLAN_MAX_STEPS]]
            self.state.stats["step_plan"] = plan
            return f"已建 {len(plan)} 步计划：" + " → ".join(p["text"] for p in plan), "ok"
        if action == "complete_step":
            plan = list(self.state.stats.get("step_plan") or [])
            for i, p in enumerate(plan, 1):
                if isinstance(p, dict) and not p.get("done"):
                    p["done"] = True
                    self.state.stats["step_plan"] = plan
                    return f"已完成步骤 {i}/{len(plan)}：{p.get('text', '')}", "ok"
            return "complete_step：无待完成步骤（计划为空或全部完成）", "noop"
        # request_plan_approval
        question = "是否确认当前规划并进入执行？"
        try:
            choice = await ask_ui_async(self.ui, question, ["确认规划", "需要修改"], multi=False)
        except ApprovalCancelled:
            return "request_plan_approval：已取消", "noop"
        if choice.indices == [0]:
            self.state.stats["plan_confirmed"] = True
            self.events.append("plan_approved", {"question": question, "picked": "确认规划"})
            # git 自动快照（拍板⑧）：审批通过即 commit；失败静默降级
            from harness.snapshot import auto_snapshot
            auto_snapshot(self.events.root, "审批通过", "规划确认门")
            return "规划已确认", "ok"
        self.events.append("plan_rejected", {"question": question, "picked": "需要修改"})
        return "规划被驳回：请按用户意见调整后重新提交", "noop"

    async def _ask_user(self, args: dict) -> tuple[str, str]:
        question = str(args.get("question") or "").strip()
        options = [str(o) for o in (args.get("options") or [])]
        if not question or not options:
            return "ask_user 需要 args.question 与 args.options（候选至少 2 个）", "noop"
        try:
            choice = await ask_ui_async(self.ui, question, options, multi=False)
        except ApprovalCancelled:
            return "ask_user：用户取消回答", "noop"
        picked = "、".join(options[i] for i in choice.indices)
        self.events.append("user_choice", {"question": question, "picked": picked,
                                           "note": choice.note})
        return f"用户选择：{picked}", "ok"

    # ------------------------------------------------------------------ 记账

    def _record_step(self, decision) -> None:
        payload = {"thought": decision.thought, "say": decision.say,
                   "action": decision.action, "args": decision.args}
        self.events.append("agent_step", payload)
        self.memory.record({"kind": "agent_step", "payload": payload})
        if decision.say:
            print(f"💭 {decision.say}")

    def _record_result(self, tool: str, obs: str) -> None:
        obs = trim_observation(obs)
        self.events.append("tool_result", {"tool": tool, "observation": obs})
        self.memory.record({"kind": "tool_result", "payload": {"tool": tool, "observation": obs}})

    def _pause(self, reason: str, detail=None) -> RunState:
        self.state.status = "paused"
        payload = {"reason": reason}
        if detail is not None:
            payload["detail"] = detail
        self.events.append("paused", payload)
        self._save()
        return self.state

    def _check_budget(self) -> RunState | None:
        """token_used 超 2M 预算 → paused + budget_exceeded 事件（决策与工具 usage 同账合计）。
        未超线返回 None。"""
        if int(self.state.stats.get("token_used", 0)) <= constants.BUDGET_MAX_TOKENS:
            return None
        self.state.status = "paused"
        self.events.append("budget_exceeded", {
            "token_used": self.state.stats["token_used"],
            "budget": constants.BUDGET_MAX_TOKENS,
        })
        self._save()
        return self.state

    def _save(self) -> None:
        # JSON 纪律：落盘前经 dumps/loads 重赋全新 dict（fresh_stats 同款的绝不共享
        # 可变模板纪律——防外部持有 stats 旧引用别名串味），再原子写。
        self.state.stats = json.loads(json.dumps(self.state.stats, ensure_ascii=False))
        self.state.save(self.store.root)

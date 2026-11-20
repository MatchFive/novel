"""Run 会话层（W0）：run 启动知识从 repl.py 抽出的 UI 无关模块。

动机：web UI（FastAPI，async 世界，与 run 同一个事件循环）与 REPL（同步世界）
要共用同一份「起 run」逻辑。因此：

- ``drive_run`` 是 **async 且不带** ``asyncio.run()`` / ``print()``——note 经
  ``on_note`` 回调代收（REPL 传 ``print``，web 传自己的推送通道）；
- 单 run 守卫、provider 检查、落盘、驱动全在 ``start_run`` 一处；
- 提示文案（``_STUB_NOTICE``、忙提示）留在 repl，这里**零 print**。

这是**纯搬移**（不改语义）：repl 侧的 asyncio.run + print 行为由 repl.py 的
``_ThreadRunner`` 保持。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from harness.agent.loop import AgentLoop
from harness.config import Settings
from harness.projects import ProjectInfo
from harness.run.approval import ApprovalUI, cancel_answer
from harness.run.constants import WORK_MEMORY_REPLAY
from harness.run.events import EventLog
from harness.run.memory import WorkMemory
from harness.run.state import TERMINAL_STATUSES, RunState
from harness.store.project import ProjectStore

if TYPE_CHECKING:
    from harness.llm.client import OpenAICompatClient
    from harness.tools.registry import ToolRegistry


@dataclass
class Components:
    """loop 三件套：llm / registry / ui（原 ``_make_components`` 的返回值）。"""

    llm: OpenAICompatClient
    registry: ToolRegistry
    ui: ApprovalUI


class RunBusyError(Exception):
    """单 run 守卫拒绝（本地同时只跑一个员工任务）。"""


class Runner(Protocol):
    """UI 无关的单 run 守卫 + 驱动协议。

    REPL 的实现（``_ThreadRunner``）：TTY 阻塞驱动、非 TTY daemon 线程后台驱动；
    web 侧的实现：把 run 绑到当前事件循环上跑。
    """

    def alive(self) -> bool:
        """当前是否有活着的 run（守卫判据）。"""
        ...

    def launch(self, session: RunSession, state: RunState, comp: Components, *,
               intake: tuple[str, list[str], bool] | None = None) -> None:
        """驱动一个已落盘的新 run（同步语义：返回即「驱动安排完毕」）。"""
        ...


class RunSession:
    """一次 run 会话的共享上下文：settings/事件流/当前 state 与记忆（原 ``_Session``）。"""

    def __init__(self, project: ProjectInfo, store: ProjectStore, settings: Settings,
                 *, force_file_ui: bool = False):
        self.project = project
        self.store = store
        self.settings = settings
        self.events = EventLog(store.root)
        self.state: RunState | None = None
        self.memory: WorkMemory | None = None
        # web 会话强制文件协议审批（2026-11-18 验收 Critical ②）：服务端进程 stdin 是
        # TTY 时（Git Bash 终端里直接 ``harness web``），审批路径选择必须与 isatty 解耦——
        # 否则 ask 走终端路径读 stdin → EOF 秒取消，pending.md 根本不落地。
        self._force_file_ui = force_file_ui

    def approval_ui(self) -> ApprovalUI:
        """本会话的审批 UI（**文件模式落项目根**）。

        落点必须与 ``/answer`` 写 pending.answer 的位置**一致**（项目根的 ``.harness/``）
        ——不一致时两边分叉 → run **永远等不到答案 → 挂死**（独立复审 R1）。
        ``force_file_ui``（web）时用 ``non_tty_path`` **强制**文件协议：不看 isatty，
        进程 stdin 是 TTY 也照走 pending.md/pending.answer（验收 Critical ②）。
        """
        base = self.store.root / ".harness"
        if self._force_file_ui:
            return ApprovalUI(non_tty_path=base)
        return ApprovalUI(file_base=base)

    def components(self) -> Components | None:
        """构建 loop 三件套 (llm, registry, ui)；未配置 provider 时返回 None（**不 print**）。

        **P14：不再要求 TTY**——审批在非 TTY 下退化为 ``.harness/pending.md`` 文件协议
        （``ApprovalUI`` 已实现），而 agent loop 本身不需要终端。此前这里直接 return None
        → 非 TTY 会话（管道 / CI / 被别的 agent 驱动）**员工循环根本不可达**，
        设计 §2.1 的「非 TTY 退化为 pending.md 文件确认」**没有 agent run 会用到它**。
        """
        provider = self.settings.providers.get(self.settings.default_provider)
        if provider is None:
            return None
        from harness.llm.client import OpenAICompatClient
        from harness.prompts.loader import PromptLoader
        from harness.tools import readonly_registry, register_modify_tools, register_outline_tools
        from harness.tools.chapters import register_chapter_tools
        from harness.tools.characters import register_character_tools
        from harness.tools.check import register_check_tools
        from harness.tools.memory import register_memory_tools
        from harness.tools.plots import register_plot_tools
        from harness.tools.web import register_web_tools

        llm = OpenAICompatClient(provider)
        prompts = PromptLoader(project_root=self.store.root)
        # 审批的文件模式落**项目根**（与 /answer 写 pending.answer 的位置一致，P14）
        ui = self.approval_ui()
        registry = readonly_registry(self.store)
        register_outline_tools(registry, store=self.store, llm=llm, prompts=prompts,
                               settings=self.settings)
        # P2 规划链两工具：注册只为【可用工具】签名与防御性同步边界——loop 按名字永远
        # 走 await 分支（registry handler 的 no-op emit 路径不主用，见 loop._async_tool）。
        register_plot_tools(registry, store=self.store, llm=llm, prompts=prompts,
                            settings=self.settings)
        register_chapter_tools(registry, store=self.store, llm=llm, prompts=prompts,
                               settings=self.settings)
        register_modify_tools(registry, store=self.store, llm=llm, prompts=prompts, ui=ui)
        # P5 extract_memory：注册只为【可用工具】签名与防御性同步边界——loop 按名字永远
        # 走 await 分支（registry handler 的 no-op emit 路径不主用，见 loop._async_tool）。
        register_memory_tools(registry, store=self.store, llm=llm, prompts=prompts,
                              settings=self.settings)
        # P5 web_search：注册只为【可用工具】签名与防御性同步边界——loop 按名字永远
        # 走 await 分支（registry handler 的 no-op emit 路径不主用，见 loop._async_tool）。
        register_web_tools(registry, store=self.store, llm=llm, prompts=prompts,
                           settings=self.settings)
        # P6 check_plots/global_check：同上（落 issues + 调 LLM 的写工具）。
        register_check_tools(registry, store=self.store, llm=llm, prompts=prompts,
                             settings=self.settings)
        # P3 write_plot：注册进 registry——缺它则提示词工具签名（loop._tool_signatures
        # 取 registry.names）没有 write_plot，白名单放行模型也看不见（第三起同款事故：
        # check/web 已各钉一回归测试，见 tests/tools/test_writing.py::
        # test_repl_components_register_write_plot）。loop 按名字走 _async_tool await 分支。
        from harness.tools import register_writing_tools
        register_writing_tools(registry, store=self.store, llm=llm, prompts=prompts,
                               settings=self.settings, ui=ui)
        # Task 17 draft_character：只起草不落卡的写流程工具（LLM 一次调用；registry
        # 同步边界走 _run_sync，loop 经 _sync_tool 的 asyncio.to_thread 驱动）。
        # P24 build_characters：自动建卡司（规划+起草+人确认门落卡）——ui 注入确认门
        # （edit_by_command 同款 ask_ui_async，web 端 pending.md 通用渲染）。
        register_character_tools(registry, store=self.store, llm=llm, prompts=prompts,
                                 settings=self.settings, ui=ui)
        return Components(llm=llm, registry=registry, ui=ui)

    def load_state(self) -> RunState | None:
        if self.state is None:
            self.state = RunState.load(self.store.root)
        return self.state

    def replay_events(self) -> list[dict]:
        """回放窗口：tail 内从最后一个 run_started 起切片（跨 run 边界回放会串味：
        上个 run 的 brief/步骤混进本 run 记忆）；无 run_started 时退回整段 tail。"""
        tail = self.events.tail(WORK_MEMORY_REPLAY * 2)
        for i in range(len(tail) - 1, -1, -1):
            if tail[i].get("kind") == "run_started":
                return tail[i:]
        return tail

    def new_run(self, raw: str, deliverables: list[str]) -> RunState:
        """原 ``_handle_brief`` 的落盘段：RunState.new → save → run_started 事件 →
        memory 清空并记 run_started。**不含**守卫 / components 检查 / 驱动（归 ``start_run``）。"""
        state = RunState.new(raw, deliverables)
        state.save(self.store.root)
        self.events.append("run_started", {"raw": raw, "deliverables": deliverables})
        self.state = state
        self.memory = WorkMemory()  # 新 run：记忆从本 run 的 run_started 起算
        self.memory.record({"kind": "run_started", "payload": {"raw": raw}})
        return state


def start_run(session: RunSession, runner: Runner, raw: str, deliverables: list[str], *,
              user_specified: bool = False) -> RunState | None:
    """起一个新 run（UI 无关，零 print）：

    ① ``runner.alive()`` → raise :class:`RunBusyError`（连 state 都不落，免得留下
    一个从没跑过的幻影 run）；② ``components()`` 为 None → return None（调用方提示）；
    ③ 落盘新 run 并 ``runner.launch(...)``；④ 返回 state。
    """
    if runner.alive():
        raise RunBusyError("本地同时只跑一个员工任务")
    comp = session.components()
    if comp is None:
        return None
    state = session.new_run(raw, deliverables)
    # P10：需求解析在 ReAct 循环**之前**（设计 §1.1 的 INTAKE 节点）
    runner.launch(session, state, comp, intake=(raw, list(deliverables), user_specified))
    return state


def _cancel_parked_file_approval(session: RunSession) -> None:
    """终审 Finding ①：若文件协议审批正挂起（``.harness/pending.md`` 在），写取消标记
    释放停放的 waiter——否则驱动任务永远活着，全局忙守卫（``runner.alive()``）永不
    解除（web/repl 后续一切起 run 全 409 busy 直到重启）。终端模式无 pending.md，天然 no-op。
    """
    harness_dir = session.store.root / ".harness"
    if (harness_dir / "pending.md").exists():
        cancel_answer(harness_dir)


def pause_run(session: RunSession, reason: str) -> RunState:
    """UI 无关的 /pause 语义（原 repl ``_pause_state`` 的纯搬移）：status=paused +
    落盘 + paused 事件 + memory 清空 + 挂起中的文件审批取消（:func:`_cancel_parked_file_approval`）。
    **零 print**——「没有进行中的 run」的提示留
    调用方（repl 文案逐字保留，web 路由自带守卫）；前置契约被破坏时 raise ValueError。
    """
    state = session.load_state()
    if state is None or state.status in TERMINAL_STATUSES:
        raise ValueError("没有进行中的 run")
    state.status = "paused"
    state.save(session.store.root)
    session.events.append("paused", {"reason": reason})
    _cancel_parked_file_approval(session)
    session.memory = None
    session.state = state
    return state


def abort_run(session: RunSession) -> RunState:
    """UI 无关的 /abort 语义（原 repl ``_cmd_abort`` 落盘段的纯搬移）：status=aborted +
    落盘 + aborted 事件 + memory 清空 + 挂起中的文件审批取消（:func:`_cancel_parked_file_approval`，
    终审 Finding ①——不取消则 waiter 永久停放、忙守卫永不释放）。守卫口径同 :func:`pause_run`。"""
    state = session.load_state()
    if state is None or state.status in TERMINAL_STATUSES:
        raise ValueError("没有进行中的 run")
    state.status = "aborted"
    state.save(session.store.root)
    session.events.append("aborted", {"reason": "user /abort"})
    _cancel_parked_file_approval(session)
    session.memory = None
    session.state = state
    return state


def resume_run(session: RunSession, runner: Runner) -> RunState | None:
    """UI 无关的 /resume 语义（原 repl ``_cmd_resume`` 的会话段）：终态/无 state → None；
    runner 忙 → :class:`RunBusyError`；未配置 provider → None（调用方提示）；否则 replay
    重建 memory 并 ``runner.launch``（**intake 补跑规则照抄 P21/S4**：brief 没有 goal
    的 run 用 raw 补跑一次需求解析，否则 constraints 整轮失效）。

    **零 print**——提示文案（_STUB_NOTICE、忙提示、续跑确认）留调用方逐字保留。
    """
    state = session.load_state()
    if state is None or state.status in TERMINAL_STATUSES:
        return None
    # **单 run 守卫**（P21）：已有活着的 run → 不起第二个（共用 state/memory/events
    # → 状态互相覆盖）。
    if runner.alive():
        raise RunBusyError("本地同时只跑一个员工任务")
    comp = session.components()
    if comp is None:
        return None
    session.memory = WorkMemory.rebuild(session.replay_events())
    raw = str((state.brief or {}).get("raw") or "")
    deliverables = list((state.brief or {}).get("deliverables") or ["outline"])
    intake = None
    if raw and not (state.brief or {}).get("goal"):
        intake = (raw, deliverables, False)
    runner.launch(session, state, comp, intake=intake)
    return state


async def drive_run(session: RunSession, state: RunState, comp: Components, *,
                    intake: tuple[str, list[str], bool] | None = None,
                    on_note: Callable[[str], None] | None = None) -> None:
    """原 ``_run_agent`` 的 async 核心（**无** ``asyncio.run``、**无** ``print``——
    note 经 ``on_note`` 代收；调用方负责 loop 边界与提示输出）。

    ``intake`` 非 None 时：**先跑需求解析**（设计 §1.1 的 INTAKE 节点），把结构化 brief
    并进 ``state.brief``，**再**起 ReAct 循环。两者在**同一个事件循环**里跑——
    LLM 客户端（AsyncOpenAI/httpx）的连接池绑定事件循环，跨 loop 复用会炸。

    解析**失败不阻断**：intake 自己吞掉异常并返回「按原始需求继续」，run 照常。
    """
    session.events.rotate_if_needed()  # run 起点一次轮转检查（50MB/50k 行阈值，超则归档）
    llm, registry, ui = comp.llm, comp.registry, comp.ui
    if session.memory is None:
        # P1 恢复路径：回放近期事件重建四层工作记忆（decided 含原 brief 原文；
        # 只回放到本 run 的 run_started，不跨 run 边界）
        session.memory = WorkMemory.rebuild(session.replay_events())
    from harness.prompts.loader import PromptLoader
    prompts = PromptLoader(project_root=session.store.root)
    # **进 run 时的状态**（P21）：需求解析（LLM 调用，可能几秒）期间用户可能敲了
    # `/pause`、`/abort` → 状态被改 → 解析完**不得**再把 run 拉起来。
    # 判据是「状态**变了**」——续跑路径进来的状态本来就是 paused（那是正常入口）。
    entry_status = state.status

    if intake is not None:
        raw, deliverables, user_specified = intake
        from harness.run.intake import run_intake
        parsed, note = await run_intake(
            store=session.store, llm=llm, prompts=prompts, settings=session.settings,
            raw=raw, deliverables=deliverables)
        del user_specified   # 权限面只由用户决定（独立复审 D2）→ 该标志不再参与解析
        # **失败时 parsed 是 None** → 不碰 brief（保持「只有 raw/deliverables」）
        if parsed:
            state.brief.update(parsed)
            state.save(session.store.root)
        if on_note is not None:
            on_note(note)
    # **需求解析期间被叫停**（P21）：`/pause`、`/abort` 在解析那几秒里到达 →
    # 状态被改 → **不得**再起 ReAct 循环（「已暂停」必须是真的）。
    # 判据：状态**变了**且变成了 paused/aborted（续跑进来的 paused 不算「变了」）。
    if state.status != entry_status and state.status in ("paused", "aborted"):
        if on_note is not None:
            on_note(f"run 在需求解析期间被终止（status={state.status}）——不起循环。")
        return
    # **范围未折算的用户可见说明**（P22/B2，独立复审 P16–P20 建议 13；P27/A1 扩章粒度）：
    # write_target 折算不出确定范围时，那句「如实说明」只进了**提示词**（模型可见）
    # → 用户**看不见**「你的范围没被执行」。此处经 on_note 给 REPL（print）/ web
    # （推送）：章区间折算得出 → 「按章拦截」info 行；卷与章都折算不出 → warn 行；
    # 卷区间折算得出 → 安静（闸会拦，不刷屏）。每 run 一次；口径与提示词内同骨架
    # （G7：不留两份说法）。
    from harness.run.scope import parse_chapter_range, parse_write_range, scope_unfold_reason
    write_target = str((state.brief or {}).get("write_target") or "").strip()
    if write_target and on_note is not None:
        chap_range = parse_chapter_range(write_target)
        if chap_range is not None:
            on_note(f"范围说明：正文范围「{write_target}」→ 按章拦截：只写第 "
                    f"{chap_range[0]}–{chap_range[1]} 章（章号为全书全局编号）。")
        elif parse_write_range(write_target) is None:
            on_note(f"范围说明：正文范围「{write_target}」{scope_unfold_reason(write_target)}"
                    " → 未折算成确定范围，本次不按范围拦截（写盘闸不生效，"
                    "模型按原文范围自行取舍）。")
    # 交付物可能被 intake 改过（模型推断的）→ 白名单**按新值**算
    deliverables_now = list(state.brief.get("deliverables") or ["outline"])
    loop = AgentLoop(store=session.store, state=state, events=session.events,
                     memory=session.memory, registry=registry, ui=ui, llm=llm,
                     prompts=prompts, deliverables=deliverables_now,
                     settings=session.settings)
    await loop.run()

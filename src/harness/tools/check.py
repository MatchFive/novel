"""check_plots / global_check 工具（P6 Task 4，设计 §1.5 验收链）：验收只报不改。

**工具层只做两件事**：参数解析 + 调编排层 ``harness.check.runner.run_check``。
组装上下文 / 分层抽样 / quote 回验 / 落 issues / supersede 全在编排层（Task 3），
本层**不复制**那套口径（copy-propagation 纪律：同一口径不留两份）。

**观测逐字**：工具返回的文本 = ``run_check`` 的返回值（不加工、不改写）——
唯一的例外是 ``plot_ids`` 非空时**追加**一行「已限定情节：…」（如实说明本期的限制：
情节级抽样仍在章层，plot_ids 尚未收窄抽样范围）。

**usage 冒泡**：``run_check`` 只返回观测文本、不返回 usage（T3 钉死的返回形状）→
usage 经**可选关键字** ``meter``（``harness.tools.base.UsageMeter``）逐层下传，
工具侧记 ``meter.total`` → 验收消耗的 token 进 agent loop 的预算账
（同 writing/revise、memory/extract 的既有计量口径）。响应未带 usage → 记 None（不臆造用量）。

**事件落盘**：事件 ``check_report`` 由编排层发（payload 口径不变）。工具侧带一个
**可选关键字** ``emit``：给了就用给的；没给 → 用 ``EventLog(store.root).append``
（事件日志是**文件型**的：``<root>/.harness/events.log``，seq 由文件末行推导——
另一个实例 append 同一文件不与 loop 持有的那个实例冲突）→ **agent 路径也落事件**。
REPL 的 ``/check`` 是直调编排层（自己绑 ``sess.events.append``）→ 仍只落一条，不重复。

**写工具**：两个工具都落 issues（``source="check"``）+ 调 LLM → ``read_only=False``、
进白名单 ``_WRITE_FLOW``（不进只读集、不进并行批）。
"""
from __future__ import annotations

from harness.check.runner import run_check
from harness.config import Settings
from harness.llm.client import OpenAICompatClient
from harness.memory.store import MemoryStore
from harness.prompts.loader import PromptLoader
from harness.run.events import EventLog
from harness.store.project import ProjectStore
from harness.tools.base import ToolResult, UsageMeter, _run_sync
from harness.tools.registry import ToolRegistry

__all__ = ["check_plots", "global_check", "register_check_tools"]


def _default_emit(store: ProjectStore):
    """默认 emit 句柄：``EventLog(store.root).append``（事件日志是文件型的）。

    EventLog 按文件末行推导 seq，另一个实例 append 同一文件不与 loop 持有的那个
    实例冲突 → 工具层自建 emit 安全（agent 路径的事件照样落进同一个 events.log）。
    """
    return EventLog(store.root).append


def _chapter_ids(args: dict) -> list[int] | None:
    """``args.chapter_ids`` → 章号列表；非法/空 → None（= 不限，全部已定稿章）。"""
    raw = args.get("chapter_ids")
    if not isinstance(raw, (list, tuple)):
        return None
    out: list[int] = []
    for item in raw:
        if isinstance(item, bool) or not isinstance(item, int):
            continue
        if item >= 1:
            out.append(item)
    return out or None


def _plot_ids(args: dict) -> list[str]:
    """``args.plot_ids`` → 情节号串列表（仅用于如实说明；本期抽样仍在章层）。"""
    raw = args.get("plot_ids")
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(p).strip() for p in raw if str(p).strip()]


def _editor_mode(args: dict) -> bool:
    """``args.editor_mode`` 只认 ``"on"``（其余值当没给——仍走总编辑模板）。"""
    return str(args.get("editor_mode") or "").strip() == "on"


async def check_plots(store: ProjectStore, llm: OpenAICompatClient,
                      prompts: PromptLoader, settings: Settings,
                      args: dict, *, emit=None) -> ToolResult:
    """情节级验收（task.auto_plot_check）：args = {plot_ids?: list}（缺省 = 全部已规划情节）。

    验什么：完成度/一致性/与目标对齐，逐条判定验收清单（stage ``outline``）。
    产出：verdict（pass/fix/replan）+ 问题清单 → 落 issues 台账（带 quote，可定位到章）。
    **只报不改**：修订走 ``/issues`` 的人工通道。

    另接受 ``args.chapter_ids``（章号列表）→ 限定章摘要的取值范围。
    ``emit`` 可选（事件落盘点）：没给 → ``EventLog(store.root).append``（见 _default_emit）。
    """
    mem = MemoryStore(store.root)
    meter = UsageMeter()
    text = await run_check(store=store, mem=mem, llm=llm, prompts=prompts,
                           settings=settings, kind="plot", chapters=_chapter_ids(args),
                           editor_mode=False,
                           emit=emit if emit is not None else _default_emit(store),
                           meter=meter)
    plot_ids = _plot_ids(args)
    if plot_ids:
        # **只报条数，不回显模型给的字符串**——plot_ids 是模型写的自由文本，回显进观测
        # 会撞上 agent loop 的瞬时词扫描（模型给 plot_ids=["连接"] 就能让整条**成功**的
        # 验收被判 transient）。与「观测不带模型原文」同一条纪律。
        text = (f"{text}\n已限定情节：{len(plot_ids)} 条"
                f"（情节级抽样本期仍在章层——未按情节收窄）")
    return ToolResult(text=text, usage=meter.total)


async def global_check(store: ProjectStore, llm: OpenAICompatClient,
                       prompts: PromptLoader, settings: Settings,
                       args: dict, *, emit=None) -> ToolResult:
    """全书级验收（task.auto_global_check / editor_mode="on" → task.auto_editor_review）：
    args = {chapter_ids?: list, editor_mode?: "on"}。

    **【收尾前必做】**：验什么 = 抽样正文，检查效果与目标达成（目标/限制/人物/结构），
    逐条判定验收清单（stage ``finish``）；产出 = verdict + 问题清单 → 落 issues 台账
    （带 quote，可定位到章），可逐章打回重写。**只报不改**。

    ``emit`` 可选（事件落盘点）：没给 → ``EventLog(store.root).append``（见 _default_emit）。
    """
    mem = MemoryStore(store.root)
    meter = UsageMeter()
    text = await run_check(store=store, mem=mem, llm=llm, prompts=prompts,
                           settings=settings, kind="global", chapters=_chapter_ids(args),
                           editor_mode=_editor_mode(args),
                           emit=emit if emit is not None else _default_emit(store),
                           meter=meter)
    return ToolResult(text=text, usage=meter.total)


def register_check_tools(reg: ToolRegistry, *, store: ProjectStore,
                         llm: OpenAICompatClient, prompts: PromptLoader,
                         settings: Settings) -> None:
    """注册 check_plots / global_check（read_only=False）：落 issues + 调 LLM。"""

    def handler_plots(store_: ProjectStore, args: dict) -> str:
        return _run_sync(check_plots(store_, llm, prompts, settings, args)).text

    def handler_global(store_: ProjectStore, args: dict) -> str:
        return _run_sync(global_check(store_, llm, prompts, settings, args)).text

    reg.register("check_plots", handler_plots, read_only=False)
    reg.register("global_check", handler_global, read_only=False)

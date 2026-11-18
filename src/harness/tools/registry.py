"""工具注册表：name → handler（+ read_only 标记），白名单拒绝不 raise，失败归 loop 分类。"""
from __future__ import annotations

import logging
from collections.abc import Callable

from harness.reasons import reason_label
from harness.run.constants import MAX_PARALLEL
from harness.store.project import ProjectStore

__all__ = ["ToolRegistry"]

# 重导出 tools.base 的 P2 基座（ToolResult/EmitFn/complete_structured/usage_add/with_shrink）。
# 惰性（PEP 562）：base 顶层已不 import outline（extract_json 惰性导入破环），但 registry
# ← outline/modify ← base 的引用链仍在，保持惰性可避免任何导入顺序拿到半成品模块。
_LAZY_BASE_EXPORTS = frozenset(
    {"ToolResult", "EmitFn", "complete_structured", "usage_add", "with_shrink"})

# handler 签名：(store, args) -> str
ToolHandler = Callable[[ProjectStore, dict], str]

_REFUSAL = "该动作不在本任务白名单"

#: handler 抛异常时的**观测标签**（走 reason_label 的 api:* 分支 → 「调用失败」）。
#: 不把异常原文拼进观测（P21）：原文里的瞬时词会把确定性失败翻成 transient。
_EXC_REASON = "api:error"

log = logging.getLogger("tools.registry")


def _trim_observation(text: str) -> str:
    """惰性导入 agent.steps（registry→agent→tools 环的断边：行为不变，导入挪进调用点）。

    agent/__init__ 会拉 agent.loop，loop 又顶层 from harness.tools import …——
    registry 顶层再 import agent.steps 就构成 tools-first 导入方向的循环 import（既有潜伏缺陷）。
    """
    from harness.agent.steps import trim_observation
    return trim_observation(text)


class ToolRegistry:
    """工具注册表。execute 系方法统一过 trim_observation，白名单外返回拒绝文案（不 raise）。"""

    def __init__(self, store: ProjectStore):
        self.store = store
        self._handlers: dict[str, ToolHandler] = {}
        self._read_only: set[str] = set()

    def register(self, name: str, handler: ToolHandler, *, read_only: bool) -> None:
        if name in self._handlers:
            raise ValueError(f"工具重复注册: {name}")
        self._handlers[name] = handler
        if read_only:
            self._read_only.add(name)

    def is_read_only(self, name: str) -> bool:
        return name in self._read_only

    @property
    def names(self) -> tuple[str, ...]:
        """已注册工具名（排序）：AgentLoop 渲染【可用工具】签名用。"""
        return tuple(sorted(self._handlers))

    def has(self, name: str) -> bool:
        return name in self._handlers

    def doc_of(self, name: str) -> str:
        """handler docstring 首行（无则空串）——闭包 handler 无 docstring，由 loop 兜底表补签名。"""
        handler = self._handlers.get(name)
        doc = (handler.__doc__ or "").strip() if handler is not None else ""
        return doc.splitlines()[0] if doc else ""

    def execute(self, name: str, args: dict, *, allowed) -> str:
        """白名单外 → 拒绝文案（带工具名与 allowed 提示）；未注册 → KeyError；
        handler 异常不吞（loop 拥有失败分类）；成功 → 截断后的观测串。"""
        if name not in allowed:
            hint = "、".join(sorted(allowed)) or "（空）"
            return f"{_REFUSAL}：{name}（本任务可用：{hint}）"
        handler = self._handlers[name]  # 未注册响亮失败（KeyError）
        return _trim_observation(handler(self.store, dict(args)))

    def execute_parallel(self, calls: list[tuple[str, dict]], *, allowed) -> list[str]:
        """全部调用须 read_only 且 in allowed 且 ≤ MAX_PARALLEL；违反的调用得逐条拒绝/错误串，不 raise。

        **逐条捕异常**（2026-11-15 独立审核 L2）：此前任一调用抛异常 → 异常穿出整个批次 →
        loop 只知道「这个批次失败了」，**不知道是哪个工具失败** → 熔断账记在**第一个**工具头上
        → 真正失败的那个工具没被冷却，没失败的那个被误冷却。
        → 现在每条调用的异常都被**就地**记成该条观测（带工具名 + ``FAILED:`` 前缀），
        且 loop 能逐条归因。

        **观测里不带异常原文**（2026-11-17 独立复审 P17-3）：原文里常含**瞬时词**
        （「连接被拒绝」「超时」）→ loop 的分类会把这条**确定性失败**翻成 transient →
        它永远进不了失败账 → 死循环重试同一个坏工具；原文也可能带路径等内部细节。
        → 观测只给**确定性标签**（``harness.reasons.reason_label``，唯一权威源），
        **异常类型与原文进日志**（可查、不上屏）。
        """
        if len(calls) > MAX_PARALLEL:
            return [f"并行调用超限：{len(calls)} > {MAX_PARALLEL}（每次最多 {MAX_PARALLEL} 个并行调用）"
                    for _ in calls]
        out = []
        for name, args in calls:
            if name not in self._handlers:
                out.append(f"未知工具：{name}")
            elif name not in self._read_only:
                out.append(f"并行调用仅允许只读工具：{name} 会写盘，请单独 execute")
            elif name not in allowed:
                hint = "、".join(sorted(allowed)) or "（空）"
                out.append(f"{_REFUSAL}：{name}（本任务可用：{hint}）")
            else:
                try:
                    out.append(_trim_observation(
                        self._handlers[name](self.store, dict(args))))
                except Exception as exc:          # noqa: BLE001 —— 逐条兜底、错误不藏
                    log.warning("只读工具 %s 抛异常：%s: %s", name,
                                type(exc).__name__, exc)
                    out.append(f"FAILED: {name} {reason_label(_EXC_REASON)}")
        return out


def __getattr__(name: str):
    """PEP 562 惰性重导出 tools.base 基座名（避免 registry→base→outline→registry import 环）。"""
    if name in _LAZY_BASE_EXPORTS:
        from harness.tools import base
        return getattr(base, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

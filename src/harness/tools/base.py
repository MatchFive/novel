"""LLM 重型工具共享基座：ToolResult / emit 协议 / complete_structured 扩额重试阶梯 / usage 记账。

P1 的 plan_arcs/edit_by_command 各自内联了同款扩额重试阶梯；P2 再增两个 LLM 重型工具，
阶梯在此收敛成一份（copy-propagation 教训：同一 ladder 多处拷贝会各自漂移）。
本基座只服务 P2 新工具——既有工具行为不动（P3 再统一，评审勿视为不一致缺陷）。

阶梯契约（与 P1 工具层一致）：
- 解析失败（extract 返回 None）→ 扩额（×2 封顶）重试恰好一次
- LLMError kind="empty" → 重试一次且直接顶 CAP_MAX_TOKENS；kind="starved" → ×2 封顶
- api/timeout 等其余分类不重试
- 分类串："truncated"（starved）/ "empty" / "api:{kind}" / "bad_json"（解析失败），供工具观测拼接

with_shrink（移植 web_dream llm/retry_util.py:19-32 语义）：批量 JSON 生成「输出超长 →
截断 → 非法」的收缩重试——首轮正常跑，产出无效（由调用方 valid 判定）则附收缩附注再跑一轮。
注意**只有产出无效才收缩重试**（有效但空语义的产出不该被当成失败重试，否则会白烧 token）。
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from harness.llm.client import CompletionUsage, LLMError, OpenAICompatClient, StreamState
from harness.llm.policy import CAP_MAX_TOKENS
from harness.text import repeat_loop_cut
from harness.writing.pure import STREAM_CHECK_INTERVAL

__all__ = ["EmitFn", "ToolResult", "UsageMeter", "complete_structured", "extract_json",
           "stream_collected", "usage_add", "with_shrink"]

# 进度上报节流节奏（seed draft_workflow._report：每 ~120 字一帧）
_STREAM_REPORT_CHARS = 120


@dataclass
class ToolResult:
    """工具结构化结果：正文观测串 + token 用量（响应未带 usage / 无成功调用时为 None）。"""

    text: str
    usage: CompletionUsage | None = None


# emit 协议：kind, payload → 落 events；同步调用，实现方 loop 绑 EventLog.append
EmitFn = Callable[[str, dict], None]


def usage_add(a: CompletionUsage | None, b: CompletionUsage | None) -> CompletionUsage | None:
    """None 容忍的 token 加法；两边都 None → None（尚无任何可记账的调用）。"""
    if a is None and b is None:
        return None
    a = a or CompletionUsage(prompt_tokens=0, completion_tokens=0)
    b = b or CompletionUsage(prompt_tokens=0, completion_tokens=0)
    return CompletionUsage(prompt_tokens=a.prompt_tokens + b.prompt_tokens,
                           completion_tokens=a.completion_tokens + b.completion_tokens)


class UsageMeter:
    """usage 累计器：跨层传递的可选计量（meter=None 时不记账）。

    一次 LLM 工具调用常散着多个子调用（写作链：策划/逐拍/审核/修正/去味/提取），
    各子调用原先把 usage 丢掉（``data, _usage, reason = ...``）——本类给跨层
    传递留一个统一计量口：``add(use)`` 逐次入账（None 容忍：响应未带 usage 的
    调用跳过），``total`` 取累计值（无任何记账时 None）。

    计量是**可选**的：调用方传 meter=None 时行为与从前逐字相同（不记账、不改返回）。
    """

    def __init__(self) -> None:
        self._total: CompletionUsage | None = None

    def add(self, usage: CompletionUsage | None) -> None:
        """入账一次子调用的 usage；None 跳过（响应未带 usage）。"""
        if usage is None:
            return
        self._total = usage_add(self._total, usage)

    @property
    def total(self) -> CompletionUsage | None:
        """累计 usage；无任何记账时 None（尚无可记账的调用）。"""
        return self._total


def _run_sync(coro):
    """同步边界驱动异步核心：无事件循环 → asyncio.run；已在循环内 → 响亮报错（并关闭协程防噪音）。

    P2 共享版（P2 pre-flight 裁定从 outline.py 抽入）：注册进 ToolRegistry 的 handler 是
    同步签名，内部经此驱动异步工具；Task 12 主循环在事件循环里，应直接 await 模块级协程
    （master_plan/plan_arcs/edit_by_command），handler 在循环内被调会在此响亮失败。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    coro.close()
    raise RuntimeError("该工具为异步工具：事件循环内请直接 await 模块级协程函数")


def extract_json(text: str) -> object | None:
    """取第一个可解析的 JSON 值（对象/数组）——复用 outline._extract_json_value，勿复制实现。

    P2 结构化工具的默认 extract：校验结构是调用方的事（extract 拿到值后再断言 dict/键）。
    惰性导入（outline 顶层 from base import _run_sync，base 顶层再 import outline 会成环）。
    """
    from harness.tools.outline import _extract_json_value
    return _extract_json_value(text)


async def with_shrink(run_with_note: Callable[[str], Awaitable], *, note: str,
                      valid: Callable[[object], bool]) -> object | None:
    """先正常跑一次（附注空串）；产出无效则附收缩附注再跑一轮。返回首个有效结果或 None。

    - ``run_with_note(note_suffix)``：首轮传 ""（正常尝试），二轮传 ``note``
      （把附注拼进提示词——调用方闭包负责拼接）。
    - ``valid``：判定「产出有效」；**务必按调用语义传入**，否则「合法的空产出」会被
      误判为失败而重复生成（白烧 token）。固定两轮：首轮 + 至多一次收缩重试。
    """
    data = await run_with_note("")
    if valid(data):
        return data
    data = await run_with_note(note)
    if valid(data):
        return data
    return None


def _escalate(max_tokens: int, *, kind: str) -> int:
    """empty 直接顶 CAP；starved 与解析失败走 ×2 封顶（与 P1 bad_json 阶梯同一公式）。"""
    if kind == "empty":
        return CAP_MAX_TOKENS
    return min(CAP_MAX_TOKENS, max_tokens * 2)


async def complete_structured(llm: OpenAICompatClient, *, model: str, temperature: float,
                              max_tokens: int, prompt: str,
                              extract: Callable[[str], object | None], escalate: bool = True,
                              ) -> tuple[object | None, CompletionUsage | None, str]:
    """非流式 complete() 一次 + 扩额重试阶梯 → (value, usage, reason)。

    成功：(extract 得值, 累计 usage, "")；失败：(None, 累计 usage 或 None, 分类串)。
    escalate=False 时单次即返（调用方已自行保证额度）。LLMError 以外的异常原样上抛
    （参数类错误 fail loud，由 loop 分类）；usage 跨尝试累计，重试不丢记账。
    """
    messages = [{"role": "user", "content": prompt}]
    usage: CompletionUsage | None = None
    for attempt in (1, 2):
        try:
            completion = await llm.complete(messages, model=model,
                                            temperature=temperature, max_tokens=max_tokens)
        except LLMError as exc:
            # 空响应/截断 → 恰好一次扩额；api/timeout 等其余分类不重试
            if attempt == 1 and escalate and exc.kind in ("starved", "empty"):
                max_tokens = _escalate(max_tokens, kind=exc.kind)
                continue
            reason = {"starved": "truncated", "empty": "empty"}.get(exc.kind, f"api:{exc.kind}")
            return None, usage, reason
        usage = usage_add(usage, completion.usage)
        value = extract(completion.text)
        if value is not None:
            return value, usage, ""
        if attempt == 1 and escalate:
            max_tokens = _escalate(max_tokens, kind="starved")  # 解析失败按 ×2 封顶扩额
            continue
        return None, usage, "bad_json"
    raise AssertionError("unreachable")  # 两轮循环必返回，防误改流程后静默坠落


async def stream_collected(llm: OpenAICompatClient, *, model: str, temperature: float,
                           max_tokens: int, system: str, prompt: str,
                           max_chars: int | None = None,
                           on_text: Callable[[str], None] | None = None,
                           ) -> tuple[str, CompletionUsage | None, str]:
    """流式单段聚合 choke point：seed ``draft_workflow._stream_llm`` 与 complete_structured
    重试阶梯的融合——单流边收边聚，usage 记账不丢，退化/超长就地截。

    - 每 ``STREAM_CHECK_INTERVAL`` 字查 ``repeat_loop_cut``，命中即截断（保留第一份）按成功返回
    - ``max_chars`` 早停：截到上限按成功返回（seed 语义：单段输出不越上限）
    - ``on_text(accumulated)`` 每 ≥120 字增量回调一次（节流，seed ``_report`` 节奏；
      None 跳过）；收尾对最新文本补一帧（截断后也能上屏）
    - 空聚合（LLMError kind="empty"/"starved"）→ ``_escalate`` 恰好重试一次
      （empty→CAP、starved→×2）；api/timeout 不重试；usage 跨尝试累计
    - 裸 SDK/httpx 瞬时错误先经 client 的 ``_reraise`` 归成 LLMError 再分类
      （参数类错误原样上抛，fail loud，同 complete_structured）
    - 返回 (text, usage, reason)：成功/截断 reason=""；终败 ("", 累计 usage, reason)
      （词表同 complete_structured：truncated/empty/api:{kind}）
    """
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": prompt}]
    usage: CompletionUsage | None = None
    for attempt in (1, 2):
        state = StreamState()
        pieces: list[str] = []  # list 累积 + join：病态小 chunk 下 text += piece 是 O(n²)
        n_chars = 0
        last_report = 0
        check_at = STREAM_CHECK_INTERVAL
        gen = llm._stream_pieces(messages, model=model, temperature=temperature,
                                 max_tokens=max_tokens, thinking=False,
                                 include_usage=True, state=state)
        try:
            async for piece in gen:
                pieces.append(piece)
                n_chars += len(piece)
                if n_chars >= check_at:
                    check_at = n_chars + STREAM_CHECK_INTERVAL
                    cut = repeat_loop_cut("".join(pieces))
                    if cut is not None:
                        pieces = ["".join(pieces)[:cut]]
                        n_chars = cut
                        break
                if max_chars is not None and n_chars >= max_chars:
                    pieces = ["".join(pieces)[:max_chars]]
                    n_chars = max_chars
                    break
                if on_text is not None and n_chars - last_report >= _STREAM_REPORT_CHARS:
                    last_report = n_chars
                    on_text("".join(pieces))
        except Exception as exc:  # noqa: BLE001 - 裸 SDK/httpx 错误先经 client 归类（见下）
            if not isinstance(exc, LLMError):
                # _stream_pieces 在 client 的 _reraise 包装之外：瞬时错误（超时/连接）
                # 先归成 LLMError(kind="timeout"/"api") 才能进契约分类；参数类错误
                # _reraise 原样重抛（fail loud，同 complete_structured）
                try:
                    llm._reraise(exc)
                except LLMError as converted:
                    exc = converted
            # 空聚合/截断空 → 恰好一次扩额；api/timeout 等其余分类不重试
            if attempt == 1 and exc.kind in ("starved", "empty"):
                max_tokens = _escalate(max_tokens, kind=exc.kind)
                continue
            reason = {"starved": "truncated", "empty": "empty"}.get(exc.kind, f"api:{exc.kind}")
            return "", usage, reason
        finally:
            # 熔断/早停/异常都得关：级联关闭底层 SDK 流（finally 里不 await 会泄漏连接）
            await gen.aclose()
        usage = usage_add(usage, state.usage)
        text = "".join(pieces)
        if on_text is not None and len(text) > last_report:
            on_text(text)  # 收尾补一帧：短稿/截断稿也要上屏
        return text, usage, ""
    raise AssertionError("unreachable")  # 两轮循环必返回，防误改流程后静默坠落

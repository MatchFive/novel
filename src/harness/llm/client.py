"""OpenAI 兼容 LLM 客户端：``complete`` / ``stream_text`` / ``stream_completion``。

契约（设计 §2.7）：

- 超时 600s（非流式）/ 1800s（流式）；``max_retries=0``（重试策略在上层，policy.py 管 max_tokens）
- temperature/参数类错误原样上抛、不重试
- thinking → ``extra_body={"thinking": {"type": "enabled"}}``
- 流式 ``<think>`` 块剥离（标签可跨 chunk，种子来自 web_dream app/llm/client.py）
- 空流 → ``LLMError("empty_stream")``；非流式空内容 → ``LLMError("empty_response")``
- 每次调用（成功 + 失败）向 ``log_path`` 追加一行 JSONL
  ``{model, duration_s, finish_reason?, error_type?}``；``log_path=None`` 跳过

``LLMError.transient=True`` 表示瞬时类（超时/连接/截断），P1 员工主循环的熔断逻辑消费该标志。
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Self

import httpx
from openai import APIConnectionError, APITimeoutError, AsyncOpenAI

from harness.config import ProviderConfig

log = logging.getLogger("llm.client")

# 思考型模型（DeepSeek/Qwen3…）会在正文前输出 <think>…</think> 推理块
_THINK_RE = re.compile(r"<think(?:ing)?>.*?</think(?:ing)?>", re.DOTALL | re.IGNORECASE)
_THINK_OPEN_RE = re.compile(r"<think(?:ing)?>", re.IGNORECASE)
_THINK_CLOSE_RE = re.compile(r"</think(?:ing)?>", re.IGNORECASE)

# 瞬时错误分类（P1 熔断消费）：超时 / 连接失败——短暂等待后重试可能成功
_TRANSIENT = (APITimeoutError, APIConnectionError, httpx.TimeoutException)


def _is_transient(exc: BaseException) -> bool:
    """识别瞬时错误（SDK/httpx 超时连接类型，外加文案兜底——部分兼容网关只回文本）。"""
    if isinstance(exc, _TRANSIENT):
        return True
    text = str(exc).lower()
    return "timed out" in text or "timeout" in text


class _ThinkStripper:
    """流式剥离 <think>…</think> 推理块（种子复制自 web_dream，可自由演进）。

    标签可能被切成多个 chunk，所以按边界缓冲：标签未闭合前不吐字，最多延迟 ``_TAIL`` 个字符。
    推理块内的一切都丢弃——否则 ``<think>…</think>`` 会被当成正文写进章节。
    """

    _TAIL = 16  # 可能被截断的标签最长长度（"<thinking>" 为 10）

    def __init__(self) -> None:
        self._buf = ""
        self._inside = False

    def feed(self, chunk: str) -> str:
        self._buf += chunk
        out: list[str] = []
        while self._buf:
            pattern = _THINK_CLOSE_RE if self._inside else _THINK_OPEN_RE
            m = pattern.search(self._buf)
            if m is None:
                keep = min(len(self._buf), self._TAIL)
                if not self._inside:
                    # 只有「不在推理块里」时才吐字；推理块内的一切都丢弃
                    out.append(self._buf[:len(self._buf) - keep])
                self._buf = self._buf[len(self._buf) - keep:]
                break
            if not self._inside:
                out.append(self._buf[:m.start()])
            self._buf = self._buf[m.end():]
            self._inside = not self._inside
        return "".join(out)

    def flush(self) -> str:
        """流结束：未闭合的推理块（多半是截断）丢弃，其余吐出。"""
        rest, self._buf = self._buf, ""
        return "" if self._inside else rest


@dataclass
class CompletionUsage:
    """token 记账（P1 预算消费）：从 openai SDK 响应的 ``usage`` 映射而来。"""
    prompt_tokens: int
    completion_tokens: int


@dataclass
class Completion:
    """``complete()`` 的非流式结果：正文 + token 用量（响应未带 usage 时为 None）。"""
    text: str
    usage: CompletionUsage | None


@dataclass
class StreamCompletion:
    """``stream_completion()`` 的流式聚合结果：全文 + 末 chunk 的 token 用量。

    usage 来自 ``stream_options={"include_usage": True}`` 的末 chunk（choices 为空、只带
    usage 的那个）； provider/网关不带 → None（容忍，同 ``Completion.usage``）。
    """
    text: str
    usage: CompletionUsage | None


@dataclass
class StreamState:
    """流式过程状态（out-param）：``_stream_pieces`` 边收边回填。

    ``stream_text``/``stream_completion`` 内部自用；``tools.base.stream_collected``
    也消费它（边收边熔断/上报的同时拿到 usage）。usage 只可能在 include_usage
    的末 chunk 出现，中途读取恒为 None。
    """
    finish_reason: str | None = None
    usage: CompletionUsage | None = None


class LLMError(Exception):
    """LLM 调用失败。

    ``kind``：稳定错误分类（``"api"`` 默认 / ``"timeout"`` 超时 /
    ``"starved"`` finish_reason="length" 被 max_tokens 截断 / ``"empty"`` 正常结束但内容为空）。
    ``transient=True`` 表示瞬时类（超时/连接/截断），P1 熔断据此决定是否重试。
    ``finish_reason``：模型给出的结束原因（"length" = 被 max_tokens 截断）。
    """

    def __init__(self, message: str, *, transient: bool = False,
                 finish_reason: str | None = None, kind: str = "api"):
        super().__init__(message)
        self.transient = transient
        self.finish_reason = finish_reason
        self.kind = kind


class OpenAICompatClient:
    """覆盖任意 OpenAI 兼容接口（DeepSeek/通义/智谱/Kimi/Ollama…）。

    ``http_client`` 可注入（测试用 ``httpx.MockTransport``）；未注入时自建。
    每次调用在 ``finally`` 路径向 ``log_path`` 追加一行 JSONL（成功失败都记）。
    """

    _TIMEOUT = 600.0        # 非流式契约超时
    _STREAM_TIMEOUT = 1800.0  # 流式长文生成契约超时

    def __init__(self, provider: ProviderConfig, *,
                 http_client: httpx.AsyncClient | None = None,
                 log_path: Path | None = None) -> None:
        self._provider = provider
        self._log_path = Path(log_path) if log_path is not None else None
        self._client = AsyncOpenAI(
            base_url=provider.base_url,
            api_key=provider.api_key or "sk-placeholder",
            http_client=http_client,
            timeout=self._TIMEOUT,
            max_retries=0,  # 重试交给上层降级策略
        )

    @property
    def provider(self) -> ProviderConfig:
        """provider 配置（tier 模型名等）只读暴露：工具层按档位选模型（plan_arcs→pro、轻量修改→standard）。"""
        return self._provider

    async def aclose(self) -> None:
        """显式关闭底层 httpx client——P1 backlog 复查项（长驻 worker 防连接泄漏）。

        所有权在调用方：共享本 client 的主循环（Task 12）在停机时 ``await client.aclose()``；
        工具内拿到的注入 client 不得关闭（否则后续 step 决策调用会撞已关闭连接池）。
        """
        await self._client.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.aclose()

    def _create_kwargs(self, *, model: str, messages: list[dict],
                       temperature: float, max_tokens: int, thinking: bool,
                       stream: bool, timeout: float) -> dict:
        kwargs: dict = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
            "timeout": timeout,
        }
        if thinking:
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
        return kwargs

    def _write_log(self, *, model: str, duration_s: float,
                   finish_reason: str | None, error: BaseException | None) -> None:
        """每次调用（成功 + 失败）追加一行 JSONL；log_path=None 跳过。"""
        if self._log_path is None:
            return
        rec: dict = {"model": model, "duration_s": round(duration_s, 3)}
        if finish_reason:
            rec["finish_reason"] = finish_reason
        if error is not None:
            rec["error_type"] = type(error).__name__
        try:
            with self._log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            log.warning("llm.log 写入失败: %s", self._log_path, exc_info=True)

    @staticmethod
    def _reraise(exc: Exception) -> None:
        """瞬时错误包成 LLMError(transient=True)；其余（temperature/参数类）原样上抛。

        超时类 kind="timeout"，其余瞬时/非瞬时包装 kind="api"。
        """
        if _is_transient(exc):
            text = str(exc).lower()
            kind = "timeout" if (isinstance(exc, (APITimeoutError, httpx.TimeoutException))
                                 or "timed out" in text or "timeout" in text) else "api"
            raise LLMError(str(exc), transient=True, kind=kind) from exc
        raise exc

    async def complete(self, messages: list[dict], *, model: str,
                       temperature: float = 0.7, max_tokens: int = 8192,
                       thinking: bool = False) -> Completion:
        """非流式调用；content 为空抛 ``LLMError("empty_response")``。

        返回 :class:`Completion`：正文 + token 用量（P1 预算记账消费）。
        """
        t0 = time.perf_counter()
        finish_reason: str | None = None
        error: BaseException | None = None
        try:
            resp = await self._client.chat.completions.create(
                **self._create_kwargs(model=model, messages=messages,
                                      temperature=temperature, max_tokens=max_tokens,
                                      thinking=thinking, stream=False,
                                      timeout=self._TIMEOUT))
            choice = resp.choices[0]
            finish_reason = getattr(choice, "finish_reason", None)
            content = _THINK_RE.sub("", choice.message.content or "").strip()
            if not content:
                # 思考模式吃满 max_tokens 时 content 会是空的（推理都在 reasoning_content 里）
                # 空内容只分两类：length=截断(starved)；其余（stop/content_filter/None…）
                # 一律 empty——complete_structured 阶梯对 empty 扩额重试一次。此前把非
                # stop 的 odd finish_reason 判成 "api"（无重试且语义骗人：并无 API 错误，
                # DeepSeek-flash 偶发空响应即死在这条上，见 P40）。
                kind = "starved" if finish_reason == "length" else "empty"
                raise LLMError("empty_response", transient=finish_reason == "length",
                               finish_reason=finish_reason, kind=kind)
            sdk_usage = resp.usage
            usage = (CompletionUsage(prompt_tokens=sdk_usage.prompt_tokens,
                                     completion_tokens=sdk_usage.completion_tokens)
                     if sdk_usage is not None else None)
            return Completion(text=content, usage=usage)
        except Exception as exc:  # noqa: BLE001 - 有意广捕：所有异常先记 llm.log，再按瞬时性分类重抛
            error = exc
            self._reraise(exc)
        finally:
            self._write_log(model=model, duration_s=time.perf_counter() - t0,
                            finish_reason=finish_reason, error=error)

    async def _stream_pieces(self, messages: list[dict], *, model: str,
                             temperature: float, max_tokens: int, thinking: bool,
                             include_usage: bool, state: StreamState) -> AsyncIterator[str]:
        """共享剥离循环：逐 chunk 去 <think> 块后产出正文片段，过程状态回填到 ``state``。

        ``stream_text`` / ``stream_completion`` / ``tools.base.stream_collected`` 三方共用
        （禁止复制两份剥离循环）；差异只在 include_usage（usage 记账开关）与返回形态。
        聚合为空在**这里**抛 ``LLMError("empty_stream")``（kind 分类见下），调用方只管
        传播。消费方提前 break 时必须 ``agen.aclose()``——finally 里会级联关闭底层 SDK 流。
        """
        stripper = _ThinkStripper()
        produced: list[str] = []
        kwargs = self._create_kwargs(model=model, messages=messages,
                                     temperature=temperature, max_tokens=max_tokens,
                                     thinking=thinking, stream=True,
                                     timeout=self._STREAM_TIMEOUT)
        if include_usage:
            kwargs["stream_options"] = {"include_usage": True}
        stream = await self._client.chat.completions.create(**kwargs)
        try:
            async for chunk in stream:
                sdk_usage = getattr(chunk, "usage", None)
                if sdk_usage is not None:
                    state.usage = CompletionUsage(prompt_tokens=sdk_usage.prompt_tokens,
                                                  completion_tokens=sdk_usage.completion_tokens)
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                if getattr(choice, "finish_reason", None):
                    state.finish_reason = choice.finish_reason
                delta = choice.delta
                if delta and delta.content:
                    text = stripper.feed(delta.content)
                    if text:
                        produced.append(text)
                        yield text
            tail = stripper.flush()
            if tail:
                produced.append(tail)
                yield tail
            if not "".join(produced).strip():
                # 一个 delta 都没吐也"正常结束" = 空稿，不能静默成功
                kind = "starved" if state.finish_reason == "length" else (
                    "empty" if state.finish_reason == "stop" else "api")
                raise LLMError("empty_stream", transient=state.finish_reason == "length",
                               finish_reason=state.finish_reason, kind=kind)
        finally:
            try:
                await stream.aclose()
            except Exception:
                log.debug("stream 关闭失败", exc_info=True)

    async def stream_text(self, messages: list[dict], *, model: str,
                          temperature: float = 0.7, max_tokens: int = 8192,
                          thinking: bool = False) -> AsyncIterator[str]:
        """流式调用，逐 chunk 剥离 <think> 块后产出；聚合为空抛 ``LLMError("empty_stream")``。"""
        t0 = time.perf_counter()
        state = StreamState()
        error: BaseException | None = None
        try:
            async for piece in self._stream_pieces(messages, model=model,
                                                   temperature=temperature, max_tokens=max_tokens,
                                                   thinking=thinking, include_usage=False,
                                                   state=state):
                yield piece
        except Exception as exc:  # noqa: BLE001 - 有意广捕：所有异常先记 llm.log，再按瞬时性分类重抛
            error = exc
            self._reraise(exc)
        finally:
            self._write_log(model=model, duration_s=time.perf_counter() - t0,
                            finish_reason=state.finish_reason, error=error)

    async def stream_completion(self, messages: list[dict], *, model: str,
                                temperature: float = 0.7, max_tokens: int = 8192,
                                thinking: bool = False) -> StreamCompletion:
        """流式聚合 + usage 记账：``stream_options={"include_usage": True}``，全部 chunk 拼全文。

        剥离循环与 ``stream_text`` 共用（``_stream_pieces``）；末 chunk 的 usage →
        :class:`CompletionUsage`（无 usage chunk → None 容忍）；聚合为空抛
        ``LLMError("empty_stream")``（kind 分类同 ``stream_text``）。llm.log 照 finally 惯例。
        """
        t0 = time.perf_counter()
        state = StreamState()
        error: BaseException | None = None
        try:
            text = "".join([piece async for piece in self._stream_pieces(
                messages, model=model, temperature=temperature, max_tokens=max_tokens,
                thinking=thinking, include_usage=True, state=state)]).strip()
            return StreamCompletion(text=text, usage=state.usage)
        except Exception as exc:  # noqa: BLE001 - 有意广捕：所有异常先记 llm.log，再按瞬时性分类重抛
            error = exc
            self._reraise(exc)
        finally:
            self._write_log(model=model, duration_s=time.perf_counter() - t0,
                            finish_reason=state.finish_reason, error=error)

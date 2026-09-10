"""LLM 统一调用服务：模板加载（经仓储）→ 渲染 → 路由 → 调用 → 留痕。

遵循设计文档 4.4：本服务不直接操作数据库；模板与日志均通过 Repository。
"""
from __future__ import annotations

import time
import traceback
from collections.abc import AsyncIterator

from app.core.config import AppConfig
from app.core.constants import TIER_PRO, TIER_STANDARD
from app.core.logger import get_logger
from app.db.repositories.generation_log_repo import GenerationLogRepository
from app.db.repositories.prompt_repo import PromptTemplateRepository
from app.db.session import Database
from app.llm.client import LLMError, OpenAICompatClient
from app.llm.prompts import render, render_messages
from app.llm.router import ResolvedModel, TaskSpec, resolve_model

log = get_logger("services.llm")


def _est_tokens(text: str) -> int:
    """粗估 token 数（中文 1 字≈1 token，英文 4 字符≈1 token；取保守上界）。"""
    return max(1, int(len(text or "") * 0.8))


def _infer_caller() -> str:
    """从调用栈推断调用来源（模块.函数），用于日志的完整调用路径。"""
    import inspect
    frame = inspect.currentframe()
    skip = ("llm_service", "router", "client", "async_utils", "_infer_caller")
    for _ in range(8):
        if frame is None or frame.f_back is None:
            break
        frame = frame.f_back
        mod = frame.f_code.co_filename.replace("\\", "/").split("/")[-1].replace(".py", "")
        if mod not in skip:
            return f"{mod}.{frame.f_code.co_name}"
    return ""


class PromptNotFoundError(KeyError):
    pass


def _salvage_json(raw: str) -> dict | None:
    """抢救被截断/含瑕疵的 JSON 输出：扫描其中所有完整的顶层对象。

    - 只有 1 个完整对象 → 直接返回它；
    - 多个完整对象（常见于 {\"points\": [{...}, {...}, ...被截断）→ 包装为 {"points": [...]}；
    - 一个都没有 → None（无法抢救）。
    """
    if not raw:
        return None
    import json as _json
    decoder = _json.JSONDecoder()
    found: list[tuple[int, int, dict]] = []  # (start, end, obj)
    for i, ch in enumerate(raw):
        if ch != "{":
            continue
        if any(s <= i < e for s, e, _ in found):
            continue  # 已被某个完整父对象覆盖
        try:
            obj, end = decoder.raw_decode(raw[i:])
        except ValueError:
            continue
        if isinstance(obj, dict):
            found.append((i, i + end, obj))
    if not found:
        return None
    first_brace = raw.index("{")
    if len(found) == 1 and found[0][0] == first_brace:
        # 唯一完整对象就是回复本身（从首个 { 开始）→ 原样返回
        return found[0][2]
    # 否则这些对象是某个更大（已截断）结构里的完整条目 → 按 points 列表上交，
    # 由各任务的解析/校验逻辑（如 handbook 的 _normalize_points）消化
    return {"points": [obj for _, _, obj in found]}


def _repair_truncated_json(raw: str) -> dict | None:
    """修复被 max_tokens 截断的 JSON：丢弃最后一个不完整的条目，补齐未闭合的
    字符串/数组/对象。截断处之前的完整内容几乎全部保住（比逐条抢救损失更小）。

    思路：先在「完整 JSON 值边界」截断（找最后一个完整的数组元素/对象成员），
    再按括号栈补闭合符。
    """
    if not raw or not raw.strip().startswith("{"):
        return None
    import json as _json

    def try_close(fragment: str):
        stack: list[str] = []
        in_str = False
        esc = False
        for ch in fragment:
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch in "{[":
                stack.append(ch)
            elif ch in "}]":
                if stack and ((stack[-1] == "{" and ch == "}") or (stack[-1] == "[" and ch == "]")):
                    stack.pop()
        if in_str:
            fragment += '"'
        for opener in reversed(stack):
            fragment += "}" if opener == "{" else "]"
        try:
            return _json.loads(fragment)
        except ValueError:
            return None

    # 从后往前找安全的截断点：完整元素边界（}, 或 } ] 等），逐个尝试补闭合
    text = raw.rstrip()
    cut_points = [i for i, ch in enumerate(text) if ch == "}"]
    for pos in reversed(cut_points[-12:] if len(cut_points) > 12 else cut_points):
        result = try_close(text[: pos + 1])
        if isinstance(result, dict):
            return result
    # 整个文本直接补闭合（最后字符串被截断的情形）
    result = try_close(text)
    return result if isinstance(result, dict) else None


def _recover_json(raw: str | None) -> dict | None:
    """JSON 失败恢复链：截断修复（保结构）→ 逐条抢救（保完整子对象）。"""
    if not raw:
        return None
    return _repair_truncated_json(raw) or _salvage_json(raw)


class LLMService:
    """所有 LLM 任务的统一入口：generate_stream / generate_structured。"""

    def __init__(self, db: Database, cfg: AppConfig):
        self.db = db
        self.cfg = cfg
        self.client = OpenAICompatClient(cfg)

    # ---------------- 模板 ----------------
    def load_prompt(self, key: str, project_id: int | None = None, **kwargs) -> str:
        """读取生效模板（project scope 优先）并渲染。"""
        with self.db.session_ctx() as session:
            tpl = PromptTemplateRepository(session).get_effective(key, project_id)
        if tpl is None:
            raise PromptNotFoundError(f"提示词模板不存在: {key}")
        return render(tpl.template, **kwargs)

    # ---------------- 路由 ----------------
    def resolve(self, task: TaskSpec, tier_override: str | None = None) -> ResolvedModel:
        return resolve_model(self.cfg, task, override=tier_override)

    def resolve_plain(self, project_id: int | None, module: str,
                      prompt_key: str = "chat") -> ResolvedModel:
        """通用解析（对话等无模板任务用）。"""
        return resolve_model(self.cfg, TaskSpec(module=module, prompt_key=prompt_key))

    async def stream_raw(self, messages: list[dict], resolved: ResolvedModel, *,
                         project_id: int | None = None, module: str = "chat",
                         prompt_key: str = "chat", caller: str = "") -> AsyncIterator[str]:
        """直接以给定消息与解析结果流式生成（对话等自定义消息场景），并留痕到日志。"""
        caller = caller or _infer_caller()
        full_input = "\n\n".join(f"[{m.get('role')}]\n{m.get('content', '')}" for m in messages)
        input_summary = full_input[:300]
        in_tokens = sum(_est_tokens(m.get("content", "")) for m in messages)
        t0 = time.perf_counter()
        out_text = ""
        try:
            async for chunk in self.client.chat_stream(messages, resolved):
                out_text += chunk
                yield chunk
        except LLMError:
            self._log(project_id, module, resolved, prompt_key, status="failed",
                      error=traceback.format_exc()[-500:], caller=caller,
                      duration_ms=int((time.perf_counter() - t0) * 1000),
                      input_summary=input_summary, full_input=full_input)
            raise
        self._log(project_id, module, resolved, prompt_key,
                  in_tokens=in_tokens, out_tokens=_est_tokens(out_text),
                  duration_ms=int((time.perf_counter() - t0) * 1000),
                  input_summary=input_summary, output_summary=out_text[:300],
                  full_input=full_input, full_output=out_text,
                  caller=caller)

    # ---------------- 生成 ----------------
    async def generate_stream(
        self,
        *,
        project_id: int | None,
        module: str,
        prompt_key: str,
        system_prompt: str,
        user_prompt: str,
        tier_override: str | None = None,
        caller: str = "",
    ) -> AsyncIterator[str]:
        """流式生成（正文/长建议）。"""
        resolved = self.resolve(TaskSpec(module=module, prompt_key=prompt_key), tier_override)
        caller = caller or _infer_caller()
        messages = render_messages(system_prompt, user_prompt)
        in_tokens = _est_tokens(system_prompt) + _est_tokens(user_prompt)
        input_summary = (system_prompt + "\n" + user_prompt)[:300]
        full_input = system_prompt + "\n\n" + user_prompt  # 文件日志记全量
        t0 = time.perf_counter()
        out_text = ""
        try:
            async for text in self.client.chat_stream(messages, resolved):
                out_text += text
                yield text
            self._log(project_id, module, resolved, prompt_key,
                      in_tokens=in_tokens, out_tokens=_est_tokens(out_text),
                      duration_ms=int((time.perf_counter() - t0) * 1000),
                      input_summary=input_summary, output_summary=out_text[:300],
                      full_input=full_input, full_output=out_text,
                      caller=caller)
        except LLMError:
            if self._can_downgrade(resolved):
                provider = self.cfg.providers[resolved.provider]
                downgraded = ResolvedModel(
                    provider=resolved.provider, model=provider.models.get(TIER_STANDARD),
                    tier=TIER_STANDARD, temperature=resolved.temperature,
                    max_tokens=resolved.max_tokens, thinking=resolved.thinking,
                )
                try:
                    async for text in self.client.chat_stream(messages, downgraded):
                        out_text += text
                        yield text
                    self._log(project_id, module, downgraded, prompt_key,
                              in_tokens=in_tokens, out_tokens=_est_tokens(out_text),
                              duration_ms=int((time.perf_counter() - t0) * 1000),
                              input_summary=input_summary, output_summary=out_text[:300],
                              full_input=full_input, full_output=out_text,
                              caller=caller)
                except LLMError:
                    self._log(project_id, module, downgraded, prompt_key, status="failed",
                              error=traceback.format_exc()[-500:], caller=caller)
                    raise
            else:
                self._log(project_id, module, resolved, prompt_key, status="failed",
                          error=traceback.format_exc()[-500:], caller=caller,
                          duration_ms=int((time.perf_counter() - t0) * 1000),
                          input_summary=input_summary)
                raise

    async def generate_structured(
        self,
        *,
        project_id: int | None,
        module: str,
        prompt_key: str,
        system_prompt: str,
        user_prompt: str,
        tier_override: str | None = None,
        retries: int = 1,
        caller: str = "",
    ) -> dict:
        """结构化 JSON 生成（规划/提取类）；失败重试 + 自动降级。"""
        resolved = self.resolve(TaskSpec(module=module, prompt_key=prompt_key), tier_override)
        caller = caller or _infer_caller()
        messages = render_messages(system_prompt, user_prompt)
        input_summary = (system_prompt + "\n" + user_prompt)[:300]
        t0 = time.perf_counter()

        last_error: Exception | None = None
        candidates = [resolved]
        if self._can_downgrade(resolved):
            provider = self.cfg.providers[resolved.provider]
            candidates.append(ResolvedModel(
                provider=resolved.provider, model=provider.models.get(TIER_STANDARD),
                tier=TIER_STANDARD, temperature=resolved.temperature,
                max_tokens=resolved.max_tokens, thinking=resolved.thinking,
            ))

        import json as _json
        full_input = system_prompt + "\n\n" + user_prompt  # 文件日志记全量
        for attempt in range(retries + 1):
            for cand in candidates:
                try:
                    data = await self.client.chat_structured(messages, cand)
                    self._log(
                        project_id, module, cand, prompt_key,
                        in_tokens=_est_tokens(system_prompt) + _est_tokens(user_prompt),
                        out_tokens=_est_tokens(_json.dumps(data, ensure_ascii=False)),
                        duration_ms=int((time.perf_counter() - t0) * 1000),
                        input_summary=input_summary,
                        output_summary=_json.dumps(data, ensure_ascii=False)[:300],
                        full_input=full_input,
                        full_output=_json.dumps(data, ensure_ascii=False),
                        caller=caller,
                    )
                    return data
                except LLMError as exc:
                    last_error = exc
                    log.warning("structured 失败（%s/%s 第 %d 次）: %s",
                                cand.provider, cand.model, attempt + 1, exc)
                    # 截断/瑕疵 JSON 立即修复（多为 max_tokens 截断，重试只会在同一位置再截一次）
                    raw = getattr(exc, "raw_content", None)
                    recovered = _recover_json(raw)
                    if recovered:
                        log.warning("structured JSON 已修复/抢救（%s，%s/%s）",
                                    prompt_key, cand.provider, cand.model)
                        self._log(project_id, module, cand, prompt_key, status="ok",
                                  in_tokens=_est_tokens(system_prompt) + _est_tokens(user_prompt),
                                  out_tokens=_est_tokens(raw or ""),
                                  duration_ms=int((time.perf_counter() - t0) * 1000),
                                  input_summary=input_summary,
                                  output_summary="[recovered] " + (raw or "")[:280],
                                  full_input=full_input,
                                  full_output="[原始输出（已修复）]\n" + (raw or ""),
                                  caller=caller)
                        return recovered
        # 全部重试/降级均失败（空响应等无原始输出可修的情况）
        self._log(project_id, module, resolved, prompt_key, status="failed",
                  error=traceback.format_exc()[-500:] if last_error else "unknown",
                  duration_ms=int((time.perf_counter() - t0) * 1000),
                  input_summary=input_summary, caller=caller)
        raise LLMError(f"结构化生成失败: {last_error}")

    # ---------------- 内部 ----------------
    def _can_downgrade(self, resolved: ResolvedModel) -> bool:
        return self.cfg.routing.auto_downgrade and resolved.tier == TIER_PRO

    def _log(self, project_id, module, resolved: ResolvedModel, prompt_key: str,
             status: str = "ok", error: str | None = None,
             in_tokens: int = 0, out_tokens: int = 0,
             duration_ms: int = 0, input_summary: str = "",
             output_summary: str = "", caller: str = "",
             full_input: str | None = None, full_output: str | None = None) -> None:
        # ① 写独立文件日志（按天，完整输入/输出，供复盘）
        try:
            from app.llm.call_log import write_llm_call
            write_llm_call(self.cfg.data_dir, {
                "module": module, "caller": caller, "provider": resolved.provider,
                "tier": resolved.tier, "model": resolved.model, "prompt_key": prompt_key,
                "input_summary": full_input if full_input is not None else input_summary,
                "output_summary": full_output if full_output is not None else output_summary,
                "in_tokens": in_tokens, "out_tokens": out_tokens,
                "duration_ms": duration_ms, "status": status, "error": error,
            })
        except Exception:
            pass
        # ② 写数据库（轻量，供 Token 用量统计）
        try:
            with self.db.session_ctx() as session:
                GenerationLogRepository(session).add_log(
                    project_id=project_id, module=module, caller=caller,
                    provider=resolved.provider, tier=resolved.tier, model=resolved.model,
                    prompt_key=prompt_key, in_tokens=in_tokens, out_tokens=out_tokens,
                    duration_ms=duration_ms, input_summary=input_summary,
                    output_summary=output_summary, status=status, error=error,
                )
                session.commit()
        except Exception:  # 日志失败不影响主流程
            log.exception("写入 generation_logs 失败")

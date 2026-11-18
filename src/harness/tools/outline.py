"""大纲写工具：plan_arcs（master_plan 段一，只产卷骨架 + planned_volume_count）。

P0 backlog「worker-owns-retry」首次落地：扩额重试住在工具 handler 内——
空响应（kind=empty/starved）或 JSON 解析失败 → 扩额重试一次（空内容直接顶 cap），
仍失败 → 返回失败观测（truncated/bad_json/empty 分类，如实报因不 raise；熔断是 loop 的事）。
Task 8 起阶梯单点化：内联扩额阶梯删除，换 base.complete_structured（同一公式：
empty 顶 cap / starved 与解析失败 ×2 封顶，恰好重试一次）——观测文本/重试次数/
扩额公式不变，返回值包 ToolResult（usage 入账，P1 预算缺口随此修复）。

异步约定：注册进 ToolRegistry 的 handler 是同步签名（registry 契约），内部 ``asyncio.run``
驱动异步核心；已处于事件循环中的调用方（Task 12 主循环）请直接 await ``plan_arcs(...)``。
client 生命周期：complete() 非流式路径无逐调用资源；注入的共享 client 由调用方持有，
停机时 ``await client.aclose()``（P0 backlog 关闭契约的落地，见 llm/client.py）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re

from harness.checklists import stage_checklist_block
from harness.config import Settings
from harness.llm.client import LLMError, OpenAICompatClient
from harness.llm.policy import resolve_max_tokens
from harness.prompts.loader import PromptLoader, insert_before_output_section
from harness.run.scope import read_brief_scope
from harness.skills import skill_injection
from harness.store.frontmatter import FileFormatError
from harness.store.project import ProjectStore
from harness.tools.base import ToolResult, _run_sync, complete_structured
from harness.tools.registry import ToolRegistry

__all__ = ["plan_arcs", "register_outline_tools"]

log = logging.getLogger("tools.outline")

_JSON_START_RE = re.compile(r"[\[{]")
_DECODER = json.JSONDecoder()


def _extract_json_value(text: str):
    """取第一个可解析的 JSON 值（对象/数组）：先全文（去 markdown 栅栏），再逐 '['/'{' 尝试 raw_decode。

    raw_decode 原生处理嵌套括号，不自带平衡扫描（复用 Task 7 同款思路的免维护形态）。
    """
    s = text.strip()
    if s.startswith("```"):
        lines = s.splitlines()
        lines = lines[1:] if len(lines) > 1 else []
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    try:
        return _DECODER.raw_decode(s)[0]
    except ValueError:
        pass
    for m in _JSON_START_RE.finditer(s):
        try:
            return _DECODER.raw_decode(s, m.start())[0]
        except ValueError:
            continue
    return None


def _normalize_arcs(arcs: list) -> tuple[list[dict], int]:
    """卷条目规整：vol 必须是 int（bool 排除——True==1 会顶掉真第 1 卷，store 合并索引同源风险）。

    返回 (有效条目, 丢弃数)。其余键（title/goal/summary/conflict/resolution/capacity_hint）原样透传——
    目标键名 goal 与 Task 6 digest 对齐，测试经 digest 输出钉死。
    """
    good: list[dict] = []
    dropped = 0
    for arc in arcs:
        if not isinstance(arc, dict):
            dropped += 1
            continue
        vol = arc.get("vol")
        if isinstance(vol, bool) or not isinstance(vol, int):
            dropped += 1
            continue
        good.append(arc)
    return good, dropped


def _parse_master_plan(text: str):
    """解析段一输出 → (arcs 列表, planned_volume_count)；结构不合法返回 None（bad_json）。"""
    value = _extract_json_value(text)
    if not isinstance(value, dict):
        return None
    arcs = value.get("arcs")
    if not isinstance(arcs, list):
        return None
    planned = value.get("planned_volume_count")
    if isinstance(planned, bool):
        planned = None
    try:
        planned = int(planned)  # 统一规整：None/字符串数字都收敛
    except (TypeError, ValueError):
        planned = len(arcs)
    return arcs, planned


async def plan_arcs(store: ProjectStore, llm: OpenAICompatClient, prompts: PromptLoader,
                    settings: Settings, args: dict) -> ToolResult:
    """渲染 task.auto_master_plan（pro 档）→ 解析 → upsert_arcs；失败返回分类观测，不 raise。

    阶梯在 base.complete_structured 单点执行（与内联版同公式同次数）；失败观测的
    detail（finish_reason/原始消息）经 _LastErrorProbe 从被吞的 LLMError 里取回——
    观测文本与迁移前逐字节一致。
    """
    instruction = str(args.get("instruction") or "").strip()
    if not instruction:
        return ToolResult(text="plan_arcs 需要 args.instruction（规划诉求），当前为空",
                          usage=None)
    provider = settings.providers.get(settings.default_provider)
    if provider is None:
        known = "、".join(sorted(settings.providers)) or "（空）"
        return ToolResult(
            text=f"plan_arcs 失败（配置）：default_provider={settings.default_provider!r}"
                 f" 未配置（providers：{known}）", usage=None)
    # 生成侧清单块（P9）：把「要满足什么」先摆到规划之前——与验收侧同一份清单文件。
    # 块插在【输出格式】**之前**（输出契约是模型最后读的指令，不能被顶到中间）；
    # 块在 skill 之前：清单是**标准**（硬约束），技能是**方法论**（怎么写），标准在前更醒目。
    checklist = await asyncio.to_thread(stage_checklist_block, store.root, "outline")
    # 范围与前提（P11）：brief 里的目标/前提/限制/正文范围 → 追加到 instruction **之后**
    # （agent 的 args.instruction 会整体替换 instruction 变量 → 范围是约束，不能被覆盖）。
    # **段一不收「限卷」行**（2026-11-15 独立审核 M1）：本模板写着「⑤ 严格遵守规划诉求里的
    # **每一条限制**」→ 若把「只规划前 N 卷」塞进段一的 instruction，模型会照办 →
    # **全书卷骨架被压成 N 卷**（实测）。段一产**全书骨架**；brief 的 plan_arcs 是
    # 「规划哪几卷做详细情节」，不是「骨架只写 N 卷」（P11 裁定 1）。
    scope = await asyncio.to_thread(read_brief_scope, store)
    scope_block = scope.block(include_volume_limit=False)
    if scope_block:
        instruction = f"{instruction}\n\n{scope_block}" if instruction else scope_block
    rendered = prompts.render("task.auto_master_plan", instruction=instruction)
    prompt = insert_before_output_section(rendered, checklist) \
        + skill_injection(store.root, "task.auto_master_plan")
    fail_reason = ""

    def _extract(text: str):
        # 与内联版同口径：JSON 解析不出 / 无有效卷条目都视为「本尝试未产出」，进扩额重试
        nonlocal fail_reason
        parsed = _parse_master_plan(text)
        if parsed is None:
            fail_reason = "响应中解析不出 {arcs:[...], planned_volume_count} JSON"
            return None
        arcs, planned = parsed
        good, dropped = _normalize_arcs(arcs)
        if not good:
            fail_reason = f"arcs 无有效卷条目（{dropped} 条均缺合法 vol）"
            return None
        return good, planned, dropped

    probe = _LastErrorProbe(llm)
    value, usage, reason = await complete_structured(
        probe, model=provider.pro, temperature=0.5,
        max_tokens=resolve_max_tokens(len(prompt)), prompt=prompt, extract=_extract)
    if value is None:
        if reason == "bad_json":
            return ToolResult(
                text=f"plan_arcs 失败（bad_json）：两次调用均未能产出卷骨架——{fail_reason}",
                usage=usage)
        exc = probe.last
        detail = (f"finish_reason={exc.finish_reason}" if exc is not None and exc.finish_reason
                  else ((str(exc) or exc.kind) if exc is not None else reason))
        return ToolResult(text=f"plan_arcs 失败（{reason}）：{detail}", usage=usage)
    good, planned, dropped = value
    try:
        store.upsert_arcs(good, planned_volume_count=planned)
    except FileFormatError as exc:
        # 坏 arcs.md（用户手改坏了）→ **分类观测**，不抛栈（独立审核 M7）
        log.warning("plan_arcs 落盘失败（arcs.md 解析失败）: %s", exc)
        return ToolResult(
            text=f"plan_arcs 失败（文件损坏）：卷骨架文件解析失败（{exc.reason}）"
                 "——请手工修正 outline/arcs.md 后重试（本次 LLM 产出未落盘）",
            usage=usage)
    titles = "、".join(f"第{a['vol']}卷《{str(a.get('title') or '无题').strip() or '无题'}》"
                       for a in good)
    note = f"；丢弃 {dropped} 条无效卷条目（vol 非 int）" if dropped else ""
    return ToolResult(text=f"已落 {len(good)} 卷骨架（planned_volume_count={planned}）："
                           f"{titles}{note}", usage=usage)


class _LastErrorProbe:
    """包一层 ``llm.complete``：complete_structured 内部把 LLMError 吞成分类串（reason），
    失败观测要拼的 detail（finish_reason / 原始消息）得从原异常取回——此处记最后一条。

    只服务观测文本等价；重试/扩额行为不受影响（异常原样重抛）。
    """

    def __init__(self, llm: OpenAICompatClient) -> None:
        self._llm = llm
        self.last: LLMError | None = None

    async def complete(self, messages, *, model, temperature=0.7, max_tokens=8192):
        try:
            return await self._llm.complete(messages, model=model, temperature=temperature,
                                            max_tokens=max_tokens)
        except LLMError as exc:
            self.last = exc
            raise


def register_outline_tools(reg: ToolRegistry, *, store: ProjectStore, llm: OpenAICompatClient,
                           prompts: PromptLoader, settings: Settings) -> None:
    """注册 plan_arcs（read_only=False）：规划写盘工具，白名单外/并行批均由 registry 拒绝。"""
    def handler(store_: ProjectStore, args: dict) -> str:
        return _run_sync(plan_arcs(store_, llm, prompts, settings, args)).text

    reg.register("plan_arcs", handler, read_only=False)

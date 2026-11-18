"""web_search 工具（P5 Task 10，设计 §2.5 联网核查行）：联网核查事实，写 research.md。

**联网纪律（硬约束）**：

- ``settings.web_verify is False`` → **明确回绝**（零 LLM 零 HTTP）——开关关着就不许联网；
- ``settings.search is None``（未配 ``[search]`` 段）→ **明确回绝**（零 LLM 零 HTTP）；
- 回绝文案明说「不得凭记忆编造」——模型拿不到结果时，正确做法是不写，不是编。

**成功路径**：``parse_queries`` 拆多条目标 → ``WebSearchService.verify_deep``（检索 +
LLM 核实 + research.md 落账）→ 观测逐条「· 核查「query」\\n 结论」+ 末尾警示
「未能核实宁可不写」。

工具是**写工具**（会写 research.md、出网、调 LLM）——进 ``_WRITE_FLOW`` 而非
``_READONLY``（不进并行批）。
"""
from __future__ import annotations

import logging
import re

from harness.config import Settings
from harness.llm.client import OpenAICompatClient
from harness.prompts.loader import PromptLoader
from harness.store.project import ProjectStore
from harness.tools.base import ToolResult, _run_sync
from harness.tools.registry import ToolRegistry
from harness.websearch import WebSearchService

__all__ = ["parse_queries", "register_web_tools", "web_search"]

log = logging.getLogger("tools.web")

#: 目标分隔符（seed web_service.py:96-102：; ； 换行）
_QUERY_SEP_RE = re.compile(r"[;；\n]")

#: 单条目标的长度上限（seed 同值）
_QUERY_MAX_CHARS = 60

#: 一次最多核查几条（seed 同值）
_QUERY_LIMIT = 3

_REFUSAL_TAIL = "——不得凭记忆编造。"


def parse_queries(raw: str, limit: int = _QUERY_LIMIT) -> list[str]:
    """``;``/``；``/换行 分隔的多条核查目标；每条 ≤60 字；最多 limit 条。"""
    out: list[str] = []
    for part in _QUERY_SEP_RE.split(raw or ""):
        text = part.strip()
        if not text:
            continue
        out.append(text[:_QUERY_MAX_CHARS])
        if len(out) >= limit:
            break
    return out


async def web_search(store: ProjectStore, llm: OpenAICompatClient,
                     prompts: PromptLoader, settings: Settings,
                     args: dict) -> ToolResult:
    """联网核查：args = {query, rounds?}；未启用/未配置 → 明确回绝。"""
    raw = str(args.get("query") or "")
    queries = parse_queries(raw)
    if not queries:
        return ToolResult(
            text="web_search 需要 args.query（核查目标；多条用 ; 分隔，每条 ≤60 字），当前为空",
            usage=None)
    if settings is not None and settings.web_verify is False:
        return ToolResult(
            text="web_search 拒绝执行：联网核查已关闭（web_verify=false）" + _REFUSAL_TAIL,
            usage=None)
    if settings is None or settings.search is None:
        return ToolResult(
            text="web_search 拒绝执行：未配置 [search] 段（联网核查不可用）" + _REFUSAL_TAIL,
            usage=None)

    rounds = args.get("rounds")
    rounds = rounds if isinstance(rounds, int) and not isinstance(rounds, bool) and rounds >= 0 else None
    service = WebSearchService(settings.search, llm, prompts)
    try:
        entries = await service.verify_deep(root=store.root, queries=queries, rounds=rounds)
    finally:
        await service.aclose()

    # **第一行是状态行**（2026-11-15 独立审核 D4）：loop 的分类器扫状态行里的瞬时词，
    # 而「核查目标」是**用户原话**、结论是**模型原话**——都可能含「连接」这类常用词
    # → 一次**成功**的核查被误判成瞬时失败（该步不算进展、白烧 noop 槽）。
    lines: list[str] = [f"联网核查完成：核实 {len(entries)} 条（结论见 .harness/research.md）"]
    for entry in entries:
        lines.append(f"· 核查「{entry.query}」")
        lines.append(f"  {entry.conclusion}")
    lines.append("（未能核实的条目宁可不写——不要凭记忆补全。）")
    return ToolResult(text="\n".join(lines), usage=service.usage_total)


def register_web_tools(reg: ToolRegistry, *, store: ProjectStore,
                        llm: OpenAICompatClient, prompts: PromptLoader,
                        settings: Settings) -> None:
    """注册 web_search（read_only=False）：写 research.md、出网、调 LLM。"""

    def handler(store_: ProjectStore, args: dict) -> str:
        return _run_sync(web_search(store_, llm, prompts, settings, args)).text

    reg.register("web_search", handler, read_only=False)

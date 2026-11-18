"""extract_memory 工具（设计附录 A.5 明列、P5 Task 4b 落地）：对指定章重做记忆提取。

**为什么是它**：设计 §1.6「每次改写之后重提取」在本仓有两条可达通道——写作链三处 hook
（P5 Task 4 已收口到 ``reextract_chapter``）与本工具。P3 已把 ``edit_by_command`` 收窄为
world.md / 角色卡 / 卷骨架 / 情节四类（``_classify_path`` 对 ``chapters/`` 直接拒绝），
故「正文被人工改过后刷新记忆」没有别的入口——本工具是那条通道。

**幂等**：``reextract_chapter`` 以**盘上正文**为准重提取，写盘顺序为先清后建
（purge → write_chapter_memory → apply_state_updates），同章重跑无重复条目。

**观测契约**（分类契约：禁瞬时词；失败原因过 reason_label 映射）：
- 成功 → ``extract_memory：第N章——记忆提取完成``；
- 提取失败 → ``extract_memory：第N章——记忆提取失败：<原因>``——刻意**不含**「失败（」
  （提取失败是不阻断的增强步骤失败，与 write_plot 路径的 notes 口径一致；
  ``loop._classify`` 见「失败（」判 failed 会计熔断）；
- 参数非法 → ``extract_memory 失败（参数）：…``（响亮失败，同 write_plot 的参数错误口径）。

工具本身不新增事件 kind：结果由 agent loop 的既有 ``tool_result`` 事件落。
"""
from __future__ import annotations

from harness.config import Settings
from harness.llm.client import OpenAICompatClient
from harness.memory.extract import reextract_chapter, reextract_failed, reextract_reason
from harness.memory.store import MemoryStore
from harness.prompts.loader import PromptLoader
from harness.store.project import ProjectStore
from harness.tools.base import ToolResult, _run_sync
from harness.tools.registry import ToolRegistry

__all__ = ["extract_memory", "register_memory_tools"]


async def extract_memory(store: ProjectStore, llm: OpenAICompatClient,
                          prompts: PromptLoader, settings: Settings,
                          args: dict) -> ToolResult:
    """对指定章重做记忆提取（正文被人工改过后用；幂等先清后建）。

    ``args`` = ``{"chapter_id": int}``（章号，1 起——设计附录 A.5 的签名）。
    行为：调统一提取入口 ``reextract_chapter``（以盘上正文为准），把其 ToolResult
    转成本工具的观测文案；usage 透传。
    """
    raw = args.get("chapter_id")
    seq = raw if isinstance(raw, int) and not isinstance(raw, bool) else None
    if seq is None or seq < 1:
        return ToolResult(
            text=(f"extract_memory 失败（参数）：args.chapter_id 缺参或格式非法"
                  f"（须为 1 起的整数），当前为 {raw!r}"),
            usage=None)
    mem = MemoryStore(store.root)
    result = await reextract_chapter(store=store, mem=mem, llm=llm, prompts=prompts,
                                     settings=settings, seq=seq)
    if reextract_failed(result.text):
        reason = reextract_reason(result.text)
        return ToolResult(text=f"extract_memory：第{seq}章——记忆提取失败：{reason}",
                          usage=result.usage)
    return ToolResult(text=f"extract_memory：第{seq}章——记忆提取完成", usage=result.usage)


def register_memory_tools(reg: ToolRegistry, *, store: ProjectStore,
                           llm: OpenAICompatClient, prompts: PromptLoader,
                           settings: Settings) -> None:
    """注册 extract_memory（read_only=False）：写 memory/ 与 state.md，不进只读集。"""

    def handler(store_: ProjectStore, args: dict) -> str:
        return _run_sync(extract_memory(store_, llm, prompts, settings, args)).text

    reg.register("extract_memory", handler, read_only=False)

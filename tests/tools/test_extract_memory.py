"""extract_memory 工具（P5 Task 4b）：人工改正文后重提取该章记忆。

四条口径：
- **幂等先清后建**：重提取后旧记忆条目消失、新条目在（不叠加）；
- **观测分层**：成功「extract_memory：第N章——记忆提取完成」；提取失败「——记忆提取失败：<原因>」
  （刻意不含「失败（」——提取失败是不阻断的增强步骤失败，与 write_plot 的 notes 口径一致，
  loop._classify 见「失败（」才判 failed 计熔断）；
- **参数错误响亮失败**：「extract_memory 失败（参数）：args.chapter_id 缺参或格式非法
  （须为 1 起的整数），当前为 <repr>」；
- **接线四处**：白名单（TOOLS_ALL + _WRITE_FLOW，非 _READONLY）/ agent loop 的 await 分支
  （按名字路由，不落 sync registry 路径）/ REPL 注册 / tools 包导出。

LLM 全 mock（FakeLLM 顺序脚本，形态遵 P3 纪律：complete() 返回 JSON body）。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from harness.agent.loop import AgentLoop
from harness.config import ProviderConfig, Settings
from harness.llm.client import CompletionUsage
from harness.memory.store import MemoryStore
from harness.projects import create_project
from harness.prompts.loader import PromptLoader
from harness.run.approval import ApprovalUI, Choice
from harness.run.constants import WORK_MEMORY_REPLAY
from harness.run.events import EventLog
from harness.run.memory import WorkMemory
from harness.run.state import RunState
from harness.run.whitelist import allowed_for_run
from harness.store.project import ChapterDoc, ProjectStore
from harness.tools import readonly_registry, register_memory_tools
from harness.tools.memory import extract_memory


class _Resp:
    def __init__(self, text, usage=None, tool_calls=None):
        self.text = text
        self.usage = usage
        self.tool_calls = tool_calls


class FakeLLM:
    """顺序吐脚本的假 llm：complete 消费脚本项；dict 含 tool_calls = 原生 tool_call 协议。"""

    def __init__(self, script, *, usage=None):
        self._script = list(script)
        self.calls = []
        self.closed = False
        self._usage = usage
        self.provider = ProviderConfig(base_url="https://api.test", api_key="k",
                                       lite="m", standard="standard-model", pro="pro-model")

    async def complete(self, messages, *, model, temperature=0.7, max_tokens=8192, thinking=False):
        self.calls.append({"model": model, "temperature": temperature,
                           "prompt": messages[0]["content"]})
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, dict) and "tool_calls" in item:
            return _Resp(item.get("content", ""), self._usage, tool_calls=item["tool_calls"])
        return _Resp(item, self._usage)

    async def aclose(self):
        self.closed = True


def make_settings():
    return Settings(
        library_root=Path("x"),
        providers={"test": ProviderConfig(base_url="https://api.test", api_key="k",
                                          lite="m", standard="standard-model", pro="pro-model")},
        default_provider="test",
        default_tier="standard",
        web_verify=True,
    )


def extract_payload() -> str:
    """五键提取 fixture（角色名不入名册 → 角色经历被过滤，事件条目仍落盘）。"""
    return json.dumps({
        "events": [{"title": "夜探山寨", "summary": "甲潜入山寨", "location": "山寨",
                    "time_marker": "深夜", "characters": ["甲"],
                    "new_info": ["布防图"], "foreshadow": "后山密道"}],
        "character_events": [], "info_updates": [], "promise_updates": [],
        "state_updates": {"characters": [], "world": []},
    }, ensure_ascii=False)


def chapter(seq=1, title="题一", body="甲拔刀，乙点火。夜风吹过山寨。"):
    return ChapterDoc(seq=seq, meta={"seq": seq, "title": title}, body=body)


@pytest.fixture
def store(tmp_path):
    info = create_project(tmp_path, "重提取书")
    return ProjectStore(info.path)


def prompts_for(root: Path) -> PromptLoader:
    return PromptLoader(project_root=root, home_dir=root)


# ---------------------------------------------------------------- 工具本体

async def test_extract_memory_tool_refreshes_chapter_memory(store):
    """幂等先清后建：先手写一条旧记忆 → 调工具 → 旧条目消失、新条目在（不叠加）。"""
    store.write_chapter(chapter())
    mem = MemoryStore(store.root)
    mem.write_chapter_memory(1, "题一", {
        "events": [{"title": "旧事件", "summary": "旧", "location": "旧地",
                    "time_marker": "", "characters": [], "new_info": [], "foreshadow": ""}],
        "character_events": [], "info_updates": [], "promise_updates": [],
    })
    assert "旧事件" in (mem.root / "memory" / "0001.md").read_text(encoding="utf-8")

    llm = FakeLLM([extract_payload()])
    result = await extract_memory(store, llm, prompts_for(store.root), make_settings(),
                                  {"chapter_id": 1})

    assert result.text == "extract_memory：第1章——记忆提取完成"
    text = (mem.root / "memory" / "0001.md").read_text(encoding="utf-8")
    assert "旧事件" not in text
    assert text.count("夜探山寨") == 1


async def test_extract_memory_tool_observation_and_usage(store):
    """观测文本逐字 + usage 透传（ToolResult.usage == mock 响应 usage）。"""
    store.write_chapter(chapter())
    usage = CompletionUsage(prompt_tokens=7, completion_tokens=3)
    llm = FakeLLM([extract_payload()], usage=usage)
    result = await extract_memory(store, llm, prompts_for(store.root), make_settings(),
                                  {"chapter_id": 1})
    assert result.text == "extract_memory：第1章——记忆提取完成"
    assert result.usage == usage


async def test_extract_memory_tool_missing_chapter(store):
    """章不存在 → 观测含「记忆提取失败：章不存在」，不 raise、零 LLM 调用。"""
    llm = FakeLLM([])
    result = await extract_memory(store, llm, prompts_for(store.root), make_settings(),
                                  {"chapter_id": 7})
    assert result.text == "extract_memory：第7章——记忆提取失败：章不存在"
    assert "失败（" not in result.text      # 提取失败不判 failed（不熔断）
    assert llm.calls == []


async def test_extract_memory_tool_bad_args(store):
    """参数非法（缺参 / 非整数 / <1）→ 响亮失败文案，零 LLM 调用。"""
    llm = FakeLLM([])
    prompts, settings = prompts_for(store.root), make_settings()

    missing = await extract_memory(store, llm, prompts, settings, {})
    assert missing.text == ("extract_memory 失败（参数）：args.chapter_id 缺参或格式非法"
                             "（须为 1 起的整数），当前为 None")
    assert missing.usage is None

    bad_type = await extract_memory(store, llm, prompts, settings, {"chapter_id": "x"})
    assert bad_type.text == ("extract_memory 失败（参数）：args.chapter_id 缺参或格式非法"
                              "（须为 1 起的整数），当前为 'x'")

    too_small = await extract_memory(store, llm, prompts, settings, {"chapter_id": 0})
    assert too_small.text == ("extract_memory 失败（参数）：args.chapter_id 缺参或格式非法"
                               "（须为 1 起的整数），当前为 0")
    assert llm.calls == []


# ---------------------------------------------------------------- 四处接线

def test_extract_memory_tool_whitelisted():
    """白名单：TOOLS_ALL + _WRITE_FLOW（含之、非 _READONLY）→ chapters/draft/world 均可用。"""
    for deliverables in (["chapters"], ["draft"], ["world"], ["outline"]):
        assert "extract_memory" in allowed_for_run(deliverables)
    assert "extract_memory" not in allowed_for_run(["discuss"])


def test_extract_memory_tool_registered_in_registry(store):
    """注册：名字进 registry.names（【可用工具】渲染得出）、read_only=False。"""
    from harness.tools.registry import ToolRegistry
    reg = ToolRegistry(store)
    register_memory_tools(reg, store=store, llm=FakeLLM([]), prompts=prompts_for(store.root),
                           settings=make_settings())
    assert "extract_memory" in reg.names
    assert reg.is_read_only("extract_memory") is False


def test_extract_memory_tool_routed_by_loop(store):
    """agent loop：action=extract_memory 走 await 分支（不落 sync registry 路径）。"""
    store.write_chapter(chapter())
    llm = FakeLLM([_extract_call(), extract_payload(), _finish()])
    settings = make_settings()
    prompts = prompts_for(store.root)
    registry = readonly_registry(store)
    register_memory_tools(registry, store=store, llm=llm, prompts=prompts, settings=settings)
    events = EventLog(store.root)
    state = RunState.new("测试 brief：重提取", ["draft"])
    memory = WorkMemory.rebuild(events.tail(WORK_MEMORY_REPLAY * 2))
    loop = AgentLoop(store=store, state=state, events=events, memory=memory,
                     registry=registry, ui=ApprovalUI(ask=lambda q, o, m=False, note=None:
                                                      Choice(indices=[0])),
                     llm=llm, prompts=prompts, deliverables=["draft"], settings=settings)

    result = asyncio.run(loop.run())

    assert result.status == "done"
    obs = [e["payload"]["observation"] for e in events.tail(100)
           if e["kind"] == "tool_result" and e["payload"]["tool"] == "extract_memory"]
    assert len(obs) == 1
    assert obs[0] == "extract_memory：第1章——记忆提取完成"   # await 分支产出（sync 路径产不出此文案）
    # 签名兜底表有它（registry 闭包无 docstring → loop._FALLBACK_SIG 补签名）
    assert "extract_memory(args: {chapter_id})" in llm.calls[0]["prompt"]


def _extract_call(**args):
    return {"tool_calls": [{"name": "extract_memory",
                            "arguments": json.dumps(args or {"chapter_id": 1})}]}


def _finish(summary="完成"):
    return json.dumps({"thought": "目标达成", "say": "收工",
                       "action": "finish", "args": {"summary": summary}}, ensure_ascii=False)

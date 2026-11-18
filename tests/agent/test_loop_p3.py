"""P3 写作工具接进 loop：write_plot 走 await 分支（emit 绑 EventLog.append）/
分类契约双方向（total 失败→failed 计熔断 / 部分成功→ok 清账 / 瞬时根因→确定性标签
不上屏→failed 计熔断）/ P1 工具迁 ToolResult 后 usage 同账入账。

LLM 全 mock（FakeLLM 顺序脚本，参照 test_loop_p2 手法 + _stream_pieces 支撑 write_plot
逐拍流式）；write_plot 白名单经 deliverables=draft 获得（draft = TOOLS_ALL−三规划工具）。
"""
import asyncio
import json
from pathlib import Path

import pytest

from harness.agent.loop import AgentLoop
from harness.config import ProviderConfig, Settings
from harness.llm.client import CompletionUsage, LLMError
from harness.projects import create_project
from harness.prompts.loader import PromptLoader
from harness.run.approval import ApprovalUI, Choice
from harness.run.constants import WORK_MEMORY_REPLAY
from harness.run.events import EventLog
from harness.run.memory import WorkMemory
from harness.run.state import RunState
from harness.store.project import ChapterDoc, ProjectStore
from harness.tools import (
    readonly_registry,
    register_modify_tools,
    register_outline_tools,
    register_writing_tools,
)


class _FakeCompletion:
    def __init__(self, text, usage=None, tool_calls=None):
        self.text = text
        self.usage = usage
        self.tool_calls = tool_calls


class FakeLLM:
    """顺序吐脚本的假 llm：complete 消费决策/非流式载荷；_stream_pieces 消费 {"chunks"}。

    Exception 项在两个入口都直接 raise（starved=LLMError(kind="starved") 模拟截断）。
    """

    def __init__(self, script, *, usage=None):
        self._script = list(script)
        self.calls = []
        self.stream_calls = []
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
        assert not (isinstance(item, dict) and "chunks" in item), "流式载荷被 complete 消费"
        if isinstance(item, dict) and "tool_calls" in item:
            return _FakeCompletion(text=item.get("content", ""),
                                   tool_calls=item["tool_calls"], usage=self._usage)
        return _FakeCompletion(text=item, usage=self._usage)

    async def _stream_pieces(self, messages, *, model, temperature, max_tokens,
                             thinking, include_usage, state):
        self.stream_calls.append({"model": model, "temperature": temperature})
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        assert isinstance(item, dict) and "chunks" in item, f"非流式项被 stream 消费: {item!r}"
        state.usage = self._usage
        for piece in item["chunks"]:
            yield piece

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


SELECT_OK = json.dumps({"world_titles": [], "character_names": [],
                        "keywords": [], "need_web": []}, ensure_ascii=False)
REVIEW_PASS = json.dumps({"pass": True, "issues": []}, ensure_ascii=False)
EXTRACT_OK = json.dumps({"events": [], "character_events": [], "info_updates": [],
                         "promise_updates": [],
                         "state_updates": {"characters": [], "world": []}},
                        ensure_ascii=False)
STARVED = LLMError("empty_stream", transient=True, finish_reason="length", kind="starved")


def draft_chunks(n_chars=1400):
    """非严格周期的长正文（逐段变编号）：repeat_loop_cut 要求 ≥24 字严格循环单元，
    变编号拼接不会命中熔断（与 tests/tools/test_writing.py 同款手法）。"""
    chunks, total, k = [], 0, 1
    while total < n_chars:
        c = f"第{k}段他沿着山道继续往前走，天色一点点暗下来，远处有鸟叫。"
        chunks.append(c)
        total += len(c)
        k += 1
    return {"chunks": chunks}


@pytest.fixture
def store(tmp_path):
    info = create_project(tmp_path, "写作书")
    s = ProjectStore(info.path)
    s.upsert_arcs([{"vol": 1, "title": "卷甲", "goal": "g", "summary": "s",
                    "conflict": "c", "resolution": "r"}])
    s.upsert_plots(1, [{"seq": 1, "title": "开端", "summary": "概要", "conflict": "冲突",
                        "resolution": "收束", "status": "planned"}])
    return s


def _chapter(seq, title, beat_events, plot_id="1-1"):
    beats = [{"pov": "甲", "location": "山道", "event": e, "weight": 1} for e in beat_events]
    meta = {"seq": seq, "title": title, "objective": f"目标{seq}", "status": "planned",
            "beats": beats, "covers": f"覆盖{seq}", "time_span": "夜",
            "start_at": "山道｜夜", "end_at": "山道｜夜",
            "plot_id": plot_id, "target_words": 3000}
    return ChapterDoc(seq=seq, meta=meta, body="")


def make_loop(store, script, *, deliverables=("draft",), usage=None):
    llm = FakeLLM(script, usage=usage)
    settings = make_settings()
    prompts = PromptLoader(home_dir=store.root)
    ui = ApprovalUI(ask=lambda q, o, m=False, note=None: Choice(indices=[0]))
    registry = readonly_registry(store)
    register_outline_tools(registry, store=store, llm=llm, prompts=prompts, settings=settings)
    register_modify_tools(registry, store=store, llm=llm, prompts=prompts, ui=ui)
    register_writing_tools(registry, store=store, llm=llm, prompts=prompts, settings=settings)
    events = EventLog(store.root)
    state = RunState.new("测试 brief：写作链", list(deliverables))
    memory = WorkMemory.rebuild(events.tail(WORK_MEMORY_REPLAY * 2))
    loop = AgentLoop(store=store, state=state, events=events, memory=memory,
                     registry=registry, ui=ui, llm=llm, prompts=prompts,
                     deliverables=list(deliverables), settings=settings)
    return loop, llm, events, state


def _write_plot_call(**args):
    return {"tool_calls": [{"name": "write_plot", "arguments": json.dumps(args or {"plot_id": "1-1"})}]}


def _finish(summary="完成"):
    return json.dumps({"thought": "目标达成", "say": "收工",
                       "action": "finish", "args": {"summary": summary}}, ensure_ascii=False)


def _tool_results(events, tool):
    return [e for e in events.tail(100)
            if e["kind"] == "tool_result" and e["payload"]["tool"] == tool]


# ---------------------------------------------------------------- 分类契约（双方向）

def test_write_plot_total_failure_classified_failed(store):
    """total 失败（坏 plot_id，参数类）：观测「write_plot 失败（参数）…」→ _classify 判
    failed 计熔断——连续两步失败 → 第二步末熔断，第三步被冷却门拒（观测「冷却中」）。"""
    call = _write_plot_call(plot_id="9-9")
    loop, llm, events, _state = make_loop(store, [call, call, call, _finish()])
    result = asyncio.run(loop.run())
    assert result.status == "done"
    obs = _tool_results(events, "write_plot")
    assert len(obs) == 3
    assert all("失败（参数）" in e["payload"]["observation"] for e in obs[:2])
    assert all(not e["payload"]["observation"].startswith("FAILED") for e in obs)
    assert "冷却中" in obs[2]["payload"]["observation"]     # failed 计熔断：第三步冷却门拒
    assert len(llm.calls) == 4                    # 4 次步决策：坏 plot_id 不进工具 LLM


def test_write_plot_partial_ok(store):
    """部分成功（第1章落库、第2章 starved 两轮 → 未成功）：观测只有「未成功」措辞、
    无「失败（」→ _classify 判 ok：effective_steps 计入该步，成功章 status=done。"""
    store.write_chapter(_chapter(1, "题一", ["一之起"]))
    store.write_chapter(_chapter(2, "题二", ["二之起"]))
    call = _write_plot_call()
    # 消耗序：决策 → ch1 select → ch1 beat 流 → ch1 review → ch1 记忆提取 → ch2 select
    # → ch2 beat 流×2（starved 扩额重试恰好两轮）→ finish 决策
    script = [call, SELECT_OK, draft_chunks(), REVIEW_PASS, EXTRACT_OK,
              SELECT_OK, STARVED, STARVED, _finish()]
    loop, llm, events, state = make_loop(store, script)
    result = asyncio.run(loop.run())
    assert result.status == "done"
    obs = _tool_results(events, "write_plot")
    assert len(obs) == 1
    text = obs[0]["payload"]["observation"]
    assert "写作完成" in text and "未成功" in text
    assert "失败（" not in text                        # 部分成功不匹配失败词表
    assert not text.startswith("FAILED")
    assert store.read_chapter(1).meta["status"] == "done"    # 成功章落库
    assert store.read_chapter(2).meta["status"] == "planned"  # 失败章不动
    assert state.stats["effective_steps"] == 2        # write_plot 部分成功步 + finish 步
    assert len(llm.stream_calls) == 3                 # ch1 一拍 + ch2 starved×2


def test_write_plot_transient(store):
    """瞬时根因（starved 截断）经 _REASON_LABELS 确定性映射后观测零瞬时词：
    不判 transient（无 FAILED-TRANSIENT 前缀）——按 failed 计熔断（双方向的另一侧）。
    若误把 masked-transient 当 transient，第二步就不会熔断（冷却观测消失）。"""
    store.write_chapter(_chapter(1, "题一", ["一之起"]))
    call = _write_plot_call()
    script = [call, SELECT_OK, STARVED, STARVED, call, SELECT_OK, STARVED, STARVED, call, _finish()]
    loop, llm, events, _state = make_loop(store, script)
    result = asyncio.run(loop.run())
    assert result.status == "done"
    obs = _tool_results(events, "write_plot")
    assert len(obs) == 3
    failed = [e for e in obs if "失败（无产出）" in e["payload"]["observation"]]
    assert len(failed) == 2                           # 前两步 total 失败
    assert all(not e["payload"]["observation"].startswith("FAILED-TRANSIENT")
               for e in failed)                       # 瞬时词未上屏 → 不判 transient
    for marker in ("truncated", "starved", "timeout", "超时", "截断", "连接"):
        assert all(marker not in e["payload"]["observation"] for e in failed)
    assert store.read_chapter(1).meta["status"] == "planned"   # 失败章不落盘
    assert "冷却中" in obs[2]["payload"]["observation"]        # 计熔断：第三步被冷却门拒
    assert len(llm.calls) == 6                        # 4 决策 + 2 select（第三步冷却门拒零消耗）
    assert len(llm.stream_calls) == 4                 # starved 扩额重试 ×2 步 ×2 轮


# ---------------------------------------------------------------- P1 迁移：usage 同账入账

def test_plan_arcs_tool_usage_counted_same_account(store):
    """plan_arcs 迁 ToolResult 后工具 usage 走 P2 同账接缝：决策与工具 usage 同账累加
    （3 次 complete 各 150 → token_used=450；若 usage 未入账则只有 300）。"""
    arcs = json.dumps({"planned_volume_count": 1,
                       "arcs": [{"vol": 1, "title": "卷甲", "goal": "踏上修行路",
                                 "summary": "s", "conflict": "c", "resolution": "r"}]},
                      ensure_ascii=False)
    call = {"tool_calls": [{"name": "plan_arcs",
                            "arguments": json.dumps({"instruction": "一卷骨架"})}]}
    usage = CompletionUsage(prompt_tokens=100, completion_tokens=50)
    loop, llm, _events, state = make_loop(store, [call, arcs, _finish()],
                                          deliverables=("outline",), usage=usage)
    result = asyncio.run(loop.run())
    assert result.status == "done"
    assert len(llm.calls) == 3                        # 决策 + plan_arcs 内部一轮 + finish
    assert state.stats["token_used"] == 3 * 150       # 工具 usage 入账（P1 预算缺口修复）
    assert [a["vol"] for a in store.read_arcs()] == [1]

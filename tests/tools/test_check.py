"""check_plots / global_check 工具（P6 Task 4，设计 §1.5 验收链）：验收只报不改。

口径钉死：
- **写工具**：落 issues（``source="check"``）+ 调 LLM → ``registry.is_read_only`` 为
  False、进白名单 ``_WRITE_FLOW``（**不进**只读集）；
- **观测逐字**：工具返回的文本 = ``run_check`` 的返回值（不加工、不改写）；
- **editor_mode 只认 "on"**：其余值当没给（仍走总编辑模板）；
- **usage 冒泡**：``run_check`` 只返回观测文本（T3 钉死的形状）→ usage 走**可选计量**
  ``meter`` 逐层下传（``harness.tools.base.UsageMeter``，同 writing/revise、memory/extract），
  工具侧记 ``meter.total``；LLM 响应未带 usage → 记 None（不臆造用量）；
- **plot_ids 本期不参与抽样**：非空时观测如实写「已限定情节：…」（并说明抽样仍在章层）；
- **chapter_ids 透传**：``args.chapter_ids`` → ``run_check(chapters=...)``。

编排层（组装/抽样/quote 回验/落库）由 tests/check/test_runner.py 覆盖；本文件钉**工具层契约**。
"""
from __future__ import annotations

import json

import pytest

from harness.config import ProviderConfig
from harness.projects import create_project
from harness.prompts.loader import PromptLoader
from harness.run.issues import IssuesStore
from harness.store.project import ChapterDoc, ProjectStore

BODY1 = "第一夜，风雪压塌了山门。少年握紧了手里的木剑。"
BODY2 = "第二夜，火光映红了半边天。老者把剑递了过去。"
QUOTE2 = "老者把剑递了过去"


class _Resp:
    def __init__(self, text, usage=None):
        self.text = text
        self.usage = usage


class FakeLLM:
    """complete 顺序脚本：_Resp 项为响应；Exception 项直接 raise。"""

    def __init__(self, script):
        self._script = list(script)
        self.calls: list[dict] = []
        self.provider = ProviderConfig(
            base_url="https://api.test", api_key="k",
            lite="m-lite", standard="m-std", pro="m-pro")

    async def complete(self, messages, *, model, temperature, max_tokens):
        self.calls.append({"model": model, "temperature": temperature,
                           "max_tokens": max_tokens,
                           "prompt": messages[-1]["content"]})
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _payload(**kw) -> str:
    return json.dumps(kw, ensure_ascii=False)


def _issue_item(chapter="第2章", quote=QUOTE2, type_="character",
                issue="老者动机不明", fix="补一句回忆") -> dict:
    return {"chapter": chapter, "type": type_, "dimension": "人物",
            "quote": quote, "issue": issue, "fix": fix}


@pytest.fixture
def project(tmp_path):
    """一个有两章定稿正文的项目（info, store）。"""
    info = create_project(tmp_path, "验收工具书")
    store = ProjectStore(info.path)
    for seq, title, body in ((1, "风雪夜", BODY1), (2, "递剑", BODY2)):
        store.write_chapter(ChapterDoc(
            seq=seq, meta={"seq": seq, "title": title, "status": "done",
                           "objective": "推进主线"}, body=body))
    return info, store


def _prompts(info) -> PromptLoader:
    return PromptLoader(home_dir=info.path)


# ------------------------------------------------------------------ 落 issues

async def test_check_plots_tool_lands_issues(project):
    """情节级：带真 quote 的意见落 issues（source="check"），观测含 T3 的首行口径。"""
    from harness.tools.check import check_plots

    info, store = project
    llm = FakeLLM([_Resp(_payload(verdict="fix", score=7, issues=[_issue_item()]))])
    result = await check_plots(store, llm, _prompts(info), None, {})

    pending = IssuesStore(info.path).list("pending")
    assert len(pending) == 1
    assert pending[0].source == "check"
    assert pending[0].chapter == 2
    assert pending[0].quote == QUOTE2
    assert result.text.splitlines()[0] == \
        "验收完成：verdict=fix、意见 1 条（丢弃无证据 0 条），已落 issues"


async def test_global_check_tool_lands_issues(project):
    """全书级：带真 quote 的意见落 issues（source="check"），观测含 T3 的首行口径。"""
    from harness.tools.check import global_check

    info, store = project
    llm = FakeLLM([_Resp(_payload(verdict="fix", score=7, issues=[_issue_item()]))])
    result = await global_check(store, llm, _prompts(info), None, {})

    pending = IssuesStore(info.path).list("pending")
    assert len(pending) == 1
    assert pending[0].source == "check"
    assert pending[0].chapter == 2
    assert result.text.splitlines()[0] == \
        "验收完成：verdict=fix、意见 1 条（丢弃无证据 0 条），已落 issues"


async def test_fabricated_quote_dropped_by_tool_path(project):
    """编造 quote 的意见不落库（quote 回验在编排层已完成，工具只透传观测）。"""
    from harness.tools.check import global_check

    info, store = project
    llm = FakeLLM([_Resp(_payload(verdict="pass",
                                   issues=[_issue_item(quote="正文里根本没有的句子")]))])
    result = await global_check(store, llm, _prompts(info), None, {})

    assert IssuesStore(info.path).list("pending") == []
    assert "丢弃无证据 1 条" in result.text.splitlines()[0]


# ------------------------------------------------------------------ 观测零瞬时词

def _markers() -> tuple:
    """分类契约瞬时词表全集（观测禁含任何成员，含中文同义词）。"""
    from harness.agent.loop import _EXTRA_TRANSIENT_MARKERS
    from harness.run import constants
    return constants.TRANSIENT_MARKERS + _EXTRA_TRANSIENT_MARKERS


async def test_check_observation_has_no_transient_markers(project):
    """新工具的观测**零瞬时词**（分类契约：TRANSIENT_MARKERS 的任何成员都不得上屏）。

    照 tests/tools/test_writing.py 与 tests/memory/test_extract.py 的既有同款写法。
    """
    from harness.tools.check import check_plots, global_check

    info, store = project
    llm = FakeLLM([_Resp(_payload(verdict="fix", issues=[_issue_item()]))])
    result = await global_check(store, llm, _prompts(info), None, {})
    for marker in _markers():
        assert marker not in result.text, f"观测泄漏瞬时词 {marker!r}：{result.text!r}"

    llm = FakeLLM([_Resp(_payload(verdict="fix", issues=[_issue_item()]))])
    result = await check_plots(store, llm, _prompts(info), None, {})
    for marker in _markers():
        assert marker not in result.text, f"观测泄漏瞬时词 {marker!r}：{result.text!r}"


async def test_failure_observation_contains_no_transient_markers(project):
    """失败观测同样零瞬时词（标签走确定性词表）+ 可被 loop 判成 failed。"""
    from harness.llm.client import LLMError
    from harness.tools.check import global_check

    info, store = project
    llm = FakeLLM([LLMError("调用失败", transient=True, kind="timeout")])
    result = await global_check(store, llm, _prompts(info), None, {})

    for marker in _markers():
        assert marker not in result.text, f"观测泄漏瞬时词 {marker!r}：{result.text!r}"

    from harness.agent.loop import AgentLoop
    assert AgentLoop._classify(result.text) == "failed"


# ------------------------------------------------------------------ 提示词路由

async def test_editor_mode_uses_editor_prompt(project):
    """editor_mode="on" → 渲染 task.auto_editor_review（签约责编模板）。"""
    from harness.tools.check import global_check

    info, store = project
    llm = FakeLLM([_Resp(_payload(verdict="pass", issues=[]))])
    await global_check(store, llm, _prompts(info), None, {"editor_mode": "on"})

    prompt = llm.calls[0]["prompt"]
    assert "签约责编" in prompt
    assert "总编辑" not in prompt


async def test_editor_mode_only_accepts_on(project):
    """editor_mode 只认 "on"：其余值（"off"/"yes"/缺省）当没给（总编辑模板）。"""
    from harness.tools.check import global_check

    info, store = project
    for value in ("off", "yes", "1", "", None):
        llm = FakeLLM([_Resp(_payload(verdict="pass", issues=[]))])
        await global_check(store, llm, _prompts(info), None, {"editor_mode": value})
        prompt = llm.calls[0]["prompt"]
        assert "总编辑" in prompt
        assert "签约责编" not in prompt


async def test_check_plots_uses_plot_prompt(project):
    """check_plots → 渲染 task.auto_plot_check（检查官模板）。"""
    from harness.tools.check import check_plots

    info, store = project
    llm = FakeLLM([_Resp(_payload(verdict="pass", issues=[]))])
    await check_plots(store, llm, _prompts(info), None, {})

    prompt = llm.calls[0]["prompt"]
    assert "你是自主创作流程的检查官" in prompt
    assert "签约责编" not in prompt


async def test_chapter_ids_forwarded_to_run_check(project):
    """args.chapter_ids → 只从这些章抽样（提示词里只有指定章的正文）。"""
    from harness.tools.check import global_check

    info, store = project
    llm = FakeLLM([_Resp(_payload(verdict="pass", issues=[]))])
    await global_check(store, llm, _prompts(info), None, {"chapter_ids": [2]})

    prompt = llm.calls[0]["prompt"]
    assert "【第2章" in prompt
    assert "【第1章" not in prompt


# ------------------------------------------------------------------ 观测逐字 / usage

async def test_observation_is_run_check_verbatim(project, monkeypatch):
    """观测 = run_check 的返回值逐字（不加工、不改写）；参数照 T3 签名透传。"""
    from harness.tools.check import global_check

    info, store = project
    seen: dict = {}

    async def fake_run_check(**kwargs):
        seen.update(kwargs)
        return "验收完成：verdict=pass、零意见（丢弃无证据 0 条）"

    monkeypatch.setattr("harness.tools.check.run_check", fake_run_check)
    result = await global_check(store, None, _prompts(info), None, {})

    assert result.text == "验收完成：verdict=pass、零意见（丢弃无证据 0 条）"
    assert seen["kind"] == "global"
    assert seen["chapters"] is None
    assert seen["editor_mode"] is False


async def test_plot_ids_note_is_honest(project, monkeypatch):
    """plot_ids 非空 → 观测如实写「已限定情节：N 条」（并说明抽样仍在章层）。

    **只报条数、不回显模型给的字符串**——plot_ids 是模型写的自由文本，回显进观测
    会撞上 agent loop 的瞬时词扫描（模型给 plot_ids=["连接"] 就能让整条**成功**的
    验收被判 transient）。
    """
    from harness.tools.check import check_plots

    info, store = project
    body = "验收完成：verdict=pass、零意见（丢弃无证据 0 条）"

    async def fake_run_check(**kwargs):
        return body

    monkeypatch.setattr("harness.tools.check.run_check", fake_run_check)
    result = await check_plots(store, None, _prompts(info), None, {"plot_ids": ["1", "2"]})

    assert result.text.startswith(body)                 # run_check 的返回逐字保留
    assert "已限定情节：2 条" in result.text
    assert "、2" not in result.text                     # 不回显模型给的字符串
    assert "章层" in result.text                         # 如实说明抽样未按情节收窄


async def test_usage_is_none_when_llm_response_has_no_usage(project):
    """LLM 响应未带 usage → 工具侧记 None（usage 不臆造）。"""
    from harness.tools.check import check_plots, global_check

    info, store = project
    llm = FakeLLM([_Resp(_payload(verdict="pass", issues=[]))])
    result = await global_check(store, llm, _prompts(info), None, {})
    assert result.usage is None

    llm = FakeLLM([_Resp(_payload(verdict="pass", issues=[]))])
    result = await check_plots(store, llm, _prompts(info), None, {})
    assert result.usage is None


# ------------------------------------------------------------------ usage 进预算账（P6 T4b）

async def test_tool_usage_bubbles_to_tool_result(project):
    """mock LLM 带 usage → 工具的 ToolResult.usage 等于该 usage（冒泡到 loop 的预算账）。"""
    from harness.llm.client import CompletionUsage
    from harness.tools.check import check_plots, global_check

    info, store = project
    usage = CompletionUsage(prompt_tokens=1234, completion_tokens=567)

    llm = FakeLLM([_Resp(_payload(verdict="pass", issues=[]), usage)])
    result = await global_check(store, llm, _prompts(info), None, {})
    assert result.usage == usage

    llm = FakeLLM([_Resp(_payload(verdict="pass", issues=[]), usage)])
    result = await check_plots(store, llm, _prompts(info), None, {})
    assert result.usage == usage


async def test_run_check_meter_records_usage(project):
    """run_check 带 meter → 本次 LLM 调用的 usage 入账（返回形状仍是 str，不变）。"""
    from harness.check.runner import run_check
    from harness.llm.client import CompletionUsage
    from harness.memory.store import MemoryStore
    from harness.tools.base import UsageMeter

    info, store = project
    usage = CompletionUsage(prompt_tokens=11, completion_tokens=22)
    llm = FakeLLM([_Resp(_payload(verdict="pass", issues=[]), usage)])
    meter = UsageMeter()
    text = await run_check(store=store, mem=MemoryStore(store.root), llm=llm,
                           prompts=_prompts(info), settings=None, kind="global",
                           emit=lambda kind, payload: None, meter=meter)

    assert isinstance(text, str)
    assert meter.total == usage


async def test_run_check_without_meter_returns_str(project):
    """run_check 不传 meter → 返回仍是 str、行为与从前逐字相同（不记账、不改返回）。"""
    from harness.check.runner import run_check
    from harness.memory.store import MemoryStore

    info, store = project
    llm = FakeLLM([_Resp(_payload(verdict="pass", issues=[]))])
    text = await run_check(store=store, mem=MemoryStore(store.root), llm=llm,
                           prompts=_prompts(info), settings=None, kind="global",
                           emit=lambda kind, payload: None)

    assert isinstance(text, str)
    assert text.splitlines()[0] == "验收完成：verdict=pass、零意见（丢弃无证据 0 条）"


async def test_run_check_meter_records_usage_on_failure(project):
    """LLM 失败（烧了 token）→ usage 照样入账（失败调用也烧 token）。"""
    from harness.check.runner import run_check
    from harness.llm.client import CompletionUsage, LLMError
    from harness.memory.store import MemoryStore
    from harness.tools.base import UsageMeter

    info, store = project
    llm = FakeLLM([LLMError("调用失败", transient=True, kind="timeout")])
    meter = UsageMeter()
    text = await run_check(store=store, mem=MemoryStore(store.root), llm=llm,
                           prompts=_prompts(info), settings=None, kind="global",
                           emit=lambda kind, payload: None, meter=meter)

    assert text.splitlines()[0] == "验收失败（调用失败）"
    assert meter.total is None      # 失败响应未带 usage → 不臆造
    assert CompletionUsage is not None


# ------------------------------------------------------------------ check_report 事件（P6 T4b）

def _check_reports(root) -> list[dict]:
    """events.log 里的 check_report 事件（按文件落盘的顺序）。"""
    from harness.run.events import EventLog

    return [e for e in EventLog(root).tail(500) if e["kind"] == "check_report"]


async def test_tool_path_emits_check_report(project):
    """agent 路径（经工具）→ events.log 里有 check_report 事件（且只一条）。"""
    from harness.tools.check import global_check

    info, store = project
    llm = FakeLLM([_Resp(_payload(verdict="fix", score=7, issues=[_issue_item()]))])
    await global_check(store, llm, _prompts(info), None, {})

    reports = _check_reports(store.root)
    assert len(reports) == 1
    assert reports[0]["payload"] == {"verdict": "fix", "score": 7, "issues": 1,
                                     "discarded": 0, "kind": "global",
                                     "checklist": 0, "checklist_failed": 0}


async def test_tool_path_emits_check_report_with_explicit_emit(project):
    """工具显式给 emit → 事件落在该 emit 上（默认 emit 只是兜底）。"""
    from harness.tools.check import global_check

    info, store = project
    events: list[tuple[str, dict]] = []
    llm = FakeLLM([_Resp(_payload(verdict="pass", score=9, issues=[]))])
    await global_check(store, llm, _prompts(info), None, {},
                       emit=lambda kind, payload: events.append((kind, payload)))

    assert events == [("check_report", {"verdict": "pass", "score": 9, "issues": 0,
                                        "discarded": 0, "kind": "global",
                 "checklist": 0, "checklist_failed": 0})]


# ------------------------------------------------------------------ 注册（写工具）

def _registered(store, info):
    """注册两个验收工具进一张空 registry（工具层惯例：register_*_tools）。"""
    from harness.tools.check import register_check_tools
    from harness.tools.registry import ToolRegistry

    reg = ToolRegistry(store)
    register_check_tools(reg, store=store, llm=None, prompts=_prompts(info), settings=None)
    return reg


def test_check_tools_registered(project):
    """注册：两工具进 registry.names、read_only=False（落 issues + 调 LLM = 写工具）。"""
    info, store = project
    reg = _registered(store, info)

    assert "check_plots" in reg.names
    assert "global_check" in reg.names
    assert reg.is_read_only("check_plots") is False
    assert reg.is_read_only("global_check") is False


def test_registry_handlers_have_no_docstring(project):
    """注册 handler 无 docstring → 签名行由 agent/loop.py 的 _FALLBACK_SIG 提供。"""
    info, store = project
    reg = _registered(store, info)

    assert reg.doc_of("check_plots") == ""
    assert reg.doc_of("global_check") == ""


def test_check_tools_signature_rendered_in_tool_list(project):
    """【可用工具】渲染得出两工具的兜底签名（名字进 registry.names 才渲染得出）。"""
    from harness.agent.loop import AgentLoop
    from harness.run.approval import ApprovalUI, Choice
    from harness.run.events import EventLog
    from harness.run.memory import WorkMemory
    from harness.run.state import RunState
    from harness.run.whitelist import allowed_for_run

    info, store = project
    reg = _registered(store, info)
    loop = AgentLoop(store=store, state=RunState.new("测试 brief", ["outline"]),
                     events=EventLog(store.root), memory=WorkMemory(), registry=reg,
                     ui=ApprovalUI(ask=lambda q, o, m=False, note=None: Choice(indices=[0])),
                     llm=None, prompts=_prompts(info), deliverables=["outline"], settings=None)

    lines = loop._tool_signatures(allowed_for_run(["outline"]))

    assert any("check_plots(args: {plot_ids?})" in line for line in lines)
    assert any("global_check(args: {chapter_ids?, editor_mode?})" in line for line in lines)


def test_loop_routes_check_tools_through_async_branch(project):
    """agent loop：两工具走 await 分支（不是 registry 同步路径）。

    同步路径在事件循环内会 RuntimeError（base._run_sync 响亮报错）——观测里出现
    「验收完成」即证明走的是异步分支（名字同时在异步工具名元组与 _async_tool 分支里）。
    """
    import asyncio

    from harness.agent.loop import AgentLoop
    from harness.run.approval import ApprovalUI, Choice
    from harness.run.events import EventLog
    from harness.run.memory import WorkMemory
    from harness.run.state import RunState
    from harness.tools.check import register_check_tools
    from harness.tools.registry import ToolRegistry

    info, store = project

    class _FakeCompletion:
        def __init__(self, text, tool_calls=None):
            self.text = text
            self.usage = None
            self.tool_calls = tool_calls

    class _LoopLLM:
        """先吐一条 global_check 的步决策 tool_call，再吐验收 JSON，最后 finish。"""

        def __init__(self):
            self.provider = ProviderConfig(base_url="https://api.test", api_key="k",
                                           lite="m", standard="m", pro="m")
            self._script = [
                _FakeCompletion("", tool_calls=[{"name": "global_check",
                                                 "arguments": "{}"}]),
                _FakeCompletion(_payload(verdict="fix", issues=[_issue_item()])),
                _FakeCompletion(json.dumps({"thought": "验收完毕", "action": "finish",
                                            "args": {"summary": "done"}},
                                           ensure_ascii=False)),
            ]

        async def complete(self, messages, *, model, temperature, max_tokens):
            return self._script.pop(0)

        async def aclose(self):
            return None

    llm = _LoopLLM()
    registry = ToolRegistry(store)
    register_check_tools(registry, store=store, llm=llm, prompts=_prompts(info), settings=None)
    loop = AgentLoop(store=store, state=RunState.new("测试 brief", ["outline"]),
                     events=EventLog(store.root), memory=WorkMemory(), registry=registry,
                     ui=ApprovalUI(ask=lambda q, o, m=False, note=None: Choice(indices=[0])),
                     llm=llm, prompts=_prompts(info), deliverables=["outline"], settings=None)

    state = asyncio.run(loop.run())

    assert state.status == "done"
    obs = [e for e in loop.events.tail(100)
           if e["kind"] == "tool_result" and e["payload"]["tool"] == "global_check"]
    assert len(obs) == 1
    assert "验收完成" in obs[0]["payload"]["observation"]
    pending = IssuesStore(info.path).list("pending")
    assert len(pending) == 1 and pending[0].source == "check"


def test_agent_path_emits_check_report_and_counts_usage(project):
    """agent loop 经工具调验收 → check_report 落进 events.log，且用量进 token_used（预算账）。

    这条钉的是 P6 T4b 的两个缺口在**真实 agent 路径**上的效果：
    事件只落一条（不重复）、验收的 token 进预算账（不再被丢掉）。
    """
    import asyncio

    from harness.agent.loop import AgentLoop
    from harness.llm.client import CompletionUsage
    from harness.run.approval import ApprovalUI, Choice
    from harness.run.events import EventLog
    from harness.run.memory import WorkMemory
    from harness.run.state import RunState
    from harness.tools.check import register_check_tools
    from harness.tools.registry import ToolRegistry

    info, store = project

    class _FakeCompletion:
        def __init__(self, text, usage=None, tool_calls=None):
            self.text = text
            self.usage = usage
            self.tool_calls = tool_calls

    class _LoopLLM:
        """先吐一条 global_check 的步决策 tool_call，再吐验收 JSON，最后 finish。"""

        def __init__(self):
            self.provider = ProviderConfig(base_url="https://api.test", api_key="k",
                                           lite="m", standard="m", pro="m")
            self._script = [
                _FakeCompletion("", usage=CompletionUsage(1, 1),
                                tool_calls=[{"name": "global_check", "arguments": "{}"}]),
                _FakeCompletion(_payload(verdict="fix", issues=[_issue_item()]),
                                usage=CompletionUsage(100, 50)),
                _FakeCompletion(json.dumps({"thought": "验收完毕", "action": "finish",
                                            "args": {"summary": "done"}},
                                           ensure_ascii=False),
                                usage=CompletionUsage(2, 2)),
            ]

        async def complete(self, messages, *, model, temperature, max_tokens):
            return self._script.pop(0)

        async def aclose(self):
            return None

    llm = _LoopLLM()
    registry = ToolRegistry(store)
    register_check_tools(registry, store=store, llm=llm, prompts=_prompts(info), settings=None)
    loop = AgentLoop(store=store, state=RunState.new("测试 brief", ["outline"]),
                     events=EventLog(store.root), memory=WorkMemory(), registry=registry,
                     ui=ApprovalUI(ask=lambda q, o, m=False, note=None: Choice(indices=[0])),
                     llm=llm, prompts=_prompts(info), deliverables=["outline"], settings=None)

    state = asyncio.run(loop.run())

    assert state.status == "done"
    reports = [e for e in EventLog(store.root).tail(500) if e["kind"] == "check_report"]
    assert len(reports) == 1                       # agent 路径落一条（不重复）
    assert reports[0]["payload"]["kind"] == "global"
    # 决策 usage（1+1、2+2）+ 验收工具 usage（100+50）同账
    assert state.stats["token_used"] == 1 + 1 + 100 + 50 + 2 + 2


def test_repl_components_register_check_tools(tmp_path, monkeypatch):
    """RunSession.components 组装 registry 时注册两个验收工具（否则 agent 调不到）。"""
    from harness.config import ProviderConfig, Settings
    from harness.projects import create_project
    from harness.session import RunSession

    info = create_project(tmp_path, "接线书")
    store = ProjectStore(info.path)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    settings = Settings(
        library_root=tmp_path,
        providers={"test": ProviderConfig(base_url="https://api.test", api_key="k",
                                          lite="m", standard="m", pro="m")},
        default_provider="test", default_tier="standard", web_verify=False)

    registry = RunSession(info, store, settings).components().registry

    assert "check_plots" in registry.names
    assert "global_check" in registry.names
    assert registry.is_read_only("check_plots") is False
    assert registry.is_read_only("global_check") is False


# ------------------------------------------------------------------ 白名单

def test_whitelist_updated():
    """白名单：TOOLS_ALL 与 _WRITE_FLOW 都含两工具；discuss（只读集）不含。"""
    from harness.run.whitelist import _WRITE_FLOW, TOOLS_ALL, allowed_for_run

    for name in ("check_plots", "global_check"):
        assert name in TOOLS_ALL
        assert name in _WRITE_FLOW
        assert name in allowed_for_run(["draft"])
        assert name in allowed_for_run(["outline"])
        assert name not in allowed_for_run(["discuss"])     # 写工具不进只读集


async def test_observation_never_carries_model_plot_ids(project):
    """plot_ids 是模型写的自由文本 → **不得回显进观测**（与「观测不带模型原文」同一条纪律）。

    模型给 plot_ids=["连接"] → 回显后整条**成功**的验收被 loop 的瞬时词扫描判 transient。
    """
    from harness.agent.loop import AgentLoop
    from harness.tools.check import check_plots

    info, store = project
    llm = FakeLLM([_Resp(_payload(verdict="pass", score=8, issues=[]))])
    result = await check_plots(store, llm, _prompts(info), None,
                               {"plot_ids": ["连接", "2-3"]})

    assert "连接" not in result.text
    assert "已限定情节" in result.text
    assert AgentLoop._classify(result.text) == "ok"

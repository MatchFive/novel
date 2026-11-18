# 验收编排（P6 Task 3）
"""run_check 语义钉死：组装 → LLM → 解析 → quote 回验 → 落 issues → supersede → 观测。

- **变量集与模板逐字一致**：三档（plot 六变量 / global 九变量 / editor 八变量）
  组装出的键集必须与模板里的 ``{{var}}`` 集合**逐一相等**（多一个少一个都炸）；
- **quote 回验**：意见的 quote 必须逐字命中**它所指那一章**的正文——编造的 quote
  不落库、计入「丢弃无证据 K 条」（回验在 check/parser.py 那一层已完成）；
- **supersede**：同章重跑 → 该章 pending 仍 1 条、旧条转 resolved（不翻倍）；
- **只报不改**：验收只落 issues，不动任何正文；
- **失败语义**：LLM 调用失败 → 观测「验收失败（<确定性标签>）」、**零写盘**、不 raise
  （文案可被 loop 的分类器捕获 → 判 failed → 计熔断）；
- **清单驱动 verdict**：清单有 fail → 观测的 verdict 至少 fix；
- **组装走 to_thread**（P4 I-1 纪律）：读章/解析记忆那段同步重活不许跑在事件循环上；
- **观测不回显模型原文**：意见行只给「章号 + 类型」（撞 loop 的瞬时词扫描 → 成功被判
  transient）；
- **未兑现承诺口径唯一实现**在 harness.memory.inject（P5 审计同款，copy-propagation）。
"""
from __future__ import annotations

import json
import re

from harness.check import runner as runner_mod
from harness.config import ProviderConfig
from harness.llm.client import LLMError
from harness.memory.store import MemoryStore
from harness.projects import create_project
from harness.prompts.loader import PromptLoader
from harness.run.issues import IssuesStore
from harness.run.state import RunState
from harness.store.project import ChapterDoc, ProjectStore

_VAR_RE = re.compile(r"\{\{(\w+)\}\}")

BODY1 = "第一夜，风雪压塌了山门。少年握紧了手里的木剑。"
BODY2 = "第二夜，火光映红了半边天。老者把剑递了过去。"
BODY3 = "第三夜，江河东去。众人立在渡口，谁也没有开口。"
QUOTE2 = "老者把剑递了过去"
QUOTE3 = "众人立在渡口"

TITLES = {1: "风雪夜", 2: "递剑", 3: "渡口"}


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


def _loader(tmp_path) -> PromptLoader:
    home = tmp_path / "home"
    (home / ".harness" / "prompts").mkdir(parents=True, exist_ok=True)
    return PromptLoader(home_dir=home)


def _emitter():
    events: list[tuple[str, dict]] = []
    return events, (lambda kind, payload: events.append((kind, payload)))


def _body(seq: int) -> str:
    return f"第{seq}夜，风雪压塌了山门。少年握紧了手里的木剑{seq}。"


def _chapter(store: ProjectStore, seq: int, *, body: str | None = None,
             status: str = "done", objective: str = "推进主线") -> None:
    store.write_chapter(ChapterDoc(
        seq=seq, meta={"seq": seq, "title": TITLES.get(seq, f"第{seq}章"),
                       "status": status, "objective": objective},
        body=body if body is not None else _body(seq)))


def _setup(tmp_path, name: str = "验收书"):
    info = create_project(tmp_path, name)
    store = ProjectStore(info.path)
    mem = MemoryStore(info.path)
    return info, store, mem


def _payload(**kw) -> str:
    return json.dumps(kw, ensure_ascii=False)


def _issue_item(chapter="第2章", quote=QUOTE2, type_="character",
                issue="老者动机不明", fix="补一句回忆") -> dict:
    return {"chapter": chapter, "type": type_, "dimension": "人物",
            "quote": quote, "issue": issue, "fix": fix}


async def _run(llm, tmp_path, store, mem, *, kind="global", chapters=None,
               editor_mode=False):
    from harness.check.runner import run_check

    events, emit = _emitter()
    text = await run_check(store=store, mem=mem, llm=llm, prompts=_loader(tmp_path),
                           settings=None, kind=kind, chapters=chapters,
                           editor_mode=editor_mode, emit=emit)
    return text, events


# ------------------------------------------------------------------ 落 issues

async def test_check_lands_issues_with_quote(tmp_path):
    """意见落 issues：source="check"、quote 逐字、chapter 透传、title 前 40 字、detail 同 P5 口径。"""
    info, store, mem = _setup(tmp_path)
    _chapter(store, 1, body=BODY1)
    _chapter(store, 2, body=BODY2)
    llm = FakeLLM([_Resp(_payload(verdict="fix", score=7,
                                   issues=[_issue_item()]))])
    text, events = await _run(llm, tmp_path, store, mem)

    pending = IssuesStore(info.path).list("pending")
    assert len(pending) == 1
    issue = pending[0]
    assert issue.source == "check"
    assert issue.chapter == 2
    assert issue.quote == QUOTE2
    assert issue.title == "老者动机不明"[:40]
    assert issue.detail == "character：老者动机不明；建议：补一句回忆"
    assert text.splitlines()[0] == \
        "验收完成：verdict=fix、意见 1 条（丢弃无证据 0 条），已落 issues"
    assert ("check_report", {"verdict": "fix", "score": 7, "issues": 1,
                              "discarded": 0, "kind": "global",
                                  "checklist": 0, "checklist_failed": 0}) in events


async def test_fabricated_quote_not_landed(tmp_path):
    """编造 quote 的意见不落库；观测如实报「丢弃无证据 1 条」。"""
    info, store, mem = _setup(tmp_path)
    _chapter(store, 1, body=BODY1)
    _chapter(store, 2, body=BODY2)
    llm = FakeLLM([_Resp(_payload(verdict="pass",
                                   issues=[_issue_item(quote="正文里根本没有的句子")]))])
    text, _events = await _run(llm, tmp_path, store, mem)

    assert IssuesStore(info.path).list("pending") == []
    assert "丢弃无证据 1 条" in text.splitlines()[0]
    assert "已落 issues" not in text.splitlines()[0]


async def test_supersede_on_rerun(tmp_path):
    """同章重跑：该章 pending 仍 1 条、旧条转 resolved（重跑不翻倍）。"""
    info, store, mem = _setup(tmp_path)
    _chapter(store, 1, body=BODY1)
    _chapter(store, 2, body=BODY2)
    for _ in range(2):
        llm = FakeLLM([_Resp(_payload(verdict="fix", issues=[_issue_item()]))])
        await _run(llm, tmp_path, store, mem)

    issues = IssuesStore(info.path)
    pending = issues.list("pending")
    assert len(pending) == 1
    assert pending[0].chapter == 2
    resolved = issues.list("resolved")
    assert len(resolved) == 1 and resolved[0].chapter == 2
    assert pending[0].id != resolved[0].id


async def test_supersede_only_same_chapter(tmp_path):
    """supersede 只清同章：别章的 pending check issue 不受影响。"""
    info, store, mem = _setup(tmp_path)
    _chapter(store, 1, body=BODY1)
    _chapter(store, 2, body=BODY2)
    item1 = _issue_item(chapter="第1章", quote="少年握紧了手里的木剑", issue="第一章的问题")
    item2 = _issue_item(chapter="第2章", quote=QUOTE2, issue="第二章的问题")
    llm = FakeLLM([_Resp(_payload(verdict="fix", issues=[item1, item2]))])
    await _run(llm, tmp_path, store, mem)
    llm = FakeLLM([_Resp(_payload(verdict="fix", issues=[item1]))])
    await _run(llm, tmp_path, store, mem)

    issues = IssuesStore(info.path)
    pending = issues.list("pending")
    assert {i.chapter for i in pending} == {1, 2}      # 第 1 章旧条被清，第 2 章仍在
    assert [i.title for i in issues.list("resolved")] == ["第一章的问题"]


async def test_supersede_reports_stale_not_reraised(tmp_path):
    """上轮有、**本轮没再报**的旧意见 → 观测**如实报出条数**（可见，但不自动清）。

    为什么**不自动清**（独立审核 D2 的建议是「按 target 全清」——**不采纳**）：
    章级意见的模型输出是「**它能说出依据的问题**」（提示词原话：「说不出依据的问题不要报」），
    **不是逐章判定** → 「本轮没报该章」≠「该章已通过」→ 清掉会**静默丢掉真实发现**
    （违反「不静默消失」的纪律）。→ 改为**可见**：用户看得见有几条没再出现，可自行忽略。

    （与清单条目不同：清单条目有逐条 verdict（pass/fail/na），是**穷举判定** → 可以清。）
    """
    info, store, mem = _setup(tmp_path)
    _chapter(store, 1, body=BODY1)
    _chapter(store, 2, body=BODY2)
    item1 = _issue_item(chapter="第1章", quote="少年握紧了手里的木剑", issue="第一章的问题")
    item2 = _issue_item(chapter="第2章", quote=QUOTE2, issue="第二章的问题")
    llm = FakeLLM([_Resp(_payload(verdict="fix", issues=[item1, item2]))])
    await _run(llm, tmp_path, store, mem)

    # 第二轮只报第 1 章 → 第 2 章的上轮意见**保留**（没被清），但**要报出来**
    llm2 = FakeLLM([_Resp(_payload(verdict="fix", issues=[item1]))])
    text, _events = await _run(llm2, tmp_path, store, mem)

    assert "另有 1 条上轮意见本轮未再出现" in text
    assert "如已过时可在 /issues 里标记忽略" in text
    issues = IssuesStore(info.path)
    assert {i.chapter for i in issues.list("pending")} == {1, 2}


async def test_global_issue_lands_with_no_chapter(tmp_path):
    """全局类意见落库：chapter=None、quote 可空（按设计允许无 quote）。"""
    info, store, mem = _setup(tmp_path)
    _chapter(store, 1, body=BODY1)
    llm = FakeLLM([_Resp(_payload(verdict="fix", issues=[
        {"chapter": "全局", "type": "goal", "dimension": "情节", "quote": "",
         "issue": "主线目标没推进", "fix": "调整主线"}]))])
    text, _events = await _run(llm, tmp_path, store, mem)

    pending = IssuesStore(info.path).list("pending")
    assert len(pending) == 1
    assert pending[0].chapter is None
    assert pending[0].quote is None
    assert pending[0].title == "主线目标没推进"
    assert pending[0].detail == "goal：主线目标没推进；建议：调整主线"
    assert "意见 1 条" in text.splitlines()[0]


# ------------------------------------------- quote 纪律按 kind 分层（端到端）

#: seed 的情节级验收输出格式**没有 quote 字段**（意见针对情节结构，未必可引原文）
_PLOT_ISSUE_NO_QUOTE = {"chapter": "第2章", "type": "情节", "dimension": "情节",
                        "quote": "", "issue": "情节推进缺一拍", "fix": "补一拍收束"}


async def test_plot_kind_lands_chapter_issue_without_quote(tmp_path):
    """kind="plot"：无 quote 的章级意见**落库**（情节级契约：不强制证据）。

    落库口径照旧：source="check"、chapter 透传、quote 为空；观测如实报
    「意见 1 条（丢弃无证据 0 条）」。
    """
    info, store, mem = _setup(tmp_path)
    _chapter(store, 1, body=BODY1)
    _chapter(store, 2, body=BODY2)
    llm = FakeLLM([_Resp(_payload(verdict="fix", score=7,
                                   issues=[_PLOT_ISSUE_NO_QUOTE]))])
    text, events = await _run(llm, tmp_path, store, mem, kind="plot")

    pending = IssuesStore(info.path).list("pending")
    assert len(pending) == 1
    issue = pending[0]
    assert issue.source == "check"
    assert issue.chapter == 2
    assert not (issue.quote or "").strip()
    assert issue.title == "情节推进缺一拍"
    assert issue.detail == "情节：情节推进缺一拍；建议：补一拍收束"
    assert text.splitlines()[0] == \
        "验收完成：verdict=fix、意见 1 条（丢弃无证据 0 条），已落 issues"
    assert ("check_report", {"verdict": "fix", "score": 7, "issues": 1,
                              "discarded": 0, "kind": "plot",
                                  "checklist": 0, "checklist_failed": 0}) in events


async def test_global_kind_still_discards_chapter_issue_without_quote(tmp_path):
    """kind="global"：同一条无 quote 的章级意见**照丢**（全书级的证据纪律不变）。"""
    info, store, mem = _setup(tmp_path)
    _chapter(store, 1, body=BODY1)
    _chapter(store, 2, body=BODY2)
    llm = FakeLLM([_Resp(_payload(verdict="fix", score=7,
                                   issues=[_PLOT_ISSUE_NO_QUOTE]))])
    text, events = await _run(llm, tmp_path, store, mem, kind="global")

    assert IssuesStore(info.path).list("pending") == []
    assert text.splitlines()[0] == "验收完成：verdict=fix、零意见（丢弃无证据 1 条）"
    assert ("check_report", {"verdict": "fix", "score": 7, "issues": 0,
                              "discarded": 1, "kind": "global",
                                  "checklist": 0, "checklist_failed": 0}) in events


async def test_explicit_chapters_filter_sampling(tmp_path):
    """chapters 非 None → 只从这些章里抽样（samples 槽只含指定章）。"""
    _info, store, mem = _setup(tmp_path)
    for seq in range(1, 21):
        _chapter(store, seq)
    llm = FakeLLM([_Resp(_payload(verdict="pass", issues=[]))])
    await _run(llm, tmp_path, store, mem, chapters=[1, 2, 3])

    prompt = llm.calls[0]["prompt"]
    sampled = [int(m) for m in re.findall(r"【第(\d+)章", prompt)]
    assert sampled == [1, 2, 3]


async def test_unknown_kind_raises(tmp_path):
    """未知 kind → fail loud（参数错不静默当验收通过）。"""
    import pytest

    _info, store, mem = _setup(tmp_path)
    llm = FakeLLM([_Resp(_payload(verdict="pass", issues=[]))])
    with pytest.raises(ValueError):
        await _run(llm, tmp_path, store, mem, kind="editor")


async def test_zero_issues_honest_line(tmp_path):
    """零意见如实说：不谎称「已落 issues」。"""
    info, store, mem = _setup(tmp_path)
    _chapter(store, 1, body=BODY1)
    llm = FakeLLM([_Resp(_payload(verdict="pass", score=9, issues=[]))])
    text, _events = await _run(llm, tmp_path, store, mem)

    first = text.splitlines()[0]
    assert first == "验收完成：verdict=pass、零意见（丢弃无证据 0 条）"
    assert "已落 issues" not in first
    assert IssuesStore(info.path).list("pending") == []


async def test_llm_failure_zero_write(tmp_path):
    """LLM 失败 → 零 issues、观测「验收失败」、不 raise（零写盘）。"""
    info, store, mem = _setup(tmp_path)
    _chapter(store, 1, body=BODY1)
    llm = FakeLLM([LLMError("调用失败", transient=True, kind="timeout")])
    text, events = await _run(llm, tmp_path, store, mem)

    assert text.splitlines()[0] == "验收失败（调用失败）"
    assert IssuesStore(info.path).list("pending") == []
    assert events == []          # 失败不发 check_report（没有验收结果）


def test_failure_observation_is_classified_as_failed():
    """失败观测必须被 loop 的分类器判成 **failed**（否则一次超时验收被计成有效步）。

    旧文案「验收未完成（调用失败）」既不以 FAILED 开头、也不含「失败（」→ 被判 ok：
    不算失败、不计熔断、白烧一个 noop 槽。
    """
    from harness.agent.loop import AgentLoop

    assert AgentLoop._classify("验收失败（调用失败）") == "failed"
    assert AgentLoop._classify("验收失败（无产出）") == "failed"


# ---------------------------------------- 观测不回显模型原文（SHOULD 2）

async def test_observation_does_not_echo_model_issue_text(tmp_path):
    """观测**不回显模型意见原文**（只给章号 + 类型）——否则撞上 loop 的瞬时词扫描。

    模型写「甲乙两人关系**连接**生硬」→ 整条**成功**的验收观测被扫到「连接」→
    被判 transient → 模型收到自相矛盾的 FAILED-TRANSIENT、该步不算进展。
    """
    _info, store, mem = _setup(tmp_path)
    _chapter(store, 1, body=BODY1)
    _chapter(store, 2, body=BODY2)
    llm = FakeLLM([_Resp(_payload(verdict="fix", issues=[
        _issue_item(issue="甲乙两人关系连接生硬")]))])
    text, _events = await _run(llm, tmp_path, store, mem)

    assert "连接生硬" not in text              # 原文不回显
    assert "· 第2章：人物" in text            # 只给章号 + **派生维度**（固定词表）
    assert "老者动机不明" not in text

    from harness.agent.loop import AgentLoop
    assert AgentLoop._classify(text) == "ok"   # 成功观测不得被判 transient


async def test_observation_note_lists_type_for_global_issue(tmp_path):
    """全局类意见的观测行也是「· 全局：<type>」（同样不回显原文）。"""
    _info, store, mem = _setup(tmp_path)
    _chapter(store, 1, body=BODY1)
    llm = FakeLLM([_Resp(_payload(verdict="fix", issues=[
        {"chapter": "全局", "type": "goal", "dimension": "情节", "quote": "",
         "issue": "主线目标没推进", "fix": "调整主线"}]))])
    text, _events = await _run(llm, tmp_path, store, mem)

    assert "· 全局：情节" in text              # 派生维度（固定词表），不是模型的 type
    assert "主线目标没推进" not in text


# ---------------------------------------- 组装走 to_thread（MUST 2 / P4 I-1）

async def test_context_assembly_runs_in_worker_thread(tmp_path, monkeypatch):
    """上下文组装（读章 + 解析记忆 + 渲染变量）整体走 ``asyncio.to_thread``。

    P4 I-1 纪律：全书扫描是 CPU/IO 密集活，跑在事件循环上会阻塞整个 loop
    （复审实测 300 章约 3 秒/次）。LLM 调用**不**在 to_thread 里。
    """
    import asyncio

    _info, store, mem = _setup(tmp_path)
    _chapter(store, 1, body=BODY1)
    _chapter(store, 2, body=BODY2)

    state = {"in_thread": False, "to_thread_calls": 0, "assembly_in_thread": []}
    real_to_thread = asyncio.to_thread
    real_done_chapters = runner_mod.done_chapters

    async def spy_to_thread(func, /, *args, **kwargs):
        state["to_thread_calls"] += 1
        state["in_thread"] = True
        try:
            return await real_to_thread(func, *args, **kwargs)
        finally:
            state["in_thread"] = False

    def spy_done_chapters(store_):
        state["assembly_in_thread"].append(state["in_thread"])
        return real_done_chapters(store_)

    monkeypatch.setattr(asyncio, "to_thread", spy_to_thread)
    monkeypatch.setattr(runner_mod, "done_chapters", spy_done_chapters)

    class _ThreadAwareLLM(FakeLLM):
        async def complete(self, messages, *, model, temperature, max_tokens):
            state["llm_in_thread"] = state["in_thread"]
            return await super().complete(messages, model=model,
                                         temperature=temperature, max_tokens=max_tokens)

    llm = _ThreadAwareLLM([_Resp(_payload(verdict="pass", issues=[]))])
    await _run(llm, tmp_path, store, mem)

    assert state["to_thread_calls"] >= 1              # 组装确实走线程
    assert state["assembly_in_thread"] == [True]      # 读章发生在线程里
    assert state["llm_in_thread"] is False            # LLM 调用不在 to_thread 里


# ---------------------------------------- M-4：落库侧的章范围 = 本次目标章

async def test_issue_outside_target_chapters_is_discarded(tmp_path):
    """chapter_ids 限定范围 → 指向**范围外**章节的意见被丢弃（无从回验）。"""
    info, store, mem = _setup(tmp_path)
    _chapter(store, 1, body=BODY1)
    _chapter(store, 2, body=BODY2)
    _chapter(store, 3, body=BODY3)
    llm = FakeLLM([_Resp(_payload(verdict="fix", issues=[
        _issue_item(chapter="第3章", quote=QUOTE3, issue="第3章的问题")]))])
    text, _events = await _run(llm, tmp_path, store, mem, chapters=[1, 2])

    assert IssuesStore(info.path).list("pending") == []
    assert "丢弃无证据 1 条" in text.splitlines()[0]


async def test_checklist_fail_downgrades_verdict(tmp_path):
    """清单有 fail → 观测的 verdict 至少 fix（模型自称 pass 也不算 pass）。"""
    _info, store, mem = _setup(tmp_path)
    _chapter(store, 1, body=BODY1)
    llm = FakeLLM([_Resp(_payload(verdict="pass", score=8, issues=[],
                                   checklist=[{"item": "限制遵守", "verdict": "fail",
                                               "evidence": "违反了限制"}]))])
    text, events = await _run(llm, tmp_path, store, mem)

    assert text.splitlines()[0] == "验收完成：verdict=fix、零意见（丢弃无证据 0 条）"
    assert ("check_report", {"verdict": "fix", "score": 8, "issues": 0,
                              "discarded": 0, "kind": "global",
                              "checklist": 1, "checklist_failed": 1}) in events


# ------------------------------------------------------------------ 抽样 / 提示词

async def test_global_check_samples_chapters(tmp_path):
    """长项目：抽样章数 ≤12 且含最新章；samples 槽只含抽中的章。"""
    _info, store, mem = _setup(tmp_path)
    for seq in range(1, 21):
        _chapter(store, seq)
    llm = FakeLLM([_Resp(_payload(verdict="pass", issues=[]))])
    await _run(llm, tmp_path, store, mem, kind="global")

    prompt = llm.calls[0]["prompt"]
    sampled = [int(m) for m in re.findall(r"【第(\d+)章", prompt)]
    assert 0 < len(sampled) <= 12
    assert 20 in sampled                       # 最新章必抽
    assert sampled == sorted(sampled)
    for seq in range(1, 21):                  # 未抽中的章正文不得进提示词
        if seq not in sampled:
            assert _body(seq) not in prompt


async def test_plot_kind_uses_plot_prompt(tmp_path):
    """kind="plot" → 渲染 task.auto_plot_check（检查官模板）。"""
    _info, store, mem = _setup(tmp_path)
    _chapter(store, 1, body=BODY1)
    llm = FakeLLM([_Resp(_payload(verdict="pass", issues=[]))])
    await _run(llm, tmp_path, store, mem, kind="plot")

    prompt = llm.calls[0]["prompt"]
    assert "你是自主创作流程的检查官" in prompt
    assert "签约责编" not in prompt


async def test_editor_mode_uses_editor_prompt(tmp_path):
    """editor_mode=True → 渲染 task.auto_editor_review（签约责编模板）。"""
    _info, store, mem = _setup(tmp_path)
    _chapter(store, 1, body=BODY1)
    llm = FakeLLM([_Resp(_payload(verdict="pass", issues=[]))])
    await _run(llm, tmp_path, store, mem, kind="global", editor_mode=True)

    prompt = llm.calls[0]["prompt"]
    assert "签约责编" in prompt
    assert "总编辑" not in prompt


async def test_global_kind_uses_global_prompt(tmp_path):
    """kind="global" → 渲染 task.auto_global_check（总编辑模板）。"""
    _info, store, mem = _setup(tmp_path)
    _chapter(store, 1, body=BODY1)
    llm = FakeLLM([_Resp(_payload(verdict="pass", issues=[]))])
    await _run(llm, tmp_path, store, mem, kind="global")

    prompt = llm.calls[0]["prompt"]
    assert "总编辑" in prompt
    assert "签约责编" not in prompt


async def test_vars_match_template_contract(tmp_path):
    """组装出的变量键集与模板的 {{var}} 集合逐一相等（多一个少一个都炸）。"""
    from harness.check.context import global_check_vars, plot_check_vars

    info, store, mem = _setup(tmp_path)
    _chapter(store, 1, body=BODY1)
    loader = _loader(tmp_path)

    plot_vars = plot_check_vars(store=store, mem=mem, goals="目标", constraints="限制")
    assert set(plot_vars) == set(_VAR_RE.findall(loader.get("task.auto_plot_check")))

    docs = store.list_chapters()
    global_vars = global_check_vars(store=store, mem=mem, goals="目标", constraints="限制",
                                    premise="需求", chapters=docs)
    assert set(global_vars) == set(_VAR_RE.findall(loader.get("task.auto_global_check")))

    editor_vars = global_check_vars(store=store, mem=mem, goals="目标", constraints="限制",
                                     premise="需求", chapters=docs, editor_mode=True)
    assert set(editor_vars) == set(_VAR_RE.findall(loader.get("task.auto_editor_review")))
    assert "stage_checklist_items" not in editor_vars
    assert info.path  # 项目根存在（防测试自身空转）


# ------------------------------------------------------------------ 共享口径

def test_shared_promises_helper():
    """unresolved_promises_text 与 P5 审计的旧口径逐字一致（章号倒序 [:10]、未回收口径）。"""
    from harness.memory.inject import unresolved_promises_text

    datas = {
        1: {"promise_updates": [
            {"kind": "foreshadow", "content": "后山密道", "by_character": "甲", "status": "open"},
            {"kind": "promise", "content": "已兑现的承诺", "by_character": "乙", "status": "resolved"},
        ]},
        2: {"promise_updates": [
            {"kind": "promise", "content": "旧约", "by_character": "丙", "status": ""},
        ]},
    }
    assert unresolved_promises_text(datas, 2) == (
        "· [promise] 旧约——丙（第2章埋）\n"
        "· [foreshadow] 后山密道——甲（第1章埋）")
    # 只看 ≤seq 的章
    assert unresolved_promises_text(datas, 1) == "· [foreshadow] 后山密道——甲（第1章埋）"


def test_audit_uses_shared_promises_helper():
    """P5 审计改为 import 共享实现（仓库里只有一份未兑现承诺口径）。"""
    from harness.audit import runner
    from harness.memory.inject import unresolved_promises_text

    assert runner.unresolved_promises_text is unresolved_promises_text
    assert not hasattr(runner, "_promises_text")


async def test_context_open_promises_uses_shared_helper(tmp_path):
    """验收的未兑现承诺槽 = 共享实现（同 P5 审计口径）。"""
    from harness.check.context import global_check_vars

    info, store, mem = _setup(tmp_path)
    _chapter(store, 1, body=BODY1)
    mem.write_chapter_memory(1, "风雪夜", {"promise_updates": [
        {"kind": "foreshadow", "content": "后山密道", "by_character": "甲", "status": "open"}]})
    docs = store.list_chapters()
    vars_ = global_check_vars(store=store, mem=mem, goals="目标", constraints="限制",
                               premise="需求", chapters=docs)
    assert vars_["open_promises"] == "· [foreshadow] 后山密道——甲（第1章埋）"
    assert info.path


# ------------------------------------------------------------------ brief 槽

async def test_brief_slots_fallback(tmp_path):
    """brief 缺 premise → goals 回落到 raw；constraints 逐行「- xxx」；premise 取 raw。"""
    from harness.check.context import brief_context, global_check_vars

    info, store, mem = _setup(tmp_path)
    state = RunState.new("一个修仙少年的成长故事", [])
    state.brief["constraints"] = ["不写系统面板", "单线叙事"]
    state.save(info.path)

    brief = brief_context(store)
    assert brief["premise"] == "一个修仙少年的成长故事"
    assert brief["goals"] == "一个修仙少年的成长故事"      # premise 缺 → 回落 raw
    assert brief["constraints"] == "- 不写系统面板\n- 单线叙事"

    _chapter(store, 1, body=BODY1)
    docs = store.list_chapters()
    vars_ = global_check_vars(store=store, mem=mem, goals=brief["goals"],
                               constraints=brief["constraints"], premise=brief["premise"],
                               chapters=docs)
    assert "不写系统面板" in vars_["constraints"]


async def test_brief_absent_defaults(tmp_path):
    """没有 state.json → premise/goals「（未提供）」、constraints「（无）」。"""
    from harness.check.context import brief_context

    _info, store, _mem = _setup(tmp_path)
    brief = brief_context(store)
    assert brief["premise"] == "（未提供）"
    assert brief["goals"] == "（未提供）"
    assert brief["constraints"] == "（无）"


async def test_check_does_not_touch_chapter_text(tmp_path):
    """只报不改：验收不写任何章正文（章文件字节不变）。"""
    info, store, mem = _setup(tmp_path)
    _chapter(store, 1, body=BODY1)
    _chapter(store, 2, body=BODY2)
    before = {p.name: p.read_bytes() for p in (info.path / "chapters").iterdir()}
    llm = FakeLLM([_Resp(_payload(verdict="fix", issues=[_issue_item()]))])
    await _run(llm, tmp_path, store, mem)
    after = {p.name: p.read_bytes() for p in (info.path / "chapters").iterdir()}
    assert before == after


def test_runner_module_exports():
    import inspect

    from harness.check.runner import run_check

    assert inspect.iscoroutinefunction(run_check)


async def test_observation_never_carries_model_type_text(tmp_path):
    """模型的 type / dimension 都是自由文本 → 观测里不得出现它们（复审 follow-up 2.1）。

    模型给 ``type="connection timeout"`` → 观测含「connection」→ loop 的瞬时词扫描
    把整条**成功**的验收判成 transient。观测只准带**我们自己那张固定词表**的维度。
    """
    _info, store, mem = _setup(tmp_path)
    _chapter(store, 1, body=BODY1)
    _chapter(store, 2, body=BODY2)
    llm = FakeLLM([_Resp(_payload(verdict="fix", issues=[
        {"chapter": "第2章", "type": "connection timeout", "dimension": "timeout",
         "quote": "老者把剑递了过去", "issue": "老者动机不明", "fix": "补一句回忆"}]))])
    text, _events = await _run(llm, tmp_path, store, mem)

    assert "connection" not in text
    assert "timeout" not in text
    # type 不在固定词表 → 维度回落「情节」（seed 同款默认），观测里没有模型原文
    assert "· 第2章：情节" in text

    from harness.agent.loop import AgentLoop
    assert AgentLoop._classify(text) == "ok"


async def test_checklist_long_title_still_superseded(tmp_path):
    """条目标题**超 40 字**时，重跑仍要替换旧条目（不累积）。

    台账里存的是**截断到 40 字**的标题；supersede 若拿未截断的原名去比就永远匹配不上
    → 旧条目留在 pending → 台账累积（自查抓到的 bug）。
    """
    _info, store, mem = _setup(tmp_path)
    _chapter(store, 1)
    long_item = "长" * 60
    payload = _payload(verdict="fix", score=6, issues=[],
                       checklist=[{"item": long_item, "verdict": "fail",
                                   "evidence": "依据"}])
    for _ in range(2):
        llm = FakeLLM([_Resp(payload)])
        await _run(llm, tmp_path, store, mem)

    issues = IssuesStore(store.root)
    assert len(issues.list("pending")) == 1
    assert len(issues.list("resolved")) == 1
    assert len(issues.list("pending")[0].title) == 40


async def test_checklist_same_item_twice_in_one_response(tmp_path):
    """模型在**一次响应里重复返回同一条目** → 只落一条（后者 supersede 前者）。

    否则台账里会出现两条同款候选（人工分诊时看着像两个问题）。
    """
    _info, store, mem = _setup(tmp_path)
    _chapter(store, 1)
    item = {"item": "限制遵守", "verdict": "fail", "evidence": "违反了限制"}
    llm = FakeLLM([_Resp(_payload(verdict="fix", score=6, issues=[],
                                checklist=[item, dict(item)]))])
    _text, _events = await _run(llm, tmp_path, store, mem)

    issues = IssuesStore(store.root)
    assert len(issues.list("pending")) == 1
    assert issues.list("pending")[0].title == "限制遵守"


async def test_checklist_item_name_drift_does_not_accumulate(tmp_path):
    """模型给**同一条清单条目**回了**不同的名字** → 第二次仍要替换第一条（不累积）。

    复审实测（真缺陷）：模型回「我的清单」与「我的清单（第三章）」→ 台账里 pending **两条**
    ——按标题 supersede 只认「同一个字符串」，名字一漂就失效 → 同一条目在台账里堆成多条，
    人工分诊时看着像两个问题。
    修法：台账标题取**规范标题**（我们给模型的那一串），模型的名字先映射回它。
    """
    info, store, mem = _setup(tmp_path)
    _chapter(store, 1)
    clist = info.path / ".harness" / "checklists" / "finish"
    clist.mkdir(parents=True, exist_ok=True)
    (clist / "我的清单.md").write_text("本书必须交代主角的师门来历。\n", encoding="utf-8")

    for name in ("我的清单", "我的清单（第三章）"):
        payload = _payload(verdict="fix", score=6, issues=[],
                           checklist=[{"item": name, "verdict": "fail", "evidence": "依据"}])
        llm = FakeLLM([_Resp(payload)])
        await _run(llm, tmp_path, store, mem)

    issues = IssuesStore(store.root)
    assert len(issues.list("pending")) == 1
    assert len(issues.list("resolved")) == 1
    assert issues.list("pending")[0].title == "我的清单"      # 规范标题，不是模型那串字


async def test_checklist_unknown_item_name_still_lands(tmp_path):
    """模型**自造**的条目名（映射不上）→ 照样落 issue（已知取舍：不做来源校验）。

    「宁可不误杀合法判定」优先于「台账干净」：模型有时会把条目名改写/截短，
    严格相等会误杀；映射不上的那条保留模型原名（代价：台账里可能有模型自造的条目）。
    """
    _info, store, mem = _setup(tmp_path)
    _chapter(store, 1)
    payload = _payload(verdict="fix", score=6, issues=[],
                       checklist=[{"item": "第三章没交代师门", "verdict": "fail",
                                   "evidence": "依据"}])
    llm = FakeLLM([_Resp(payload)])
    await _run(llm, tmp_path, store, mem)

    issues = IssuesStore(store.root)
    assert len(issues.list("pending")) == 1
    assert issues.list("pending")[0].title == "第三章没交代师门"


async def test_checklist_evidence_not_in_observation(tmp_path):
    """清单条目的 **evidence（模型原文）不得进观测、也不得进事件 payload**。

    纪律与 P6 裁定 54 同款：观测只准带固定词与计数；模型原文只落 issues 台账
    （台账是数据面，不是分类器的输入）。
    **复审的变异检验抓出来的盲区**：此前没有测试钉住这条纪律——把 evidence 拼进观测
    全量套件仍然全绿。
    """
    _info, store, mem = _setup(tmp_path)
    _chapter(store, 1)
    llm = FakeLLM([_Resp(_payload(verdict="fix", score=6, issues=[],
                                checklist=[{"item": "限制遵守", "verdict": "fail",
                                            "evidence": "EVIDENCE_SECRET"}]))])
    text, events = await _run(llm, tmp_path, store, mem)

    assert "EVIDENCE_SECRET" not in text
    assert all("EVIDENCE_SECRET" not in str(payload) for _kind, payload in events)
    # 但台账里有（详情是数据面，人要看得见依据）
    assert "EVIDENCE_SECRET" in IssuesStore(store.root).list("pending")[0].detail


async def test_checklist_invalid_verdict_item_dropped(tmp_path):
    """verdict **非法**的清单条目 → 整条丢弃：不落 issue，**也不计入清单条数**。

    非法值不猜、更不当 pass（清单契约：verdict 只认 pass/fail/na）。
    **复审的变异检验抓出来的盲区**：此前把非法 verdict 当成 pass 处理，全量套件仍全绿
    ——但清单条数会因此多算一条（观测与事件都虚高）。
    """
    _info, store, mem = _setup(tmp_path)
    _chapter(store, 1)
    llm = FakeLLM([_Resp(_payload(verdict="pass", score=8, issues=[],
                                checklist=[{"item": "限制遵守", "verdict": "maybe",
                                            "evidence": "依据"}]))])
    text, events = await _run(llm, tmp_path, store, mem)

    assert IssuesStore(store.root).list("pending") == []
    assert "清单 0 条" in text
    report = next(p for kind, p in events if kind == "check_report")
    assert report["checklist"] == 0
    assert report["checklist_failed"] == 0

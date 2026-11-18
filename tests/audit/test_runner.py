# 审计编排（P5 Task 3）
"""run_audit 语义钉死：

- 确定性段：R1-R4 输入从 ProjectStore/MemoryStore/DigestKeeper 装配，findings 落
  issues（source="audit"、chapter 透传、quote 逐字）；正文扫描段整体 asyncio.to_thread；
- supersede（裁定 5）：同章重跑先把旧 pending audit issue 置 resolved 再落新条——
  该章 pending 仍 1 条、旧条 resolved；确定性段先落、LLM 段后落，同章不互相清；
- LLM 段：chapters=None → seq 降序最近 llm_recent（默认 12）章；chapters 非 None →
  指定章；review 抛 LLMError/ValueError → 观测「LLM 复核失败」+ 继续，确定性结果保留；
- 落库举证门：LLM finding 的 quote 必须 locate_quote 命中该章正文，未命中丢弃计入
  「丢弃无证据 K 条」；emit("audit_done", {...}) 计数与首行逐字。
"""
from __future__ import annotations

from harness.audit.rules import AuditFinding
from harness.llm.client import LLMError
from harness.memory.store import MemoryStore
from harness.projects import create_project
from harness.run.issues import IssuesStore
from harness.store.project import ChapterDoc, ProjectStore


def _emit_recorder():
    events: list[tuple[str, dict]] = []
    return events, (lambda kind, payload: events.append((kind, payload)))


def _done_chapter(store: ProjectStore, seq: int, body: str, *, pov: str = "乙",
                  status: str = "done") -> None:
    store.write_chapter(ChapterDoc(
        seq=seq, meta={"seq": seq, "title": f"第{seq}章", "status": status,
                       "beats": [{"pov": pov}]},
        body=body))


def _offstage_project(tmp_path):
    """两章定稿 + state.md 甲 status=dead（updated/last_seen=第1章）；第 2 章正文含「甲」。"""
    info = create_project(tmp_path, "审计书")
    store = ProjectStore(info.path)
    _done_chapter(store, 1, "乙走进山门。")
    _done_chapter(store, 2, "甲的牌位立在堂上。")
    mem = MemoryStore(info.path)
    mem.apply_state_updates({"characters": [{"name": "甲", "status": "dead"}]}, seq=1)
    return info, store, mem


async def test_deterministic_findings_land(tmp_path):
    """dead 角色第 1 章退场、第 2 章正文仍出现 → 落 1 条 source="audit"，quote 逐字。"""
    from harness.audit.runner import run_audit

    info, store, mem = _offstage_project(tmp_path)
    events, emit = _emit_recorder()
    text = await run_audit(store=store, mem=mem, llm=None, prompts=None, settings=None,
                           chapters=[], emit=emit)
    pending = IssuesStore(info.path).list("pending")
    assert len(pending) == 1
    issue = pending[0]
    assert issue.source == "audit"
    assert issue.chapter == 2
    assert issue.title == "dead角色晚出场"
    assert issue.quote == "甲的牌位立在堂上"      # 含名句子逐字（切句去句末标点）
    assert ("audit_done", {"deterministic": 1, "llm": 0, "discarded": 0}) in events
    assert text.splitlines()[0] == \
        "一致性审计完成：确定性 1 条、LLM 复核 0 条（丢弃无证据 0 条），已落 issues"


async def test_zero_findings_honest_line(tmp_path):
    """零发现如实说：不谎称「已落 issues」。"""
    from harness.audit.runner import run_audit

    info = create_project(tmp_path, "干净书")
    store = ProjectStore(info.path)
    _done_chapter(store, 1, "乙走进山门。")
    mem = MemoryStore(info.path)
    _events, emit = _emit_recorder()
    text = await run_audit(store=store, mem=mem, llm=None, prompts=None, settings=None,
                           chapters=[], emit=emit)
    first = text.splitlines()[0]
    assert "零发现" in first and "已落 issues" not in first


async def test_supersede_on_rerun(tmp_path):
    """同章连跑两次：该章 pending 仍 1 条，旧条 resolved（裁定 5）。"""
    from harness.audit.runner import run_audit

    info, store, mem = _offstage_project(tmp_path)
    issues = IssuesStore(info.path)
    for _ in range(2):
        _events, emit = _emit_recorder()
        await run_audit(store=store, mem=mem, llm=None, prompts=None, settings=None,
                        chapters=[], emit=emit)
    pending = issues.list("pending")
    assert len(pending) == 1
    assert pending[0].chapter == 2
    resolved = issues.list("resolved")
    assert len(resolved) == 1 and resolved[0].chapter == 2
    assert pending[0].id != resolved[0].id


async def test_llm_recent_default_12(tmp_path, monkeypatch):
    """15 章 + chapters=None → review 只被调最近 12 章（seq 降序）。"""
    from harness.audit import runner

    info = create_project(tmp_path, "十五章书")
    store = ProjectStore(info.path)
    for seq in range(1, 16):
        _done_chapter(store, seq, f"第{seq}章正文。")
    mem = MemoryStore(info.path)
    calls: list[int] = []

    async def fake_review(**kwargs):
        calls.append(kwargs["chapter_seq"])
        return [], None

    monkeypatch.setattr(runner, "review_chapter", fake_review)
    _events, emit = _emit_recorder()
    await runner.run_audit(store=store, mem=mem, llm=None, prompts=None, settings=None,
                           chapters=None, emit=emit)
    assert calls == list(range(15, 3, -1))


async def test_llm_explicit_chapters(tmp_path, monkeypatch):
    """chapters 非 None → 只跑指定章（planned 章不送审）。"""
    from harness.audit import runner

    info = create_project(tmp_path, "指定书")
    store = ProjectStore(info.path)
    _done_chapter(store, 1, "一。")
    _done_chapter(store, 2, "二。")
    _done_chapter(store, 3, "三。", status="planned")
    mem = MemoryStore(info.path)
    calls: list[int] = []

    async def fake_review(**kwargs):
        calls.append(kwargs["chapter_seq"])
        return [], None

    monkeypatch.setattr(runner, "review_chapter", fake_review)
    _events, emit = _emit_recorder()
    await runner.run_audit(store=store, mem=mem, llm=None, prompts=None, settings=None,
                           chapters=[1, 2, 3], emit=emit)
    assert calls == [1, 2]                     # 第 3 章 planned，不送审


async def test_llm_failure_keeps_deterministic(tmp_path, monkeypatch):
    """review raise → 观测含「LLM 复核失败」，确定性 issues 仍在，剩余章继续。"""
    from harness.audit import runner

    info, store, mem = _offstage_project(tmp_path)
    calls: list[int] = []

    async def fake_review(**kwargs):
        calls.append(kwargs["chapter_seq"])
        raise LLMError("一致性复核失败（调用失败）：第X章", transient=True, kind="timeout")

    monkeypatch.setattr(runner, "review_chapter", fake_review)
    _events, emit = _emit_recorder()
    text = await runner.run_audit(store=store, mem=mem, llm=None, prompts=None,
                                  settings=None, chapters=None, emit=emit)
    pending = IssuesStore(info.path).list("pending")
    assert len(pending) == 1 and pending[0].source == "audit"   # 确定性结果未丢
    assert sorted(calls) == [1, 2]                              # 失败章不拖垮后续章
    assert "LLM 复核失败" in text


async def test_llm_fabricated_quote_discarded_at_landing(tmp_path, monkeypatch):
    """LLM finding 的 quote 落库前回验正文：编造 quote 丢弃计入「丢弃无证据 K 条」。"""
    from harness.audit import runner

    info = create_project(tmp_path, "编造书")
    store = ProjectStore(info.path)
    _done_chapter(store, 1, "乙走进山门。")
    mem = MemoryStore(info.path)

    async def fake_review(**kwargs):
        return [AuditFinding(rule="consistency_timeline", chapter=1, title="t",
                             detail="d", quote="正文里根本没有的句子")], None

    monkeypatch.setattr(runner, "review_chapter", fake_review)
    _events, emit = _emit_recorder()
    text = await runner.run_audit(store=store, mem=mem, llm=None, prompts=None,
                                  settings=None, chapters=[1], emit=emit)
    assert IssuesStore(info.path).list("pending") == []
    assert "丢弃无证据 1 条" in text.splitlines()[0]


async def test_deterministic_and_llm_findings_coexist(tmp_path, monkeypatch):
    """同章确定性+LLM 候选并存：LLM 段不得把本 run 刚落的确定性候选 supersede 掉。"""
    from harness.audit import runner

    info, store, mem = _offstage_project(tmp_path)

    async def fake_review(**kwargs):
        return [AuditFinding(rule="consistency_timeline", chapter=2, title="时间线矛盾",
                             detail="d", quote="甲的牌位立在堂上")], None

    monkeypatch.setattr(runner, "review_chapter", fake_review)
    _events, emit = _emit_recorder()
    await runner.run_audit(store=store, mem=mem, llm=None, prompts=None, settings=None,
                           chapters=None, emit=emit)
    pending = IssuesStore(info.path).list("pending")
    assert len(pending) == 2                       # 确定性 1 + LLM 1 都在
    assert {i.title for i in pending} == {"dead角色晚出场", "时间线矛盾"}
    assert all(i.chapter == 2 for i in pending)


async def test_llm_finding_lands_with_chapter(tmp_path, monkeypatch):
    """真 quote 的 LLM finding 落 issues：source="audit"、chapter=章号、quote 逐字。"""
    from harness.audit import runner

    info = create_project(tmp_path, "复核书")
    store = ProjectStore(info.path)
    _done_chapter(store, 1, "乙走进山门。")
    mem = MemoryStore(info.path)

    async def fake_review(**kwargs):
        return [AuditFinding(rule="consistency_timeline", chapter=1, title="时间线矛盾",
                             detail="d", quote="乙走进山门")], None

    monkeypatch.setattr(runner, "review_chapter", fake_review)
    events, emit = _emit_recorder()
    await runner.run_audit(store=store, mem=mem, llm=None, prompts=None, settings=None,
                           chapters=[1], emit=emit)
    pending = IssuesStore(info.path).list("pending")
    assert len(pending) == 1
    assert pending[0].chapter == 1 and pending[0].quote == "乙走进山门"
    assert ("audit_done", {"deterministic": 0, "llm": 1, "discarded": 0}) in events


async def test_deterministic_scan_runs_off_event_loop(tmp_path, monkeypatch):
    """P4 I-1 纪律：正文扫描段整体 asyncio.to_thread（不在事件循环线程执行）。"""
    import threading

    from harness.audit import runner

    _info, store, mem = _offstage_project(tmp_path)
    seen: dict[str, int] = {}
    original = runner._deterministic_findings

    def spy(*args, **kwargs):
        seen["thread"] = threading.get_ident()
        return original(*args, **kwargs)

    monkeypatch.setattr(runner, "_deterministic_findings", spy)
    _events, emit = _emit_recorder()
    await runner.run_audit(store=store, mem=mem, llm=None, prompts=None, settings=None,
                           chapters=[], emit=emit)
    assert seen["thread"] != threading.get_ident()          # 不在事件循环线程
    assert seen["thread"] != threading.main_thread().ident  # 确在 worker 线程


def test_runner_module_exports():
    import inspect

    from harness.audit.runner import run_audit

    assert inspect.iscoroutinefunction(run_audit)

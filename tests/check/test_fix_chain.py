# 按章聚合修订（P8 Task 2）：聚合修订链（一次 LLM → 一张卡 → 一次写盘 → 逐条 resolved）
"""`fix_chapter` 语义钉死：

- 一次 LLM 调用产出该章**多条**意见的替换文本（`task.check_fix_chapter`，路由 standard/0.3/4096）；
- 每条意见按**它自己的 quote** 在**本章正文**里定位（定位不到 → 跳过该条，保持 pending）；
- 多段替换**倒序**执行 → 前一段的定位不被后一段的改写带偏；
- **一张确认卡**（本章意见清单 + 合并 diff）→ 确认后**一次写盘**（其余正文逐字不动）；
- 落地后：记忆重提取该章 + **实际落地**的意见逐条 resolved；
- 失败/取消：零写盘、意见保持 pending、观测只带计数（**不带模型原文**）。
"""
from __future__ import annotations

import json
from pathlib import Path

from harness.check.fix import fix_chapter
from harness.config import ProviderConfig, Settings
from harness.memory.store import MemoryStore
from harness.projects import create_project
from harness.prompts.loader import PromptLoader
from harness.run.approval import ApprovalCancelled, ApprovalUI, Choice
from harness.run.issues import IssuesStore
from harness.store.project import ChapterDoc, ProjectStore

BODY = "甲拔刀，乙点火。夜风吹过山寨。丙沉默不语。"
QUOTE_A = "夜风吹过山寨"
QUOTE_B = "丙沉默不语"
#: 替换文本与原引文**不等长**——等长替换会让「正序替换导致偏移串位」这类 bug 被掩盖
#: （长度不变 → 偏移不变 → 改错地方也看不出来）。这里故意让两段都变长。
NEW_A = "晨风掠过那座幽暗的山寨"
NEW_B = "丙长久地默然不语"


class _Resp:
    def __init__(self, text, usage=None):
        self.text = text
        self.usage = usage


class FakeLLM:
    """顺序吐脚本的假 llm（形态同 tests/audit/test_fix.py）。"""

    def __init__(self, script):
        self._script = list(script)
        self.calls: list[dict] = []
        self.provider = ProviderConfig(
            base_url="https://api.test", api_key="k",
            lite="m-lite", standard="m-std", pro="m-pro")

    async def complete(self, messages, *, model, temperature=0.7, max_tokens=8192):
        self.calls.append({"model": model, "temperature": temperature,
                           "max_tokens": max_tokens,
                           "prompt": messages[-1]["content"]})
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return _Resp(item)


def make_settings():
    return Settings(
        library_root=Path("x"),
        providers={"test": ProviderConfig(base_url="https://api.test", api_key="k",
                                          lite="m-lite", standard="m-std", pro="m-pro")},
        default_provider="test",
        default_tier="standard",
        web_verify=True,
    )


def prompts_for(root: Path) -> PromptLoader:
    return PromptLoader(project_root=root, home_dir=root)


def extract_payload() -> str:
    """记忆提取 fixture（章记忆落盘用）。"""
    return json.dumps({
        "events": [{"title": "夜探山寨", "summary": "甲潜入山寨", "location": "山寨",
                    "time_marker": "深夜", "characters": ["甲"],
                    "new_info": ["布防图"], "foreshadow": "后山密道"}],
        "character_events": [], "info_updates": [], "promise_updates": [],
        "state_updates": {"characters": [], "world": []},
    }, ensure_ascii=False)


def fix_payload(*pairs: tuple[str, str]) -> str:
    return json.dumps({"replacements": [{"id": i, "replacement": r} for i, r in pairs]},
                      ensure_ascii=False)


def _project(tmp_path):
    info = create_project(tmp_path, "聚合修订书")
    store = ProjectStore(info.path)
    store.write_chapter(ChapterDoc(seq=1, meta={"seq": 1, "title": "第1章",
                                                "status": "done"}, body=BODY))
    mem = MemoryStore(info.path)
    issues = IssuesStore(info.path)
    return info, store, mem, issues


def _ui(indices=(0,), raiser=None):
    """假确认门：记录问题文本，返回指定选项；raiser 非 None 时抛（审批取消）。"""
    seen: list[str] = []

    def ask(question, options, multi=False, note=None):
        seen.append(question)
        if raiser is not None:
            raise raiser
        return Choice(indices=list(indices))

    return ApprovalUI(ask=ask), seen


async def _run(store, mem, issues, llm, ui, chapter=1):
    return await fix_chapter(store=store, mem=mem, llm=llm, prompts=prompts_for(store.root),
                             settings=make_settings(), chapter=chapter,
                             issues=issues, ui=ui)


# ------------------------------------------------------------------ 主路径

async def test_fix_chapter_applies_all_replacements(tmp_path):
    """同一章两条意见 → 一次确认 → 两处都改 → **两条**都 resolved、记忆重提取。"""
    _info, store, mem, issues = _project(tmp_path)
    a = issues.add("check", "时间锚点错位", "夜里却写黎明", QUOTE_A, chapter=1)
    b = issues.add("check", "人物反应平淡", "该有个动作", QUOTE_B, chapter=1)
    llm = FakeLLM([fix_payload((a.id, NEW_A), (b.id, NEW_B)), extract_payload()])
    ui, _seen = _ui()

    text = await _run(store, mem, [a, b], llm, ui)

    assert text == "已修改第1章：改写 2 段，标记 resolved 2 条意见，记忆已重提取"
    body = store.read_chapter(1).body
    assert NEW_A in body and NEW_B in body
    assert QUOTE_A not in body and QUOTE_B not in body
    assert body.startswith("甲拔刀，乙点火。")     # 未命中的部分逐字不动
    assert issues.list("pending") == []
    assert [i.id for i in issues.list("resolved")] == [a.id, b.id]
    # 记忆重提取落盘
    assert (mem.root / "memory" / "0001.md").exists()


async def test_fix_chapter_one_card_for_the_chapter(tmp_path):
    """一章**一张**确认卡（不是每条一张）——卡里带本章意见清单与合并 diff。"""
    _info, store, mem, issues = _project(tmp_path)
    a = issues.add("check", "标题甲", "详情甲", QUOTE_A, chapter=1)
    b = issues.add("check", "标题乙", "详情乙", QUOTE_B, chapter=1)
    llm = FakeLLM([fix_payload((a.id, NEW_A), (b.id, NEW_B)), extract_payload()])
    ui, seen = _ui()

    await _run(store, mem, [a, b], llm, ui)

    assert len(seen) == 1
    card = seen[0]
    assert "标题甲" in card and "标题乙" in card     # 意见清单
    assert NEW_A in card and NEW_B in card           # 合并后的 diff


async def test_fix_chapter_replacements_applied_in_reverse(tmp_path):
    """两段替换**倒序**落地：靠前的一段不被靠后一段的改写带偏（偏移不串位）。"""
    _info, store, mem, issues = _project(tmp_path)
    a = issues.add("check", "标题甲", "详情甲", QUOTE_A, chapter=1)
    b = issues.add("check", "标题乙", "详情乙", QUOTE_B, chapter=1)
    llm = FakeLLM([fix_payload((a.id, NEW_A), (b.id, NEW_B)), extract_payload()])
    ui, _seen = _ui()

    await _run(store, mem, [a, b], llm, ui)

    body = store.read_chapter(1).body
    assert body == f"甲拔刀，乙点火。{NEW_A}。{NEW_B}。"


async def test_fix_chapter_marks_only_applied_resolved(tmp_path):
    """模型漏掉一条意见 → 那条**保持 pending**（不得静默消失），落地的那条才 resolved。"""
    _info, store, mem, issues = _project(tmp_path)
    a = issues.add("check", "标题甲", "详情甲", QUOTE_A, chapter=1)
    b = issues.add("check", "标题乙", "详情乙", QUOTE_B, chapter=1)
    llm = FakeLLM([fix_payload((a.id, NEW_A)), extract_payload()])
    ui, _seen = _ui()

    text = await _run(store, mem, [a, b], llm, ui)

    assert text == "已修改第1章：改写 1 段，标记 resolved 1 条意见，记忆已重提取"
    assert [i.id for i in issues.list("pending")] == [b.id]
    assert [i.id for i in issues.list("resolved")] == [a.id]


async def test_fix_chapter_observation_has_no_model_text(tmp_path):
    """观测**不带模型原文**（只带章号 + 计数）——与「观测不带模型原文」纪律同款。"""
    _info, store, mem, issues = _project(tmp_path)
    a = issues.add("check", "标题甲", "详情甲", QUOTE_A, chapter=1)
    llm = FakeLLM([fix_payload((a.id, NEW_A)), extract_payload()])
    ui, _seen = _ui()

    text = await _run(store, mem, [a], llm, ui)

    assert NEW_A not in text
    assert "改写 1 段" in text


# ------------------------------------------------------------------ 失败与取消

async def test_fix_chapter_cancel_writes_nothing(tmp_path):
    """确认卡选「取消」→ 零写盘、意见保持 pending。"""
    _info, store, mem, issues = _project(tmp_path)
    a = issues.add("check", "标题甲", "详情甲", QUOTE_A, chapter=1)
    b = issues.add("check", "标题乙", "详情乙", QUOTE_B, chapter=1)
    llm = FakeLLM([fix_payload((a.id, NEW_A), (b.id, NEW_B))])
    ui, _seen = _ui(indices=(1,))

    text = await _run(store, mem, [a, b], llm, ui)

    assert text == "已取消，意见保持 pending"
    assert store.read_chapter(1).body == BODY
    assert [i.id for i in issues.list("pending")] == [a.id, b.id]


async def test_fix_chapter_approval_cancelled(tmp_path):
    """审批抛 ApprovalCancelled → 同「取消」：零写盘、保持 pending。"""
    _info, store, mem, issues = _project(tmp_path)
    a = issues.add("check", "标题甲", "详情甲", QUOTE_A, chapter=1)
    llm = FakeLLM([fix_payload((a.id, NEW_A))])
    ui, _seen = _ui(raiser=ApprovalCancelled())

    text = await _run(store, mem, [a], llm, ui)

    assert text == "已取消，意见保持 pending"
    assert store.read_chapter(1).body == BODY
    assert [i.id for i in issues.list("pending")] == [a.id]


async def test_fix_chapter_llm_failure_writes_nothing(tmp_path):
    """LLM 失败 → 零写盘、意见保持 pending、失败原因走 reason_label（禁瞬时词）。"""
    _info, store, mem, issues = _project(tmp_path)
    a = issues.add("check", "标题甲", "详情甲", QUOTE_A, chapter=1)
    llm = FakeLLM(["完全不是 JSON", "依然不是 JSON"])
    ui, _seen = _ui()

    text = await _run(store, mem, [a], llm, ui)

    assert text.startswith("修复失败（")
    assert "意见保持 pending" in text
    assert store.read_chapter(1).body == BODY
    assert [i.id for i in issues.list("pending")] == [a.id]


async def test_fix_chapter_no_replacements_stays_pending(tmp_path):
    """模型给了空替换集 → 「无可用替换」、零写盘、保持 pending。"""
    _info, store, mem, issues = _project(tmp_path)
    a = issues.add("check", "标题甲", "详情甲", QUOTE_A, chapter=1)
    llm = FakeLLM([fix_payload()])
    ui, _seen = _ui()

    text = await _run(store, mem, [a], llm, ui)

    assert text == "无可用替换，意见保持 pending"
    assert store.read_chapter(1).body == BODY
    assert [i.id for i in issues.list("pending")] == [a.id]


async def test_fix_chapter_unknown_id_writes_nothing(tmp_path):
    """模型自造 id → 不认（不猜它是哪条意见）→ 无可用替换、零写盘。"""
    _info, store, mem, issues = _project(tmp_path)
    a = issues.add("check", "标题甲", "详情甲", QUOTE_A, chapter=1)
    llm = FakeLLM([fix_payload(("9999", NEW_A))])
    ui, _seen = _ui()

    text = await _run(store, mem, [a], llm, ui)

    # 观测要**说清是哪一种**（独立复审 D4）：id 对不上 ≠ 模型没干活
    assert text.startswith("无可用替换，意见保持 pending")
    assert "1 条的意见编号与台账不匹配" in text
    assert store.read_chapter(1).body == BODY
    assert [i.id for i in issues.list("pending")] == [a.id]


async def test_fix_chapter_unlocatable_quote_skipped(tmp_path):
    """引文在正文里定位不到（正文被人手改过）→ 跳过该条、保持 pending，其余照改。"""
    _info, store, mem, issues = _project(tmp_path)
    a = issues.add("check", "标题甲", "详情甲", "根本不存在的片段", chapter=1)
    b = issues.add("check", "标题乙", "详情乙", QUOTE_B, chapter=1)
    llm = FakeLLM([fix_payload((a.id, NEW_A), (b.id, NEW_B)), extract_payload()])
    ui, _seen = _ui()

    text = await _run(store, mem, [a, b], llm, ui)

    assert "改写 1 段" in text
    assert "标记 resolved 1 条意见" in text
    assert store.read_chapter(1).body == f"甲拔刀，乙点火。夜风吹过山寨。{NEW_B}。"
    assert [i.id for i in issues.list("pending")] == [a.id]
    assert [i.id for i in issues.list("resolved")] == [b.id]


async def test_fix_chapter_overlong_replacement_dropped(tmp_path):
    """替换文本超长（模型把一段扩写成一段）→ 丢弃该条 → 零写盘、意见保持 pending。"""
    _info, store, mem, issues = _project(tmp_path)
    a = issues.add("check", "标题甲", "详情甲", QUOTE_A, chapter=1)
    overlong = "x" * (int(len(QUOTE_A) * 1.8) + 41)
    llm = FakeLLM([fix_payload((a.id, overlong))])
    ui, _seen = _ui()

    text = await _run(store, mem, [a], llm, ui)

    # 超长与「id 对不上」要**分开说**（独立复审 D4）
    assert text.startswith("无可用替换，意见保持 pending")
    assert "1 条为空或超出长度上限" in text
    assert store.read_chapter(1).body == BODY
    assert [i.id for i in issues.list("pending")] == [a.id]


async def test_fix_chapter_same_quote_twice_applies_one(tmp_path):
    """两条意见指向**同一段** → 只改一次（重叠的段不能都替换），另一条保持 pending。"""
    _info, store, mem, issues = _project(tmp_path)
    a = issues.add("check", "标题甲", "详情甲", QUOTE_A, chapter=1)
    b = issues.add("check", "标题乙", "详情乙", QUOTE_A, chapter=1)
    llm = FakeLLM([fix_payload((a.id, NEW_A), (b.id, NEW_B)), extract_payload()])
    ui, _seen = _ui()

    text = await _run(store, mem, [a, b], llm, ui)

    assert "改写 1 段" in text
    body = store.read_chapter(1).body
    assert NEW_A in body
    assert NEW_B not in body            # 重叠的那条被跳过
    assert [i.id for i in issues.list("pending")] == [b.id]
    assert [i.id for i in issues.list("resolved")] == [a.id]


async def test_fix_chapter_missing_chapter_stays_pending(tmp_path):
    """章不存在 → 如实说明、零写盘、保持 pending。"""
    _info, store, mem, issues = _project(tmp_path)
    a = issues.add("check", "标题甲", "详情甲", QUOTE_A, chapter=9)
    llm = FakeLLM([])
    ui, _seen = _ui()

    text = await _run(store, mem, [a], llm, ui, chapter=9)

    assert text == "第9章未找到，意见保持 pending"
    assert llm.calls == []
    assert [i.id for i in issues.list("pending")] == [a.id]


# ------------------------------------------------------------------ 独立复审的修复（D2/D4/D6/D7）

async def test_fix_chapter_identical_replacement_keeps_pending(tmp_path):
    """**D2**：替换文本与引文**逐字相同** → 等于没改 → 该条**不进 spans**、
    意见**保持 pending**，观测**不得**说「已修改」。

    不护栏的后果（独立复审实测）：正文一个字节没动，意见却被标 resolved，
    观测还谎称「已修改第 1 章：改写 1 段」——契约 13 禁止的「静默消失」。
    """
    _info, store, mem, issues = _project(tmp_path)
    a = issues.add("check", "标题甲", "详情甲", QUOTE_A, chapter=1)
    llm = FakeLLM([fix_payload((a.id, QUOTE_A))])       # 替换 = 原文
    ui, _seen = _ui()

    text = await _run(store, mem, [a], llm, ui)

    assert "已修改" not in text
    assert "1 条的替换文本与原文相同" in text
    assert store.read_chapter(1).body == BODY           # 正文逐字未动
    assert [i.id for i in issues.list("pending")] == [a.id]
    assert issues.list("resolved") == []


async def test_fix_chapter_id_zero_padding_normalized(tmp_path):
    """**D4**：模型把 id 写成 ``1``（台账里是 ``0001``）→ **认得出**是同一条意见。

    不归一的后果（独立复审实测）：整批替换被当成「模型自造 id」丢弃 → ``/fix`` 空转，
    且提示语里没有任何指向 id 不匹配的线索。
    """
    _info, store, mem, issues = _project(tmp_path)
    a = issues.add("check", "标题甲", "详情甲", QUOTE_A, chapter=1)
    assert a.id == "0001"                                  # 台账 id 是零填充的
    llm = FakeLLM([fix_payload(("1", NEW_A))])              # 模型去掉了零填充
    ui, _seen = _ui()

    text = await _run(store, mem, [a], llm, ui)

    assert "改写 1 段" in text
    assert NEW_A in store.read_chapter(1).body
    assert [i.id for i in issues.list("resolved")] == [a.id]


async def test_fix_chapter_write_failure_not_reported_as_modified(tmp_path):
    """**D6**：写盘 0 段 → 观测**不得**说「已修改第 N 章」（一个字节都没写）。"""
    _info, store, mem, issues = _project(tmp_path)
    a = issues.add("check", "标题甲", "详情甲", QUOTE_A, chapter=1)
    llm = FakeLLM([fix_payload((a.id, NEW_A))])
    ui, _seen = _ui()

    # 让写盘闸失败：模拟「章文件定位失败」
    import harness.check.fix as fix_mod
    orig = fix_mod._rewrite_spans_in_place
    fix_mod._rewrite_spans_in_place = lambda *a, **k: False
    try:
        text = await _run(store, mem, [a], llm, ui)
    finally:
        fix_mod._rewrite_spans_in_place = orig

    assert "已修改" not in text
    assert "未改写" in text
    assert [i.id for i in issues.list("pending")] == [a.id]


async def test_group_by_chapter_skips_non_numeric_chapter():
    """**D7**：台账里的章号被手改成「第一章」→ **跳过**（不抛），REPL 不带栈崩掉。"""
    from harness.check.fix import group_by_chapter

    class _Fake:
        id = "0001"
        chapter = "第一章"
        quote = "夜风吹过山寨"

    assert group_by_chapter([_Fake()]) == {}


async def test_fix_chapter_writes_once_for_all_spans(tmp_path):
    """**D8**：一章多段改写 → **一次写盘**（不是每段一次）。

    逐段写 N 次时，中途崩溃会留下**半改写**的章：已改的段进了盘、台账一条没动 →
    重跑时那些引文定位不到 → 意见永远卡在 pending（独立复审 D8）。
    """
    _info, store, mem, issues = _project(tmp_path)
    a = issues.add("check", "标题甲", "详情甲", QUOTE_A, chapter=1)
    b = issues.add("check", "标题乙", "详情乙", QUOTE_B, chapter=1)
    llm = FakeLLM([fix_payload((a.id, NEW_A), (b.id, NEW_B)), extract_payload()])
    ui, _seen = _ui()

    writes = []
    import harness.check.fix as fix_mod
    orig = fix_mod._rewrite_spans_in_place

    def spy(store_, doc, spans):
        writes.append(len(spans))
        return orig(store_, doc, spans)

    fix_mod._rewrite_spans_in_place = spy
    try:
        text = await _run(store, mem, [a, b], llm, ui)
    finally:
        fix_mod._rewrite_spans_in_place = orig

    assert writes == [2]                  # 一次写盘，两段一起
    assert "改写 2 段" in text
    body = store.read_chapter(1).body
    assert NEW_A in body and NEW_B in body
    assert {i.id for i in issues.list("resolved")} == {a.id, b.id}


def test_rewrite_spans_in_place_preserves_crlf_and_frontmatter(tmp_path):
    """**D8**：一次写盘时，盘上的 front-matter 与 **CRLF 行尾**原样保留。"""
    from harness.audit.fix import _rewrite_spans_in_place
    from harness.store.project import ProjectStore

    store = ProjectStore(tmp_path)
    store.write_chapter(ChapterDoc(seq=1, meta={"seq": 1, "title": "风雪夜"},
                                   body="甲。\n夜风吹过山寨。\n丙。"))
    path = store.chapter_file_path(1)
    raw = path.read_bytes().decode("utf-8")
    path.write_bytes(raw.replace("\n", "\r\n").encode("utf-8"))

    doc = store.read_chapter(1)
    assert "\r\n" in path.read_bytes().decode("utf-8")

    ok = _rewrite_spans_in_place(store, doc, [(doc.body.index("夜风吹过山寨"),
                                               doc.body.index("夜风吹过山寨") + len("夜风吹过山寨"),
                                               # **跨行**的替换：单行替换根本不触发行尾转换，
                                               # 那样这条断言会「假绿」（变异检验 R10 的教训）
                                               "晨风掠过\n那座幽暗的山寨")])
    assert ok
    out = path.read_bytes().decode("utf-8")
    assert "晨风掠过" in out and "那座幽暗的山寨" in out
    # 行尾风格保留：**全文**没有裸 LF（断言要够强——只查「含 \r\n」会被别处的 CRLF 骗过）
    assert "\n" not in out.replace("\r\n", "")
    assert out.startswith("---")             # front-matter 保留
    assert "甲。" in out and "丙。" in out    # 未命中的部分逐字不动


def test_rewrite_spans_in_place_refuses_when_body_changed(tmp_path):
    """**D8**：盘上正文与内存里的不一致 → **一个字节都不写**（失败关闭闸）。"""
    from harness.audit.fix import _rewrite_spans_in_place
    from harness.store.project import ProjectStore

    store = ProjectStore(tmp_path)
    store.write_chapter(ChapterDoc(seq=1, meta={"seq": 1, "title": "风雪夜"},
                                   body="甲。\n夜风吹过山寨。\n丙。"))
    doc = store.read_chapter(1)
    path = store.chapter_file_path(1)
    path.write_text(path.read_text(encoding="utf-8").replace("甲。", "被人改过了。"),
                    encoding="utf-8")
    before = path.read_bytes()

    assert not _rewrite_spans_in_place(store, doc, [(0, 1, "改")])
    assert path.read_bytes() == before        # 一个字节都没写



# ------------------------------------------------------------------ 提示词

async def test_fix_chapter_prompt_carries_vars(tmp_path):
    """提示词带三变量：章节标题、章节正文、意见清单（含 id 与引文）。"""
    _info, store, mem, issues = _project(tmp_path)
    a = issues.add("check", "标题甲", "详情甲", QUOTE_A, chapter=1)
    llm = FakeLLM([fix_payload((a.id, NEW_A)), extract_payload()])
    ui, _seen = _ui()

    await _run(store, mem, [a], llm, ui)

    prompt = llm.calls[0]["prompt"]
    assert "第1章" in prompt              # 章节标题
    assert "甲拔刀" in prompt              # 章节正文
    assert a.id in prompt                 # 意见编号（模型按 id 指认）
    assert "标题甲" in prompt and "详情甲" in prompt
    assert QUOTE_A in prompt              # 待替换原文
    assert "{{" not in prompt
    # 路由参数按 WRITING_ROUTING 发出
    assert llm.calls[0]["model"] == "m-std"
    assert llm.calls[0]["temperature"] == 0.3
    assert llm.calls[0]["max_tokens"] == 4096

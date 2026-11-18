# MemoryStore：记忆提取主链（P4 Task 5）
"""extract_chapter_memory 语义钉死（seed memory_service.py:53-133 提取 +:193-263 投影）：

- 五键单次调用（task.memory_extract，路由 standard/0.3/4096）→ 解析 → 先清后建写
  ``memory/<seq>.md`` 四层 + ``state.md`` 现状投影；
- 幂等（Review Focus #1）：同章跑两次 purge 生效——文件无重复条目、state 无重复行；
- 无名册角色过滤（裁定 1）：character_events 与 state_updates.characters 中非名册
  角色名一律不进记忆文件/现状（LLM 幻觉角色名不得污染 state.md）；
- 写入侧消毒（裁定 2）：LLM 字段值含钉死分隔符（；/｜）的先替换为安全字符再写盘；
- 出场 seen 合成（裁定 3）：apply 前从 character_events 合成 seen 条目
  （last_seen=本章 seq），未给现状的出场角色 last_seen 也推进；
- 提取失败不 raise：status="failed"（reason 过 harness.reasons 映射表，禁瞬时词）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.config import ProviderConfig, Settings
from harness.llm.client import CompletionUsage, LLMError
from harness.memory.extract import (
    CHAPTER_BROKEN_LABEL,
    CHAPTER_MISSING_LABEL,
    REEXTRACT_FAIL_PREFIX,
    REEXTRACT_OK_TEXT,
    cast_cards_text,
    cast_roster,
    extract_chapter_memory,
    reextract_chapter,
    reextract_failed,
    reextract_reason,
)
from harness.memory.store import MemoryStore
from harness.prompts.loader import PromptLoader
from harness.store.project import ChapterDoc, ProjectStore


class _Resp:
    def __init__(self, text):
        self.text = text
        self.usage = CompletionUsage(prompt_tokens=1, completion_tokens=1)


class FakeLLM:
    """complete 顺序脚本：str 项为响应文本；Exception 项直接 raise。"""

    def __init__(self, script):
        self._script = list(script)
        self.calls: list[str] = []

    async def complete(self, messages, *, model, temperature, max_tokens):
        self.calls.append(messages[-1]["content"])
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return _Resp(item)


def make_settings():
    return Settings(
        library_root=Path("x"),
        providers={"test": ProviderConfig(base_url="https://api.test", api_key="k",
                                          lite="m", standard="m", pro="m")},
        default_provider="test",
        default_tier="standard",
        web_verify=True,
    )


CAST = [
    {"name": "甲", "role_type": "主角", "profile": {"personality": "冷静", "ability": "刀法"}},
    {"name": "乙", "role_type": "配角", "profile": {"personality": "莽"}},
]


def doc(body="甲拔刀，乙点火。夜风吹过山寨。", seq=1, title="题一"):
    return ChapterDoc(seq=seq, meta={"seq": seq, "title": title}, body=body)


def extract_payload() -> str:
    """五键全量 fixture：events×2（其一含名册外角色「幻觉丙」）/character_events×2
    （乙正常 + 幻觉丙跳过）/info/promise/state（甲全字段 + 幻觉丁跳过 + world×1）。"""
    return json.dumps({
        "events": [
            {"title": "夜探山寨", "summary": "甲潜入山寨", "location": "山寨",
             "time_marker": "深夜", "characters": ["甲", "幻觉丙"],
             "new_info": ["布防图"], "foreshadow": "后山密道"},
            {"title": "河边点火", "summary": "乙生起火堆", "location": "河边",
             "time_marker": "", "characters": ["乙"], "new_info": [], "foreshadow": ""},
        ],
        "character_events": [
            {"character": "甲", "summary": "翻进寨墙", "quotes": "就是这儿",
             "acted": "潜入", "felt": "紧张", "got_lost": "", "secrets": "身怀血仇"},
            {"character": "幻觉丙", "summary": "不该出现的人", "quotes": "",
             "acted": "", "felt": "", "got_lost": "", "secrets": ""},
        ],
        "info_updates": [
            {"title": "布防图", "content": "山寨布防全貌",
             "revealed_to": ["甲", "幻觉丙"]},
        ],
        "promise_updates": [
            {"kind": "foreshadow", "content": "后山密道终将启用", "by_character": "甲",
             "status": "pending", "resolution": ""},
        ],
        "state_updates": {
            "characters": [
                {"name": "甲", "realm": "筑基后期", "level": 12, "location": "山寨",
                 "affiliation": "刀客", "status": "alive", "notes": "负伤"},
                {"name": "幻觉丁", "realm": "化神", "location": "九霄",
                 "affiliation": "", "status": "", "notes": ""},
            ],
            "world": [
                {"kind": "territory", "key": "青石城", "label": "青石城",
                 "value": "二级镇 · 粮320石", "importance": 4},
            ],
        },
    }, ensure_ascii=False)


@pytest.fixture
def mem(tmp_path):
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "state.md").write_text(
        "# 现状快照\n\n## 角色现状\n\n## 世界现状\n", encoding="utf-8")
    return MemoryStore(tmp_path)


def loader(home):
    (home / ".harness" / "prompts").mkdir(parents=True, exist_ok=True)
    return PromptLoader(home_dir=home)


# ---------------------------------------------------------------- 主链

async def test_extract_writes_all_layers(mem, tmp_path):
    """五键 → 四层落 memory/0001.md（五节结构）+ state.md 投影（角色+世界）。"""
    llm = FakeLLM([extract_payload()])
    status = await extract_chapter_memory(
        mem=mem, llm=llm, prompts=loader(tmp_path / "home"), settings=make_settings(),
        doc=doc(), cast_cards=CAST)
    assert status == "extracted"

    text = (mem.root / "memory" / "0001.md").read_text(encoding="utf-8")
    assert "# 第1章记忆《题一》" in text
    assert text.count("- [全知] ") == 2                  # events 两条都在
    assert "### 甲" in text and "逐字：「就是这儿」" in text
    assert "- 布防图：山寨布防全貌（revealed_to：甲；源自第1章）" in text  # 幻觉丙被滤
    assert "- 甲 知道「布防图」（第1章起）" in text
    assert "[foreshadow] 后山密道终将启用——甲（第1章埋；状态 pending；）" in text

    state = (mem.root / "memory" / "state.md").read_text(encoding="utf-8")
    assert "- 甲｜status: alive｜realm: 筑基后期｜level: 12｜location: 山寨" in state
    assert "notes: 负伤" in state
    assert "- [territory] 青石城｜青石城：二级镇 · 粮320石（importance 4；updated 第1章）" in state
    # 名册外角色不得进 state.md（裁定 1：防永久污染）
    assert "幻觉丁" not in state and "幻觉丙" not in text


async def test_extract_prompt_renders_cast_and_body(mem, tmp_path):
    """提示词 = 逐字模板渲染：cast_cards 名册行（seed :69-71 口径）+ 章节正文。"""
    llm = FakeLLM([extract_payload()])
    await extract_chapter_memory(
        mem=mem, llm=llm, prompts=loader(tmp_path / "home"), settings=make_settings(),
        doc=doc(body="甲拔刀。"), cast_cards=CAST)
    prompt = llm.calls[0]
    assert "你是小说记忆整理员" in prompt
    assert "【甲】(主角) " in prompt and '"personality": "冷静"' in prompt
    assert "【乙】(配角)" in prompt
    assert "【章节正文】\n甲拔刀。" in prompt


async def test_extract_purge_before_write_idempotent(mem, tmp_path):
    """Review Focus #1：同章跑两次（措辞不同）→ 文件无重复条目、state 无重复行。"""
    second = json.dumps({"events": [{"title": "二次提取", "summary": "s", "location": "l",
                                     "time_marker": "", "characters": ["甲"],
                                     "new_info": [], "foreshadow": ""}],
                         "character_events": [], "info_updates": [], "promise_updates": [],
                         "state_updates": {"characters": [], "world": []}},
                        ensure_ascii=False)
    for payload in (extract_payload(), second):
        llm = FakeLLM([payload])
        status = await extract_chapter_memory(
            mem=mem, llm=llm, prompts=loader(tmp_path / "home"),
            settings=make_settings(), doc=doc(), cast_cards=CAST)
        assert status == "extracted"

    text = (mem.root / "memory" / "0001.md").read_text(encoding="utf-8")
    assert text.count("- [全知] ") == 1                   # 先清后建：旧条目全清
    assert "二次提取" in text and "夜探山寨" not in text
    assert text.count("### 甲") == 0                      # 第二次无角色经历，旧的不残留
    state = (mem.root / "memory" / "state.md").read_text(encoding="utf-8")
    assert state.count("- 甲｜") == 1                     # state 无重复行


async def test_extract_skips_unknown_characters(mem, tmp_path, caplog):
    """裁定 1：character_events/state_updates 中的名册外角色名一律跳过（含日志）。"""
    llm = FakeLLM([extract_payload()])
    with caplog.at_level("WARNING", logger="memory.extract"):
        status = await extract_chapter_memory(
            mem=mem, llm=llm, prompts=loader(tmp_path / "home"),
            settings=make_settings(), doc=doc(), cast_cards=CAST)
    assert status == "extracted"
    assert "### 幻觉丙" not in (mem.root / "memory" / "0001.md").read_text(encoding="utf-8")
    assert "幻觉丁" not in (mem.root / "memory" / "state.md").read_text(encoding="utf-8")
    assert any("幻觉丙" in r.message for r in caplog.records)


async def test_extract_sanitizes_pinned_separators(mem, tmp_path):
    """裁定 2：字段值含钉死分隔符（；/｜）的写入前替换为安全字符（防解析错档）。"""
    payload = json.dumps({"events": [{"title": "t", "summary": "s",
                                      "location": "A；B｜C", "time_marker": "",
                                      "characters": [], "new_info": [], "foreshadow": ""}],
                          "character_events": [], "info_updates": [], "promise_updates": [],
                          "state_updates": {"characters": [
                              {"name": "甲", "location": "东；城｜西", "status": "alive"}],
                              "world": []}}, ensure_ascii=False)
    llm = FakeLLM([payload])
    await extract_chapter_memory(
        mem=mem, llm=llm, prompts=loader(tmp_path / "home"), settings=make_settings(),
        doc=doc(), cast_cards=CAST)
    # 写盘后按钉死格式回读：location 不再错档到 time_marker
    data = mem.read_chapter_memory(1)
    assert data["events"][0]["location"] == "A;B|C"
    state = mem.read_state()
    assert state["characters"]["甲"]["location"] == "东;城|西"


async def test_extract_seen_synthesis_advances_last_seen(mem, tmp_path):
    """裁定 3：乙在 character_events 出场但模型没给现状 → seen 合成使 last_seen 推进。"""
    llm = FakeLLM([extract_payload()])
    await extract_chapter_memory(
        mem=mem, llm=llm, prompts=loader(tmp_path / "home"), settings=make_settings(),
        doc=doc(), cast_cards=CAST)
    state = mem.read_state()
    assert state["characters"]["乙"]["last_seen"] == 1   # 出场即记账（seed :251-260）
    assert state["characters"]["乙"]["realm"] == ""      # 其他字段不给（解析层钉死键位空值）


# ---------------------------------------------------------------- 失败路径

async def test_extract_failure_bad_json_returns_failed(mem, tmp_path):
    """LLM 产出不可解析（两轮扩额后仍 bad_json）→ status="failed"、不写盘、不 raise。"""
    llm = FakeLLM(["不是 JSON", "还是不是 JSON"])
    status = await extract_chapter_memory(
        mem=mem, llm=llm, prompts=loader(tmp_path / "home"), settings=make_settings(),
        doc=doc(), cast_cards=CAST)
    assert status == "failed"
    assert not (mem.root / "memory" / "0001.md").exists()
    assert (mem.root / "memory" / "state.md").read_text(encoding="utf-8") \
        == "# 现状快照\n\n## 角色现状\n\n## 世界现状\n"


async def test_extract_failure_api_reason_mapped(mem, tmp_path, caplog):
    """api 类瞬时根因 → status="failed"，日志经映射表（禁瞬时词上屏）。"""
    llm = FakeLLM([LLMError("boom", transient=True, kind="timeout")])
    from harness.agent.loop import _EXTRA_TRANSIENT_MARKERS
    from harness.run import constants
    markers = constants.TRANSIENT_MARKERS + _EXTRA_TRANSIENT_MARKERS
    with caplog.at_level("WARNING", logger="memory.extract"):
        status = await extract_chapter_memory(
            mem=mem, llm=llm, prompts=loader(tmp_path / "home"),
            settings=make_settings(), doc=doc(), cast_cards=CAST)
    assert status == "failed"
    warn = [r.message for r in caplog.records if "记忆提取失败" in r.message]
    assert warn and all(m not in warn[0] for m in markers)


# ---------------------------------------------------------------- cast_cards 组装

def test_cast_cards_text_seed_format():
    """seed :69-71 口径：【名】(role_type) {json profile[:200]} 逐行；坏卡跳过。"""
    cards = [{"name": "甲", "role_type": "主角", "profile": {"a": "x"}},
             {"name": "乙", "role_type": "", "profile": None},
             {"name": " 丙 ", "role_type": None, "profile": {"b": 1}},
             {"role_type": "无名"}]
    text = cast_cards_text(cards)
    assert text.splitlines() == [
        "【甲】(主角) {\"a\": \"x\"}",
        "【乙】(角色) ",
        "【丙】(角色) {\"b\": 1}",
    ]
    assert cast_cards_text([]) == ""


# ---------------------------------------------------------------- 统一提取入口 reextract_chapter（P5 Task 4）

@pytest.fixture
def store(tmp_path):
    """项目 store（与 mem 同一根）：重提取入口按 seq 读**盘上**正文。"""
    return ProjectStore(tmp_path)


async def _reextract(store, mem, llm, tmp_path, seq=1, cast_cards=CAST):
    return await reextract_chapter(store=store, mem=mem, llm=llm,
                                   prompts=loader(tmp_path / "home"),
                                   settings=make_settings(), seq=seq,
                                   cast_cards=cast_cards)


def _markers() -> tuple:
    """分类契约瞬时词表全集（观测禁含任何成员，含中文同义词）。"""
    from harness.agent.loop import _EXTRA_TRANSIENT_MARKERS
    from harness.run import constants
    return constants.TRANSIENT_MARKERS + _EXTRA_TRANSIENT_MARKERS


async def test_reextract_idempotent(mem, store, tmp_path):
    """同章连调两次（两次措辞不同的 payload）→ memory/0001.md 无重复条目、
    state.md 无重复行（先清后建，幂等）。"""
    store.write_chapter(doc())
    second = json.dumps({"events": [{"title": "二次提取", "summary": "s", "location": "l",
                                     "time_marker": "", "characters": ["甲"],
                                     "new_info": [], "foreshadow": ""}],
                         "character_events": [], "info_updates": [], "promise_updates": [],
                         "state_updates": {"characters": [], "world": []}},
                        ensure_ascii=False)
    for payload in (extract_payload(), second):
        result = await _reextract(store, mem, FakeLLM([payload]), tmp_path)
        assert result.text == REEXTRACT_OK_TEXT

    text = (mem.root / "memory" / "0001.md").read_text(encoding="utf-8")
    assert text.count("- [全知] ") == 1                   # 先清后建：旧条目全清
    assert "二次提取" in text and "夜探山寨" not in text
    state = (mem.root / "memory" / "state.md").read_text(encoding="utf-8")
    assert state.count("- 甲｜") == 1                     # state 无重复行


async def test_reextract_usage_returned(mem, store, tmp_path):
    """ToolResult.usage = 本次提取那次 LLM 调用的 usage（响应未带 usage 记 None）。"""
    store.write_chapter(doc())
    result = await _reextract(store, mem, FakeLLM([extract_payload()]), tmp_path)
    assert result.text == REEXTRACT_OK_TEXT
    assert result.usage == CompletionUsage(prompt_tokens=1, completion_tokens=1)


async def test_reextract_failure_text_no_transient(mem, store, tmp_path):
    """LLM 两次都吐非 JSON（bad_json）→ 失败文案 = 前缀 + reason_label 映射值
    （bad_json 原样透传，确定性词非瞬时词）；观测零瞬时词、不留半套记忆。"""
    store.write_chapter(doc())
    result = await _reextract(store, mem, FakeLLM(["不是 JSON", "还是不是 JSON"]), tmp_path)
    assert result.text == f"{REEXTRACT_FAIL_PREFIX}bad_json"
    assert reextract_failed(result.text) is True
    assert reextract_reason(result.text) == "bad_json"
    for marker in _markers():
        assert marker not in result.text
    assert not (mem.root / "memory" / "0001.md").exists()


async def test_reextract_missing_chapter(mem, store, tmp_path):
    """章不存在 → 「记忆提取失败：章不存在」，不 raise、零 LLM 调用。"""
    llm = FakeLLM([])
    result = await _reextract(store, mem, llm, tmp_path)
    assert result.text == f"{REEXTRACT_FAIL_PREFIX}{CHAPTER_MISSING_LABEL}"
    assert reextract_failed(result.text) is True
    assert llm.calls == []


async def test_reextract_broken_chapter(mem, store, tmp_path):
    """章 front-matter 坏档（FileFormatError）→ 「记忆提取失败：章坏档」，不 raise。"""
    (tmp_path / "chapters").mkdir(parents=True, exist_ok=True)
    (tmp_path / "chapters" / "0001-bad.md").write_text(
        "---\nfoo: [1, 2\n---\n\n正文\n", encoding="utf-8")
    llm = FakeLLM([])
    result = await _reextract(store, mem, llm, tmp_path)
    assert result.text == f"{REEXTRACT_FAIL_PREFIX}{CHAPTER_BROKEN_LABEL}"
    assert reextract_failed(result.text) is True
    assert llm.calls == []


async def test_reextract_llm_error_no_raise(mem, store, tmp_path):
    """LLM 抛 LLMError(kind="timeout") → 「记忆提取失败：调用失败」（api:{kind} 经
    reason_label 映射），不 raise、零瞬时词。"""
    store.write_chapter(doc())
    llm = FakeLLM([LLMError("boom", transient=True, kind="timeout")])
    result = await _reextract(store, mem, llm, tmp_path)
    assert result.text == f"{REEXTRACT_FAIL_PREFIX}调用失败"
    for marker in _markers():
        assert marker not in result.text


def test_reextract_helpers_on_success_text():
    """成功文案的判定/取因：reextract_failed=False、reextract_reason=""。"""
    assert reextract_failed(REEXTRACT_OK_TEXT) is False
    assert reextract_reason(REEXTRACT_OK_TEXT) == ""
    assert reextract_failed(f"{REEXTRACT_FAIL_PREFIX}章不存在") is True
    assert reextract_reason(f"{REEXTRACT_FAIL_PREFIX}章不存在") == "章不存在"


def test_cast_roster_reads_character_cards(tmp_path):
    """cast_roster（单一权威源）：全书名册 [{name, role_type, profile}]——
    profile 取角色卡 front-matter 的 profile 子 dict；无子 dict 时平铺标量键兜底；
    单卡读坏不拖垮名册。"""
    store = ProjectStore(tmp_path)
    (tmp_path / "characters").mkdir(parents=True, exist_ok=True)
    (tmp_path / "characters" / "甲.md").write_text(
        "---\nname: 甲\nrole_type: 主角\nprofile:\n  personality: 冷静\n---\n\n甲\n",
        encoding="utf-8")
    (tmp_path / "characters" / "乙.md").write_text(
        "---\nname: 乙\nrole_type: 配角\nage: 17\n---\n\n乙\n", encoding="utf-8")
    (tmp_path / "characters" / "丙.md").write_text("---\n坏: [\n---\n\n丙\n", encoding="utf-8")

    roster = cast_roster(store)
    by_name = {c["name"]: c for c in roster}
    assert set(by_name) == {"甲", "乙"}                  # 坏卡跳过（list_characters 口径）
    assert by_name["甲"]["role_type"] == "主角"
    assert by_name["甲"]["profile"] == {"personality": "冷静"}
    assert by_name["乙"]["profile"] == {"role_type": "配角", "age": 17}  # 无子 dict → 平铺标量键兜底
    assert cast_roster(store) == roster


# ---------------------------------------------------------------- 重提取按定位到的章（P5 复审遗留 1）

async def test_reextract_with_doc_uses_that_chapter(mem, store, tmp_path):
    """给了 ``doc`` → 就用它（**不**按 seq 再 glob 一次）→ 记忆落到 doc 自己的章号上。

    手写章常见「front-matter 章号 ≠ 文件名前缀」：``0001-甲.md`` 的 fm seq 是 2、
    ``0002-乙.md`` 的 fm seq 是 1。按 ``seq=2`` 再 glob 一次会读到 ``0002-乙.md``
    → 记忆刷新到**别的章**上（那一章的记忆被清掉重写，本章的记忆从不刷新）。
    """
    chapters = tmp_path / "chapters"
    chapters.mkdir(parents=True, exist_ok=True)
    (chapters / "0001-甲.md").write_text("---\nseq: 2\ntitle: 甲\n---\n\n甲的正文。\n",
                                         encoding="utf-8")
    (chapters / "0002-乙.md").write_text("---\nseq: 1\ntitle: 乙\n---\n\n乙的正文。\n",
                                         encoding="utf-8")
    jia = next(d for d in store.list_chapters() if d.meta.get("title") == "甲")
    assert jia.seq == 2 and jia.path.name == "0001-甲.md"

    result = await reextract_chapter(store=store, mem=mem, llm=FakeLLM([extract_payload()]),
                                     prompts=loader(tmp_path / "home"),
                                     settings=make_settings(), seq=jia.seq, doc=jia,
                                     cast_cards=CAST)

    assert result.text == REEXTRACT_OK_TEXT
    assert (mem.root / "memory" / "0002.md").exists()      # 甲的章号（fm seq=2）
    assert not (mem.root / "memory" / "0001.md").exists()  # 乙的记忆没被碰


async def test_reextract_without_doc_keeps_seq_glob(mem, store, tmp_path):
    """不传 ``doc`` → 行为与从前逐字不变：仍按 ``seq`` glob 读盘上正文。"""
    store.write_chapter(doc(body="甲拔刀，乙点火。夜风吹过山寨。", seq=1))
    llm = FakeLLM([extract_payload()])

    result = await reextract_chapter(store=store, mem=mem, llm=llm,
                                     prompts=loader(tmp_path / "home"),
                                     settings=make_settings(), seq=1, cast_cards=CAST)

    assert result.text == REEXTRACT_OK_TEXT
    assert "甲拔刀" in llm.calls[0]                        # 提的是盘上正文
    assert (mem.root / "memory" / "0001.md").exists()                  # 幂等

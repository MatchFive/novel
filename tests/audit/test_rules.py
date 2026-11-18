"""审计确定性规则纯函数层测试（P5 Task 1）。

全部输入为 plain data，零 I/O 零 LLM；逐条对应计划裁定 2 的 R1-R4 口径。
"""
from harness.audit.rules import (
    DECLINE_MARKERS,
    AuditFinding,
    check_digest_anchors,
    check_knowledge,
    check_monotonic,
    check_offstage,
    check_state_vs_events,
)


# ---------------------------------------------------------------- R1 数值单调
def test_monotonic_drop_without_event_flagged():
    updates = [
        {"chapter": 2, "character": "张三", "field": "level", "value": "5"},
        {"chapter": 5, "character": "张三", "field": "level", "value": "3"},
    ]
    findings = check_monotonic(updates, [])
    assert len(findings) == 1
    f = findings[0]
    assert isinstance(f, AuditFinding)
    assert f.rule == "monotonic"
    assert f.title == "数值回退"
    assert f.chapter == 5
    assert "5→3" in f.quote


def test_monotonic_drop_with_decline_event_ok():
    updates = [
        {"chapter": 2, "character": "张三", "field": "level", "value": "5"},
        {"chapter": 5, "character": "张三", "field": "level", "value": "3"},
    ]
    events = [{"chapter": 4, "character": "张三", "text": "张三重伤倒地，气息萎靡。"}]
    assert check_monotonic(updates, events) == []


def test_monotonic_non_numeric_skipped():
    updates = [
        {"chapter": 1, "character": "张三", "field": "realm", "value": "筑基期"},
        {"chapter": 2, "character": "张三", "field": "realm", "value": "金丹期"},
        {"chapter": 3, "character": "张三", "field": "level", "value": "10"},
        {"chapter": 4, "character": "张三", "field": "level", "value": "9"},
    ]
    findings = check_monotonic(updates, [])
    # 境界名不可 int 解析不报；10→9 报且仅此一条
    assert len(findings) == 1
    assert "10→9" in findings[0].quote


def test_monotonic_cross_character_isolated():
    updates = [
        {"chapter": 2, "character": "张三", "field": "level", "value": "5"},
        {"chapter": 2, "character": "李四", "field": "level", "value": "7"},
        {"chapter": 5, "character": "张三", "field": "level", "value": "3"},
        {"chapter": 5, "character": "李四", "field": "level", "value": "7"},
    ]
    findings = check_monotonic(updates, [])
    assert len(findings) == 1
    assert "张三" in findings[0].quote
    assert "李四" not in findings[0].quote


# ---------------------------------------------------------------- R2 晚出场
def test_offstage_late_appearance_flagged():
    chapters = [(7, "夜色深沉。张三突然出现在门口。")]
    offstage = [{"name": "张三", "status": "dead", "exit_chapter": 5, "aliases": None}]
    findings = check_offstage(chapters, offstage)
    assert len(findings) == 1
    f = findings[0]
    assert f.rule == "offstage"
    assert f.title == "dead角色晚出场"
    assert f.chapter == 7
    assert "张三" in f.quote
    assert "夜色" not in f.quote  # quote 是含名句子而非全段
    assert len(f.quote) <= 40


def test_offstage_alias_hit():
    chapters = [(7, "张公子推门而入。")]
    offstage = [{"name": "张三", "status": "exited", "exit_chapter": 5, "aliases": ["张公子"]}]
    findings = check_offstage(chapters, offstage)
    assert len(findings) == 1
    assert "张公子" in findings[0].quote


def test_offstage_before_exit_ok():
    chapters = [(3, "张三尚在。"), (5, "张三退场当章。")]
    offstage = [{"name": "张三", "status": "dead", "exit_chapter": 5, "aliases": None}]
    assert check_offstage(chapters, offstage) == []


def test_offstage_short_alias_ignored():
    chapters = [(7, "他排行老三。")]
    offstage = [{"name": "张三丰", "status": "dead", "exit_chapter": 5, "aliases": ["三"]}]
    assert check_offstage(chapters, offstage) == []


# ---------------------------------------------------------------- R3 知晓泄漏
def test_knowledge_leak_flagged():
    chapters = [(3, "张三看着密信，心中一惊。", ["张三"])]
    infos = [{"title": "密信内容", "terms": ["密信"], "knows": ["李四"]}]
    findings = check_knowledge(chapters, infos)
    assert len(findings) == 1
    f = findings[0]
    assert f.rule == "knowledge"
    assert f.title == "疑似知晓泄漏"
    assert f.chapter == 3
    assert "密信" in f.quote


def test_knowledge_known_character_ok():
    chapters = [(3, "张三看着密信，心中一惊。", ["张三"])]
    infos = [{"title": "密信内容", "terms": ["密信"], "knows": ["张三", "李四"]}]
    assert check_knowledge(chapters, infos) == []


def test_knowledge_term_in_other_chapter_ok():
    chapters = [
        (3, "本章没有相关物。", ["张三"]),
        (4, "密信摊在桌上。", ["李四"]),
    ]
    infos = [{"title": "密信内容", "terms": ["密信"], "knows": ["李四"]}]
    assert check_knowledge(chapters, infos) == []


# ---------------------------------------------------------------- R4 时间锚
def test_digest_anchors_inversion():
    digest = "前文摘要\n[第3章] 发生甲事\n[第2章] 发生乙事\n"
    findings = check_digest_anchors(digest)
    assert len(findings) == 1
    f = findings[0]
    assert f.rule == "timeline"
    assert f.title == "时间锚倒挂"
    assert "[第2章]" in f.quote


def test_digest_anchors_non_decreasing_ok():
    digest = "[第2章] 甲事\n[第2章] 补述\n[第5章] 乙事\n"
    assert check_digest_anchors(digest) == []


def test_state_vs_events_inversion():
    state_rows = [{"character": "张三", "updated_chapter": 3}]
    event_chapters = {"张三": [2, 5, 7]}
    findings = check_state_vs_events(state_rows, event_chapters)
    assert len(findings) == 1
    f = findings[0]
    assert f.rule == "timeline"
    assert f.title == "现状章号倒挂"
    assert f.quote == "state 张三 updated_chapter=3 < 事件最大章 7"


def test_state_vs_events_ok():
    state_rows = [
        {"character": "张三", "updated_chapter": 7},
        {"character": "李四", "updated_chapter": 1},  # 无事件记录不报
    ]
    event_chapters = {"张三": [2, 5, 7]}
    assert check_state_vs_events(state_rows, event_chapters) == []


def test_decline_markers_frozen():
    # 裁定 2 词表逐字
    assert DECLINE_MARKERS == (
        "跌落", "下降", "退化", "散功", "重伤",
        "被废", "降级", "削弱", "修为尽失", "武功全失",
    )

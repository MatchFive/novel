# 按章聚合修订（P8 Task 1）：分组与替换集校验（纯函数）
"""`group_by_chapter` / `parse_replacements` 语义钉死：

- 分组：**只收「有 chapter 且有 quote」**的 issue（全局类意见与无章意见**不参与**聚合）；
  按章号升序、组内按 id 升序；
- 替换集校验：模型按**意见 id** 指认要改哪一条（定位永远用**我们自己的 quote**）→
  自造 id 丢弃、同 id 只采纳**第一条合规的**、空替换丢弃、超长替换丢弃（长度护栏）。
"""
from __future__ import annotations

from harness.check.fix import group_by_chapter, parse_replacements
from harness.run.issues import Issue


def _issue(id_: str, *, chapter: int | None = 1, quote: str | None = "夜风吹过山寨") -> Issue:
    return Issue(id=id_, source="check", title=f"意见{id_}", detail="详情",
                 quote=quote, status="pending", created_at="2026-11-15T00:00:00+00:00",
                 chapter=chapter)


# ------------------------------------------------------------------ 分组

def test_group_by_chapter_groups_and_sorts():
    """两条同章 + 一条别章 → 按章号升序、组内按 id 升序。"""
    groups = group_by_chapter([
        _issue("0003", chapter=2),
        _issue("0002", chapter=1),
        _issue("0001", chapter=1),
    ])
    assert list(groups) == [1, 2]
    assert [i.id for i in groups[1]] == ["0001", "0002"]
    assert [i.id for i in groups[2]] == ["0003"]


def test_group_skips_global_and_chapterless():
    """全局类意见（无 quote）与无章意见**不参与**聚合（没有可定位的片段）。"""
    groups = group_by_chapter([
        _issue("0001", chapter=1, quote="夜风吹过山寨"),
        _issue("0002", chapter=1, quote=None),      # 全局类：无原文片段
        _issue("0003", chapter=None, quote="夜风吹过山寨"),   # 无章
    ])
    assert list(groups) == [1]
    assert [i.id for i in groups[1]] == ["0001"]


def test_group_empty_returns_empty():
    assert group_by_chapter([]) == {}


# ------------------------------------------------------------------ 替换集校验

def test_parse_replacements_uses_own_quote():
    """返回的 quote 取自**我们自己的 issue**——不是模型给的（模型不必复述引文）。"""
    issues = [_issue("0001")]
    out = parse_replacements({"replacements": [{"id": "0001", "replacement": "晨风掠过山寨"}]},
                             issues)
    assert out == [{"id": "0001", "quote": "夜风吹过山寨", "replacement": "晨风掠过山寨"}]


def test_parse_replacements_ignores_unknown_id():
    """模型自造 id → 丢弃（不猜它是哪条意见）。"""
    issues = [_issue("0001")]
    out = parse_replacements({"replacements": [{"id": "9999", "replacement": "随便改"}]},
                             issues)
    assert out == []


def test_parse_replacements_first_wins_per_id():
    """同一 id 出现多次 → 只采纳**第一条合规的**。"""
    issues = [_issue("0001")]
    out = parse_replacements({"replacements": [
        {"id": "0001", "replacement": "第一版"},
        {"id": "0001", "replacement": "第二版"},
    ]}, issues)
    assert [r["replacement"] for r in out] == ["第一版"]


def test_parse_replacements_drops_empty():
    """空 / 非字符串替换 → 丢弃该条（不把空替换写进正文）。"""
    issues = [_issue("0001"), _issue("0002")]
    out = parse_replacements({"replacements": [
        {"id": "0001", "replacement": "   "},
        {"id": "0002", "replacement": 42},
    ]}, issues)
    assert out == []


def test_parse_replacements_drops_overlong():
    """超长替换（> 原 quote × 1.8 + 40）→ 丢弃该条（模型把一段扩写成一段）。"""
    issues = [_issue("0001")]
    quote = "夜风吹过山寨"
    limit = int(len(quote) * 1.8) + 40
    ok = "x" * limit
    too_long = "x" * (limit + 1)
    out = parse_replacements({"replacements": [
        {"id": "0001", "replacement": too_long},
    ]}, issues)
    assert out == []
    out2 = parse_replacements({"replacements": [
        {"id": "0001", "replacement": ok},
    ]}, issues)
    assert [r["replacement"] for r in out2] == [ok]


def test_parse_replacements_bad_shape():
    """顶层不是 dict / replacements 不是 list / 条目不是 dict → 空列表（不炸）。"""
    issues = [_issue("0001")]
    assert parse_replacements(None, issues) == []
    assert parse_replacements({"replacements": "x"}, issues) == []
    assert parse_replacements({"replacements": ["x"]}, issues) == []
    assert parse_replacements({}, issues) == []

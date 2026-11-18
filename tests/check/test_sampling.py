# 分层抽样 + 章定位（P6 Task 2）
"""sample_chapters / match_chapter 语义钉死（seed
``backend/app/workflows/autopilot_runner.py:2862 _sample_chapters`` 与
``:2708 _match_chapter`` 的**口径**移植，判据不改）。

抽样判据（ConStory-Bench《Lost in the Middle》——一致性错误多集中在全书 40–70%
进度区间；旧行为取最前 N 章，书越往后写验收越只看见开头）：

- 章数 ≤ 上限 → **全抽**（小书不受影响），返回按 seq 升序；
- 长书抽样必须**覆盖 40–70% 中段**，且**必含最新已写章**（尾段问题只有看尾部才发现）；
- 确定性伪随机：同 seed + 同输入 → 两次结果相同（**不碰全局 random**）；
- 中段保底：任何 seed 下中段至少抽 4 章（防权重抽样偶发落空）；
- 章号优先（「第3章」「3」都比标题可靠），标题用**包含**语义，找不到 → None。
"""
from __future__ import annotations

import random

from harness.check.sampling import (
    SAMPLE_CHARS_PER_CHAPTER,
    SAMPLE_MAX_CHAPTERS,
    match_chapter,
    sample_chapters,
)
from harness.store.project import ChapterDoc

TITLES = {
    1: "风雪夜",
    2: "递剑",
    3: "渡口",
    4: "雨夜逃亡",
    5: "终局",
}


def _chapters(n: int) -> list[ChapterDoc]:
    """n 章（seq 从 1 起），正文各不相同、标题取自 TITLES。"""
    out: list[ChapterDoc] = []
    for i in range(1, n + 1):
        title = TITLES.get(i, f"卷{i}幕")
        out.append(ChapterDoc(seq=i,
                              meta={"seq": i, "title": title, "status": "done"},
                              body=f"这是第{i}章的正文。夜里风起，火光摇曳，江湖路远。"))
    return out


def _mid_band(n: int) -> range:
    """40–70% 位置比例对应的章号区间（seq 从 1 起）。"""
    return range(int(n * 0.4) + 1, int(n * 0.7) + 1)


# ------------------------------------------------------------------ 常量

def test_sample_constants_pinned():
    assert SAMPLE_CHARS_PER_CHAPTER == 500
    assert SAMPLE_MAX_CHAPTERS == 12


# ------------------------------------------------------------------ 抽样

def test_sample_all_when_under_limit():
    """10 章、上限 12 → 全抽、顺序按 seq 升序。"""
    picked = sample_chapters(_chapters(10), 12, seed="s")
    assert [c.seq for c in picked] == list(range(1, 11))


def test_sample_respects_limit():
    """章数超上限 → 抽满上限（不多抽）。"""
    picked = sample_chapters(_chapters(30), 12, seed="s")
    assert len(picked) == 12


def test_sample_zero_max_returns_empty():
    """上限 0 → **空列表**（不能无条件抽最新章）。"""
    assert sample_chapters(_chapters(5), 0, seed="s") == []
    assert sample_chapters(_chapters(5), -1, seed="s") == []


def test_sample_sorted_by_seq():
    """返回按 seq 升序（抽样顺序是随机的，输出顺序不是）。"""
    picked = sample_chapters(_chapters(30), 12, seed="s")
    seqs = [c.seq for c in picked]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)          # 不重复抽同一章


def test_sample_covers_middle_segment():
    """30 章 → 抽中的章**含** 40–70% 区间内的章（取最前 N 章的实现这里必红）。"""
    band = set(_mid_band(30))
    picked = sample_chapters(_chapters(30), 12, seed="mid")
    seqs = {c.seq for c in picked}
    assert len(seqs & band) >= 4
    # 旧行为（取最前 12 章）会一条都落不到中段——这里必须至少中一条
    assert seqs & band


def test_sample_middle_floor():
    """中段保底 ≥4 章：**任何 seed** 都成立（防权重抽样偶发落空）。"""
    band = set(_mid_band(30))
    for seed in ("a", "b", "c", "d", "e", "f", "g", "h", "i", "j"):
        picked = sample_chapters(_chapters(30), 12, seed=seed)
        assert len({c.seq for c in picked} & band) >= 4, seed


def test_sample_always_includes_latest():
    """最新已写章必抽（取最前 N 章的实现这里必红）。"""
    chapters = _chapters(30)
    for seed in ("a", "b", "c", "d", "e"):
        picked = sample_chapters(chapters, 12, seed=seed)
        assert max(c.seq for c in picked) == 30, seed


def test_sample_deterministic():
    """同 seed + 同输入 → 两次结果相同；不同 seed 结果可以不同但都合法。"""
    chapters = _chapters(30)
    first = [c.seq for c in sample_chapters(chapters, 12, seed="abc")]
    random.seed(20261115)                       # 干扰全局随机状态
    random.random()
    second = [c.seq for c in sample_chapters(chapters, 12, seed="abc")]
    assert first == second
    # 不同 seed：结果可以不同，但每个都合法（≤上限、含最新章、含中段）
    band = set(_mid_band(30))
    variants = set()
    for seed in ("s1", "s2", "s3", "s4", "s5"):
        seqs = {c.seq for c in sample_chapters(chapters, 12, seed=seed)}
        assert len(seqs) <= 12
        assert max(seqs) == 30
        assert len(seqs & band) >= 4
        variants.add(tuple(sorted(seqs)))
    assert len(variants) > 1                    # seed 真的参与了抽样


def test_sample_small_book_ignores_seed():
    """章数 ≤ 上限 → 全抽（与 seed 无关）。"""
    chapters = _chapters(10)
    assert ([c.seq for c in sample_chapters(chapters, 12, seed="a")]
            == [c.seq for c in sample_chapters(chapters, 12, seed="b")])
    assert [c.seq for c in sample_chapters(chapters, 12, seed="a")] == list(range(1, 11))


def test_sample_tiny_limit_keeps_latest():
    """上限极小时也守上限，且最新章必抽。"""
    picked = sample_chapters(_chapters(30), 2, seed="tiny")
    assert len(picked) <= 2
    assert max(c.seq for c in picked) == 30


# ------------------------------------------------------------------ 章定位

def test_match_chapter_by_index():
    """「第3章」/「3」→ 第 3 章。"""
    chapters = _chapters(5)
    assert match_chapter("第3章", chapters).seq == 3
    assert match_chapter("3", chapters).seq == 3
    assert match_chapter("第 3 章", chapters).seq == 3


def test_match_chapter_number_wins_over_title():
    """章号优先：定位串是数字时先按 seq 找，不被同名标题截胡。"""
    chapters = [ChapterDoc(seq=2, meta={"seq": 2, "title": "3"}, body="甲"),
                ChapterDoc(seq=3, meta={"seq": 3, "title": "渡口"}, body="乙")]
    assert match_chapter("3", chapters).seq == 3


def test_match_chapter_by_title():
    """标题匹配用包含语义（精确优先，其次双向包含）。"""
    chapters = _chapters(5)
    assert match_chapter("渡口", chapters).seq == 3
    assert match_chapter("雨夜逃亡", chapters).seq == 4
    # 包含：定位串比标题长（标题是定位串的子串）→ 仍能定位到那一章
    assert match_chapter("雨夜逃亡记", chapters).seq == 4
    # 包含：定位串是标题的子串 → 仍能定位
    chapters2 = _chapters(5) + [ChapterDoc(seq=6, meta={"seq": 6, "title": "夜奔"}, body="丙")]
    assert match_chapter("夜奔", chapters2).seq == 6


def test_match_chapter_missing():
    """找不到 → None。"""
    chapters = _chapters(5)
    assert match_chapter("第99章", chapters) is None
    assert match_chapter("不存在的标题", chapters) is None
    assert match_chapter("", chapters) is None
    assert match_chapter("   ", chapters) is None

# 来源：seed 的全局验收抽样（web_dream/backend/app/workflows/autopilot_runner.py:2862
# `_sample_chapters`）与章定位（:2708 `_match_chapter`）的**口径**移植——判据不改，
# 只把「章对象」换成本地的 ChapterDoc、把入库动作去掉（这里是纯函数）。
"""分层抽样 + 章定位串解析（纯函数：零 I/O、零 LLM、零 store 依赖）。

为什么要有抽样器：全局验收要读正文，全书正文全塞进提示词既超预算又被稀释。旧行为
是取最前 N 章（``written[:max]``），书越往后写验收越只看见开头——ConStory-Bench
《Lost in the Middle》指出一致性错误多集中在全书 **40–70% 进度区间**，所以这里照
seed 的口径做三段分层抽样：

1. **前段打底 + 中段（40–70%）加权 + 尾段必检**（最新已写章必抽——相邻章的问题
   只有看尾部才能发现）；
2. **确定性伪随机**：种子 ``f"{seed}:{n}"``（同书同进度抽样可复现，便于回归对比），
   不引入真随机、**不碰全局 random**；
3. **中段保底**：至少抽 ``min(4, 中段章数)`` 章，防权重抽样偶发落空；
4. **章数 ≤ 上限时全抽**（小书不受影响）。
"""
from __future__ import annotations

import random
import re

from harness.store.project import ChapterDoc

__all__ = [
    "SAMPLE_CHARS_PER_CHAPTER",
    "SAMPLE_MAX_CHAPTERS",
    "match_chapter",
    "sample_chapters",
]

SAMPLE_CHARS_PER_CHAPTER = 500      # 每章抽样正文字数
SAMPLE_MAX_CHAPTERS = 12            # 抽样章数上限（控制成本）

# 中段口径（ConStory-Bench）：位置比例 0.4–0.7
_MID_LO, _MID_HI = 0.4, 0.7
_MID_WEIGHT = 2                     # 中段权重（其余 1）
_MID_MIN = 4                        # 中段保底章数

# 「第3章」「第三章」「3」三种定位写法
_CHAPTER_NO = re.compile(r"第\s*(\d+|[零〇一二三四五六七八九十百千两]+)\s*章")
_BARE_NO = re.compile(r"^\s*(\d+)\s*$")

_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}


def _norm(text: str | None) -> str:
    """归一：去掉全部空白（seed ``_norm`` 同款——标题里常有空格与换行）。"""
    return "".join((text or "").split())


def _title_of(chapter: ChapterDoc) -> str:
    """章标题（front-matter 的 ``title``；没有就当空串）。"""
    meta = getattr(chapter, "meta", None)
    if not isinstance(meta, dict):
        return ""
    title = meta.get("title")
    return str(title or "")


def _cn_to_int(text: str) -> int | None:
    """中文数字 → int（十/百/千 进位）；不是中文数字 → None。"""
    if not text:
        return None
    total = 0
    current = 0
    for ch in text:
        if ch in _CN_DIGITS:
            current = current * 10 + _CN_DIGITS[ch]
        elif ch in _CN_UNITS:
            total += (current or 1) * _CN_UNITS[ch]
            current = 0
        else:
            return None
    return total + current


def _chapter_no(raw: str) -> int | None:
    """从定位串里抽章号：「第3章」「第三章」「3」→ 3；抽不出 → None。"""
    hit = _CHAPTER_NO.search(raw)
    if hit is not None:
        token = hit.group(1)
        if token.isdigit():
            return int(token)
        return _cn_to_int(token)
    bare = _BARE_NO.match(raw)
    if bare is not None:
        return int(bare.group(1))
    return None


def sample_chapters(written: list[ChapterDoc], max_chapters: int = 12, *,
                     seed: str = "") -> list[ChapterDoc]:
    """分层抽样：前段打底 + 中段（40–70%）加权 + 尾段必检（含最新已写章）。

    - 上限 ≤ 0 → **空列表**（不能无条件抽最新章——上限 0 的意思是「一章都不抽」）；
    - 章数 ≤ 上限 → **全抽**（小书不受影响）；
    - 确定性伪随机：``random.Random(f"{seed}:{n}")``（同书同进度抽样可复现，不引入真随机）；
    - 中段（位置比例 0.4–0.7）权重 2，其余权重 1；**最新已写章必抽**；
    - 中段保底：至少抽 ``min(4, 中段章数)`` 章，防权重抽样偶发落空；
    - 返回按 seq 升序。

    与 seed（autopilot_runner.py:2862）的差别只有一处：seed 假定入参 ``written``
    已按 seq 有序，本地版先按 seq 排一次（输出恒为 seq 升序，调用方不必自己保证）。
    """
    if max_chapters <= 0:
        return []
    chapters = sorted(written, key=lambda c: c.seq)
    if len(chapters) <= max_chapters:
        return list(chapters)
    n = len(chapters)
    mid_start, mid_end = int(n * _MID_LO), int(n * _MID_HI)
    weights = [_MID_WEIGHT if mid_start <= i < mid_end else 1 for i in range(n)]
    rng = random.Random(f"{seed}:{n}")
    remaining = list(range(n))
    picked: list[int] = []

    # 最新已写章必抽
    tail = remaining.pop()
    picked.append(tail)
    # 中段保底（shuffle 后按序取，仍由 rng 驱动 → 整体确定性不变）
    mid_pool = [i for i in remaining if mid_start <= i < mid_end]
    rng.shuffle(mid_pool)
    # 保底最多 min(4, 中段章数) 章；上限很小时留一个名额给最新章（最新章必抽）
    floor = min(_MID_MIN, len(mid_pool), max(max_chapters - 1, 0))
    for i in mid_pool[:floor]:
        picked.append(i)
        remaining.remove(i)
    # 其余按权重无放回抽样
    while len(picked) < max_chapters and remaining:
        total = sum(weights[i] for i in remaining)
        r = rng.uniform(0, total)
        acc = 0.0
        for i in list(remaining):
            acc += weights[i]
            if r <= acc:
                picked.append(i)
                remaining.remove(i)
                break
    picked.sort()
    return [chapters[i] for i in picked]


def mentions_chapter(text: str) -> bool:
    """定位串是否**提到了某一章**（「第3章」「第三章」）。

    用于区分两种「全局词开头」的定位串：「全局节奏」（说的是全局意思）vs
    「全书第99章」（说的是某一章，只是那章解析不到）——后者**不是**全局口径，
    解析不到就是无从回验 → 丢弃（不能借「全局」的外壳绕过 quote 回验）。
    """
    raw = (text or "").strip()
    if not raw:
        return False
    if _CHAPTER_NO.search(raw) is not None:
        return True
    return _BARE_NO.match(raw) is not None


def match_chapter(name: str, chapters: list[ChapterDoc]) -> ChapterDoc | None:
    """按定位串找章：支持「第3章」「3」「标题」三种写法；找不到 → None。

    **章号优先**（模型给的「第3章」比标题可靠——标题会被模型改写、章号不会），
    章号找不到再按标题找：先精确相等，再**包含**语义（双向包含，归一化去空白后比对，
    seed ``_match_chapter`` 同款）。

    「全局」这类定位串定位不到章 → 返回 None（由调用方按全局口径处理）。
    """
    raw = (name or "").strip()
    if not raw:
        return None
    # ① 章号优先
    no = _chapter_no(raw)
    if no is not None:
        for c in chapters:
            if c.seq == no:
                return c
    # ② 标题：精确 → 包含
    target = _norm(raw)
    if not target:
        return None
    for c in chapters:
        if _norm(_title_of(c)) == target:
            return c
    for c in chapters:
        title = _norm(_title_of(c))
        if title and (title in target or target in title):
            return c
    return None

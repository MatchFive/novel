"""提示词上下文组装（P2 Task 4）——**文件格式 → LLM 提示词**的接缝。

五个 builtin 模板声明的每个变量都在这里取值：**只经 ProjectStore 读**（本模块零 LLM、
零写盘）。渲染逐字移植自 web_dream（d:/project/web_dream/backend/app/，行号见
task-4-brief.md）：outline_service.py:199-348（units_block/chapter_line/anchors_block）、
:983-994（selfcheck 章清单渲染）、:629-637（headers 组渲染）、autopilot_runner.py:202-268
（plan_plots 变量形态）、:762-777（相邻情节承接上下文）。

与 seed 的两处口径差异（均在 brief「控制器补充」裁定）：
- ``existing_world`` 取 read_world 返回的**摘要**键 [:200]（本地摘要是更短口径）；
- ``existing_characters`` 调 character_digest 前 ``characters[:20]``——seed 的 limit=20
  截断在 brief 签名里丢了，调用侧补偿（Task 1 deferred 的落地点）。
"""
from __future__ import annotations

import json
import logging
import re

from harness.planning.pure import (
    DEFAULT_TARGET_WORDS,
    DEFAULT_TOTAL_CHAPTERS,
    UNIT_WORDS,
    character_digest,
    content_repeat,
    min_units,
    scale_block,
    story_block,
)
from harness.projects import README_FIELDS
from harness.store.frontmatter import FileFormatError
from harness.store.project import ChapterDoc, ProjectStore
from harness.text import gags_text

__all__ = [
    "anchors_block",
    "beats_vars",
    "chapter_line",
    "headers_vars",
    "plan_plots_vars",
    "plot_units_vars",
    "selfcheck_vars",
    "story_settings",
    "units_block",
]

log = logging.getLogger("planning.context")

_ANCHOR_KEYS = ("covers", "start_at", "end_at", "time_span")
_LABEL2KEY = {label: key for key, label in README_FIELDS}
_KV_RE = re.compile(r"^([^\s:：]+)[:：]\s*(.*)$")


# ================================================================ 章级渲染（逐字移植 seed）

def _covers_text(meta: dict) -> str:
    """meta["covers"] 的字符串形态：list[dict]（骨架可能存段清单）则 join event。"""
    raw = meta.get("covers")
    if isinstance(raw, list):
        return "；".join(str(u.get("event") or "").strip()
                         for u in raw if isinstance(u, dict) and str(u.get("event") or "").strip())
    return str(raw or "")


def _anchors_of(meta: dict) -> dict[str, str]:
    return {k: (_covers_text(meta) if k == "covers" else str(meta.get(k) or "")).strip()
            for k in _ANCHOR_KEYS}


def chapter_line(meta: dict) -> str:
    """喂给模型的一行章节描述（outline_service.py:284-299 逐字）。

    有锚点就写全（让并行扩写也能看到全线时空走向）：
    ``第9章 标题｜覆盖：…｜跨度：…｜起：…｜止：…``；无锚点退回「第N章 标题：目标」。
    """
    a = _anchors_of(meta)
    seq = meta.get("seq") or 0
    title = str(meta.get("title") or "")
    objective = str(meta.get("objective") or "")
    if not any(a.values()):
        return f"第{seq}章 {title}：{objective}"
    bits = [f"覆盖：{a['covers'] or objective}"]
    if a["time_span"]:
        bits.append(f"跨度：{a['time_span']}")
    if a["start_at"]:
        bits.append(f"起：{a['start_at']}")
    if a["end_at"]:
        bits.append(f"止：{a['end_at']}")
    return f"第{seq}章 {title}｜" + "｜".join(bits)


def anchors_block(meta: dict, prev: dict | None = None, next_: dict | None = None) -> str:
    """【本章时空位置】块（:326-348 逐字）——骨架已定 + 相邻章已认领内容 + ⚠ 重复提示。

    ``prev`` / ``next_`` 为同情节组内相邻章的 meta（没有即本情节首/末章）。
    ``content_repeat`` 命中时附 ⚠（要求从上一章**结束之后**写起）——只做提示，不阻断。
    """
    a = _anchors_of(meta)
    objective = str(meta.get("objective") or "")
    if not any(a.values()):
        return "【本章时空位置】骨架未提供时空锚点（老数据），按情节节拍线推进即可。"
    lines = [
        "【本章时空位置（骨架已定，必须遵守）】",
        f"  覆盖内容：{a['covers'] or objective or '（未标）'}",
        f"  时间跨度：{a['time_span'] or '（未标）'}",
        f"  开场（须在上一章结束**之后**）：{a['start_at'] or '（未标）'}",
        f"  本章收尾（下一章从这里之后开始）：{a['end_at'] or '（未标）'}",
        "【相邻章（它们已认领的内容，不得重复铺陈）】",
        f"  上一章：{chapter_line(prev) if prev is not None else '（本情节第一章）'}",
        f"  下一章：{chapter_line(next_) if next_ is not None else '（本情节最后一章）'}",
    ]
    if prev is not None:
        prev_covers = _anchors_of(prev).get("covers") or str(prev.get("objective") or "")
        if content_repeat(prev_covers, a["covers"] or objective or ""):
            lines.append(
                f"  ⚠ **与上一章重复**：上一章已覆盖「{prev_covers}」，本章又写「{a['covers']}」——"
                "本章必须从上一章**结束之后**开始（时间/地点可以跳跃），"
                "**不得回头把上一章已写完的阶段再写一遍**；确实要提，只能一句话追溯。")
    return "\n".join(lines)


def units_block(units: list[dict] | None) -> str:
    """【本章剧情描述】块（:199-228 逐字）——拆章第一步所定的段，**必须照此展开**。

    无段/段全空 event → 空串（调用方直接不拼，行为与 seed 逐字节一致）。
    """
    if not units:
        return ""
    lines = [("【本章剧情描述（拆章已定：节拍按此顺序细写，事件不得与之矛盾；"
              "人物动机/台词逻辑以描述中的状态为准，不得现编与剧情设定冲突的言行）】")]
    for i, u in enumerate(units, 1):
        if not isinstance(u, dict):
            continue
        where = "｜".join(x for x in (str(u.get("time") or "").strip(),
                                      str(u.get("location") or "").strip()) if x)
        who = "、".join(str(c) for c in (u.get("characters") or [])
                        if str(c).strip()) if isinstance(u.get("characters"), list) else ""
        event = str(u.get("event") or "").strip()
        if not event:
            continue
        head = f"  {i}." + (f" [{where}]" if where else "")
        if who:
            head += f"（出场：{who}）"
        lines.append(f"{head} {event}")
    if len(lines) == 1:
        return ""
    return "\n".join(lines) + "\n"


# ================================================================ store 读取助手

def _safe_chapters(store: ProjectStore) -> list[ChapterDoc]:
    """全书章节；单个坏章（FileFormatError）经 on_error 收集后跳过，不拖垮列表。"""
    bad: list[tuple] = []
    docs = store.list_chapters(on_error=lambda p, e: bad.append((p, e)))
    for path, exc in bad:
        log.warning("planning.context 跳过坏章: %s (%s)", path.name, exc)
    return docs


def story_settings(store: ProjectStore) -> dict[str, str]:
    """README.md 的项目级创作设定（projects.py：README 是 story_settings 唯一来源）。

    **公开名**（2026-11-15）：``run.intake`` 要读同一份口径（同一口径不留两份实现）——
    跨模块引私有名不合适，故提为公开 ``story_settings``；``_story_settings`` 保留为别名。

    只解析 README_FIELDS 六个已知标签，未设置的字段缺省留空。
    """
    readme = store.root / "README.md"
    story: dict[str, str] = {}
    if not readme.exists():
        return story
    for line in readme.read_text(encoding="utf-8").splitlines():
        m = _KV_RE.match(line.strip())
        if m and m.group(1) in _LABEL2KEY:
            story[_LABEL2KEY[m.group(1)]] = m.group(2).strip()
    return story


def _story_int(story: dict, key: str, default: int) -> int:
    try:
        value = int(float(story.get(key) or 0))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


#: 旧私有名（保留兼容；新代码用公开的 :func:`story_settings`）
_story_settings = story_settings


def _meta_int(meta: dict, key: str, default: int) -> int:
    try:
        value = int(float(meta.get(key)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _cast_text(store: ProjectStore) -> str:
    """【出场角色】渲染（seed _cast_text :474-478 逐字）：卡 profile JSON 截 200 字。

    list_characters 只回 name/role_type/aliases，profile 逐卡回读（read_character）；
    单卡读失败按无 profile 渲染，不拖垮整卷。
    """
    lines = []
    for card in store.list_characters():
        profile: dict = {}
        try:
            full = store.read_character(card["name"])
        except (FileNotFoundError, FileFormatError):
            full = {}
        if isinstance(full.get("profile"), dict):
            profile = full["profile"]
        lines.append(f"【{card['name']}】({card['role_type'] or '角色'}) "
                     f"{json.dumps(profile, ensure_ascii=False)[:200]}")
    return "\n".join(lines)


def _world_text(store: ProjectStore, limit: int = 15) -> str:
    """【世界观设定】渲染（seed _world_text :480-482 逐字）：content 截 150 字，前 limit 条。

    本地条目 content 可能为空（只写了摘要），此时退回摘要键。
    """
    return "\n".join(
        f"【{e['title']}】{(e.get('content') or e.get('summary') or '')[:150]}"
        for e in store.read_world()[:limit])


def _beats_text(plot: dict) -> str:
    """plot["beats"] 的「拍1 → 拍2」文本；list/str 容错，缺失返回空串。"""
    beats = plot.get("beats")
    if isinstance(beats, list) and beats:
        return " → ".join(str(b) for b in beats)
    if isinstance(beats, str) and beats.strip():
        return beats
    return ""


def _beats_line(plot: dict) -> str:
    """plot_units 的「  节拍线：…」行（无节拍线则空串）。"""
    text = _beats_text(plot)
    return f"\n  节拍线：{text}" if text else ""


# ================================================================ 段二：逐卷情节规划

def plan_plots_vars(store: ProjectStore, *, arc: dict, arcs_planned: int,
                    characters: list[dict]) -> dict:
    """task.auto_plan_plots 的变量组装（autopilot_runner.py:202-268 的变量形态）。

    - ``instruction``：README 项目卡已设定的字段（规划诉求：全书方向与要求）；
    - ``arcs_planned``=planned_volume_count（调用方落项目设定），缺省用弧数；
    - ``existing_world`` 取 read_world 的**摘要**键 [:200]（brief「控制器补充」裁定，
      本地摘要是比 seed content 更短的口径），前 20 条，无则「（无）」。
    """
    story = _story_settings(store)
    target_words = _story_int(story, "chapter_words", DEFAULT_TARGET_WORDS)
    total_chapters = _story_int(story, "target_chapters", DEFAULT_TOTAL_CHAPTERS)
    try:
        planned = int(arcs_planned)
    except (TypeError, ValueError):
        planned = 0
    arc_chapters = max(1, round(total_chapters / max(1, planned)))
    # 调用侧补偿：seed CharacterRepository.limit=20 的截断在 brief 签名里丢了
    # （Task 1 deferred：character_digest 本身不做卡数截断），这里 [:20] 落地点。
    cards = characters[:20]
    worlds = store.read_world()[:20]
    existing_world = "\n".join(
        f"· {e['title']}——{(e.get('summary') or '')[:200]}" for e in worlds) or "（无）"
    return {
        "instruction": "\n".join(f"{label}：{story[key]}"
                                 for key, label in README_FIELDS if story.get(key)),
        "target_words": str(target_words),
        "total_chapters": str(total_chapters),
        "arc_chapters": str(arc_chapters),
        "arc_title": str(arc.get("title") or ""),
        "arc_goal": str(arc.get("goal") or ""),
        "arc_summary": str(arc.get("summary") or ""),
        "arc_conflict": str(arc.get("conflict") or ""),
        "arc_resolution": str(arc.get("resolution") or ""),
        "existing_characters": character_digest(cards, 260),
        "existing_world": existing_world,
        "scale_block": scale_block(target_words, total_chapters, arc_chapters),
        "research": "",   # research 是 P5 联网核查链路的预留变量，P2 恒为空串（无调用方覆盖）
        "meme_bank": "",
    }


# ================================================================ 段三：情节拆剧情描述

def plot_units_vars(store: ProjectStore, *, arc: dict, plot: dict,
                    plots: list[dict], target_words: int) -> dict:
    """task.plot_units 的变量组装（outline_service.py:747-823 的承接形态）。

    - ``prev_context`` / ``next_context``：同卷相邻情节（按 seq 定位本情节）；
      前一情节已拆章节取 ``chapter_line`` 末 8 行（章 meta ``plot_id`` = "vol-seq" 命中，
      见 T6 写盘契约），未拆则「与概要衔接」；首/末情节不炸（卷首/卷末固定文案）。
    - ``existing_chapters``：全书已有章节 ``chapter_line`` 末 60 行（坏章收集跳过）。
    """
    story = _story_settings(store)
    total_chapters = _story_int(story, "target_chapters", DEFAULT_TOTAL_CHAPTERS)
    # **每卷章数用「预计总卷数」**（独立复审 M4）：段二已改用 `planned_volume_count`
    # （P13），段三若还用**实际弧数** → 同一本书段二说「每卷约 50 章」、段三说「约 333 章」
    # → 拆章模型被喂了错的容量参考。→ 两段口径统一（没有预计卷数时回落实际弧数）。
    planned = store.read_planned_volume_count()
    arc_chapters = max(1, round(total_chapters / max(1, planned or len(store.read_arcs()))))
    target = _meta_int({"v": target_words}, "v", DEFAULT_TARGET_WORDS)
    chapters = _safe_chapters(store)
    known = [chapter_line(c.meta) for c in chapters]
    chapters_by_plot: dict[str, list[str]] = {}
    for c in chapters:
        pid = c.meta.get("plot_id")
        if pid:
            chapters_by_plot.setdefault(str(pid), []).append(chapter_line(c.meta))
    seq = plot.get("seq")
    idx = next((i for i, p in enumerate(plots) if p.get("seq") == seq), None)
    if idx is None:
        idx = next((i for i, p in enumerate(plots) if p is plot), 0)
    prev_plot = plots[idx - 1] if idx > 0 else None
    next_plot = plots[idx + 1] if idx + 1 < len(plots) else None
    vol = arc.get("vol")

    def _ref(p: dict) -> str:
        return f"{vol}-{p.get('seq')}"

    if prev_plot is not None:
        prev_chs = chapters_by_plot.get(_ref(prev_plot)) or []
        prev_context = (
            f"【{prev_plot.get('title')}】{str(prev_plot.get('summary') or '')[:150]}\n"
            + ("已拆章节（本次开头必须与之衔接、不得重复）：\n"
               + "\n".join(f"  · {x}" for x in prev_chs[-8:])
               if prev_chs else "（该情节尚未拆章——开头请与它的概要衔接）"))
    else:
        prev_context = "（本情节是卷首，无上一情节）"
    if next_plot is not None:
        next_context = (
            f"【{next_plot.get('title')}】{str(next_plot.get('summary') or '')[:150]}\n"
            f"  冲突：{next_plot.get('conflict') or ''}；收束：{next_plot.get('resolution') or ''}"
            "\n（本情节结束要为它留钩子，衔接要顺，避免转折生硬）")
    else:
        next_context = "（本情节是卷末，结尾须为下一卷留钩子）"
    gags = plot.get("gags")
    plots_text = (
        f"【{plot.get('title')}】{plot.get('summary') or ''}\n"
        f"  冲突：{plot.get('conflict') or ''}；收束：{plot.get('resolution') or ''}"
        + _beats_line(plot)
        + (f"\n  已设计的梗/名场面：\n{gags_text(gags)}" if gags else ""))
    batch_note = (f"本次**只拆「{plot.get('title')}」这一个情节**"
                  f"（全卷待拆 {len(plots)} 个中的第 {idx + 1} 个）；不要规划其它情节。")
    return {
        "batch_note": batch_note,
        "arc_goal": str(arc.get("goal") or ""),
        "arc_conflict": str(arc.get("conflict") or ""),
        "arc_summary": str(arc.get("summary") or ""),
        "prev_context": prev_context,
        "plots": plots_text,
        "next_context": next_context,
        "existing_chapters": "\n".join(known[-60:]) or "（无）",
        "cast": _cast_text(store),
        "world_entries": _world_text(store),
        "target_words": str(target),
        "arc_chapters": str(arc_chapters),
        "min_units": str(min_units(target)),
    }


# ================================================================ 段四：章节标题/目标

def _unit_words(u: dict) -> int:
    """段的预估字数（seed _unit_words 同口径）：非正数回退 UNIT_WORDS。"""
    try:
        w = int(u.get("expected_words"))
    except (TypeError, ValueError):
        return UNIT_WORDS
    return w if w > 0 else UNIT_WORDS


def headers_vars(*, arc: dict, plot: dict, groups: list[list[dict]],
                 story: dict | None = None) -> dict:
    """task.chapter_headers 的变量组装（outline_service.py:619-637 的 groups 渲染）。

    Task 3 Medium carry-forward：模板契约删掉了卷目标/情节四要素行，groups 字符串**首部**
    嵌回「本情节四要素 + 卷目标」两行（reviewer 裁定，契约测试钉死）；组行 :629-637 逐字。
    """
    story = story or {}

    def _s(key: str) -> str:
        return str(plot.get(key) or "")

    lines = [
        (f"本情节：{_s('title')}｜概要：{_s('summary')[:80]}｜冲突：{_s('conflict')[:60]}"
         f"｜收束：{_s('resolution')[:60]}"),
        f"卷目标：{arc.get('goal') or ''}",
    ]
    for gi, group in enumerate(groups, 1):
        words = sum(_unit_words(u) for u in group)
        segs = " / ".join(
            ("｜".join(x for x in (str(u.get("time") or "").strip(),
                                   str(u.get("location") or "").strip()) if x)
             + " " + str(u.get("covers") or u.get("event") or "").strip()).strip()
            for u in group)
        lines.append(f"第{gi}章（含 {len(group)} 段，预估 {words} 字）：{segs}")
    return {
        "story_block": story_block(str(story.get("genre") or ""),
                                   str(story.get("tone") or ""),
                                   str(story.get("ending") or "")),
        "groups": "\n".join(lines),
    }


# ================================================================ 段五：单章细纲

def beats_vars(store: ProjectStore, *, plot: dict, chapter: ChapterDoc,
               siblings: list[ChapterDoc], units: list[dict]) -> dict:
    """task.chapter_beats 的变量组装（outline_service.py:1078-1147 的渲染形态）。

    - ``anchors_block``：同情节组内相邻章（siblings 按 seq 排序定位）——首/末章给固定文案；
    - ``all_chapters``：全书 skeleton（chapter_line 全书，坏章收集跳过）；
    - ``units_block``：**附加块**（非模板变量），渲染后经 ``render_with_blocks`` 追加；
    - ``story_block``：README 项目级创作设定（题材/基调/结局，全空则空串整节省略）。
    """
    story = _story_settings(store)
    ordered = sorted(siblings, key=lambda c: c.seq)
    idx = next((i for i, c in enumerate(ordered) if c.seq == chapter.seq), -1)
    prev = ordered[idx - 1] if idx > 0 else None
    nxt = ordered[idx + 1] if 0 <= idx < len(ordered) - 1 else None
    return {
        "story_block": story_block(story.get("genre", ""), story.get("tone", ""),
                                   story.get("ending", "")),
        "plot_title": str(plot.get("title") or ""),
        "plot_summary": str(plot.get("summary") or ""),
        "plot_conflict": str(plot.get("conflict") or ""),
        "plot_resolution": str(plot.get("resolution") or ""),
        "plot_beats": _beats_text(plot) or "（无）",
        "chapter_seq": str(chapter.seq),
        "chapter_title": str(chapter.meta.get("title") or ""),
        "chapter_objective": str(chapter.meta.get("objective") or ""),
        "anchors_block": anchors_block(chapter.meta,
                                       prev.meta if prev is not None else None,
                                       nxt.meta if nxt is not None else None),
        "all_chapters": "\n".join(chapter_line(c.meta) for c in _safe_chapters(store)),
        "cast": _cast_text(store),
        "world_entries": _world_text(store),
        "target_words": str(_meta_int(chapter.meta, "target_words", DEFAULT_TARGET_WORDS)),
        "units_block": units_block(units),
    }


# ================================================================ 段六：章节规划审核

def selfcheck_vars(*, plot: dict, chapters: list[ChapterDoc]) -> dict:
    """task.chapter_selfcheck 的变量组装（outline_service.py:983-994 的渲染逐字）。

    每章一行锚点摘要 + 逐拍编号 event（空 event 过滤后重排；无节拍给「（无节拍）」）。
    ``target_words`` 取首章 meta（T6 写盘契约带 target_words），缺省 DEFAULT_TARGET_WORDS。
    """
    ordered = sorted(chapters, key=lambda c: c.seq)
    target_words = DEFAULT_TARGET_WORDS
    for ch in ordered:
        if "target_words" in ch.meta:
            target_words = _meta_int(ch.meta, "target_words", DEFAULT_TARGET_WORDS)
            break
    lines: list[str] = []
    for ch in ordered:
        a = _anchors_of(ch.meta)
        beats = ch.meta.get("beats")
        beats = beats if isinstance(beats, list) else []
        evs = [str((b or {}).get("event") or "")
               for b in beats if isinstance(b, dict)]
        body = "\n".join(f"    {i + 1}. {e}" for i, e in enumerate(evs) if e) or "    （无节拍）"
        lines.append(
            f"第{ch.seq}章「{ch.meta.get('title') or ''}」｜目标：{ch.meta.get('objective') or ''}"
            f"｜覆盖：{a['covers'] or ch.meta.get('objective') or ''}"
            f"｜跨度：{a['time_span'] or '（未标）'}"
            f"｜起：{a['start_at'] or '（未标）'}｜止：{a['end_at'] or '（未标）'}"
            f"｜节拍 {len(beats)} 个：\n{body}")
    return {
        "plot_title": str(plot.get("title") or ""),
        "chapters": "\n".join(lines),
        "target_words": str(target_words),
    }

"""进度摘要：从 store 纯代码汇总项目现状，零 LLM。

每步注入 prompt 的确定性状态块：卷（标题+目标+情节计数）/ 章节总计数+最近一章 /
角色名册 / 世界观覆盖。全部数据经 ProjectStore 读取（P1+ 只通过 store 碰文件），
排序稳定，同一份项目文件永远渲染出同一段文本。
"""
from __future__ import annotations

import logging

from harness.store.frontmatter import FileFormatError
from harness.store.project import ProjectStore

__all__ = ["progress_digest"]

log = logging.getLogger("run.digest")

_PLOT_STATUSES = ("planned", "approved", "done")
_CHAPTER_STATUSES = ("done", "writing", "planned")


def _arc_vol(arc: dict):
    """卷号：真 int 才认（bool 是 int 子类，排除——vol: true 不当第 1 卷也不渲染「第True卷」），否则 None。"""
    vol = arc.get("vol")
    return vol if isinstance(vol, int) and not isinstance(vol, bool) else None


def progress_digest(store: ProjectStore) -> str:
    """渲染多行进度摘要；坏章/坏卡跳过（store 层已警告），不中断摘要。"""
    lines = ["【进度摘要】"]

    # 卷：seq + 标题 + 目标，每卷一行附情节 planned/approved/done 计数
    # 坏 arcs.md 不容错会让整个 agent loop 每步都炸，容忍为零卷 + 警告
    arcs_broken = False
    try:
        raw_arcs = store.read_arcs()
    except FileFormatError as exc:
        log.warning("digest 坏 arcs.md，按零卷处理: %s", exc)
        raw_arcs = []
        arcs_broken = True
    arcs = sorted((a for a in raw_arcs if isinstance(a, dict)),
                  key=lambda a: (_arc_vol(a) is not None, _arc_vol(a) or 0))
    if arcs_broken:
        # **摘要里要看得见**（2026-11-15 独立审核 M8）：只 log.warning 的话，
        # agent 看到的是「卷（0）」→ 以为没有骨架 → 回头调 plan_arcs 重产
        # → 正好撞上「坏 arcs.md 让规划链抛栈」（同一个坑的另一头）。
        lines.append("卷（0）：**卷骨架文件解析失败**（outline/arcs.md 已损坏，"
                     "请手工修正；本次摘要按零卷处理）")
    else:
        lines.append(f"卷（{len(arcs)}）：")
    for arc in arcs:
        vol = _arc_vol(arc)
        title = str(arc.get("title") or "")
        goal = str(arc.get("goal") or "").strip()
        counts = {s: 0 for s in _PLOT_STATUSES}
        if vol is not None:
            for plot in store.read_plots(vol):
                status = str(plot.get("status") or "planned")
                if status in counts:
                    counts[status] += 1
        parts = [f"- 第{vol}卷"] if vol is not None else ["- （卷号缺失）"]
        if title:
            parts.append(title)
        parts.append(f"目标：{goal or '—'}")
        parts.append("情节 " + " / ".join(f"{s} {counts[s]}" for s in _PLOT_STATUSES))
        lines.append(" ".join(parts))

    # 章节：done/writing/planned 计数 + 最近一章 seq+标题（坏章跳过）
    chapters = store.list_chapters(on_error=lambda p, exc: log.warning("digest 跳过坏章: %s", exc))
    ccounts = {s: 0 for s in _CHAPTER_STATUSES}
    for doc in chapters:
        status = str(doc.meta.get("status") or "planned")
        if status in ccounts:
            ccounts[status] += 1
    recent = (f"第{chapters[-1].seq}章《{chapters[-1].meta.get('title', '')}》"
              if chapters else "无")
    lines.append(f"章节（{len(chapters)}）："
                 + " / ".join(f"{s} {ccounts[s]}" for s in _CHAPTER_STATUSES)
                 + f"；最近：{recent}")

    # 角色：计数 + 名字（store 按文件名排序，输出稳定）
    cards = store.list_characters()
    names = "、".join(c["name"] for c in cards) or "—"
    lines.append(f"角色（{len(cards)}）：{names}")

    # 世界观：条目数 + 分类计数（按文件内出现顺序，确定性）
    entries = store.read_world()
    cats: dict[str, int] = {}
    for entry in entries:
        category = str(entry.get("category") or "未分类")
        cats[category] = cats.get(category, 0) + 1
    cat_text = " / ".join(f"{k} {v}" for k, v in cats.items()) or "—"
    lines.append(f"世界观（{len(entries)}）：{cat_text}")

    return "\n".join(lines)

"""只读工具集（附录 A P1 子集）：项目状态 / 角色名册 / 世界观条目。

handler 签名统一 (store, args) -> str；观测由 ToolRegistry.execute 过 trim_observation。
全部 read_only=True，可在任意白名单内（含并行批）使用。
"""
from __future__ import annotations

import logging

from harness.store.frontmatter import FileFormatError
from harness.store.project import ProjectStore
from harness.text import as_text

__all__ = [
    "READ_ONLY_TOOLS",
    "get_character",
    "get_project_state",
    "get_world",
    "list_characters",
    "list_world",
]

log = logging.getLogger("tools.read")


def _arc_vol(arc: dict):
    """卷号：真 int 才认（bool 是 int 子类，排除——vol: true 不当第 1 卷），否则 None（缺号/损坏）。"""
    vol = arc.get("vol")
    return vol if isinstance(vol, int) and not isinstance(vol, bool) else None

# 工具名 → handler（read.py 内全部只读；注册进 ToolRegistry 时一律 read_only=True）
READ_ONLY_TOOLS = {
    "get_project_state": None,  # 占位：下方定义后回填
}


def get_project_state(store: ProjectStore, args: dict) -> str:
    """卷/情节树 + 章节树 + 角色名列表 + 设定标题列表。坏 arcs.md 按零卷 + 注明（digest 同款容错）。"""
    lines = ["【项目状态】"]
    try:
        raw_arcs = store.read_arcs()
    except FileFormatError as exc:
        log.warning("get_project_state 坏 arcs.md，按零卷处理: %s", exc)
        raw_arcs = []
        lines.append(f"（注：outline/arcs.md 解析失败，按零卷处理：{exc}）")
    arcs = sorted((a for a in raw_arcs if isinstance(a, dict)),
                  key=lambda a: (_arc_vol(a) is not None, _arc_vol(a) or 0))
    lines.append(f"卷（{len(arcs)}）：")
    for arc in arcs:
        vol = _arc_vol(arc)
        title = str(arc.get("title") or "")
        parts = [f"- 第{vol}卷"] if vol is not None else ["- （卷号缺失）"]
        if title:
            parts.append(title)
        goal = str(arc.get("goal") or "").strip()
        if goal:
            parts.append(f"目标：{goal}")
        lines.append(" ".join(parts))
        if vol is not None:
            for plot in store.read_plots(vol):
                lines.append(f"  {plot['seq']}. {plot['title']}（{plot.get('status') or 'planned'}）")

    chapters = store.list_chapters(on_error=lambda p, exc: log.warning("get_project_state 跳过坏章: %s", exc))
    lines.append(f"章节（{len(chapters)}）：")
    for doc in chapters:
        lines.append(f"- 第{doc.seq}章《{doc.meta.get('title', '')}》（{doc.meta.get('status') or 'planned'}）")

    cards = store.list_characters()
    names = "、".join(c["name"] for c in cards) or "—"
    lines.append(f"角色（{len(cards)}）：{names}")

    entries = store.read_world()
    titles = "、".join(str(e.get("title") or "") for e in entries) or "—"
    lines.append(f"世界观（{len(entries)}）：{titles}")
    return "\n".join(lines)


def list_characters(store: ProjectStore, args: dict) -> str:
    """名册：每行 名字 / 定位 / 别名。"""
    cards = store.list_characters()
    if not cards:
        return "（无角色）"
    lines = ["【角色名册】"]
    for card in cards:
        aliases = card["aliases"]
        alias_text = "、".join(str(a) for a in aliases) if aliases else "—"
        lines.append(f"- {card['name']}｜定位：{card['role_type'] or '—'}｜别名：{alias_text}")
    return "\n".join(lines)


def get_character(store: ProjectStore, args: dict) -> str:
    """单卡全文（front-matter 键值）；未找到 → 明确提示。"""
    name = str(args.get("name") or "")
    try:
        meta = store.read_character(name)
    except FileNotFoundError:
        return f"未找到角色：{name}"
    lines = [f"【角色卡】{name}"]
    for key, value in meta.items():
        if key == "name" or value is None:
            continue
        text = as_text(value)
        if text is None:
            continue
        lines.append(f"{key}：{text}")
    return "\n".join(lines)


def list_world(store: ProjectStore, args: dict) -> str:
    """设定条目清单：每行 标题（分类）。"""
    entries = store.read_world()
    if not entries:
        return "（无世界观条目）"
    lines = ["【世界观条目】"]
    for entry in entries:
        lines.append(f"- {entry.get('title') or '（无标题）'}（{entry.get('category') or '未分类'}）")
    return "\n".join(lines)


def get_world(store: ProjectStore, args: dict) -> str:
    """按标题取条目：精确命中优先，否则首个包含匹配（大小写敏感）；未找到 → 明确提示。"""
    title = str(args.get("title") or "").strip()
    if not title:
        return "请提供设定标题（args.title 为空）"
    entries = store.read_world()
    exact = next((e for e in entries if e.get("title") == title), None)
    hit = exact if exact is not None else next((e for e in entries if title in str(e.get("title") or "")), None)
    if hit is None:
        return f"未找到世界观条目：{title}"
    match_note = "（精确匹配）" if exact is not None else "（包含匹配）"
    lines = [f"【世界观条目】{hit.get('title')}{match_note}",
             f"分类：{hit.get('category') or '未分类'}",
             f"重要度：{hit.get('importance') or '中'}"]
    summary = str(hit.get("summary") or "").strip()
    if summary:
        lines.append(f"摘要：{summary}")
    content = str(hit.get("content") or "").strip()
    if content:
        lines.append(f"正文：{content}")
    return "\n".join(lines)


READ_ONLY_TOOLS = {
    "get_project_state": get_project_state,
    "list_characters": list_characters,
    "get_character": get_character,
    "list_world": list_world,
    "get_world": get_world,
}

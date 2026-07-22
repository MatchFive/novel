"""导出服务：长/短篇导出 Markdown / TXT；项目完整数据备份（JSON）。"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Project, LongOutline, LongCharacter, LongForeshadow, LongWorldSetting,
    LongPlotNode, LongChapter, ShortSetting, ShortChapter, ShortHotspot,
)


async def _gather_long(db: AsyncSession, project_id: str) -> dict:
    async def all(model):
        res = await db.execute(select(model).where(model.project_id == project_id))
        return [dict(r.__table__.columns and {c.name: getattr(r, c.name) for c in model.__table__.columns}) for r in res.scalars().all()]
    return {
        "outlines": await all(LongOutline),
        "characters": await all(LongCharacter),
        "foreshadows": await all(LongForeshadow),
        "world": await all(LongWorldSetting),
        "plot": await all(LongPlotNode),
        "chapters": await all(LongChapter),
    }


async def _gather_short(db: AsyncSession, project_id: str) -> dict:
    res = await db.execute(select(ShortSetting).where(ShortSetting.id == project_id))
    s = res.scalars().first()
    short_setting = {c.name: getattr(s, c.name) for c in ShortSetting.__table__.columns} if s else {}
    res = await db.execute(select(ShortChapter).where(ShortChapter.project_id == project_id))
    chapters = [{c.name: getattr(r, c.name) for c in ShortChapter.__table__.columns} for r in res.scalars().all()]
    return {"setting": short_setting, "chapters": chapters}


def _build_outline_tree(outlines: list[dict]) -> list[dict]:
    by_id = {o["id"]: o for o in outlines}
    roots = []
    for o in outlines:
        pid = o.get("parent_id")
        if not pid or pid not in by_id:
            roots.append(o)
    return roots


def _render_outline_node(outlines: list[dict], node: dict, depth: int = 0) -> str:
    indent = "  " * depth
    header = {"broad": "#", "period": "##", "volume": "###"}.get(node.get("type"), "#")
    title = node.get("title") or "（无标题）"
    lines = [f"{indent}{header} {title}", f"{indent}{node.get('content', '')}", ""]
    for child in (c for c in outlines if c.get("parent_id") == node.get("id")):
        lines.append(_render_outline_node(outlines, child, depth + 1))
    return "\n".join(lines)


def render_markdown_long(title: str, data: dict) -> str:
    out = [f"# {title}", ""]
    out.append("## 角色")
    for c in data["characters"]:
        out.append(f"- **{c.get('name')}**（{c.get('status')}）：{c.get('traits')} / {c.get('ability')}")
    out.append("\n## 世界观")
    for w in data["world"]:
        out.append(f"### {w.get('category')}\n{w.get('content')}")
    out.append("\n## 大纲")
    tree = _build_outline_tree(data["outlines"])
    for node in tree:
        out.append(_render_outline_node(data["outlines"], node))
    out.append("\n## 伏笔")
    for f in data["foreshadows"]:
        out.append(f"- [{f.get('state')}] {f.get('title')}：{f.get('content')}")
    out.append("\n## 章节")
    for ch in sorted(data["chapters"], key=lambda x: x.get("order", 0)):
        out.append(f"\n### 第{ch.get('order')}章 {ch.get('title')}\n\n{ch.get('content')}")
    return "\n".join(out)


def render_markdown_short(title: str, data: dict) -> str:
    s = data["setting"]
    out = [f"# {title}", "", f"> 爽点：{s.get('core_hook','')}", ""]
    out.append("## 详细规划\n" + (s.get("detail_plan") or ""))
    out.append("\n## 正文")
    for w in (s.get("writing") or []):
        out.append(f"\n### {w.get('title')}\n\n{w.get('content')}")
    out.append("\n## 整合\n" + (s.get("integration") or ""))
    return "\n".join(out)


async def export_project(db: AsyncSession, project_id: str, fmt: str = "markdown") -> dict:
    proj = await db.get(Project, project_id)
    if not proj:
        return {"error": "项目不存在"}
    if proj.type == "long":
        data = await _gather_long(db, project_id)
        content = render_markdown_long(proj.title, data) if fmt == "markdown" else json.dumps(data, ensure_ascii=False, indent=2)
    else:
        data = await _gather_short(db, project_id)
        content = render_markdown_short(proj.title, data) if fmt == "markdown" else json.dumps(data, ensure_ascii=False, indent=2)
    return {"title": proj.title, "type": proj.type, "format": fmt, "content": content}

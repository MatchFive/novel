"""热搜获取：请求 URL + 可配置适配器（无本地爬虫）。"""
from __future__ import annotations

import time
from typing import Any, Optional

import httpx

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as app_settings
from app.core.llm_client import LLMClient
from app.models import ShortHotspot, UserSetting


class SourceAdapter:
    """根据 user_settings.hotspot_sources 中的 {url, adapter} 配置抓取热搜。"""

    @staticmethod
    async def fetch_source(url: str, adapter: dict | None = None, timeout: float = 20.0) -> list[dict]:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; NovelStudio/0.1)"}
        async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            raw = resp.text

        adapter = adapter or {}
        kind = adapter.get("kind", "json")
        title_field = adapter.get("title", "title")
        url_field = adapter.get("url")

        items: list[dict] = []
        if kind == "json":
            try:
                data = resp.json() if False else __import__("json").loads(raw)
            except Exception:
                return items
            # 支持 path 导航
            path = adapter.get("path")
            node = data
            if path:
                for p in path.split("."):
                    if isinstance(node, dict):
                        node = node.get(p, [])
            if isinstance(node, list):
                for it in node:
                    if isinstance(it, dict):
                        items.append({
                            "title": str(it.get(title_field, "")),
                            "url": str(it.get(url_field, "")) if url_field else "",
                        })
        elif kind == "html":
            # 极简：依赖 title 字段 xpath 暂不支持，回退只抓文本标题行
            pass
        return items


class HotspotService:
    def __init__(self, db: AsyncSession, llm: LLMClient):
        self.db = db
        self.llm = llm

    async def _sources(self) -> list[dict]:
        res = await self.db.execute(select(UserSetting))
        s = res.scalars().first()
        return (s.hotspot_sources if s and s.hotspot_sources else [])

    async def fetch(self, project_id: str, source_url: Optional[str] = None) -> list[dict]:
        sources = await self._sources()
        if source_url:
            sources = [{"url": source_url}] + sources
        if not sources:
            return []

        results: list[dict] = []
        for src in sources[:5]:
            url = src.get("url") if isinstance(src, dict) else src
            if not url:
                continue
            try:
                items = await SourceAdapter.fetch_source(url, src if isinstance(src, dict) else None)
            except Exception:
                items = []
            for it in items[:20]:
                h = ShortHotspot(
                    project_id=project_id,
                    source=url,
                    title=it.get("title", ""),
                    url=it.get("url", ""),
                )
                self.db.add(h)
                results.append({
                    "id": h.id, "source": h.source,
                    "title": h.title, "url": h.url,
                })
        await self.db.commit()
        return results

    async def stored(self, project_id: str) -> list[dict]:
        res = await self.db.execute(
            select(ShortHotspot).where(ShortHotspot.project_id == project_id)
            .order_by(ShortHotspot.created_at.desc()))
        rows = res.scalars().all()
        return [{
            "id": r.id, "source": r.source, "title": r.title,
            "url": r.url, "used": r.used,
            "analysis": r.analysis,
        } for r in rows]

    async def analyze(self, project_id: str, hotspot_ids: Optional[list] = None) -> list[dict]:
        rows = await self.stored(project_id)
        targets = [r for r in rows if (hotspot_ids is None or r["id"] in hotspot_ids)]
        updated = []
        for t in targets[:10]:
            msgs = [
                {"role": "system", "content": "你是小说选题顾问。针对热搜，给出适合改编为短篇小说的创作角度与爽点建议。返回中文。"},
                {"role": "user", "content": f"热搜：「{t['title']}」"},
            ]
            try:
                advice = await self.llm.chat(msgs)
            except Exception as e:
                advice = f"（分析失败：{e}）"
            res = await self.db.execute(select(ShortHotspot).where(ShortHotspot.id == t["id"]))
            obj = res.scalars().first()
            if obj:
                obj.analysis = {"advice": advice}
                await self.db.commit()
            updated.append({**t, "analysis": {"advice": advice}})
        return updated

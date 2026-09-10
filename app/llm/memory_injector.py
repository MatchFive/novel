"""记忆注入器（设计文档 6.4）：按"视点优先 + 检索命中"组装记忆注入文本。

M2 阶段实现检索逻辑（数据由 M3 提取流水线填充，为空时安全返回空串）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.db.models import Chapter
from app.db.repositories.character_repo import CharacterRepository
from app.db.repositories.memory_repo import (
    CharacterEventRepository,
    KnowledgeRepository,
    PromiseRepository,
    StoryEventRepository,
)
from app.db.session import Database


@dataclass
class MemoryBundle:
    pov_memories: str = ""
    other_char_events: str = ""
    knowledge_state: str = ""
    related_events: str = ""
    open_promises: str = ""
    hits: dict = field(default_factory=dict)  # 各区块命中数量（供 UI 展示）

    def any_hits(self) -> bool:
        return bool(self.pov_memories or self.other_char_events
                    or self.knowledge_state or self.related_events or self.open_promises)


class MemoryInjector:
    def __init__(self, db: Database):
        self.db = db

    def inject_draft_memory(self, project_id: int, chapter: Chapter | None,
                            pov_char_id: int | None = None,
                            cast_ids: list[int] | None = None,
                            keywords: list[str] | None = None,
                            memory_strength: str = "standard") -> MemoryBundle:
        """为正文生成组装记忆注入（POV 优先；空数据安全返回）。

        memory_strength：loose=仅 POV top-3（快速草稿）/ standard=默认 / strict=全量注入。
        """
        bundle = MemoryBundle()
        kws = [k.strip() for k in (keywords or []) if len(k.strip()) >= 2]
        loose = memory_strength == "loose"
        pov_limit = 3 if loose else 8

        with self.db.session_ctx() as session:
            # ① POV 角色经历档案
            pov_id = pov_char_id or (chapter.pov_char_id if chapter else None)
            if pov_id:
                repo = CharacterEventRepository(session)
                events = repo.list_for_character(pov_id, 50)
                if kws:
                    hits: list = []
                    seen: set[int] = set()
                    for kw in kws:
                        for ev in repo.search_for_character(pov_id, kw, 4):
                            if ev.id not in seen:
                                seen.add(ev.id)
                                hits.append(ev)
                    if hits:
                        events = hits
                bundle.pov_memories = self._format_events(events[:pov_limit])
                bundle.hits["pov_memories"] = min(len(events), pov_limit)

            if loose:
                return bundle  # 宽松模式：仅 POV 记忆

            # ② 其他角色相关经历 top-3
            others = [c for c in (cast_ids or []) if c != pov_id]
            if others:
                lines: list[str] = []
                for cid in others[:4]:
                    evs = CharacterEventRepository(session).list_for_character(cid, 20)
                    if kws:
                        merged: list = []
                        seen: set[int] = set()
                        for kw in kws:
                            for ev in CharacterEventRepository(session).search_for_character(cid, kw, 3):
                                if ev.id not in seen:
                                    seen.add(ev.id)
                                    merged.append(ev)
                        if merged:
                            evs = merged
                    for ev in evs[:3]:
                        lines.append(f"· {self._format_event_line(ev)}")
                bundle.other_char_events = "\n".join(lines)
                bundle.hits["other_char_events"] = len(lines)

            # ③ 知识状态（涉及信息项 + 谁知晓）
            if pov_id:
                knows = KnowledgeRepository(session).list_for_character(pov_id)
                if knows:
                    from app.db.repositories.memory_repo import InfoItemRepository
                    items = InfoItemRepository(session).list_by_project(project_id)
                    item_map = {i.id: i for i in items}
                    char = CharacterRepository(session).get(pov_id)
                    char_name = char.name if char else "该角色"
                    lines = []
                    for k in knows[:10]:
                        item = item_map.get(k.info_item_id)
                        if item:
                            state = "知道" if k.knows else "不知道"
                            lines.append(f"· {item.title}：{char_name} {state}")
                    if lines:
                        bundle.knowledge_state = "\n".join(lines)
                        bundle.hits["knowledge_state"] = len(lines)

            # ④ 相关历史事件 top-3
            if kws:
                events = StoryEventRepository(session).search(kws[0], 3)
                if events:
                    bundle.related_events = "\n".join(
                        f"· [{e.chapter_id or '?'}章] {e.summary or e.title or ''}" for e in events
                    )
                    bundle.hits["related_events"] = len(events)

            # ⑤ 未回收承诺/伏笔提醒
            promises = PromiseRepository(session).list_open(project_id)
            if promises:
                lines = []
                for pr in promises[:6]:
                    marker = "承诺" if pr.kind == "promise" else ("伏笔" if pr.kind == "foreshadow" else "悬念")
                    lines.append(f"· [{marker}] {pr.content}")
                bundle.open_promises = "\n".join(lines)
                bundle.hits["open_promises"] = len(lines)

        return bundle

    # ---------------- 格式化 ----------------
    def _format_events(self, events) -> str:
        return "\n".join(f"· {self._format_event_line(e)}" for e in events)

    def _format_event_line(self, e) -> str:
        parts = [e.summary or ""]
        if e.quotes:
            parts.append(f"台词：「{e.quotes}」")
        if e.acted:
            parts.append(f"作为：{e.acted}")
        if e.felt:
            parts.append(f"感受：{e.felt}")
        if e.got_lost:
            parts.append(f"得失：{e.got_lost}")
        if e.secrets:
            parts.append(f"秘密：{e.secrets}")
        return "；".join(p for p in parts if p)

"""为 Worker 提供相关上下文检索。

策略：结构/关系优先 > 名称/关键词匹配 > 向量语义兜底（预留）。
不依赖额外的 LLM 调用做精选，减少每次 worker 请求的延迟和 token 消耗。
"""
from __future__ import annotations

import logging
import re

from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo


logger = logging.getLogger(__name__)


_STOP_WORDS = {
    "的", "了", "是", "我", "你", "他", "她", "它", "我们", "你们", "他们",
    "这", "那", "这些", "那些", "一个", "一些", "这个", "那个", "在", "和",
    "与", "或", "就", "都", "而", "及", "对", "为", "被", "让", "把", "给",
    "上", "下", "中", "前", "后", "里", "外", "内", "间", "之", "地", "得",
    "着", "过", "到", "从", "向", "往", "于", "关于", "对于", "以及", "还是",
    "或者", "如果", "那么", "因为", "所以", "虽然", "但是", "然而", "可以",
    "需要", "想要", "应该", "进行", "完成", "实现", "创建", "修改", "调整",
    "设计", "生成", "添加", "删除", "更新", "改为", "变成", "一下", "看看",
    "请", "吧", "吗", "呢", "啊", "哦", "嗯",
}

_ENTITY_CONFIG = {
    "character": {
        "repo": repo.list_characters,
        "label": "相关角色",
        "fields": ["name", "traits", "ability", "status"],
    },
    "outline": {
        "repo": repo.list_outlines,
        "label": "相关大纲",
        "fields": ["title", "content"],
    },
    "plot": {
        "repo": repo.list_plot,
        "label": "相关剧情节点",
        "fields": ["title", "summary", "timeline_pos"],
    },
    "foreshadow": {
        "repo": repo.list_foreshadows,
        "label": "相关伏笔",
        "fields": ["title", "content", "state"],
    },
    "world": {
        "repo": repo.list_world,
        "label": "相关世界观",
        "fields": ["category", "content"],
    },
    "chapter": {
        "repo": repo.list_chapters,
        "label": "相关章节",
        "fields": ["title", "content", "detailed_outline"],
    },
}

_SELECT_TOP_N = 5
_RELATED_BOOST = 1000  # 确保通过显式关系召回的实体排在关键词召回之上

# 兼容从 assistant.py 传下来的 context 键名（复数）与 _ENTITY_CONFIG 键名（单数）
_ENTITY_KEY_ALIASES = {
    "characters": "character",
    "outlines": "outline",
    "foreshadows": "foreshadow",
    "chapters": "chapter",
}


def build_entities_from_context(context: dict) -> dict[str, list[dict]]:
    """把 /chat 中组装的 context 映射为 ContextBuilder 期望的实体键。"""
    return {
        "character": context.get("characters") or [],
        "outline": context.get("outlines") or [],
        "plot": context.get("plot") or [],
        "foreshadow": context.get("foreshadows") or [],
        "world": context.get("world") or [],
        "chapter": context.get("chapters") or [],
    }


def _extract_keywords(text: str) -> list[str]:
    if not text:
        return []
    # 英文/数字单独成词；连续中文字符生成 1-3 gram
    tokens = re.findall(r"[a-zA-Z0-9]+|[一-鿿]+", text)
    result = []
    for t in tokens:
        t = t.strip().lower()
        if re.match(r"^[a-zA-Z0-9]+$", t):
            if len(t) > 1 and t not in _STOP_WORDS:
                result.append(t)
            continue
        # 中文：生成 2-gram、3-gram、1-gram，过滤停用字
        chars = [c for c in t if c not in _STOP_WORDS]
        for n in (2, 3, 1):
            for i in range(len(chars) - n + 1):
                gram = "".join(chars[i : i + n])
                if gram and gram not in _STOP_WORDS:
                    result.append(gram)
    return list(dict.fromkeys(result))


def _entity_text(entity: dict) -> str:
    """把实体的所有字符串字段拼成一段文本，用于子串匹配。"""
    parts = []
    for v in entity.values():
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, list):
            parts.append(" ".join(str(x) for x in v))
    return " ".join(parts)


def _score_entity(entity: dict, keywords: list[str]) -> int:
    if not keywords:
        return 0
    parts = []
    for v in entity.values():
        if isinstance(v, str):
            parts.append(v.lower())
        elif isinstance(v, list):
            parts.append(" ".join(str(x).lower() for x in v))
    full_text = " ".join(parts)
    score = 0
    for kw in keywords:
        for key in ("name", "title", "category"):
            if key in entity and kw in str(entity.get(key, "")).lower():
                score += 3
        score += full_text.count(kw)
    return score


def _format_entity(entity: dict, fields: list[str]) -> str:
    lines = [f"- [{entity.get('id')}]"]
    for field in fields:
        value = entity.get(field)
        if value is not None and str(value):
            lines.append(f"  {field}: {value}")
    return "\n".join(lines)


class ContextBuilder:
    def __init__(
        self,
        db: AsyncSession,
        llm=None,  # 保留参数以兼容旧调用；当前实现不再使用 LLM 精选
        entities: dict[str, list[dict]] | None = None,
    ):
        self.db = db
        self.llm = llm
        self._entities = self._normalize_entities(entities) if entities is not None else None

    @staticmethod
    def _normalize_entities(entities: dict[str, list[dict]]) -> dict[str, list[dict]]:
        """把复数键名（如 characters）归一化为 _ENTITY_CONFIG 使用的单数键名（character）。"""
        normalized = dict(entities)
        for plural, singular in _ENTITY_KEY_ALIASES.items():
            if plural in normalized and singular not in normalized:
                normalized[singular] = normalized[plural]
        return normalized

    async def build(
        self,
        query: str,
        focus_entity_type: str | None = None,
        focus_entity_id: str | None = None,
        project_id: str | None = None,
    ) -> str:
        """基于显式关系、名称匹配和关键词评分生成相关上下文文本。"""
        candidates = await self._fetch_entities(project_id=project_id)
        if not any(candidates.values()):
            return ""

        focus_type, focus_entity = self._resolve_focus(
            query, focus_entity_type, focus_entity_id, candidates
        )
        related = self._expand_related(focus_type, focus_entity, candidates)

        selected = self._select(query, candidates, related)
        return self._format(selected)

    async def _fetch_entities(
        self,
        project_id: str | None = None,
    ) -> dict[str, list[dict]]:
        candidates: dict[str, list[dict]] = {}
        for entity_type, config in _ENTITY_CONFIG.items():
            if self._entities is not None:
                entities = self._entities.get(entity_type, [])
            else:
                if not project_id:
                    entities = []
                else:
                    entities = await config["repo"](self.db, project_id)
            candidates[entity_type] = entities
        return candidates

    def _resolve_focus(
        self,
        query: str,
        focus_entity_type: str | None,
        focus_entity_id: str | None,
        candidates: dict[str, list[dict]],
    ) -> tuple[str | None, dict | None]:
        """确定当前任务聚焦的实体：优先 context 传入的 id，其次从 query 里提取名称。"""
        # 1. 显式 focus_entity_id
        if focus_entity_type and focus_entity_id:
            for e in candidates.get(focus_entity_type, []):
                if e.get("id") == focus_entity_id:
                    return focus_entity_type, e

        # 2. 从 query 中匹配实体名称/标题/分类
        query_lower = query.lower()
        for entity_type, entities in candidates.items():
            for e in entities:
                for key in ("name", "title", "category"):
                    val = e.get(key)
                    if val and str(val).lower() in query_lower:
                        return entity_type, e
        return None, None

    def _expand_related(
        self,
        focus_type: str | None,
        focus_entity: dict | None,
        candidates: dict[str, list[dict]],
    ) -> dict[str, set[str]]:
        """根据项目实体间的显式关系，召回与 focus 实体相关的其他实体 id。"""
        related: dict[str, set[str]] = {k: set() for k in _ENTITY_CONFIG}
        if not focus_entity or not focus_type:
            return related

        focus_id = focus_entity.get("id")
        focus_name = (
            focus_entity.get("name")
            or focus_entity.get("title")
            or focus_entity.get("category", "")
        )
        focus_name_lower = focus_name.lower() if focus_name else ""

        if focus_type == "character":
            # 通过 relations 找到关联角色
            for c in candidates.get("character", []):
                if c.get("id") == focus_id:
                    continue
                for rel in c.get("relations") or []:
                    target = rel.get("target")
                    if target and (target == focus_id or str(target).lower() == focus_name_lower):
                        related["character"].add(c.get("id"))
            # 其他实体文本中提到该角色名称
            self._add_by_text_match(candidates, related, focus_name_lower, exclude_type="character")

        elif focus_type == "chapter":
            chapter_id = focus_id
            chapter_title_lower = focus_entity.get("title", "").lower()
            # 剧情节点已分配到本章
            for p in candidates.get("plot", []):
                if p.get("chapter_id") == chapter_id:
                    related["plot"].add(p.get("id"))
            # 其他实体提到章节标题
            self._add_by_text_match(
                candidates, related, chapter_title_lower,
                exclude_type="chapter", include_types={"character", "foreshadow", "outline", "world"}
            )

        elif focus_type == "plot":
            # 所属章节
            chapter_id = focus_entity.get("chapter_id")
            if chapter_id:
                related["chapter"].add(chapter_id)
            plot_title_lower = focus_entity.get("title", "").lower()
            self._add_by_text_match(
                candidates, related, plot_title_lower,
                exclude_type="plot", include_types={"character", "outline", "foreshadow", "chapter"}
            )

        elif focus_type == "foreshadow":
            subplot_id = focus_entity.get("subplot_id")
            if subplot_id:
                for p in candidates.get("plot", []):
                    if p.get("id") == subplot_id:
                        related["plot"].add(subplot_id)
            fs_title_lower = focus_entity.get("title", "").lower()
            self._add_by_text_match(
                candidates, related, fs_title_lower,
                exclude_type="foreshadow", include_types={"plot", "outline", "chapter"}
            )

        elif focus_type == "outline":
            outline_title_lower = focus_entity.get("title", "").lower()
            self._add_by_text_match(
                candidates, related, outline_title_lower,
                exclude_type="outline", include_types={"plot", "chapter", "foreshadow"}
            )

        elif focus_type == "world":
            category_lower = focus_entity.get("category", "").lower()
            self._add_by_text_match(
                candidates, related, category_lower,
                exclude_type="world", include_types={"outline", "chapter", "plot", "character"}
            )

        return related

    def _add_by_text_match(
        self,
        candidates: dict[str, list[dict]],
        related: dict[str, set[str]],
        needle: str,
        exclude_type: str | None = None,
        include_types: set[str] | None = None,
    ) -> None:
        """把文本中包含 needle 的实体加入 related。"""
        if not needle:
            return
        for entity_type, entities in candidates.items():
            if entity_type == exclude_type:
                continue
            if include_types is not None and entity_type not in include_types:
                continue
            for e in entities:
                if needle in _entity_text(e).lower():
                    related[entity_type].add(e.get("id"))

    def _select(
        self,
        query: str,
        candidates: dict[str, list[dict]],
        related: dict[str, set[str]],
    ) -> dict[str, list[dict]]:
        keywords = _extract_keywords(query)
        result: dict[str, list[dict]] = {}
        for entity_type in _ENTITY_CONFIG:
            entities = candidates.get(entity_type, [])
            if not entities:
                continue
            related_ids = related.get(entity_type, set())
            scored = []
            for e in entities:
                score = _score_entity(e, keywords)
                if e.get("id") in related_ids:
                    score += _RELATED_BOOST
                scored.append((e, score))
            scored.sort(key=lambda x: x[1], reverse=True)
            result[entity_type] = [e for e, s in scored[:_SELECT_TOP_N] if s > 0]
        return result

    def _format(self, selected: dict[str, list[dict]]) -> str:
        lines: list[str] = []
        for entity_type, config in _ENTITY_CONFIG.items():
            entities = selected.get(entity_type, [])
            if not entities:
                continue
            lines.append(f"## {config['label']}")
            for e in entities:
                lines.append(_format_entity(e, config["fields"]))
            lines.append("")
        return "\n".join(lines).strip()

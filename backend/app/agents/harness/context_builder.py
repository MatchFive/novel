"""为 Worker 提供语义相关上下文检索。"""
from __future__ import annotations

import json
import logging
import re

from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo
from app.core.llm_client import LLMClient


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
}

_COARSE_TOP_N = 15
_SELECT_TOP_N = 5


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
        # 中文：生成 1-gram、2-gram、3-gram，过滤停用字
        chars = [c for c in t if c not in _STOP_WORDS]
        for n in (2, 3, 1):
            for i in range(len(chars) - n + 1):
                gram = "".join(chars[i : i + n])
                if gram and gram not in _STOP_WORDS:
                    result.append(gram)
    return list(dict.fromkeys(result))


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


def _coarse_filter(entities: list[dict], keywords: list[str], top_n: int) -> list[dict]:
    if not keywords:
        return entities[:top_n]
    scored = [(e, _score_entity(e, keywords)) for e in entities]
    scored.sort(key=lambda x: x[1], reverse=True)
    selected = [e for e, s in scored if s > 0]
    return selected[:top_n] if selected else entities[:top_n]


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
        llm: LLMClient,
        entities: dict[str, list[dict]] | None = None,
    ):
        self.db = db
        self.llm = llm
        self._entities = entities

    async def build(
        self,
        query: str,
        focus_entity_type: str | None = None,
        project_id: str | None = None,
    ) -> str:
        keywords = _extract_keywords(query)
        candidates = await self._fetch_entities(keywords, project_id=project_id)

        if not any(candidates.values()):
            return ""

        selected = await self._select_relevant(query, focus_entity_type, candidates)
        return self._format(selected)

    async def _fetch_entities(
        self,
        keywords: list[str],
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
            candidates[entity_type] = _coarse_filter(entities, keywords, _COARSE_TOP_N)
        return candidates

    async def _select_relevant(
        self,
        query: str,
        focus_entity_type: str | None,
        candidates: dict[str, list[dict]],
    ) -> dict[str, list[dict]]:
        prompt = self._build_selection_prompt(query, focus_entity_type, candidates)
        selected_ids: dict[str, list[str]] = {}
        try:
            resp = await self.llm.chat([{"role": "user", "content": prompt}])
            selected_ids = self._parse_selection(resp)
        except Exception:
            logger.exception("ContextBuilder LLM selection failed, falling back to coarse filter")
            selected_ids = {}

        result: dict[str, list[dict]] = {}
        for entity_type, config in _ENTITY_CONFIG.items():
            entities = candidates.get(entity_type, [])
            ids = selected_ids.get(entity_type, []) if isinstance(selected_ids, dict) else []
            if not isinstance(ids, list):
                ids = []
            selected = [e for e in entities if e.get("id") in ids]
            if not selected:
                selected = entities[:_SELECT_TOP_N]
            else:
                selected = selected[:_SELECT_TOP_N]
            result[entity_type] = selected
        return result

    def _build_selection_prompt(
        self,
        query: str,
        focus_entity_type: str | None,
        candidates: dict[str, list[dict]],
    ) -> str:
        lines = [
            "你是小说创作助手的内容检索器。",
            "",
            f"用户目标：{query}",
        ]
        if focus_entity_type:
            lines.append(f"当前关注实体类型：{focus_entity_type}")
        lines.extend([
            "",
            "下面是从项目中粗筛出的候选条目，按类型分组，每条包含 id 和完整内容。",
            f"请为每个实体类型选出与用户目标最相关的最多 {_SELECT_TOP_N} 个条目 id。",
            "",
            "相关标准：",
            "- 用户目标中明确提到或可能引用该条目。",
            "- 该条目的内容会影响当前变更决策。",
            "- 保持世界观、角色关系、剧情逻辑一致需要参考该条目。",
            "",
            "返回严格 JSON，不要解释：",
            "{",
            '  "character": ["id1", "id2"],',
            '  "outline": [],',
            '  "plot": ["id3"],',
            '  "foreshadow": [],',
            '  "world": ["id4"]',
            "}",
        ])

        for entity_type, config in _ENTITY_CONFIG.items():
            entities = candidates.get(entity_type, [])
            if not entities:
                continue
            lines.append("")
            lines.append(f"【{entity_type}】")
            for e in entities:
                lines.append(f"id: {e.get('id')}")
                for field in config["fields"]:
                    lines.append(f"{field}: {e.get(field, '')}")
                lines.append("---")
        return "\n".join(lines)

    def _parse_selection(self, text: str) -> dict[str, list[str]]:
        text = text.strip()
        if "```" in text:
            parts = text.split("```")
            for part in parts[1:]:
                block = part.strip()
                if block.lower().startswith("json"):
                    block = block[4:]
                try:
                    parsed = json.loads(block.strip())
                    if isinstance(parsed, dict):
                        return parsed
                except Exception:
                    continue
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
        return {}

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

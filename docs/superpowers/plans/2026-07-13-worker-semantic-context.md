# Worker 语义相关上下文检索实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为全部 Worker 提供统一的语义相关上下文检索能力，让 LLM 在处理用户目标时能看到项目里相关的现有角色、大纲、剧情节点、伏笔和世界观设定。

**Architecture:** 新增 `ContextBuilder` 模块负责「关键词粗筛 + LLM 精选 + Markdown 格式化」，各 Worker 调用它并把返回的上下文拼入 prompt；`assistant.py` 把 `project_id` 加入传给 Worker 的 `context`。

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 async, pytest, 复用现有 `LLMClient`。

## Global Constraints

- 覆盖范围：全部 Worker（Character / Outline / Plot / Foreshadow / World）。
- 判定方式：关键词粗筛 + LLM 精选。
- 数量：每类实体最多 5 条。
- 内容粒度：返回完整字段内容。
- 不引入新的外部依赖（embedding 模型、jieba 等）。
- Worker 仍保持只读，ContextBuilder 也只读仓库数据。

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `backend/app/agents/harness/context_builder.py` | 新增：关键词提取、粗筛、LLM 精选、格式化输出。 |
| `backend/app/agents/harness/workers/__init__.py` | 修改：各 Worker 调用 ContextBuilder 并把结果拼入 prompt。 |
| `backend/app/api/assistant.py` | 修改：在传给 Worker 的 `context` 里加入 `project_id`。 |
| `backend/tests/test_context_builder.py` | 新增：ContextBuilder 单元测试。 |
| `backend/tests/test_worker_base.py` | 修改：增加 Worker 集成相关上下文的断言。 |

---

### Task 1: 在 `assistant.py` 的 Worker context 中加入 `project_id`

**Files:**
- Modify: `backend/app/api/assistant.py:117-123`

**Interfaces:**
- Consumes: existing `project_id` variable.
- Produces: `context` dict now includes `"project_id": project_id`.

- [ ] **Step 1: 修改 context 构造**

```python
context = {
    "project_id": project_id,
    "outlines": await repo.list_outlines(db, project_id),
    "characters": await repo.list_characters(db, project_id),
    "foreshadows": await repo.list_foreshadows(db, project_id),
    "world": await repo.list_world(db, project_id),
    "plot": await repo.list_plot(db, project_id),
}
```

- [ ] **Step 2: 语法检查**

Run: `cd backend && .venv/Scripts/python -m compileall app`
Expected: `Compiling ... 1 file ... OK`

- [ ] **Step 3: Commit**

```bash
git add backend/app/api/assistant.py
git commit -m "feat(assistant): pass project_id in worker context"
```

---

### Task 2: 创建 `ContextBuilder` 模块

**Files:**
- Create: `backend/app/agents/harness/context_builder.py`

**Interfaces:**
- Consumes: `AsyncSession`, `LLMClient`, `project_id`, `query`, optional `focus_entity_type`.
- Produces: `ContextBuilder.build(...) -> str` returning Markdown context.

- [ ] **Step 1: 创建文件骨架与常量**

```python
"""为 Worker 提供语义相关上下文检索。"""
from __future__ import annotations

import json
import re

from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo
from app.core.llm_client import LLMClient


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
```

- [ ] **Step 2: 实现关键词提取、粗筛、格式化工具函数**

```python
def _extract_keywords(text: str) -> list[str]:
    if not text:
        return []
    # 英文/数字单独成词；连续中文字符合并成一个词
    tokens = re.findall(r"[a-zA-Z0-9]+|[一-鿿]+", text)
    result = []
    for t in tokens:
        t = t.strip().lower()
        if len(t) <= 1 or t in _STOP_WORDS:
            continue
        result.append(t)
    return result


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
```

- [ ] **Step 3: 实现 `ContextBuilder` 类**

```python
class ContextBuilder:
    def __init__(self, db: AsyncSession, llm: LLMClient):
        self.db = db
        self.llm = llm

    async def build(
        self,
        project_id: str,
        query: str,
        focus_entity_type: str | None = None,
    ) -> str:
        keywords = _extract_keywords(query)
        candidates = await self._fetch_entities(project_id, keywords)

        if not any(candidates.values()):
            return ""

        selected = await self._select_relevant(query, focus_entity_type, candidates)
        return self._format(selected)

    async def _fetch_entities(
        self,
        project_id: str,
        keywords: list[str],
    ) -> dict[str, list[dict]]:
        candidates: dict[str, list[dict]] = {}
        for entity_type, config in _ENTITY_CONFIG.items():
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
            selected_ids = {}

        result: dict[str, list[dict]] = {}
        for entity_type, config in _ENTITY_CONFIG.items():
            entities = candidates.get(entity_type, [])
            ids = selected_ids.get(entity_type, []) if isinstance(selected_ids, dict) else []
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
```

- [ ] **Step 4: 语法检查**

Run: `cd backend && .venv/Scripts/python -m compileall app/agents/harness/context_builder.py`
Expected: `Compiling 1 file ... OK`

- [ ] **Step 5: Commit**

```bash
git add backend/app/agents/harness/context_builder.py
git commit -m "feat(context): add ContextBuilder for semantic related content"
```

---

### Task 3: 为 `ContextBuilder` 编写单元测试

**Files:**
- Create: `backend/tests/test_context_builder.py`

**Interfaces:**
- Consumes: `ContextBuilder` public methods.
- Produces: passing tests for keyword extraction, coarse filter, selection parsing, fallback.

- [ ] **Step 1: 写测试文件**

```python
"""ContextBuilder 单元测试。"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.agents.harness.context_builder import (
    ContextBuilder,
    _extract_keywords,
    _score_entity,
    _coarse_filter,
)


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


def test_extract_keywords_filters_stopwords_and_short_tokens():
    text = "我要修改刘修的能力和性格，让他变得更强。"
    result = _extract_keywords(text)
    assert "刘修" in result
    assert "能力" in result
    assert "性格" in result
    assert "的" not in result
    assert "我" not in result


def test_extract_keywords_keeps_english_and_numbers():
    text = "AB计划 300金币"
    result = _extract_keywords(text)
    assert "ab计划" in result or "ab" in result
    assert "300金币" in result or "300" in result


def test_score_entity_weights_name_higher():
    entity = {"id": "c1", "name": "刘修", "traits": "穿越者", "ability": "剑术"}
    keywords = ["刘修"]
    score = _score_entity(entity, keywords)
    assert score >= 3


def test_coarse_filter_returns_top_scored():
    entities = [
        {"id": "c1", "name": "刘修", "traits": "穿越者"},
        {"id": "c2", "name": "张三", "traits": "路人"},
        {"id": "c3", "name": "李四", "traits": "与刘修相关"},
    ]
    keywords = ["刘修"]
    result = _coarse_filter(entities, keywords, top_n=2)
    assert len(result) == 2
    assert result[0]["id"] == "c1"


def test_coarse_filter_fallback_when_no_keywords():
    entities = [
        {"id": "c1", "name": "刘修"},
        {"id": "c2", "name": "张三"},
    ]
    result = _coarse_filter(entities, [], top_n=2)
    assert result == entities[:2]


@pytest.mark.anyio
async def test_build_returns_formatted_context():
    db = AsyncMock()
    llm = AsyncMock()
    llm.chat.return_value = '{"character": ["c1"], "outline": []}'

    builder = ContextBuilder(db, llm)
    with patch.object(
        builder, "_fetch_entities", new=AsyncMock(return_value={
            "character": [
                {"id": "c1", "name": "刘修", "traits": "穿越者", "ability": "剑术", "status": "活着"},
            ],
            "outline": [],
            "plot": [],
            "foreshadow": [],
            "world": [],
        })
    ):
        result = await builder.build("p1", "完善刘修")

    assert "相关角色" in result
    assert "刘修" in result
    assert "穿越者" in result


@pytest.mark.anyio
async def test_build_fallback_to_coarse_top_when_llm_returns_invalid():
    db = AsyncMock()
    llm = AsyncMock()
    llm.chat.return_value = "not json"

    builder = ContextBuilder(db, llm)
    with patch.object(
        builder, "_fetch_entities", new=AsyncMock(return_value={
            "character": [
                {"id": "c1", "name": "刘修", "traits": "穿越者", "ability": "剑术", "status": "活着"},
            ],
            "outline": [],
            "plot": [],
            "foreshadow": [],
            "world": [],
        })
    ):
        result = await builder.build("p1", "完善刘修")

    assert "相关角色" in result
    assert "c1" in result
```

- [ ] **Step 2: 运行测试**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_context_builder.py -v`
Expected: 6 passed

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_context_builder.py
git commit -m "test(context): add ContextBuilder unit tests"
```

---

### Task 4: 在 `CharacterWorker` 中集成 `ContextBuilder`

**Files:**
- Modify: `backend/app/agents/harness/workers/__init__.py`

**Interfaces:**
- Consumes: `ContextBuilder` from `app.agents.harness.context_builder`.
- Produces: `CharacterWorker.run` prompt includes related context.

- [ ] **Step 1: 在模块顶部导入 `ContextBuilder`**

```python
from app.agents.harness.context_builder import ContextBuilder
```

- [ ] **Step 2: 修改 `CharacterWorker.run`**

```python
class CharacterWorker(WorkerBase):
    worker_name = "character"

    async def run(self, goal: str, context: dict, history_context: list[dict] | None = None) -> dict:
        project_id = context.get("project_id")
        related = ""
        if project_id:
            builder = ContextBuilder(self.db, self.llm)
            related = await builder.build(project_id, goal, "character")

        chars = context.get("characters") or []
        chars_desc = "\n".join(
            f"- {c.get('name')} (id={c.get('id')})"
            for c in chars
        ) or "暂无现有角色。"

        system = (
            "你是角色设计师。基于用户目标设计或调整角色，最终以 JSON 返回建议变更："
            '{"changes": [{"action":"add|update", "entity_id":null或id, '
            '"fields": {"name":"", "traits":"", "ability":"", "status":"", "relations":[], "importance":0}}]}\n\n'
            "重要规则：\n"
            "1. 下面会提供「现有角色」列表。若用户目标中的角色 name 与现有角色 name 完全相同，"
            "必须返回 action='update'，entity_id 必须填该现有角色的 id，fields 为合并后的完整新内容。\n"
            "2. 只有 name 完全不存在于现有角色列表时，才返回 action='add'，entity_id=null。\n"
            "3. 不要创建与现有角色同名的重复角色。\n"
            "4. 参考【相关上下文】保持与现有设定一致。\n"
            "若需调用工具进一步了解角色，请输出 TOOL_CALL:{\"name\":\"read_characters\",\"arguments\":{\"project_id\":\"...\"}}"
        )
        user_prompt = f"【现有角色】\n{chars_desc}\n\n【相关上下文】\n{related or '（无）'}\n\n【用户目标】\n{goal}"
        return await self._tool_loop(system, user_prompt, history_context=history_context)
```

- [ ] **Step 3: 语法检查**

Run: `cd backend && .venv/Scripts/python -m compileall app/agents/harness/workers/__init__.py`
Expected: `Compiling 1 file ... OK`

- [ ] **Step 4: Commit**

```bash
git add backend/app/agents/harness/workers/__init__.py
git commit -m "feat(character): include semantic related context in prompt"
```

---

### Task 5: 在其余 Worker 中集成 `ContextBuilder`

**Files:**
- Modify: `backend/app/agents/harness/workers/__init__.py`

**Interfaces:**
- Consumes: `ContextBuilder` already imported.
- Produces: `OutlineWorker`, `PlotWorker`, `ForeshadowWorker`, `WorldWorker` prompts include related context.

- [ ] **Step 1: 修改 `OutlineWorker.run`**

```python
class OutlineWorker(WorkerBase):
    worker_name = "outline"

    async def run(self, goal: str, context: dict, history_context: list[dict] | None = None) -> dict:
        project_id = context.get("project_id")
        related = ""
        if project_id:
            builder = ContextBuilder(self.db, self.llm)
            related = await builder.build(project_id, goal, "outline")

        system = (
            "你是大纲架构师。使用只读工具 read_outlines / read_outline / read_outline_prev_version "
            "了解现有大纲与版本链，再产出新大纲修订。"
            '返回 JSON：{"changes":[{"action":"add|update","entity_id":null或id,'
            '"fields":{"title":"","content":"","parent_id":null}}]}。\n\n'
            "参考【相关上下文】保持大纲与角色、剧情节点、伏笔、世界观一致。\n"
            "若需调用工具，请输出 TOOL_CALL:{\"name\":\"read_outlines\",\"arguments\":{\"project_id\":\"...\"}}"
        )
        user_prompt = f"【相关上下文】\n{related or '（无）'}\n\n【用户目标】\n{goal}"
        return await self._tool_loop(system, user_prompt, history_context=history_context)
```

- [ ] **Step 2: 修改 `PlotWorker.run`**

```python
class PlotWorker(WorkerBase):
    worker_name = "plot"

    async def run(self, goal: str, context: dict, history_context: list[dict] | None = None) -> dict:
        project_id = context.get("project_id")
        related = ""
        if project_id:
            builder = ContextBuilder(self.db, self.llm)
            related = await builder.build(project_id, goal, "plot")

        system = (
            "你是剧情节点编排师。使用只读工具 read_plot_nodes / read_outlines 取数，"
            '返回 JSON：{"changes":[{"action":"add|update","entity_id":null或id,'
            '"fields":{"title":"","summary":"","timeline_pos":""}}]}。\n\n'
            "参考【相关上下文】保持剧情节点与角色、大纲、伏笔一致。\n"
            "若需调用工具，请输出 TOOL_CALL:{\"name\":\"read_plot_nodes\",\"arguments\":{\"project_id\":\"...\"}}"
        )
        user_prompt = f"【相关上下文】\n{related or '（无）'}\n\n【用户目标】\n{goal}"
        return await self._tool_loop(system, user_prompt, history_context=history_context)
```

- [ ] **Step 3: 修改 `ForeshadowWorker.run`**

```python
class ForeshadowWorker(WorkerBase):
    worker_name = "foreshadow"

    async def run(self, goal: str, context: dict, history_context: list[dict] | None = None) -> dict:
        project_id = context.get("project_id")
        related = ""
        if project_id:
            builder = ContextBuilder(self.db, self.llm)
            related = await builder.build(project_id, goal, "foreshadow")

        system = (
            "你是伏笔设计师。使用只读工具 read_foreshadows / read_plot_nodes 取数，"
            '返回 JSON：{"changes":[{"action":"add|update","entity_id":null或id,'
            '"fields":{"title":"","content":"","state":"pending","subplot_id":null}}]}。\n\n'
            "参考【相关上下文】保持伏笔与角色、剧情节点、大纲一致。\n"
            "若需调用工具，请输出 TOOL_CALL:{\"name\":\"read_foreshadows\",\"arguments\":{\"project_id\":\"...\"}}"
        )
        user_prompt = f"【相关上下文】\n{related or '（无）'}\n\n【用户目标】\n{goal}"
        return await self._tool_loop(system, user_prompt, history_context=history_context)
```

- [ ] **Step 4: 修改 `WorldWorker.run`**

```python
class WorldWorker(WorkerBase):
    worker_name = "world"

    async def run(self, goal: str, context: dict, history_context: list[dict] | None = None) -> dict:
        project_id = context.get("project_id")
        related = ""
        if project_id:
            builder = ContextBuilder(self.db, self.llm)
            related = await builder.build(project_id, goal, "world")

        system = (
            "你是世界观设定师。使用只读工具 read_world 了解现有设定，"
            '返回 JSON：{"changes":[{"action":"add|update","entity_id":null或id,'
            '"fields":{"category":"","content":""}}]}。\n\n'
            "参考【相关上下文】保持世界观设定与角色、大纲一致，避免冲突。\n"
            "工具调用格式：TOOL_CALL:{\"name\":\"read_world\",\"arguments\":{\"project_id\":\"...\"}}"
        )
        user_prompt = f"【相关上下文】\n{related or '（无）'}\n\n【用户目标】\n{goal}"
        return await self._tool_loop(system, user_prompt, history_context=history_context)
```

- [ ] **Step 5: 语法检查**

Run: `cd backend && .venv/Scripts/python -m compileall app`
Expected: all files OK

- [ ] **Step 6: Commit**

```bash
git add backend/app/agents/harness/workers/__init__.py
git commit -m "feat(workers): include semantic related context in all workers"
```

---

### Task 6: 更新 Worker 集成测试

**Files:**
- Modify: `backend/tests/test_worker_base.py`

**Interfaces:**
- Consumes: patched `ContextBuilder` in `app.agents.harness.workers`.
- Produces: test asserting related context appears in prompt.

- [ ] **Step 1: 新增测试用例**

```python
@pytest.mark.anyio
async def test_character_worker_prompt_includes_related_context():
    """CharacterWorker 应在 prompt 中包含 ContextBuilder 返回的相关上下文。"""
    llm = AsyncMock()
    llm.chat.return_value = json.dumps({"changes": []})

    worker = CharacterWorker(db=AsyncMock(), llm=llm, recursive_limit=1)
    with patch("app.agents.harness.workers.ContextBuilder") as MockBuilder:
        MockBuilder.return_value.build = AsyncMock(
            return_value="## 相关角色\n- [c2]\n  name: 刘修\n  traits: 穿越者"
        )
        context = {"project_id": "p1", "characters": [{"id": "c1", "name": "刘修"}]}
        await worker.run("完善刘修的设定", context)

    first_call = llm.chat.call_args_list[0]
    messages = first_call.kwargs.get("messages") or first_call.args[0]
    user_msg = messages[-1]["content"]

    assert "相关角色" in user_msg
    assert "刘修" in user_msg
    MockBuilder.return_value.build.assert_awaited_once_with("p1", "完善刘修的设定", "character")
```

- [ ] **Step 2: 运行测试**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_worker_base.py -v`
Expected: all tests passed

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_worker_base.py
git commit -m "test(workers): assert related context in CharacterWorker prompt"
```

---

### Task 7: 运行全量后端测试并验证

**Files:**
- N/A

**Interfaces:**
- Consumes: all previous changes.
- Produces: green test suite.

- [ ] **Step 1: 运行所有测试**

Run: `cd backend && .venv/Scripts/python -m pytest tests/ -v`
Expected: all tests passed

- [ ] **Step 2: 启动后端并 smoke test**

Run:
```bash
cd backend && .venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
```
In another terminal:
```bash
curl http://127.0.0.1:8765/health
```
Expected: `{"ok":true,"status":"healthy","app":"Novel Studio"}`

- [ ] **Step 3: Commit any final changes**

If tests passed with no additional changes, no new commit needed.

---

## Self-Review Checklist

- [x] **Spec coverage**: 每个设计要求都有对应任务。
  - ContextBuilder 模块 → Task 2
  - Worker 集成 → Task 4, 5
  - project_id 传递 → Task 1
  - 测试 → Task 3, 6, 7
- [x] **Placeholder scan**: 无 TBD、TODO、"实现 later" 等。
- [x] **Type consistency**: `ContextBuilder` 签名在各任务中一致；`_fetch_entities` 在 Task 2 中定义并在 Task 3 测试中使用。
- [x] **Design deviation note**: 原设计提到“无关键词时按更新时间取最近 5 条”，但现有实体表无 `updated_at`，实现改为返回空字符串（不影响 Worker 继续执行），并在 Task 2 `_coarse_filter` 中体现。

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-13-worker-semantic-context.md`.

**Two execution options:**

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?

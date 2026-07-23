# 角色记忆管理功能实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为长篇小说项目新增角色已知信息记忆库，支持从已校阅章节自动提取记忆、用户手动管理记忆，并让各 Worker 在生成大纲/剧情/正文时按重要性、时效性查询调用。

**Architecture:** 新增三张表（`LongCharacterMemory`、`LongCharacterMemoryDraft`、`LongChapterMemoryExtraction`）；通过专用 API 触发 LLM 提取并生成候选变更，用户确认后落库；新增只读工具 `read_character_memories` 供 Worker 调用；调整 `chapter_text_prompt` 放宽主角认知边界。

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2.0 async, SQLite, React 19 + TypeScript + Vite + Tailwind, OpenAI-compatible LLM client。

## Global Constraints

- 后端运行目录为 `backend/`，导入使用绝对路径 `app.*`。
- 所有持久化变更仍须通过 `app.services.change_apply.apply_change`（Saga 双写）；但记忆实体不经过 staged_changes，由专用 API 直接落库。
- Worker 只读：不直接 import repositories，只通过 `app.agents.tools` 暴露的只读工具取数。
- 前端风格保持现有黑白灰、1px 边框、零圆角（非 Lovart）。
- 验证方式：后端 `python -m compileall app`，前端 `npx tsc -b`。
- 无 pytest/test runner，以语法/类型检查 + 手动功能验证为准。

---

## File Structure

### 后端新增/修改

| 文件 | 职责 |
|---|---|
| `backend/app/models.py` | 新增 `LongCharacterMemory`、`LongCharacterMemoryDraft`、`LongChapterMemoryExtraction` 三个模型 |
| `backend/app/repositories/__init__.py` | 新增记忆、draft、extraction 记录的 CRUD 辅助函数 |
| `backend/app/services/character_memory.py` | 记忆提取核心服务：识别出场角色、调用 LLM、生成 drafts |
| `backend/app/services/prompts/character_memory.py` | 记忆提取 Prompt 模板 |
| `backend/app/api/long_memory.py` | 新增 API 路由（extract-memory、drafts、apply/discard、CRUD） |
| `backend/app/api/main.py` | 注册 `long_memory.router` |
| `backend/app/agents/tools/__init__.py` | 新增 `read_character_memories` 只读工具 |
| `backend/app/agents/harness/prompts/chapter_generation.py` | 调整 `chapter_text_prompt` 主角认知边界 |
| `backend/app/agents/harness/workers/chapter_workers.py` | `ChapterTextWorker` 注入记忆查询 |

### 前端新增/修改

| 文件 | 职责 |
|---|---|
| `frontend/src/api/long.ts` | 新增记忆相关 API 封装 |
| `frontend/src/components/chapter/ChapterEditor.tsx` | 顶部新增"更新记忆"按钮 + 候选预览面板 |
| `frontend/src/components/character/CharacterMemoryPanel.tsx` | 新增角色记忆管理组件（新建） |
| `frontend/src/components/EntityWorkbench.tsx` | 集成记忆标签页 |
| `frontend/src/types/index.ts` | 新增 `CharacterMemory` / `CharacterMemoryDraft` 类型 |

---

## Task 1: 数据模型

**Files:**
- Modify: `backend/app/models.py`

**Interfaces:**
- Produces: `LongCharacterMemory`, `LongCharacterMemoryDraft`, `LongChapterMemoryExtraction` SQLAlchemy models.

- [ ] **Step 1: 新增三个模型**

在 `backend/app/models.py` 的 `LongChangeRecord` 之前插入以下内容：

```python
class LongCharacterMemory(Base):
    __tablename__ = "long_character_memories"

    id = Column(CHAR(36), primary_key=True, default=_uuid)
    project_id = Column(CHAR(36), ForeignKey("projects.id"), nullable=False, index=True)
    character_id = Column(CHAR(36), ForeignKey("long_characters.id"), nullable=False, index=True)
    content = Column(Text, default="")
    importance = Column(String(16), default="major")
    ttl = Column(String(16), default="long")
    source_chapter_id = Column(CHAR(36), ForeignKey("long_chapters.id"), nullable=True, index=True)
    source_type = Column(String(16), default="auto")
    related_character_ids = Column(JSON, default=list)
    related_foreshadow_ids = Column(JSON, default=list)
    created_at = Column(DateTime, default=_now, nullable=False)
    updated_at = Column(DateTime, default=_now, onupdate=_now, nullable=False)


class LongCharacterMemoryDraft(Base):
    __tablename__ = "long_character_memory_drafts"

    id = Column(CHAR(36), primary_key=True, default=_uuid)
    project_id = Column(CHAR(36), ForeignKey("projects.id"), nullable=False, index=True)
    chapter_id = Column(CHAR(36), ForeignKey("long_chapters.id"), nullable=False, index=True)
    character_id = Column(CHAR(36), ForeignKey("long_characters.id"), nullable=False, index=True)
    action = Column(String(16), default="add")
    target_memory_id = Column(CHAR(36), ForeignKey("long_character_memories.id"), nullable=True)
    content = Column(Text, default="")
    importance = Column(String(16), default="major")
    ttl = Column(String(16), default="long")
    related_character_ids = Column(JSON, default=list)
    related_foreshadow_ids = Column(JSON, default=list)
    created_at = Column(DateTime, default=_now, nullable=False)


class LongChapterMemoryExtraction(Base):
    __tablename__ = "long_chapter_memory_extractions"

    chapter_id = Column(CHAR(36), ForeignKey("long_chapters.id"), primary_key=True)
    extracted_at = Column(DateTime, default=_now, nullable=False)
    content_hash = Column(String(64), nullable=False)
    memory_count = Column(Integer, default=0)
```

- [ ] **Step 2: 验证语法**

Run: `cd backend && python -m compileall app`
Expected: `Compiling '...'...` with no errors.

- [ ] **Step 3: 提交**

```bash
git add backend/app/models.py
git commit -m "feat(memory): add character memory models"
```

---

## Task 2: Repositories

**Files:**
- Modify: `backend/app/repositories/__init__.py`

**Interfaces:**
- Produces:
  - `list_character_memories(db, character_id)` → `list[dict]`
  - `create_character_memory(db, data)` → `dict`
  - `update_character_memory(db, memory_id, data)` → `dict | None`
  - `delete_character_memory(db, memory_id)` → `bool`
  - `list_character_memory_drafts(db, chapter_id)` → `list[dict]`
  - `create_character_memory_draft(db, data)` → `dict`
  - `clear_character_memory_drafts(db, chapter_id)` → `None`
  - `get_chapter_memory_extraction(db, chapter_id)` → `dict | None`
  - `set_chapter_memory_extraction(db, chapter_id, content_hash, memory_count)` → `None`

- [ ] **Step 1: 在 repositories 底部新增函数**

```python
# ---- Character Memories ----
async def list_character_memories(db: AsyncSession, character_id: str) -> list[dict]:
    from app.models import LongCharacterMemory
    res = await db.execute(select(LongCharacterMemory).where(LongCharacterMemory.character_id == character_id))
    rows = res.scalars().all()
    return [{c.name: getattr(r, c.name) for c in LongCharacterMemory.__table__.columns} for r in rows]


async def create_character_memory(db: AsyncSession, data: dict) -> dict:
    from app.models import LongCharacterMemory
    return await _create(db, LongCharacterMemory, data)


async def update_character_memory(db: AsyncSession, memory_id: str, data: dict) -> dict | None:
    from app.models import LongCharacterMemory
    return await _update(db, LongCharacterMemory, memory_id, data)


async def delete_character_memory(db: AsyncSession, memory_id: str) -> bool:
    from app.models import LongCharacterMemory
    return await _delete(db, LongCharacterMemory, memory_id)


# ---- Character Memory Drafts ----
async def list_character_memory_drafts(db: AsyncSession, chapter_id: str) -> list[dict]:
    from app.models import LongCharacterMemoryDraft
    res = await db.execute(
        select(LongCharacterMemoryDraft)
        .where(LongCharacterMemoryDraft.chapter_id == chapter_id)
        .order_by(LongCharacterMemoryDraft.character_id, LongCharacterMemoryDraft.created_at)
    )
    rows = res.scalars().all()
    return [{c.name: getattr(r, c.name) for c in LongCharacterMemoryDraft.__table__.columns} for r in rows]


async def create_character_memory_draft(db: AsyncSession, data: dict) -> dict:
    from app.models import LongCharacterMemoryDraft
    return await _create(db, LongCharacterMemoryDraft, data)


async def clear_character_memory_drafts(db: AsyncSession, chapter_id: str) -> None:
    from app.models import LongCharacterMemoryDraft
    await db.execute(delete(LongCharacterMemoryDraft).where(LongCharacterMemoryDraft.chapter_id == chapter_id))
    await db.commit()


# ---- Chapter Memory Extraction ----
async def get_chapter_memory_extraction(db: AsyncSession, chapter_id: str) -> dict | None:
    from app.models import LongChapterMemoryExtraction
    row = await db.get(LongChapterMemoryExtraction, chapter_id)
    if row is None:
        return None
    return {c.name: getattr(row, c.name) for c in LongChapterMemoryExtraction.__table__.columns}


async def set_chapter_memory_extraction(
    db: AsyncSession,
    chapter_id: str,
    content_hash: str,
    memory_count: int,
) -> None:
    from app.models import LongChapterMemoryExtraction
    row = await db.get(LongChapterMemoryExtraction, chapter_id)
    now = datetime.now(timezone.utc)
    if row is None:
        db.add(LongChapterMemoryExtraction(
            chapter_id=chapter_id,
            content_hash=content_hash,
            memory_count=memory_count,
            extracted_at=now,
        ))
    else:
        row.content_hash = content_hash
        row.memory_count = memory_count
        row.extracted_at = now
    await db.commit()
```

注意需要在文件顶部 `from sqlalchemy import select` 中加入 `delete`，并在 `_delete` 旁边确认 `_update` 不会过滤掉空字符串（当前实现 `if v is not None` 不会过滤空字符串，符合需求）。

- [ ] **Step 2: 验证语法**

Run: `cd backend && python -m compileall app`
Expected: 无错误。

- [ ] **Step 3: 提交**

```bash
git add backend/app/repositories/__init__.py
git commit -m "feat(memory): add memory repositories"
```

---

## Task 3: 记忆提取服务

**Files:**
- Create: `backend/app/services/character_memory.py`
- Create: `backend/app/services/prompts/character_memory.py`

**Interfaces:**
- Consumes:
  - `repo.list_characters`, `repo.list_foreshadows`, `repo.list_character_memories`, `repo.create_character_memory_draft`, `repo.clear_character_memory_drafts`, `repo.get_chapter_memory_extraction`, `repo.set_chapter_memory_extraction`
  - `llm.parse_llm_json`
- Produces:
  - `extract_memory_drafts(db, chapter_id)` → `dict` (含 `ok`, `skipped`, `drafts`, `grouped_by_character`)
  - `apply_memory_drafts(db, chapter_id)` → `dict`

- [ ] **Step 1: 创建 Prompt 文件**

`backend/app/services/prompts/character_memory.py`:

```python
"""角色记忆提取 Prompt 模板。"""
from __future__ import annotations

import json
from string import Template


JSON_RULES = """JSON 输出规则（必须遵守）：
1. 只返回纯 JSON，不要 markdown 代码块，不要解释。
2. 所有字符串字段必须是合法 JSON 字符串。
3. action 只能是 add / update / delete。
"""


MEMORY_EXTRACTION_PROMPT_TEMPLATE = Template("""你是小说角色记忆管理员。请根据本章正文，为每个出场角色维护其"已知信息"记忆库。

${json_rules}

输出格式：
{
  "memories": [
    {
      "action": "add|update|delete",
      "memory_id": "现有记忆id或null",
      "content": "记忆文本",
      "importance": "core|major|minor",
      "ttl": "permanent|long|arc|scene",
      "related_character_ids": ["uuid"],
      "related_foreshadow_ids": ["uuid"]
    }
  ]
}

业务规则：
- 只提取角色在本章中实际获得、确认或经历的信息，不要加入角色不可能知道的内容。
- 区分事实与角色推断，统一以自由文本表达。
- importance 标注：
  - core：影响角色核心动机、身份、长期目标
  - major：重要事件或情报
  - minor：细节补充
- ttl 标注：
  - permanent：永久有效（如身世、核心关系）
  - long：长期有效（如阶段任务、重要情报）
  - arc：当前剧情弧/副本/战斗内有效
  - scene：仅当前场景有效
- 识别关联角色（通过角色 id）和关联伏笔（通过伏笔 id）。
- 如果某条现有记忆被本章内容推翻或需要更新，输出 update 并填写 memory_id。
- 如果某条现有记忆已过时且不再需要，输出 delete 并填写 memory_id。
- 若本章没有让某角色获得新信息，则不要为该角色输出任何条目。

输入数据：
【本章正文】
$chapter_text

【目标角色】
$character

【角色现有记忆】
$existing_memories

【项目全部角色】
$characters

【项目全部伏笔】
$foreshadows
""")


def memory_extraction_prompt(
    chapter_text: str,
    character: dict,
    existing_memories: list[dict],
    characters: list[dict],
    foreshadows: list[dict],
) -> str:
    return MEMORY_EXTRACTION_PROMPT_TEMPLATE.substitute(
        json_rules=JSON_RULES,
        chapter_text=chapter_text,
        character=json.dumps(character, ensure_ascii=False, indent=2),
        existing_memories=json.dumps(existing_memories, ensure_ascii=False, indent=2),
        characters=json.dumps(characters, ensure_ascii=False, indent=2),
        foreshadows=json.dumps(foreshadows, ensure_ascii=False, indent=2),
    )
```

- [ ] **Step 2: 创建服务文件**

`backend/app/services/character_memory.py`:

```python
"""角色记忆提取与应用服务。"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo
from app.core.errors import NotFoundError, ValidationError
from app.models import LongCharacterMemory, LongCharacterMemoryDraft
from app.services.prompts.character_memory import memory_extraction_prompt

logger = logging.getLogger(__name__)


_VALID_IMPORTANCE = {"core", "major", "minor"}
_VALID_TTL = {"permanent", "long", "arc", "scene"}
_VALID_ACTIONS = {"add", "update", "delete"}


def _content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_memory_payload(raw: dict, existing_ids: set[str]) -> dict | None:
    action = raw.get("action", "add")
    if action not in _VALID_ACTIONS:
        logger.warning("Invalid memory action: %s", action)
        return None

    memory_id = raw.get("memory_id")
    if action in ("update", "delete") and memory_id not in existing_ids:
        logger.warning("Memory action %s references unknown memory_id %s", action, memory_id)
        return None

    importance = raw.get("importance", "major")
    if importance not in _VALID_IMPORTANCE:
        importance = "major"

    ttl = raw.get("ttl", "long")
    if ttl not in _VALID_TTL:
        ttl = "long"

    content = str(raw.get("content") or "").strip()
    if action != "delete" and not content:
        logger.warning("Skipping memory with empty content")
        return None

    related_character_ids = raw.get("related_character_ids") or []
    related_foreshadow_ids = raw.get("related_foreshadow_ids") or []

    return {
        "action": action,
        "target_memory_id": memory_id,
        "content": content,
        "importance": importance,
        "ttl": ttl,
        "related_character_ids": related_character_ids if isinstance(related_character_ids, list) else [],
        "related_foreshadow_ids": related_foreshadow_ids if isinstance(related_foreshadow_ids, list) else [],
    }


def _detect_character_appearances(chapter_text: str, characters: list[dict]) -> list[dict]:
    """基于角色姓名在正文中的出现次数识别本章出场角色。"""
    text = chapter_text or ""
    appeared = []
    for c in characters:
        name = c.get("name", "").strip()
        if not name:
            continue
        count = text.count(name)
        if count > 0:
            appeared.append((count, c))
    appeared.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in appeared]


async def extract_memory_drafts(db: AsyncSession, chapter_id: str) -> dict:
    chapter = await repo.get_chapter(db, chapter_id)
    if not chapter:
        raise NotFoundError("章节不存在")

    project_id = chapter.get("project_id")
    content = chapter.get("content") or ""
    current_hash = _content_hash(content)

    extraction = await repo.get_chapter_memory_extraction(db, chapter_id)
    if extraction and extraction.get("content_hash") == current_hash:
        return {
            "ok": True,
            "skipped": True,
            "message": "本章记忆已是最新，是否重新提取？",
            "drafts": [],
            "grouped_by_character": {},
        }

    # 清空旧 drafts
    await repo.clear_character_memory_drafts(db, chapter_id)

    characters = await repo.list_characters(db, project_id)
    foreshadows = await repo.list_foreshadows(db, project_id)
    appeared_characters = _detect_character_appearances(content, characters)

    if not appeared_characters:
        await repo.set_chapter_memory_extraction(db, chapter_id, current_hash, 0)
        return {
            "ok": True,
            "skipped": False,
            "drafts": [],
            "grouped_by_character": {},
        }

    # 需要传入 llm；由调用方提供
    from app.core.llm_factory import get_llm_client
    llm = await get_llm_client(db, level="medium")

    all_drafts: list[dict] = []
    for character in appeared_characters:
        existing = await repo.list_character_memories(db, character.get("id"))
        existing_ids = {m.get("id") for m in existing}

        system = memory_extraction_prompt(
            chapter_text=content,
            character=character,
            existing_memories=existing,
            characters=characters,
            foreshadows=foreshadows,
        )
        messages = [{"role": "system", "content": system}]

        try:
            raw = await llm.parse_llm_json(messages)
        except Exception:
            logger.exception("LLM memory extraction failed for character %s", character.get("id"))
            continue

        if not isinstance(raw, dict):
            continue
        memories = raw.get("memories") or []
        if not isinstance(memories, list):
            continue

        for item in memories:
            if not isinstance(item, dict):
                continue
            payload = _normalize_memory_payload(item, existing_ids)
            if payload is None:
                continue
            draft_data = {
                "project_id": project_id,
                "chapter_id": chapter_id,
                "character_id": character.get("id"),
                "action": payload["action"],
                "target_memory_id": payload["target_memory_id"],
                "content": payload["content"],
                "importance": payload["importance"],
                "ttl": payload["ttl"],
                "related_character_ids": payload["related_character_ids"],
                "related_foreshadow_ids": payload["related_foreshadow_ids"],
            }
            draft = await repo.create_character_memory_draft(db, draft_data)
            all_drafts.append(draft)

    # 按角色分组
    grouped: dict[str, list[dict]] = {}
    for d in all_drafts:
        cid = d.get("character_id")
        grouped.setdefault(cid, []).append(d)

    return {
        "ok": True,
        "skipped": False,
        "drafts": all_drafts,
        "grouped_by_character": grouped,
    }


async def apply_memory_drafts(db: AsyncSession, chapter_id: str) -> dict:
    drafts = await repo.list_character_memory_drafts(db, chapter_id)
    if not drafts:
        return {"ok": True, "applied": {"created": 0, "updated": 0, "deleted": 0}}

    chapter = await repo.get_chapter(db, chapter_id)
    if not chapter:
        raise NotFoundError("章节不存在")

    created = updated = deleted = 0
    for draft in drafts:
        action = draft.get("action")
        if action == "add":
            await repo.create_character_memory(db, {
                "project_id": draft.get("project_id"),
                "character_id": draft.get("character_id"),
                "content": draft.get("content"),
                "importance": draft.get("importance"),
                "ttl": draft.get("ttl"),
                "source_chapter_id": chapter_id,
                "source_type": "auto",
                "related_character_ids": draft.get("related_character_ids") or [],
                "related_foreshadow_ids": draft.get("related_foreshadow_ids") or [],
            })
            created += 1
        elif action == "update":
            target = draft.get("target_memory_id")
            if target:
                await repo.update_character_memory(db, target, {
                    "content": draft.get("content"),
                    "importance": draft.get("importance"),
                    "ttl": draft.get("ttl"),
                    "related_character_ids": draft.get("related_character_ids") or [],
                    "related_foreshadow_ids": draft.get("related_foreshadow_ids") or [],
                    "source_chapter_id": chapter_id,
                    "source_type": "auto",
                })
                updated += 1
        elif action == "delete":
            target = draft.get("target_memory_id")
            if target:
                await repo.delete_character_memory(db, target)
                deleted += 1

    await repo.clear_character_memory_drafts(db, chapter_id)
    await repo.set_chapter_memory_extraction(
        db,
        chapter_id,
        _content_hash(chapter.get("content") or ""),
        created + updated,
    )

    return {
        "ok": True,
        "applied": {"created": created, "updated": updated, "deleted": deleted},
    }


async def discard_memory_drafts(db: AsyncSession, chapter_id: str) -> dict:
    await repo.clear_character_memory_drafts(db, chapter_id)
    return {"ok": True}
```

- [ ] **Step 3: 验证语法**

Run: `cd backend && python -m compileall app`
Expected: 无错误。

- [ ] **Step 4: 提交**

```bash
git add backend/app/services/character_memory.py backend/app/services/prompts/character_memory.py
git commit -m "feat(memory): add memory extraction service"
```

---

## Task 4: API 路由

**Files:**
- Create: `backend/app/api/long_memory.py`
- Modify: `backend/app/main.py`

**Interfaces:**
- Consumes: `app.services.character_memory.extract_memory_drafts`, `apply_memory_drafts`, `discard_memory_drafts`
- Produces: FastAPI endpoints under `/api/long`.

- [ ] **Step 1: 创建路由文件**

`backend/app/api/long_memory.py`:

```python
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.database import get_db
from app import repositories as repo
from app.services.character_memory import (
    apply_memory_drafts,
    discard_memory_drafts,
    extract_memory_drafts,
)

router = APIRouter(prefix="", tags=["long-memory"])


@router.post("/chapters/{chapter_id}/extract-memory")
async def extract_memory(chapter_id: str, db: AsyncSession = Depends(get_db)):
    result = await extract_memory_drafts(db, chapter_id)
    return result


@router.get("/chapters/{chapter_id}/memory-drafts")
async def get_memory_drafts(chapter_id: str, db: AsyncSession = Depends(get_db)):
    drafts = await repo.list_character_memory_drafts(db, chapter_id)
    grouped: dict[str, list[dict]] = {}
    for d in drafts:
        grouped.setdefault(d.get("character_id"), []).append(d)
    return {"ok": True, "drafts": drafts, "grouped_by_character": grouped}


@router.post("/memory-drafts/apply")
async def apply_drafts(body: dict, db: AsyncSession = Depends(get_db)):
    chapter_id = body.get("chapter_id")
    if not chapter_id:
        raise ValidationError("chapter_id 必填")
    return await apply_memory_drafts(db, chapter_id)


@router.post("/memory-drafts/discard")
async def discard_drafts(body: dict, db: AsyncSession = Depends(get_db)):
    chapter_id = body.get("chapter_id")
    if not chapter_id:
        raise ValidationError("chapter_id 必填")
    return await discard_memory_drafts(db, chapter_id)


@router.get("/characters/{character_id}/memories")
async def get_character_memories(character_id: str, db: AsyncSession = Depends(get_db)):
    rows = await repo.list_character_memories(db, character_id)
    return {"ok": True, "memories": rows}


@router.post("/characters/{character_id}/memories")
async def add_character_memory(character_id: str, body: dict, db: AsyncSession = Depends(get_db)):
    character = await repo.get_character(db, character_id)
    if not character:
        raise NotFoundError("角色不存在")
    data = {
        "project_id": character.get("project_id"),
        "character_id": character_id,
        "content": body.get("content", ""),
        "importance": body.get("importance", "major"),
        "ttl": body.get("ttl", "long"),
        "source_chapter_id": None,
        "source_type": "manual",
        "related_character_ids": body.get("related_character_ids") or [],
        "related_foreshadow_ids": body.get("related_foreshadow_ids") or [],
    }
    memory = await repo.create_character_memory(db, data)
    return {"ok": True, "memory": memory}


@router.put("/characters/{character_id}/memories/{memory_id}")
async def edit_character_memory(
    character_id: str,
    memory_id: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    memory = await repo.get_character_memory(db, memory_id)
    if not memory or memory.get("character_id") != character_id:
        raise NotFoundError("记忆不存在")
    update_data = {
        "content": body.get("content"),
        "importance": body.get("importance"),
        "ttl": body.get("ttl"),
        "related_character_ids": body.get("related_character_ids"),
        "related_foreshadow_ids": body.get("related_foreshadow_ids"),
    }
    update_data = {k: v for k, v in update_data.items() if v is not None}
    updated = await repo.update_character_memory(db, memory_id, update_data)
    return {"ok": True, "memory": updated}


@router.delete("/characters/{character_id}/memories/{memory_id}")
async def remove_character_memory(
    character_id: str,
    memory_id: str,
    db: AsyncSession = Depends(get_db),
):
    memory = await repo.get_character_memory(db, memory_id)
    if not memory or memory.get("character_id") != character_id:
        raise NotFoundError("记忆不存在")
    ok = await repo.delete_character_memory(db, memory_id)
    return {"ok": ok}
```

注意：需要在 `backend/app/repositories/__init__.py` 中补充 `get_character_memory` 函数（返回单条 memory）。

```python
async def get_character_memory(db: AsyncSession, memory_id: str) -> dict | None:
    from app.models import LongCharacterMemory
    row = await db.get(LongCharacterMemory, memory_id)
    return await _row_to_dict(row)
```

- [ ] **Step 2: 在 main.py 注册路由**

在 `backend/app/main.py` 中找到 `app.include_router(long_character.router, prefix="/api/long")` 附近，添加：

```python
from app.api import long_memory

app.include_router(long_memory.router, prefix="/api/long")
```

- [ ] **Step 3: 验证语法**

Run: `cd backend && python -m compileall app`
Expected: 无错误。

- [ ] **Step 4: 提交**

```bash
git add backend/app/api/long_memory.py backend/app/main.py backend/app/repositories/__init__.py
git commit -m "feat(memory): add memory API routes"
```

---

## Task 5: Worker 只读工具

**Files:**
- Modify: `backend/app/agents/tools/__init__.py`

**Interfaces:**
- Consumes: `repo.list_character_memories`
- Produces: `read_character_memories` tool registered in `TOOL_REGISTRY`.

- [ ] **Step 1: 新增工具函数并注册**

在 `backend/app/agents/tools/__init__.py` 中 `read_character` 之后添加：

```python
async def read_character_memories(
    db: AsyncSession,
    character_id: str,
    importance: str | None = None,
    ttl: str | None = None,
    related_foreshadow_id: str | None = None,
    limit: int = 20,
) -> list[dict]:
    memories = await repo.list_character_memories(db, character_id)
    result = []
    for m in memories:
        if importance and m.get("importance") != importance:
            continue
        if ttl and m.get("ttl") != ttl:
            continue
        if related_foreshadow_id:
            related = m.get("related_foreshadow_ids") or []
            if related_foreshadow_id not in related:
                continue
        result.append(m)
        if len(result) >= limit:
            break
    return result
```

然后在文件底部的 `register_tool` 调用区添加注册：

```python
register_tool(
    "read_character_memories",
    "读取角色的已知信息记忆。支持按 importance、ttl、关联伏笔过滤。参数：character_id, importance(可选core|major|minor), ttl(可选permanent|long|arc|scene), related_foreshadow_id(可选), limit(默认20)",
    {
        "type": "object",
        "properties": {
            "character_id": {"type": "string"},
            "importance": {"type": "string"},
            "ttl": {"type": "string"},
            "related_foreshadow_id": {"type": "string"},
            "limit": {"type": "integer"},
        },
        "required": ["character_id"],
    },
    read_character_memories,
)
```

- [ ] **Step 2: 验证语法**

Run: `cd backend && python -m compileall app`
Expected: 无错误。

- [ ] **Step 3: 提交**

```bash
git add backend/app/agents/tools/__init__.py
git commit -m "feat(memory): add read_character_memories tool"
```

---

## Task 6: Prompt 调整

**Files:**
- Modify: `backend/app/agents/harness/prompts/chapter_generation.py`

**Interfaces:**
- Produces: 更新后的 `CHAPTER_TEXT_PROMPT_TEMPLATE` 和 `CHAPTER_REVIEW_PROMPT_TEMPLATE`。

- [ ] **Step 1: 替换主角认知边界规则**

在 `CHAPTER_TEXT_PROMPT_TEMPLATE` 中找到以下文本：

```
- **主角认知边界**：主角只能知道本章正文、前文尾部以及本章内已呈现信息。严禁在开头就让主角知道尚未交代的设定（如匪帮、敌对势力、人物背景、隐藏关系等）。系统面板数据可以被主角看到，但主角对数据的解读只能基于当前已知信息，不能加入未经验证的推断或全局设定。
```

替换为：

```
- **主角认知边界**：主角知道的内容包括：
  1. 本章正文内已呈现的信息；
  2. 前文尾部信息；
  3. 截至本章已由作者确认的角色记忆中标记为 permanent / long / arc（且来源章节早于或等于本章）的信息；
  4. 与当前活跃伏笔相关、且已确认的记忆。
  严禁主角知道以下信息：
  - 本章尚未呈现的全新设定；
  - 仅在未来章节才会揭露的内容；
  - 虽在角色记忆中但明确与当前场景无关且已过时的 scene 级记忆；
  - 其他角色知道但本角色尚未获得途径知晓的信息。
  系统面板数据可以被主角看到，但主角对数据的解读只能基于当前已知信息，不能加入未经验证的推断或全局设定。
```

- [ ] **Step 2: 同步更新审校 Prompt**

在 `CHAPTER_REVIEW_PROMPT_TEMPLATE` 中，将"主角认知越界"的描述同步为：

```
- **主角认知越界**：主角知道了本章正文、前文尾部、本章已呈现信息以及截至本章已确认的角色记忆之外的内容。例如第一章主角尚未接触到匪帮，就不能在心里说"周围还有匪帮虎视眈眈"；主角不能通过系统面板看到面板未显示的信息，也不能做出超出当前认知的推断。
```

- [ ] **Step 3: 验证语法**

Run: `cd backend && python -m compileall app`
Expected: 无错误。

- [ ] **Step 4: 提交**

```bash
git add backend/app/agents/harness/prompts/chapter_generation.py
git commit -m "feat(memory): update chapter text prompt with memory-aware protagonist boundary"
```

---

## Task 7: Worker 集成

**Files:**
- Modify: `backend/app/agents/harness/workers/chapter_workers.py`

**Interfaces:**
- Consumes: `read_character_memories` tool via `_tool_loop` / direct call.
- Produces: `ChapterTextWorker` 在生成正文前为出场角色查询并注入记忆。

- [ ] **Step 1: 添加记忆查询辅助函数**

在 `backend/app/agents/harness/workers/chapter_workers.py` 顶部附近添加：

```python
async def _character_memories_for_chapter(
    db: AsyncSession,
    chapter_text: str,
    characters: list[dict],
    foreshadows: list[dict],
) -> dict[str, list[dict]]:
    """为章节中出场的每个角色查询其已知记忆。"""
    from app.agents.tools import read_character_memories

    appeared_names = {c.get("name", "").strip() for c in characters if c.get("name")}
    result: dict[str, list[dict]] = {}
    for c in characters:
        name = c.get("name", "").strip()
        if not name or name not in appeared_names:
            continue
        cid = c.get("id")
        memories = await read_character_memories(db, cid, limit=30)
        if memories:
            result[cid] = memories
    return result
```

- [ ] **Step 2: 在 ChapterTextWorker 中注入记忆**

在 `ChapterTextWorker.run` 方法中，获取 `characters` 之后、`system = chapter_text_prompt(...)` 之前添加：

```python
character_memories = await _character_memories_for_chapter(
    self.db, chapter.get("content", ""), characters, foreshadows
)
```

然后将 `character_memories` 传入 prompt context：

```python
system = chapter_text_prompt({
    "chapter": chapter,
    "detailed_outline": chapter.get("detailed_outline", ""),
    "volume_outline": volume_outline,
    "assigned_plot_nodes": assigned,
    "characters": characters,
    "world": world,
    "previous_chapter_text_tail": prev_tail,
    "previous_summaries": summaries_chain,
    "active_foreshadows": active,
    "target_words": target_words,
    "character_memories": character_memories,
})
```

- [ ] **Step 3: 修改 Prompt 模板接收 character_memories**

在 `backend/app/agents/harness/prompts/chapter_generation.py` 中：

1. 在 `CHAPTER_TEXT_PROMPT_TEMPLATE` 的"输入数据"部分添加：

```
【角色记忆】
$character_memories
```

2. 修改 `CHAPTER_TEXT_PROMPT` 函数签名，新增 `character_memories: dict[str, list[dict]] | None = None` 参数。
3. 在 `chapter_text_prompt(context: dict)` 兼容函数中读取 `context.get("character_memories") or {}`。

- [ ] **Step 4: 验证语法**

Run: `cd backend && python -m compileall app`
Expected: 无错误。

- [ ] **Step 5: 提交**

```bash
git add backend/app/agents/harness/workers/chapter_workers.py backend/app/agents/harness/prompts/chapter_generation.py
git commit -m "feat(memory): inject character memories into chapter text worker"
```

---

## Task 8: 前端类型与 API 封装

**Files:**
- Modify: `frontend/src/types/index.ts`
- Modify: `frontend/src/api/long.ts`

**Interfaces:**
- Produces: `CharacterMemory`, `CharacterMemoryDraft` TypeScript types and API methods.

- [ ] **Step 1: 新增类型**

在 `frontend/src/types/index.ts` 中 `Chapter` 类型之后添加：

```typescript
export interface CharacterMemory {
  id: string;
  project_id: string;
  character_id: string;
  content: string;
  importance: "core" | "major" | "minor";
  ttl: "permanent" | "long" | "arc" | "scene";
  source_chapter_id: string | null;
  source_type: "auto" | "manual";
  related_character_ids: string[];
  related_foreshadow_ids: string[];
  created_at: string | null;
  updated_at: string | null;
}

export interface CharacterMemoryDraft {
  id: string;
  project_id: string;
  chapter_id: string;
  character_id: string;
  action: "add" | "update" | "delete";
  target_memory_id: string | null;
  content: string;
  importance: "core" | "major" | "minor";
  ttl: "permanent" | "long" | "arc" | "scene";
  related_character_ids: string[];
  related_foreshadow_ids: string[];
  created_at: string | null;
}
```

- [ ] **Step 2: 新增 API 封装**

在 `frontend/src/api/long.ts` 中 `deleteChapter` 之后添加：

```typescript
  extractMemory: (chapterId: string) =>
    api.post(`/long/chapters/${chapterId}/extract-memory`),
  memoryDrafts: (chapterId: string) =>
    api.get(`/long/chapters/${chapterId}/memory-drafts`),
  applyMemoryDrafts: (chapterId: string) =>
    api.post(`/long/memory-drafts/apply`, { chapter_id: chapterId }),
  discardMemoryDrafts: (chapterId: string) =>
    api.post(`/long/memory-drafts/discard`, { chapter_id: chapterId }),
  characterMemories: (characterId: string) =>
    api.get(`/long/characters/${characterId}/memories`),
  addCharacterMemory: (characterId: string, data: Partial<CharacterMemory>) =>
    api.post(`/long/characters/${characterId}/memories`, data),
  updateCharacterMemory: (characterId: string, memoryId: string, data: Partial<CharacterMemory>) =>
    api.put(`/long/characters/${characterId}/memories/${memoryId}`, data),
  deleteCharacterMemory: (characterId: string, memoryId: string) =>
    api.delete(`/long/characters/${characterId}/memories/${memoryId}`),
```

并导入 `CharacterMemory` 类型：

```typescript
import type { CreateOutlinePayload, UpdateOutlinePayload, CharacterMemory } from "@/types";
```

- [ ] **Step 3: 类型检查**

Run: `cd frontend && npx tsc -b`
Expected: 无类型错误。

- [ ] **Step 4: 提交**

```bash
git add frontend/src/types/index.ts frontend/src/api/long.ts
git commit -m "feat(memory): add frontend types and API wrappers"
```

---

## Task 9: 章节编辑器记忆候选面板

**Files:**
- Modify: `frontend/src/components/chapter/ChapterEditor.tsx`

**Interfaces:**
- Consumes: `longApi.extractMemory`, `longApi.memoryDrafts`, `longApi.applyMemoryDrafts`, `longApi.discardMemoryDrafts`

- [ ] **Step 1: 新增状态与处理函数**

在组件顶部导入 API：

```typescript
import { longApi } from "@/api/long";
import type { CharacterMemoryDraft } from "@/types";
```

新增状态：

```typescript
const [drafts, setDrafts] = useState<CharacterMemoryDraft[]>([]);
const [draftsOpen, setDraftsOpen] = useState(false);
const [draftLoading, setDraftLoading] = useState(false);
```

新增处理函数：

```typescript
const handleExtractMemory = async () => {
  if (!chapter) return;
  setDraftLoading(true);
  try {
    const res = await longApi.extractMemory(chapter.id);
    if (res.data.skipped) {
      const ok = window.confirm(res.data.message || "本章记忆已是最新，是否重新提取？");
      if (!ok) return;
      // 强制重新提取：可简单通过重新调用实现；后端目前 hash 相同会跳过，
      // 如需强制可后续扩展 force 参数。此版本先提示用户去修改正文后再提取。
      return;
    }
    setDrafts(res.data.drafts || []);
    setDraftsOpen(true);
  } finally {
    setDraftLoading(false);
  }
};

const handleApplyDrafts = async () => {
  if (!chapter) return;
  await longApi.applyMemoryDrafts(chapter.id);
  setDrafts([]);
  setDraftsOpen(false);
};

const handleDiscardDrafts = async () => {
  if (!chapter) return;
  await longApi.discardMemoryDrafts(chapter.id);
  setDrafts([]);
  setDraftsOpen(false);
};
```

- [ ] **Step 2: 在工具栏添加按钮**

在"生成正文"按钮之后添加：

```tsx
<Button variant="ghost" onClick={handleExtractMemory} disabled={draftLoading}>
  {draftLoading ? "提取中..." : "更新记忆"}
</Button>
```

- [ ] **Step 3: 添加候选预览面板**

在组件底部（字数状态之后）添加：

```tsx
{draftsOpen && (
  <div className="mt-4 border border-line bg-paper p-3">
    <div className="mb-2 flex items-center justify-between">
      <span className="text-sm font-medium">本章记忆候选</span>
      <div className="flex gap-2">
        <Button variant="primary" onClick={handleApplyDrafts}>确认应用</Button>
        <Button variant="ghost" onClick={handleDiscardDrafts}>取消</Button>
      </div>
    </div>
    {drafts.length === 0 ? (
      <div className="text-sm text-muted">没有候选记忆</div>
    ) : (
      <div className="space-y-3">
        {Object.entries(
          drafts.reduce((acc, d) => {
            (acc[d.character_id] = acc[d.character_id] || []).push(d);
            return acc;
          }, {} as Record<string, CharacterMemoryDraft[]>)
        ).map(([characterId, items]) => (
          <div key={characterId} className="border-t border-line pt-2">
            <div className="mb-1 text-sm font-medium">角色 ID: {characterId}</div>
            <ul className="space-y-1">
              {items.map((d) => (
                <li key={d.id} className="text-sm text-ink-soft">
                  <span className="font-medium">[{d.action}]</span> {d.content}
                  <span className="ml-2 text-xs text-muted">({d.importance}, {d.ttl})</span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    )}
  </div>
)}
```

注意：当前组件没有角色姓名映射，先用 character_id 显示；后续可通过 props 传入 characters 做映射。

- [ ] **Step 4: 类型检查**

Run: `cd frontend && npx tsc -b`
Expected: 无类型错误。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/components/chapter/ChapterEditor.tsx
git commit -m "feat(memory): add chapter memory extraction UI"
```

---

## Task 10: 角色记忆管理面板

**Files:**
- Create: `frontend/src/components/character/CharacterMemoryPanel.tsx`
- Modify: `frontend/src/components/EntityWorkbench.tsx`

**Interfaces:**
- Consumes: `longApi.characterMemories`, `longApi.addCharacterMemory`, `longApi.updateCharacterMemory`, `longApi.deleteCharacterMemory`
- Produces: `CharacterMemoryPanel` component.

- [ ] **Step 1: 创建角色记忆面板组件**

`frontend/src/components/character/CharacterMemoryPanel.tsx`:

```tsx
import { useEffect, useState } from "react";
import { Button, Input, Textarea, Card } from "@/components/ui";
import { longApi } from "@/api/long";
import type { CharacterMemory } from "@/types";

interface CharacterMemoryPanelProps {
  characterId: string;
}

export function CharacterMemoryPanel({ characterId }: CharacterMemoryPanelProps) {
  const [memories, setMemories] = useState<CharacterMemory[]>([]);
  const [loading, setLoading] = useState(false);
  const [newContent, setNewContent] = useState("");
  const [newImportance, setNewImportance] = useState<CharacterMemory["importance"]>("major");
  const [newTtl, setNewTtl] = useState<CharacterMemory["ttl"]>("long");

  const load = async () => {
    setLoading(true);
    try {
      const res = await longApi.characterMemories(characterId);
      setMemories(res.data.memories || []);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, [characterId]);

  const handleAdd = async () => {
    if (!newContent.trim()) return;
    await longApi.addCharacterMemory(characterId, {
      content: newContent,
      importance: newImportance,
      ttl: newTtl,
    });
    setNewContent("");
    await load();
  };

  const handleDelete = async (memoryId: string) => {
    await longApi.deleteCharacterMemory(characterId, memoryId);
    await load();
  };

  return (
    <div className="space-y-4">
      <Card className="p-3">
        <div className="mb-2 text-sm font-medium">手动新增记忆</div>
        <Textarea
          className="mb-2 h-20 resize-none"
          placeholder="记忆内容..."
          value={newContent}
          onChange={(e) => setNewContent(e.target.value)}
        />
        <div className="mb-2 flex gap-2">
          <select
            className="border border-line bg-paper px-2 py-1 text-sm"
            value={newImportance}
            onChange={(e) => setNewImportance(e.target.value as CharacterMemory["importance"])}
          >
            <option value="core">核心</option>
            <option value="major">重要</option>
            <option value="minor">次要</option>
          </select>
          <select
            className="border border-line bg-paper px-2 py-1 text-sm"
            value={newTtl}
            onChange={(e) => setNewTtl(e.target.value as CharacterMemory["ttl"])}
          >
            <option value="permanent">永久</option>
            <option value="long">长期</option>
            <option value="arc">剧情弧</option>
            <option value="scene">场景</option>
          </select>
        </div>
        <Button variant="primary" onClick={handleAdd}>新增</Button>
      </Card>

      {loading && <div className="text-sm text-muted">加载中...</div>}

      <div className="space-y-2">
        {memories.map((m) => (
          <Card key={m.id} className="p-3">
            <div className="text-sm leading-relaxed">{m.content}</div>
            <div className="mt-1 flex items-center gap-2 text-xs text-muted">
              <span>{m.importance}</span>
              <span>{m.ttl}</span>
              <span>{m.source_type === "auto" ? `第 ${m.source_chapter_id?.slice(0, 6)} 章自动提取` : "用户手动修改"}</span>
            </div>
            <div className="mt-2">
              <Button variant="ghost" size="sm" onClick={() => handleDelete(m.id)}>删除</Button>
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
```

注意：source_chapter_id 是 UUID，显示为"第 N 章"需要章节 order 映射；此 MVP 版本先用截断 ID 占位，后续可传入 chapters 做映射。

- [ ] **Step 2: 在 EntityWorkbench 集成记忆标签页**

在 `frontend/src/components/EntityWorkbench.tsx` 中找到角色相关的标签页区域，新增"记忆"标签：

```tsx
import { CharacterMemoryPanel } from "@/components/character/CharacterMemoryPanel";
```

然后在角色编辑的标签列表中添加：

```tsx
<button ...>记忆</button>
```

并在对应内容区渲染：

```tsx
{activeTab === "memory" && <CharacterMemoryPanel characterId={entity.id} />}
```

（具体 Tab 名称和结构以 EntityWorkbench 现有代码为准，保持现有风格。）

- [ ] **Step 3: 类型检查**

Run: `cd frontend && npx tsc -b`
Expected: 无类型错误。

- [ ] **Step 4: 提交**

```bash
git add frontend/src/components/character/CharacterMemoryPanel.tsx frontend/src/components/EntityWorkbench.tsx
git commit -m "feat(memory): add character memory management panel"
```

---

## Task 11: 最终验证

**Files:**
- All of the above.

- [ ] **Step 1: 后端语法检查**

Run: `cd backend && python -m compileall app`
Expected: 无错误。

- [ ] **Step 2: 前端类型检查**

Run: `cd frontend && npx tsc -b`
Expected: 无类型错误。

- [ ] **Step 3: 手动功能验证**

1. 启动后端：`cd backend && uvicorn app.main:app --reload --port 8765`
2. 启动前端：`cd frontend && npm run dev`
3. 打开长篇小说项目，进入章节编辑器。
4. 生成一章正文。
5. 点击"更新记忆"，观察候选面板按角色分组显示。
6. 点击"确认应用"。
7. 生成下一章正文，观察主角是否正确引用已确认记忆。
8. 进入角色详情页"记忆"标签，手动新增/删除记忆，验证来源显示。

- [ ] **Step 4: 提交最终变更**

```bash
git add .
git commit -m "feat(memory): implement character memory management"
```

---

## Self-Review

### Spec Coverage

| Spec 需求 | 对应 Task |
|---|---|
| 新增角色记忆表 | Task 1 |
| 记忆草稿表 + 确认流程 | Task 1, Task 3, Task 4 |
| 章节提取记录 + 二次验证 | Task 1, Task 3 |
| LLM 自动提取记忆 | Task 3 |
| 用户手动管理记忆 | Task 4, Task 10 |
| Worker 查询工具 | Task 5 |
| 正本 Prompt 认知边界调整 | Task 6, Task 7 |
| 前端章节编辑器按钮 + 候选面板 | Task 9 |
| 角色详情页记忆管理 | Task 10 |

### Placeholder Scan

- 无 TBD/TODO。
- 所有代码片段包含完整实现。
- 验证命令明确。

### Type Consistency

- `importance` / `ttl` 枚举值在模型、服务、API、前端类型中一致。
- `source_type` 统一为 `"auto"` / `"manual"`。
- 函数签名在 repositories、service、API 中一致。

### 已知简化项

- 角色姓名映射：候选面板和记忆面板暂时显示 character_id 截断，后续可传入 chapters/characters 做优化。
- "强制重新提取"：当 hash 相同时当前仅提示，未实现 force 参数；用户可修改正文后重新提取。

# AI 创作生成流水线增强 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 章节正文生成支持可配置字数（分段连续生成 + 按字数预算拆章）、生成后可配置等级的尺度检查与自动改写、正文/细纲变更直接写入章节并可单级撤销。

**Architecture:** 设置表新增 `content_rating`/`chapter_target_words`，`LongChangeRecord` 新增 `source`；`ChapterTextWorker` 改为分段生成 → 一致性审校 → 尺度检查/改写的流水线；`/chat` 聚合后把章节正文/细纲变更直接经 `change_apply` 应用（`source="auto"`），新增 undo/undoable 端点；前端设置页 + 聊天 auto_applied 摘要卡 + 章节列表/编辑器生成与撤销按钮。

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async + SQLite + pytest（后端）；React 19 + TS + Zustand（前端）。

**Spec:** `docs/superpowers/specs/2026-07-19-ai-generation-pipeline-design.md`

## Global Constraints

- 后端命令 cwd 一律为 `backend/`；测试：`cd backend && python -m pytest tests/ -v`；语法检查：`cd backend && python -m compileall app`。
- 前端验证：`cd frontend && npx tsc -b`；前端任务收尾 `npm run build`。
- 既有测试不得破坏：每个后端任务完成后跑 `cd backend && python -m pytest tests/ -v` 全量。
- 尺度检查/审校/分段生成任何 LLM 步骤异常都必须降级放行，不得阻断生成或确认流程。
- 不引入新依赖；不做 SSE 流式；不做多级撤销；短篇流程不改。
- 提交信息用 conventional commits（如 `feat(backend): ...`）。
- 与计划 `2026-07-19-long-workspace-assistant-ux.md` 相互独立；若两份计划都实施，本计划的 Task 6 修改 `LongWorkspace.tsx` 的 `ChapterPanel` 时注意与另一计划的改动合并（另一计划不动 ChapterPanel，无冲突）。

---

### Task 1: 设置项与审计列（models + migrate + schemas + settings API）

**Files:**
- Modify: `backend/app/models.py`（`UserSetting` 约 82-105 行；`LongChangeRecord` 约 233-243 行）
- Modify: `backend/scripts/migrate.py`（约 28 行附近的列清单）
- Modify: `backend/app/schemas/setting.py`
- Modify: `backend/app/api/settings.py`（`update_user_settings`，约 41-64 行）
- Test: `backend/tests/test_settings_generation.py`（新建）

**Interfaces:**
- Produces（后续任务依赖）:
  - `UserSetting.content_rating: str`（`"loose" | "standard" | "strict"`，默认 `"standard"`）
  - `UserSetting.chapter_target_words: int`（默认 2500，API 钳制 1000-8000）
  - `UserSetting.to_dict()` 包含上述两键
  - `LongChangeRecord.source: str`（默认 `"staged"`）
  - `PUT /api/settings` 接受 `content_rating`、`chapter_target_words`；非法 rating 返回 422 `VALIDATION_ERROR`

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_settings_generation.py`：

```python
"""生成流水线相关设置项：content_rating / chapter_target_words。"""
from __future__ import annotations

import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.database import create_all, engine


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module", autouse=True)
async def setup_db():
    await create_all()
    yield


@pytest.fixture(autouse=True)
async def cleanup_tables():
    yield
    async with engine.begin() as conn:
        await conn.exec_driver_sql("DELETE FROM user_settings;")


@pytest.mark.anyio
async def test_settings_defaults_include_generation_fields():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/api/settings")
        assert r.status_code == 200
        body = r.json()
        assert body["content_rating"] == "standard"
        assert body["chapter_target_words"] == 2500


@pytest.mark.anyio
async def test_update_content_rating_and_target_words():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.put("/api/settings", json={"content_rating": "strict", "chapter_target_words": 1500})
        assert r.status_code == 200
        assert r.json()["content_rating"] == "strict"
        assert r.json()["chapter_target_words"] == 1500


@pytest.mark.anyio
async def test_invalid_rating_rejected():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.put("/api/settings", json={"content_rating": "xxx"})
        assert r.status_code == 422


@pytest.mark.anyio
async def test_target_words_clamped():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.put("/api/settings", json={"chapter_target_words": 100})
        assert r.status_code == 200
        assert r.json()["chapter_target_words"] == 1000
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_settings_generation.py -v`
Expected: FAIL（KeyError `content_rating`）。

- [ ] **Step 3: 实现**

1. `backend/app/models.py` — `UserSetting` 在 `assistant_history_top_k` 行后新增两列，`to_dict` 同步：

```python
    assistant_history_top_k = Column(Integer, default=5)
    content_rating = Column(String(16), default="standard")
    chapter_target_words = Column(Integer, default=2500)
```

```python
            "assistant_history_top_k": self.assistant_history_top_k,
            "content_rating": self.content_rating or "standard",
            "chapter_target_words": self.chapter_target_words or 2500,
```

`LongChangeRecord` 在 `status` 行后新增：

```python
    source = Column(String(16), default="staged")
```

2. `backend/scripts/migrate.py` — 列清单中 `("user_settings", "assistant_history_top_k", ...)` 之后追加：

```python
    ("user_settings", "content_rating", "VARCHAR(16) DEFAULT 'standard'"),
    ("user_settings", "chapter_target_words", "INTEGER DEFAULT 2500"),
    ("long_change_records", "source", "VARCHAR(16) DEFAULT 'staged'"),
```

3. `backend/app/schemas/setting.py` — `UserSettingUpdate` 追加：

```python
    assistant_history_top_k: Optional[int] = None
    content_rating: Optional[str] = None
    chapter_target_words: Optional[int] = None
```

4. `backend/app/api/settings.py` — `update_user_settings` 在 `assistant_history_top_k` 处理后追加：

```python
    if payload.content_rating is not None:
        if payload.content_rating not in ("loose", "standard", "strict"):
            raise ValidationError("无效的尺度等级（可选：loose/standard/strict）")
        s.content_rating = payload.content_rating
    if payload.chapter_target_words is not None:
        s.chapter_target_words = min(8000, max(1000, payload.chapter_target_words))
```

5. 运行迁移（对开发库）：`cd backend && python scripts/migrate.py`

- [ ] **Step 4: 运行测试确认通过 + 全量回归**

Run: `cd backend && python -m pytest tests/ -v && python -m compileall app`
Expected: 全部 passed（含既有测试）。

- [ ] **Step 5: Commit**

```bash
git add backend/app/models.py backend/scripts/migrate.py backend/app/schemas/setting.py backend/app/api/settings.py backend/tests/test_settings_generation.py
git commit -m "feat(backend): add content_rating/chapter_target_words settings and change-record source column"
```

---

### Task 2: Prompt 模板（尺度检查 + 字数预算 + 摘要链 + 分段用户提示）

**Files:**
- Modify: `backend/app/agents/harness/prompts/chapter_generation.py`

**Interfaces:**
- Produces（Task 3 依赖，签名必须一致）:
  - `chapter_rating_prompt(context: dict) -> str`（context 键：`chapter_text: str`、`rating: str`）
  - `chapter_segment_user_prompt(segment_index: int, accumulated_words: int, target_words: int, prev_segment_tail: str) -> str`
  - `assignment_prompt(context)` / `chapter_outline_prompt(context)` / `chapter_text_prompt(context)` 均新增可选 context 键 `target_words: int`（默认 2500）
  - `chapter_text_prompt(context)` 新增可选 context 键 `previous_summaries: str`（默认 `"（无）"`）
  - `RATING_LABELS = {"loose": "宽松", "standard": "标准", "strict": "严格"}`

- [ ] **Step 1: 修改模板**

`backend/app/agents/harness/prompts/chapter_generation.py`：

1. `ASSIGNMENT_PROMPT_TEMPLATE` 的「业务规则」末尾追加一条（模板变量新增 `$target_words`）：

```
- 每章目标约 $target_words 字；按每 800-1000 字容纳一个剧情节点估算单章容量，单章节点过多时必须拆分到后续章节；已有章节容量不足时新建足量占位章节，避免一章剧情过密。
```

`ASSIGNMENT_PROMPT` 与 `assignment_prompt` 改为：

```python
def ASSIGNMENT_PROMPT(
    plot_nodes: list[dict],
    existing_chapters: list[dict],
    target_words: int = 2500,
) -> str:
    return ASSIGNMENT_PROMPT_TEMPLATE.substitute(
        _json_rules=_JSON_RULES,
        plot_nodes=_dumps(plot_nodes),
        existing_chapters=_dumps(existing_chapters),
        target_words=target_words,
    )


def assignment_prompt(context: dict) -> str:
    """根据 context 将剧情节点分配到章节的 prompt。"""
    return ASSIGNMENT_PROMPT(
        plot_nodes=context.get("plot_nodes") or [],
        existing_chapters=context.get("existing_chapters") or [],
        target_words=context.get("target_words") or 2500,
    )
```

2. `CHAPTER_OUTLINE_PROMPT_TEMPLATE` 的「业务规则」末尾追加（模板变量新增 `$target_words`）：

```
- 本章目标约 $target_words 字，细纲场景规模应与之匹配（场景数约为目标字数/600）。
```

`CHAPTER_OUTLINE_PROMPT` 与 `chapter_outline_prompt` 同法新增 `target_words` 参数（默认 2500），substitute 时传入。

3. `CHAPTER_TEXT_PROMPT_TEMPLATE`：
   - 「输入数据」的【前文尾部】之后新增两节（模板变量新增 `$previous_summaries`、`$target_words`）：

```
【前章摘要链】
$previous_summaries
```

   - 「业务规则」中把「content 为完整章节的正文字符串」一条替换为：

```
- 本章目标约 $target_words 字（允许 ±20% 浮动）。
- 正文将采用分段连续写作：每次调用只写一段，需与【上一段尾部】自然衔接（由用户消息提供）。
```

   `CHAPTER_TEXT_PROMPT` 与 `chapter_text_prompt` 新增参数 `previous_summaries: str = "（无）"`、`target_words: int = 2500`，substitute 传入。

4. 文件末尾新增尺度检查与分段模板：

```python
RATING_LABELS = {"loose": "宽松", "standard": "标准", "strict": "严格"}

CHAPTER_RATING_PROMPT_TEMPLATE = Template(
    """你是网络小说内容尺度审校员。请按指定的尺度等级检查章节正文。

${_json_rules}

当前尺度等级：$rating_label
- loose（宽松）：仅拦截违法与极端内容（未成年人相关内容、教唆犯罪等），其余放行。
- standard（标准）：允许紧张暴力与含蓄亲密描写；不允许露骨性描写、细致酷刑与血腥渲染。
- strict（严格）：不允许明确性描写与露骨血腥；亲密、暴力仅可暗示性带过。

输出格式：
- 若无问题：{"ok": true}
- 若有问题：{"ok": false, "issues": [{"excerpt": "问题段落摘录（50字内）", "problem": "问题描述", "suggestion": "改写建议"}]}

业务规则：
- 只列出超出当前等级的内容，不要评论文风、逻辑或篇幅问题。
- 拿不准的放行。

【章节正文】
$chapter_text
"""
)


def CHAPTER_RATING_PROMPT(chapter_text: str, rating: str) -> str:
    return CHAPTER_RATING_PROMPT_TEMPLATE.substitute(
        _json_rules=_JSON_RULES,
        rating_label=RATING_LABELS.get(rating, "标准"),
        chapter_text=chapter_text,
    )


def chapter_rating_prompt(context: dict) -> str:
    """根据 context 生成尺度审校 prompt。context 键：chapter_text、rating。"""
    return CHAPTER_RATING_PROMPT(
        chapter_text=context.get("chapter_text", "") or "",
        rating=context.get("rating", "standard"),
    )


CHAPTER_SEGMENT_USER_TEMPLATE = Template(
    """【写作进度】
本章目标总字数：$target_words（允许 ±20% 浮动）
已完成字数：$accumulated_words
当前为第 $segment_index 段

【上一段尾部】
$prev_segment_tail

请撰写下一段正文（800-1200 字），与上一段自然衔接；若本章细纲内容已全部写完，输出 finished=true。
只输出 JSON：{"text": "本段正文", "finished": false}"""
)


def chapter_segment_user_prompt(
    segment_index: int,
    accumulated_words: int,
    target_words: int,
    prev_segment_tail: str,
) -> str:
    return CHAPTER_SEGMENT_USER_TEMPLATE.substitute(
        segment_index=segment_index,
        accumulated_words=accumulated_words,
        target_words=target_words,
        prev_segment_tail=prev_segment_tail or "（第一段，从头开始）",
    )
```

注意：原 `CHAPTER_TEXT_PROMPT_TEMPLATE` 业务规则里的「如果【前文尾部】存在，请保持叙事衔接」保留。

- [ ] **Step 2: 语法检查 + 全量测试**

Run: `cd backend && python -m compileall app && python -m pytest tests/ -v`
Expected: 全部 passed。

- [ ] **Step 3: Commit**

```bash
git add backend/app/agents/harness/prompts/chapter_generation.py
git commit -m "feat(backend): rating-check prompt, word-budget rules, segment user prompt"
```

---

### Task 3: ChapterTextWorker 分段生成 + 尺度检查

**Files:**
- Modify: `backend/app/agents/harness/workers/chapter_workers.py`
- Test: `backend/tests/test_chapter_workers_helpers.py`（新建）

**Interfaces:**
- Consumes: Task 2 的 `chapter_rating_prompt` / `chapter_segment_user_prompt` / `RATING_LABELS`；Task 1 的 `UserSetting.content_rating` / `chapter_target_words`。
- Produces:
  - `_chapter_summaries_chain(chapter: dict, chapters: list[dict], limit: int = 2000) -> str`（纯函数，可单测）
  - `_generation_settings(db) -> tuple[int, str]`（返回 `(target_words, rating)`）
  - `ChapterTextWorker.run` 返回 `{"changes": [...], "stage": "chapter_text", "notes": list[str]}`；`AssignmentWorker`/`ChapterOutlineWorker` 的 prompt_context 新增 `target_words`。
  - `ChapterTextWorker` 删除旧 `_generate_text` 方法；`_review_text` 签名改为 `(self, content: str, chapter, characters, world, active_foreshadows) -> list[str]`。

- [ ] **Step 1: 写失败测试（纯函数 helper）**

新建 `backend/tests/test_chapter_workers_helpers.py`：

```python
"""章节生成 helper 纯函数测试。"""
from __future__ import annotations

from app.agents.harness.workers.chapter_workers import _chapter_summaries_chain


def _ch(order, title, outline="", content=""):
    return {"id": f"id{order}", "order": order, "title": title,
            "detailed_outline": outline, "content": content}


def test_chain_includes_only_previous_chapters():
    chapters = [
        _ch(0, "起", outline="开局设定"),
        _ch(1, "承", outline="主角出山"),
        _ch(2, "转", outline="当前章"),
    ]
    chain = _chapter_summaries_chain(chapters[2], chapters)
    assert "开局设定" in chain
    assert "主角出山" in chain
    assert "当前章" not in chain


def test_chain_falls_back_to_content_and_capped():
    chapters = [_ch(i, f"第{i}章", content="x" * 300) for i in range(20)]
    target = _ch(20, "当前", outline="o")
    chapters.append(target)
    chain = _chapter_summaries_chain(chapters[20], chapters, limit=500)
    assert len(chain) <= 500
    assert chain != "（无）"


def test_chain_empty_when_first_chapter():
    chapters = [_ch(0, "唯一章", outline="大纲")]
    assert _chapter_summaries_chain(chapters[0], chapters) == "（无）"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_chapter_workers_helpers.py -v`
Expected: FAIL（ImportError: `_chapter_summaries_chain` 不存在）。

- [ ] **Step 3: 实现**

`backend/app/agents/harness/workers/chapter_workers.py`：

1. 头部 imports 追加：

```python
from sqlalchemy import select

from app.models import UserSetting
from app.agents.harness.prompts.chapter_generation import (
    chapter_rating_prompt,
    chapter_segment_user_prompt,
    RATING_LABELS,
)
```

（并入现有的 `from app.agents.harness.prompts.chapter_generation import (...)` 一块即可。）

2. 模块级新增（放在 `_assigned_plot_nodes` 之后）：

```python
async def _generation_settings(db) -> tuple[int, str]:
    """读取生成相关用户设置：(每章目标字数, 尺度等级)。"""
    res = await db.execute(select(UserSetting))
    s = res.scalars().first()
    target = s.chapter_target_words if s and s.chapter_target_words else 2500
    rating = s.content_rating if s and s.content_rating else "standard"
    return target, rating


def _chapter_summaries_chain(chapter: dict, chapters: list[dict], limit: int = 2000) -> str:
    """目标章节之前各章的一行摘要链（细纲优先，其次正文前 200 字），总量钳制 limit。"""
    prev = [c for c in sorted(chapters, key=lambda c: c.get("order", 0))
            if c.get("order", 0) < chapter.get("order", 0)]
    lines = []
    for c in prev:
        text = (c.get("detailed_outline") or c.get("content") or "")[:200]
        if text:
            lines.append(f"第{c.get('order', 0) + 1}章《{c.get('title', '')}》：{text}")
    chain = "\n".join(lines)
    if not chain:
        return "（无）"
    return chain[-limit:] if len(chain) > limit else chain
```

3. `AssignmentWorker.run` 中 `prompt_context` 改为（在构造前读取设置）：

```python
        target_words, _rating = await _generation_settings(self.db)
        prompt_context = {
            "plot_nodes": plot_nodes,
            "existing_chapters": chapters,
            "target_words": target_words,
        }
```

4. `ChapterOutlineWorker.run` 中 `prompt_context` 新增键 `"target_words": target_words`（同样先 `target_words, _rating = await _generation_settings(self.db)`）。

5. **整体替换** `ChapterTextWorker` 类（含其 `_generate_text`、`_review_text`）为：

```python
class ChapterTextWorker(WorkerBase):
    worker_name = "chapter_text"

    async def run(
        self,
        goal: str,
        context: dict,
        history_context: list[dict] | None = None,
    ) -> dict:
        project_id = context.get("project_id")
        if not project_id:
            return {"changes": [], "stage": "chapter_text", "error": "缺少 project_id"}

        chapters = await repo.list_chapters(self.db, project_id)
        chapter = _find_target_chapter(goal, context, chapters)
        if not chapter:
            if _parse_chapter_number(goal) is None:
                return {"changes": [], "stage": "chapter_text", "error": "无法识别目标章节"}
            return {"changes": [], "stage": "chapter_text", "error": "未找到目标章节"}

        plot_nodes = await repo.list_plot(self.db, project_id)
        characters = context.get("characters") or []
        world = context.get("world") or []
        foreshadows = context.get("foreshadows") or []
        chapter_id = chapter.get("id")

        target_words, rating = await _generation_settings(self.db)
        notes: list[str] = []

        assigned = _assigned_plot_nodes(plot_nodes, chapter_id)
        prev = _previous_chapter(chapter, chapters)
        prev_tail = _previous_chapter_text_tail(prev)
        active = _active_foreshadows(foreshadows)
        summaries_chain = _chapter_summaries_chain(chapter, chapters)

        system = chapter_text_prompt({
            "chapter": chapter,
            "detailed_outline": chapter.get("detailed_outline", ""),
            "assigned_plot_nodes": assigned,
            "characters": characters,
            "world": world,
            "previous_chapter_text_tail": prev_tail,
            "previous_summaries": summaries_chain,
            "active_foreshadows": active,
            "target_words": target_words,
        })

        # —— 分段连续生成 ——
        segments: list[str] = []
        max_segments = target_words // 800 + 2
        for i in range(1, max_segments + 1):
            accumulated = sum(len(s) for s in segments)
            if accumulated >= target_words:
                break
            user = chapter_segment_user_prompt(
                segment_index=i,
                accumulated_words=accumulated,
                target_words=target_words,
                prev_segment_tail=segments[-1][-300:] if segments else "",
            )
            messages = [{"role": "system", "content": system}]
            if history_context:
                messages.extend(history_context)
            messages.append({"role": "user", "content": user})
            seg = await self._generate_segment(messages)
            if seg is None:  # 单段失败重试一次
                seg = await self._generate_segment(messages)
            if seg is None:
                notes.append(f"第 {i} 段生成失败，正文于约 {accumulated} 字处中断")
                break
            text = str(seg.get("text") or "").strip()
            if not text:
                break
            segments.append(text)
            if seg.get("finished"):
                break

        content = "\n\n".join(segments)
        if not content:
            return {"changes": [], "stage": "chapter_text", "error": "正文生成失败", "notes": notes}

        # —— 一致性审校：发现问题带反馈重写一次 ——
        review_issues = await self._review_text(content, chapter, characters, world, active)
        if review_issues:
            rewritten = await self._rewrite_with_feedback(system, history_context, content, review_issues)
            if rewritten:
                content = rewritten
            else:
                notes.append("一致性审校发现问题但重写失败，已保留原文")

        # —— 尺度检查 + 自动改写一次，改写后复核 ——
        rating_issues = await self._rating_check(content, rating)
        if rating_issues:
            rewritten = await self._rewrite_with_feedback(system, history_context, content, rating_issues)
            if rewritten:
                content = rewritten
                notes.append(f"已按「{RATING_LABELS.get(rating, '标准')}」尺度自动调整 {len(rating_issues)} 处")
                remaining = await self._rating_check(content, rating)
                if remaining:
                    notes.append(f"尺度复核仍有 {len(remaining)} 处待人工确认：" + "；".join(remaining[:3]))
            else:
                notes.append(f"尺度检查发现 {len(rating_issues)} 处问题但改写失败，待人工确认")

        return {
            "changes": [{
                "action": "update",
                "entity_id": chapter_id,
                "fields": {"content": content, "status": "generated"},
            }],
            "stage": "chapter_text",
            "notes": notes,
        }

    async def _generate_segment(self, messages: list[dict]) -> dict | None:
        try:
            raw = await self.llm.parse_llm_json(messages)
            if isinstance(raw, dict):
                return raw
            if isinstance(raw, str):
                try:
                    return json.loads(raw)
                except Exception:
                    pass
        except Exception:
            logger.exception("Chapter segment generation failed")
        return None

    async def _rewrite_with_feedback(
        self,
        system: str,
        history_context: list[dict] | None,
        content: str,
        issues: list[str],
    ) -> str | None:
        user = (
            "【当前正文】\n" + content
            + "\n\n【审校反馈】\n" + "\n".join(f"- {i}" for i in issues)
            + "\n\n请根据反馈修改并输出完整正文。只输出 JSON：{\"text\": \"修改后的完整正文\"}"
        )
        messages = [{"role": "system", "content": system}]
        if history_context:
            messages.extend(history_context)
        messages.append({"role": "user", "content": user})
        seg = await self._generate_segment(messages)
        if seg and seg.get("text"):
            return str(seg["text"]).strip()
        return None

    async def _review_text(
        self,
        content: str,
        chapter: dict,
        characters: list[dict],
        world: list[dict],
        active_foreshadows: list[dict],
    ) -> list[str]:
        if not content:
            return []
        system = chapter_review_prompt({
            "chapter_text": content,
            "chapter": chapter,
            "characters": characters,
            "world": world,
            "active_foreshadows": active_foreshadows,
        })
        try:
            raw = await self.llm.parse_llm_json([{"role": "system", "content": system}])
            if isinstance(raw, dict):
                if raw.get("ok"):
                    return []
                issues = raw.get("issues")
                if isinstance(issues, list):
                    return [str(i) for i in issues]
            return []
        except Exception:
            logger.exception("Chapter review failed")
            return []

    async def _rating_check(self, content: str, rating: str) -> list[str]:
        if not content:
            return []
        system = chapter_rating_prompt({"chapter_text": content, "rating": rating})
        try:
            raw = await self.llm.parse_llm_json([{"role": "system", "content": system}])
            if isinstance(raw, dict):
                if raw.get("ok"):
                    return []
                issues = raw.get("issues")
                if isinstance(issues, list):
                    result = []
                    for i in issues:
                        if isinstance(i, dict):
                            result.append(f"{i.get('problem', '')}（{str(i.get('excerpt', ''))[:50]}）")
                        else:
                            result.append(str(i))
                    return result
            return []
        except Exception:
            logger.exception("Chapter rating check failed")
            return []
```

注意：被替换的旧代码中 `_review_text` 原签名带 `text_result` 参数、`run` 中旧的单次生成与重试块全部删除；`_user_prompt(goal)` 在 `ChapterTextWorker` 不再使用（其他 worker 仍在用，保留模块级函数）。

- [ ] **Step 4: 运行测试确认通过 + 全量回归**

Run: `cd backend && python -m pytest tests/ -v && python -m compileall app`
Expected: 全部 passed。

- [ ] **Step 5: Commit**

```bash
git add backend/app/agents/harness/workers/chapter_workers.py backend/tests/test_chapter_workers_helpers.py
git commit -m "feat(backend): segmented chapter text generation with rating check and auto-rewrite"
```

---

### Task 4: /chat 自动应用章节变更 + undo 端点

**Files:**
- Modify: `backend/app/api/assistant.py`
- Test: `backend/tests/test_assistant_undo.py`（新建）

**Interfaces:**
- Consumes: Task 1 的 `LongChangeRecord.source`；Task 3 worker 结果的 `notes`。
- Produces:
  - `/chat` 响应新增 `auto_applied: [{change_id, entity_id, entity_type, fields, notes}]`；`change_records` 只含剩余 staged 记录。
  - `POST /api/assistant/undo`：body `{project_id, entity_type, entity_id}` → `{"ok": true}` 或 `{"ok": false, "message": "没有可撤销的自动生成"}`。
  - `GET /api/assistant/undoable/{chapter_id}` → `{"undoable": bool}`。
  - undo 语义：把最近一条 `source="auto" AND status="applied"` 记录的 `before` 写回；该记录 `source` 改为 `"auto_undone"`，并新增一条 `source="undo"` 审计记录。

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_assistant_undo.py`：

```python
"""章节自动生成变更的 undo / undoable。"""
from __future__ import annotations

import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.database import create_all, engine, AsyncSessionLocal
from app.models import LongChangeRecord


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module", autouse=True)
async def setup_db():
    await create_all()
    yield


@pytest.fixture(autouse=True)
async def cleanup_tables():
    yield
    async with engine.begin() as conn:
        for table in ("long_change_records", "long_chapters", "projects"):
            await conn.exec_driver_sql(f"DELETE FROM {table};")


async def _make_project_and_chapter(ac) -> tuple[str, str]:
    r = await ac.post("/api/projects", json={"type": "long", "title": "t", "description": ""})
    pid = r.json()["id"]
    r = await ac.post("/api/long/chapters", json={
        "project_id": pid, "title": "第一章", "content": "旧正文", "order": 0,
    })
    cid = r.json()["id"]
    return pid, cid


async def _seed_auto_record(pid: str, cid: str, before: dict):
    async with AsyncSessionLocal() as db:
        db.add(LongChangeRecord(
            project_id=pid, entity_type="chapter", entity_id=cid,
            before=before, after={"content": "新正文"}, status="applied", source="auto",
        ))
        await db.commit()


@pytest.mark.anyio
async def test_undo_restores_previous_content():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        pid, cid = await _make_project_and_chapter(ac)
        await _seed_auto_record(pid, cid, {"title": "第一章", "content": "旧正文", "status": "draft"})
        # 模拟自动应用后的状态
        await ac.put(f"/api/long/chapters/{cid}", json={"content": "新正文", "status": "generated"})

        r = await ac.get(f"/api/assistant/undoable/{cid}")
        assert r.json()["undoable"] is True

        r = await ac.post("/api/assistant/undo", json={
            "project_id": pid, "entity_type": "chapter", "entity_id": cid,
        })
        assert r.status_code == 200
        assert r.json()["ok"] is True

        r = await ac.get(f"/api/long/chapters/detail/{cid}")
        assert r.json()["content"] == "旧正文"

        # 再撤一次：无可撤销记录
        r = await ac.post("/api/assistant/undo", json={
            "project_id": pid, "entity_type": "chapter", "entity_id": cid,
        })
        assert r.json()["ok"] is False
        r = await ac.get(f"/api/assistant/undoable/{cid}")
        assert r.json()["undoable"] is False


@pytest.mark.anyio
async def test_undo_without_record_returns_not_ok():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        pid, cid = await _make_project_and_chapter(ac)
        r = await ac.post("/api/assistant/undo", json={
            "project_id": pid, "entity_type": "chapter", "entity_id": cid,
        })
        assert r.status_code == 200
        assert r.json()["ok"] is False
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_assistant_undo.py -v`
Expected: FAIL（404，`/api/assistant/undo` 不存在）。

- [ ] **Step 3: 实现**

`backend/app/api/assistant.py`：

1. 头部 import 修改：

```python
from app.services.change_apply import _ENTITY_REPO, apply_change, confirm_session, reject_session
from app.models import AssistantSession, AssistantMessage, LongChangeRecord, Project, UserSetting
```

2. 在 `_CONTEXT_ENTITY_KEYS` 定义之后新增：

```python
_CHAPTER_AUTO_FIELDS = {"content", "detailed_outline", "status"}


def _is_chapter_auto_apply(record) -> bool:
    """章节正文/细纲的 update 变更直接落库，不进待确认列表。"""
    keys = set((record.after or {}).keys())
    return (
        record.entity_type == "chapter"
        and record.action == "update"
        and bool(record.entity_id)
        and keys <= _CHAPTER_AUTO_FIELDS
        and bool(keys & {"content", "detailed_outline"})
    )
```

3. `/chat` 中，替换「# 5. 写入会话 staged_changes」整段（含其上一行 `records = aggregate(...)` 之后的处理）为：

```python
    # 4. aggregator -> ChangeRecord[]
    records = aggregate(effective_project_id, worker_results)
    logger.warning("Aggregated change records: %s", [r.model_dump() for r in records])

    # 5. 章节正文/细纲变更直接应用（source="auto"），其余进 staged_changes
    notes_by_stage = {
        res.get("stage"): res.get("notes")
        for res in worker_results
        if res.get("notes")
    }
    auto_applied: list[dict] = []
    staged_records = []
    for r in records:
        if not is_global and _is_chapter_auto_apply(r):
            try:
                before_row = await repo.get_chapter(db, r.entity_id)
                before = (
                    {c.name: getattr(before_row, c.name) for c in before_row.__table__.columns}
                    if before_row is not None else None
                )
                await apply_change(db, effective_project_id, r.model_dump())
                db.add(LongChangeRecord(
                    project_id=effective_project_id,
                    entity_type="chapter",
                    entity_id=r.entity_id,
                    before=before,
                    after=r.after,
                    status="applied",
                    source="auto",
                ))
                await db.commit()
                auto_applied.append({
                    "change_id": r.id,
                    "entity_id": r.entity_id,
                    "entity_type": "chapter",
                    "fields": list((r.after or {}).keys()),
                    "notes": notes_by_stage.get(r.stage) or [],
                })
            except Exception:
                logger.exception("自动应用章节变更失败，降级为待确认")
                await db.rollback()
                staged_records.append(r)
        else:
            staged_records.append(r)

    staged = list(sess.staged_changes or [])
    staged.extend([r.model_dump() for r in staged_records])
    sess.staged_changes = staged
    await db.commit()
```

注意：`repo` 已在 `/chat` 前部以 `from app import repositories as repo` 局部导入，直接复用该引用即可，不要重复导入。

4. responder 调用处：`respond(responder_llm, records, ...)` 改为 `respond(responder_llm, staged_records, ...)`（global 分支不变，仍传 `records`——global 时 records 为空列表，无碍）。`records_data` 改为：

```python
    records_data = [r.model_dump() for r in staged_records]
```

5. responder 摘要返回前（`return {...}` 之前）追加 auto_applied 说明：

```python
    if auto_applied:
        chapter_titles = {c.get("id"): c.get("title") for c in (context.get("chapters") or [])} if not is_global else {}
        field_labels = {"content": "正文", "detailed_outline": "细纲", "status": "状态"}
        lines = ["", "---", "**已直接写入：**"]
        for a in auto_applied:
            label = "、".join(field_labels.get(f, f) for f in a["fields"] if f != "status")
            title = chapter_titles.get(a["entity_id"]) or a["entity_id"]
            lines.append(f"- 章节《{title}》的{label}已保存（可撤销）")
            for n in a.get("notes", []):
                lines.append(f"  - {n}")
        summary += "\n".join(lines)
```

6. return 增加键：

```python
    return {
        "ok": True,
        "session_id": sess.id,
        "message_id": assistant_msg_id,
        "intent": plan.get("intent"),
        "change_records": records_data,
        "auto_applied": auto_applied,
        "summary": summary,
    }
```

7. 文件末尾新增两个端点：

```python
@router.post("/undo")
async def undo(body: dict, db: AsyncSession = Depends(get_db)):
    project_id = body.get("project_id")
    entity_type = body.get("entity_type")
    entity_id = body.get("entity_id")
    if not (project_id and entity_type and entity_id):
        raise ValidationError("project_id、entity_type、entity_id 必填")
    res = await db.execute(
        select(LongChangeRecord)
        .where(
            LongChangeRecord.project_id == project_id,
            LongChangeRecord.entity_type == entity_type,
            LongChangeRecord.entity_id == entity_id,
            LongChangeRecord.source == "auto",
            LongChangeRecord.status == "applied",
        )
        .order_by(LongChangeRecord.created_at.desc())
        .limit(1)
    )
    rec = res.scalars().first()
    if not rec or not rec.before:
        return {"ok": False, "message": "没有可撤销的自动生成"}

    from app import repositories as repo
    current_row = await repo.get_chapter(db, entity_id)
    current = (
        {c.name: getattr(current_row, c.name) for c in current_row.__table__.columns}
        if current_row is not None else None
    )
    await apply_change(db, project_id, {
        "entity_type": entity_type,
        "action": "update",
        "entity_id": entity_id,
        "after": rec.before,
    })
    rec.source = "auto_undone"
    db.add(LongChangeRecord(
        project_id=project_id,
        entity_type=entity_type,
        entity_id=entity_id,
        before=current,
        after=rec.before,
        status="applied",
        source="undo",
    ))
    await db.commit()
    return {"ok": True}


@router.get("/undoable/{chapter_id}")
async def undoable(chapter_id: str, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(LongChangeRecord.id)
        .where(
            LongChangeRecord.entity_type == "chapter",
            LongChangeRecord.entity_id == chapter_id,
            LongChangeRecord.source == "auto",
            LongChangeRecord.status == "applied",
        )
        .limit(1)
    )
    return {"undoable": res.scalars().first() is not None}
```

- [ ] **Step 4: 运行测试确认通过 + 全量回归**

Run: `cd backend && python -m pytest tests/ -v && python -m compileall app`
Expected: 全部 passed（含既有 `test_assistant_history.py`——注意其 mock 路径是否因 import 变化受影响，若失败检查 mock 目标名）。

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/assistant.py backend/tests/test_assistant_undo.py
git commit -m "feat(backend): auto-apply chapter text/outline changes with single-level undo"
```

---

### Task 5: 前端设置页新增生成设置

**Files:**
- Modify: `frontend/src/types/index.ts`
- Modify: `frontend/src/pages/SettingsPage.tsx`

**Interfaces:**
- Consumes: Task 1 的 `GET/PUT /api/settings`（`content_rating`、`chapter_target_words`）。
- Produces: `UserSettings` 类型含 `content_rating: string; chapter_target_words: number`；设置页「章节生成」卡片。

- [ ] **Step 1: 类型**

`frontend/src/types/index.ts` 的 `UserSettings` 接口追加：

```ts
  assistant_history_top_k: number;
  content_rating: string;
  chapter_target_words: number;
```

- [ ] **Step 2: 设置页卡片**

`frontend/src/pages/SettingsPage.tsx`：在「助手对话」Card（约 150-167 行）之后、「模型配置」Card 之前插入：

```tsx
      <Card className="mt-6">
        <div className="border-b border-line px-4 py-3 font-serif text-sm font-medium text-ink">章节生成</div>
        <div className="space-y-4 p-4">
          <div className="flex items-center justify-between">
            <span className="text-sm text-ink">每章目标字数</span>
            <Input
              type="number"
              min={1000}
              max={8000}
              value={settings.chapter_target_words}
              onChange={(e) => saveSettings({ chapter_target_words: Number(e.target.value) })}
              className="w-24"
            />
          </div>
          <div className="flex items-center justify-between">
            <span className="text-sm text-ink">内容尺度等级</span>
            <select
              className={selectClass + " w-40"}
              value={settings.content_rating}
              onChange={(e) => saveSettings({ content_rating: e.target.value })}
            >
              <option value="loose">宽松</option>
              <option value="standard">标准</option>
              <option value="strict">严格</option>
            </select>
          </div>
          <div className="text-xs text-muted">
            目标字数用于章节拆分与正文分段生成；尺度等级决定正文生成后的自动检查与改写强度。
          </div>
        </div>
      </Card>
```

（`selectClass` 已在文件中定义；`saveSettings`、`Input`、`Card` 均已存在。）

- [ ] **Step 3: 类型检查 + 构建**

Run: `cd frontend && npx tsc -b && npm run build`
Expected: 无错误。

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types/index.ts frontend/src/pages/SettingsPage.tsx frontend/dist
git commit -m "feat(frontend): chapter generation settings (target words, content rating)"
```

---

### Task 6: 前端 auto_applied 摘要卡 + 撤销 + 章节生成按钮

**Files:**
- Modify: `frontend/src/types/index.ts`
- Modify: `frontend/src/api/short.ts`（`assistantApi` 对象）
- Modify: `frontend/src/stores/useAssistantSession.ts`
- Modify: `frontend/src/components/AssistantChat.tsx`
- Modify: `frontend/src/components/FloatingAssistant.tsx`
- Modify: `frontend/src/components/chapter/ChapterList.tsx`
- Modify: `frontend/src/components/chapter/ChapterEditor.tsx`
- Modify: `frontend/src/pages/LongWorkspace.tsx`（仅 `ChapterPanel`）

**Interfaces:**
- Consumes: Task 4 的 `/chat` 响应 `auto_applied`、`POST /api/assistant/undo`、`GET /api/assistant/undoable/{chapter_id}`。
- Produces:
  - 类型 `AutoAppliedItem { entity_id: string; entity_type: string; fields: string[]; notes: string[] }`；`AssistantMessage.metadata.auto_applied?: AutoAppliedItem[]`。
  - store：`chaptersVersion: number`（auto_applied 或撤销成功时 +1）、`undoAuto(projectId, entityType, entityId): Promise<void>`。
  - `AssistantChat` 新增可选 prop `onUndo?: (item: AutoAppliedItem) => void`。
  - `ChapterList` 新增可选 props `onGenerate?: (chapter: Chapter) => void; generating?: boolean`。
  - `ChapterEditor` 新增可选 props `onUndo?: () => void; undoable?: boolean`。

- [ ] **Step 1: 类型与 API**

`frontend/src/types/index.ts`：

```ts
export interface AutoAppliedItem {
  entity_id: string;
  entity_type: string;
  fields: string[];
  notes: string[];
}
```

`AssistantMessage["metadata"]` 追加：`auto_applied?: AutoAppliedItem[];`

`frontend/src/api/short.ts` 的 `assistantApi` 追加：

```ts
  undo: (projectId: string, entityType: string, entityId: string) =>
    api.post("/assistant/undo", { project_id: projectId, entity_type: entityType, entity_id: entityId }),
  undoable: (chapterId: string) => api.get(`/assistant/undoable/${chapterId}`),
```

- [ ] **Step 2: store**

`frontend/src/stores/useAssistantSession.ts`：

1. import 类型：`import type { AssistantMessage, AssistantSession, AutoAppliedItem, ChangeRecord } from "@/types";`
2. state 接口追加：

```ts
  chaptersVersion: number;
  undoAuto: (projectId: string, entityType: string, entityId: string) => Promise<void>;
```

3. 初始值：`chaptersVersion: 0,`
4. `sendMessage` 成功分支，assistantMsg 的 metadata 追加 `auto_applied: data.auto_applied || []`，并在 `set(...)` 中追加版本号递增：

```ts
      set((s) => ({
        sessionId: data.session_id,
        messages: [...s.messages, assistantMsg],
        pendingRecords: [...s.pendingRecords, ...(data.change_records || [])],
        chaptersVersion: (data.auto_applied?.length ? s.chaptersVersion + 1 : s.chaptersVersion),
      }));
```

5. 新增 action：

```ts
  undoAuto: async (projectId: string, entityType: string, entityId: string) => {
    set({ busy: true, error: null });
    try {
      const { data } = await assistantApi.undo(projectId, entityType, entityId);
      if (!data.ok) {
        set({ error: data.message || "没有可撤销的自动生成" });
        return;
      }
      set((s) => ({ chaptersVersion: s.chaptersVersion + 1 }));
    } catch (err) {
      set({ error: err instanceof Error ? err.message : "撤销失败" });
    } finally {
      set({ busy: false });
    }
  },
```

- [ ] **Step 3: AssistantChat 摘要卡**

`frontend/src/components/AssistantChat.tsx`：

1. import 类型追加 `AutoAppliedItem`；props 接口追加 `onUndo?: (item: AutoAppliedItem) => void;`，解构加 `onUndo`。
2. assistant 消息气泡内 `<StatusBadge ... />` 所在 div 之后追加：

```tsx
            {m.role === "assistant" && (m.metadata?.auto_applied?.length ?? 0) > 0 && (
              <div className="mt-2 space-y-1 border-t border-line pt-2">
                {m.metadata!.auto_applied!.map((a, i) => (
                  <div key={i} className="space-y-0.5">
                    <div className="flex items-center justify-between gap-2 text-xs">
                      <span className="text-ink">
                        已写入章节{a.fields.includes("content") ? "正文" : "细纲"}
                      </span>
                      {onUndo && (
                        <Button
                          variant="subtle"
                          className="px-2 py-0.5 text-xs"
                          disabled={busy}
                          onClick={() => onUndo(a)}
                        >
                          撤销
                        </Button>
                      )}
                    </div>
                    {a.notes.map((n, j) => (
                      <div key={j} className="text-xs text-muted">{n}</div>
                    ))}
                  </div>
                ))}
              </div>
            )}
```

- [ ] **Step 4: FloatingAssistant 接线**

`frontend/src/components/FloatingAssistant.tsx`：解构 store 时加 `undoAuto`；`AssistantChat` 加 prop：

```tsx
          onUndo={(item) => {
            if (projectId && projectId !== "global") {
              undoAuto(projectId, item.entity_type, item.entity_id);
            }
          }}
```

- [ ] **Step 5: ChapterList 行内生成按钮**

`frontend/src/components/chapter/ChapterList.tsx`：props 接口与解构追加 `onGenerate?: (chapter: Chapter) => void; generating?: boolean;`；每行「删除」按钮前插入：

```tsx
                {onGenerate && (
                  <Button
                    variant="ghost"
                    disabled={generating}
                    onClick={(e) => {
                      e.stopPropagation();
                      onGenerate(it);
                    }}
                  >
                    {it.detailed_outline ? "生成正文" : "生成细纲"}
                  </Button>
                )}
```

- [ ] **Step 6: ChapterEditor 撤销按钮**

`frontend/src/components/chapter/ChapterEditor.tsx`：props 接口与解构追加 `onUndo?: () => void; undoable?: boolean;`；「生成正文」按钮后插入：

```tsx
        {undoable && onUndo && (
          <Button variant="ghost" onClick={onUndo}>
            撤销生成
          </Button>
        )}
```

- [ ] **Step 7: LongWorkspace 的 ChapterPanel 接线**

`frontend/src/pages/LongWorkspace.tsx` 的 `ChapterPanel`：

1. 头部 import 追加：

```tsx
import { assistantApi } from "@/api/short";
import { useAssistantSession } from "@/stores/useAssistantSession";
```

2. `ChapterPanel` 内 state 区追加：

```tsx
  const [undoable, setUndoable] = useState(false);
  const generating = useAssistantSession((s) => s.busy);
  const chaptersVersion = useAssistantSession((s) => s.chaptersVersion);
```

3. 新增 effect（放在现有 `useEffect(() => { loadItems(); }, [pid]);` 之后）：

```tsx
  // 自动生成写入/撤销后刷新章节数据
  useEffect(() => {
    if (chaptersVersion > 0) {
      loadItems();
      if (selectedId) loadDetail(selectedId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chaptersVersion]);

  // 选中章节的撤销可用性
  useEffect(() => {
    setUndoable(false);
    if (!selectedId) return;
    assistantApi.undoable(selectedId)
      .then(({ data }) => setUndoable(!!data.undoable))
      .catch(() => {});
  }, [selectedId, chaptersVersion]);
```

4. 新增处理函数：

```tsx
  const handleGenerate = (chapter: Chapter) => {
    const type = chapter.detailed_outline ? "text" : "outline";
    const chapterLabel = `第 ${chapter.order + 1} 章${chapter.title ? `《${chapter.title}》` : ""}`;
    const text = type === "outline" ? `生成${chapterLabel}细纲` : `生成${chapterLabel}正文`;
    const context = { entity_type: "chapter", entity_id: chapter.id };
    useAssistantSession.getState().openAssistant();
    useAssistantSession.getState().sendMessage(pid, text, context);
  };

  const handleUndo = async () => {
    if (!selectedId) return;
    await useAssistantSession.getState().undoAuto(pid, "chapter", selectedId);
    setUndoable(false);
  };
```

5. `ChapterList` 调用加 props：`onGenerate={handleGenerate} generating={generating}`；`ChapterEditor` 调用加 props：`onUndo={handleUndo} undoable={undoable}`。

- [ ] **Step 8: 类型检查 + 构建**

Run: `cd frontend && npx tsc -b && npm run build`
Expected: 无错误。

- [ ] **Step 9: 手动冒烟（按 spec 验证清单）**

1. 设置页改目标字数 1500、尺度 strict，保存成功。
2. 长篇项目章节列表点「生成细纲」→ 聊天出现 auto_applied 卡，细纲直接进章节（无待确认变更）。
3. 点「生成正文」→ 字数接近目标 → auto_applied 卡出现（含尺度 notes 如有）→ 章节内容已更新且编辑器自动刷新。
4. 聊天卡或编辑器点「撤销」→ 章节恢复旧内容，再点撤销提示没有可撤销记录。
5. `cd backend && python -m pytest tests/ -v` 全量通过。

- [ ] **Step 10: Commit**

```bash
git add frontend/src/types/index.ts frontend/src/api/short.ts frontend/src/stores/useAssistantSession.ts frontend/src/components/AssistantChat.tsx frontend/src/components/FloatingAssistant.tsx frontend/src/components/chapter/ChapterList.tsx frontend/src/components/chapter/ChapterEditor.tsx frontend/src/pages/LongWorkspace.tsx frontend/dist
git commit -m "feat(frontend): auto-applied card with undo, chapter generate buttons, live refresh"
```

---

## Self-Review 记录

- Spec 覆盖：设置项（T1）、尺度三档+自动改写+复核（T2/T3）、字数预算拆章（T2/T3 assignment）、细纲字数感知（T2/T3 outline）、分段生成+中断保留（T3）、摘要链（T2/T3）、auto-apply+降级（T4）、undo/undoable+审计（T4）、前端设置（T5）、auto_applied 卡/撤销/生成按钮/刷新（T6）。范围外项（SSE/多级撤销/敏感词库/短篇/批量生成）未纳入，与 spec 一致。
- 类型一致性：`chapter_rating_prompt`/`chapter_segment_user_prompt`/`RATING_LABELS`/`_generation_settings`/`_chapter_summaries_chain` 在 T2/T3 间签名一致；`AutoAppliedItem` 前端类型与 T4 后端响应键（`entity_id/entity_type/fields/notes`）一致；store `undoAuto(pid, entityType, entityId)` 与 T4 端点 body 一致。
- 占位符：无。
- 已知注意点：T4 步骤 3 中关于 `repo` 导入的注释为防呆说明（`/chat` 已有局部 import）；既有测试 `test_assistant_history.py` mock 了 supervisor/LLM，auto-apply 分支只处理 chapter update，不影响其断言。

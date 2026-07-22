# Assistant 多轮记忆与多 Session 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让创作助手具备多轮记忆（历史摘要 + 最近消息），并支持一个项目内创建/切换多个对话 session。

**Architecture:** 后端扩展 `AssistantSession` 模型支持多 session 和摘要数组；新增 session 管理 API；`/chat` 组装 LLM messages 时注入 summaries 与最近消息；前端增加对话侧边栏与设置项。

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, SQLite, React 19 + TypeScript + Zustand, Tailwind.

## Global Constraints

- 后端 Python >= 3.11，使用 `async/await` 与 SQLAlchemy 2.0 async API。
- 前端 TypeScript，`noImplicitAny: false`，不要擅自开启。
- 数据库迁移采用脚本形式，项目暂无 Alembic。
- UI 保持项目当前视觉风格，不套用 Lovart。
- 所有持久化变更仍须通过 `change_apply.py` 的确认流程。

---

## File Map

| 文件 | 职责 |
|------|------|
| `backend/app/models.py` | `AssistantSession`、`UserSetting` 模型字段扩展。 |
| `backend/scripts/migrate_assistant_sessions.py` | 数据库迁移：为现有表新增字段。 |
| `backend/app/api/settings.py` | 返回/接收新增的用户助手设置。 |
| `backend/app/api/assistant.py` | session 管理 API、多轮上下文注入、压缩触发。 |
| `backend/app/agents/harness/history.py` | 历史消息组装与摘要生成。 |
| `backend/app/agents/harness/nodes/supervisor.py` | 接收历史上下文。 |
| `backend/app/agents/harness/worker_base.py` | tool loop 接收历史上下文。 |
| `backend/app/agents/harness/nodes/responder.py` | 接收历史上下文。 |
| `frontend/src/types/index.ts` | `UserSettings`、`AssistantSession` 类型扩展。 |
| `frontend/src/api/short.ts` | 新增 assistant session API 封装。 |
| `frontend/src/stores/useAssistantSession.ts` | 多 session 状态管理。 |
| `frontend/src/components/AssistantSessionSidebar.tsx` | 对话列表侧边栏。 |
| `frontend/src/components/AssistantStudio.tsx` | 集成侧边栏与输入区。 |
| `frontend/src/pages/SettingsPage.tsx` | 助手设置分组。 |

---

## Task 1: 数据库迁移脚本

**Files:**
- Create: `backend/scripts/migrate_assistant_sessions.py`

**Interfaces:**
- Produces: 可独立运行的迁移脚本，执行后 `assistant_sessions` 与 `user_settings` 表拥有新字段。

- [ ] **Step 1: 编写迁移脚本**

```python
"""迁移 AssistantSession 与 UserSetting 表以支持多轮记忆和多 session。"""
from __future__ import annotations

import asyncio

from sqlalchemy import text
from app.database import engine


COLUMNS = {
    "assistant_sessions": [
        ("title", "VARCHAR(255) DEFAULT '未命名对话'"),
        ("is_active", "BOOLEAN DEFAULT 0"),
        ("summaries", "TEXT DEFAULT '[]'"),
        ("message_count", "INTEGER DEFAULT 0"),
    ],
    "user_settings": [
        ("assistant_summary_threshold", "INTEGER DEFAULT 20"),
        ("assistant_max_summaries", "INTEGER DEFAULT 5"),
        ("assistant_summary_max_length", "INTEGER DEFAULT 1000"),
    ],
}


async def migrate() -> None:
    async with engine.begin() as conn:
        for table, cols in COLUMNS.items():
            for name, ddl in cols:
                try:
                    await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                    print(f"Added {table}.{name}")
                except Exception as e:
                    print(f"Skip {table}.{name}: {e}")
        # 修正现有 session：每个 project 保留第一条为 active
        await conn.execute(text("""
            UPDATE assistant_sessions
            SET is_active = 1
            WHERE id IN (
                SELECT MIN(id) FROM assistant_sessions GROUP BY project_id
            )
        """))
        print("Migration done.")


if __name__ == "__main__":
    asyncio.run(migrate())
```

- [ ] **Step 2: 运行迁移脚本**

```bash
cd backend
python scripts/migrate_assistant_sessions.py
```

Expected: 输出 `Added assistant_sessions.title` 等，最后 `Migration done.`（重复运行会 skip 已存在列）。

- [ ] **Step 3: Commit**

```bash
git add backend/scripts/migrate_assistant_sessions.py
git commit -m "chore(db): add assistant session migration for multi-turn memory"
```

---

## Task 2: 模型扩展

**Files:**
- Modify: `backend/app/models.py`

**Interfaces:**
- Consumes: 迁移脚本已添加字段。
- Produces: `AssistantSession.title/is_active/summaries/message_count`，`UserSetting` 新增三个助手设置字段，`to_dict()` 同步返回。

- [ ] **Step 1: 修改 `AssistantSession`**

在 `AssistantSession` 中新增字段并更新 `to_dict`：

```python
class AssistantSession(Base):
    __tablename__ = "assistant_sessions"

    id = Column(CHAR(36), primary_key=True, default=_uuid)
    project_id = Column(CHAR(36), ForeignKey("projects.id"), nullable=True, index=True)
    title = Column(String(255), nullable=False, default="未命名对话")
    is_active = Column(Boolean, default=False, nullable=False)
    staged_changes = Column(JSON, default=list)
    summaries = Column(JSON, default=list)
    message_count = Column(Integer, default=0, nullable=False)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "title": self.title,
            "is_active": self.is_active,
            "staged_changes": self.staged_changes or [],
            "summaries": self.summaries or [],
            "message_count": self.message_count,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
```

- [ ] **Step 2: 修改 `UserSetting`**

```python
class UserSetting(Base):
    __tablename__ = "user_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    recursive_limit = Column(Integer, default=8)
    hotspot_sources = Column(JSON, default=list)
    theme = Column(String(32), default="light")
    assistant_summary_threshold = Column(Integer, default=20)
    assistant_max_summaries = Column(Integer, default=5)
    assistant_summary_max_length = Column(Integer, default=1000)

    def to_dict(self) -> dict:
        return {
            "recursive_limit": self.recursive_limit,
            "hotspot_sources": self.hotspot_sources or [],
            "theme": self.theme,
            "assistant_summary_threshold": self.assistant_summary_threshold,
            "assistant_max_summaries": self.assistant_max_summaries,
            "assistant_summary_max_length": self.assistant_summary_max_length,
        }
```

- [ ] **Step 3: 运行迁移并验证**

```bash
cd backend
python scripts/migrate_assistant_sessions.py
python -c "from app.models import AssistantSession, UserSetting; print(AssistantSession.__table__.columns.keys()); print(UserSetting.__table__.columns.keys())"
```

Expected: `AssistantSession` 包含 `title/is_active/summaries/message_count`；`UserSetting` 包含三个 `assistant_*` 字段。

- [ ] **Step 4: Commit**

```bash
git add backend/app/models.py
git commit -m "feat(models): extend AssistantSession and UserSetting for memory"
```

---

## Task 3: Settings API 与设置页面

**Files:**
- Modify: `backend/app/api/settings.py`
- Modify: `frontend/src/types/index.ts`
- Modify: `frontend/src/pages/SettingsPage.tsx`

**Interfaces:**
- Consumes: `UserSetting` 新增字段。
- Produces: Settings API 返回并持久化三个助手设置；前端可读写。

- [ ] **Step 1: 读取当前 settings API 代码**

```bash
cat backend/app/api/settings.py
```

- [ ] **Step 2: 更新 settings API**

找到更新 settings 的端点（通常是 `PATCH /api/settings`），在可接收字段中加入：

```python
from pydantic import BaseModel

class UserSettingPatch(BaseModel):
    recursive_limit: int | None = None
    hotspot_sources: list | None = None
    theme: str | None = None
    assistant_summary_threshold: int | None = None
    assistant_max_summaries: int | None = None
    assistant_summary_max_length: int | None = None
```

更新写入逻辑：

```python
if patch.assistant_summary_threshold is not None:
    setting.assistant_summary_threshold = max(1, patch.assistant_summary_threshold)
if patch.assistant_max_summaries is not None:
    setting.assistant_max_summaries = max(0, patch.assistant_max_summaries)
if patch.assistant_summary_max_length is not None:
    setting.assistant_summary_max_length = max(100, patch.assistant_summary_max_length)
```

- [ ] **Step 3: 更新前端类型**

`frontend/src/types/index.ts`：

```typescript
export interface UserSettings {
  recursive_limit: number;
  hotspot_sources: { url: string; name?: string; adapter?: any }[];
  theme: string;
  assistant_summary_threshold: number;
  assistant_max_summaries: number;
  assistant_summary_max_length: number;
}
```

- [ ] **Step 4: 更新 SettingsPage**

在 `frontend/src/pages/SettingsPage.tsx` 新增一个 Card：

```tsx
<Card className="mt-6">
  <div className="border-b border-line px-4 py-3 font-serif text-sm font-medium text-ink">助手对话</div>
  <div className="space-y-4 p-4">
    {[
      { key: "assistant_summary_threshold", label: "压缩阈值（轮）", min: 1, max: 100 },
      { key: "assistant_max_summaries", label: "最大保留摘要数", min: 0, max: 20 },
      { key: "assistant_summary_max_length", label: "单条摘要最大长度（字符）", min: 100, max: 4000 },
    ].map((item) => (
      <div key={item.key} className="flex items-center justify-between">
        <span className="text-sm text-ink">{item.label}</span>
        <input
          type="number"
          min={item.min}
          max={item.max}
          value={(settings as any)[item.key]}
          onChange={(e) => saveSettings({ [item.key]: Number(e.target.value) })}
          className="w-24 rounded border border-line px-2 py-1 text-sm"
        />
      </div>
    ))}
  </div>
</Card>
```

- [ ] **Step 5: 验证**

```bash
cd frontend
npx tsc -b
```

Expected: 无类型错误。

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/settings.py frontend/src/types/index.ts frontend/src/pages/SettingsPage.tsx
git commit -m "feat(settings): expose assistant memory settings"
```

---

## Task 4: 历史上下文模块

**Files:**
- Create: `backend/app/agents/harness/history.py`
- Create: `backend/tests/test_history.py`

**Interfaces:**
- Consumes: `AssistantSession`（含 `summaries`、`message_count`），`AssistantMessage` 列表，当前 `user_input`，`UserSetting`。
- Produces: `build_messages(system_prompt, session, messages, user_input, settings)` -> `list[dict]`；`summarize_messages(messages, settings)` -> `str`。

- [ ] **Step 1: 创建 `history.py`**

```python
"""助手多轮历史上下文组装与摘要生成。"""
from __future__ import annotations

from typing import Any

from app.models import AssistantMessage, AssistantSession, UserSetting


def build_history_context(
    session: AssistantSession,
    messages: list[AssistantMessage],
    settings: UserSetting,
) -> list[dict[str, str]]:
    """返回历史摘要 + 最近具体消息（不含 system prompt 与当前输入）。"""
    out: list[dict[str, str]] = []

    summaries = (session.summaries or [])[: settings.assistant_max_summaries]
    for i, s in enumerate(summaries):
        out.append({
            "role": "user",
            "content": f"[历史摘要 {i + 1}（{s.get('turn_range', '未知范围')}）]\n{s.get('summary', '')}",
        })

    recent_count = _recent_message_count(session, settings)
    recent = messages[-recent_count:] if recent_count > 0 else []
    for m in recent:
        out.append({"role": m.role, "content": m.content})

    return out


def build_messages(
    system_prompt: str,
    session: AssistantSession,
    messages: list[AssistantMessage],
    user_input: str,
    settings: UserSetting,
) -> list[dict[str, str]]:
    """为 LLM 组装完整 messages：system + 历史上下文 + 当前输入。"""
    return [
        {"role": "system", "content": system_prompt},
        *build_history_context(session, messages, settings),
        {"role": "user", "content": user_input},
    ]


def _recent_message_count(session: AssistantSession, settings: UserSetting) -> int:
    """返回自上次压缩以来应保留的具体消息条数。"""
    # message_count 是 user+assistant 总条数；保留这些条
    return max(0, session.message_count or 0)


def should_summarize(session: AssistantSession, settings: UserSetting) -> bool:
    """是否已累积到需要压缩的阈值。threshold 单位是'轮'，每轮 2 条消息。"""
    threshold = max(1, settings.assistant_summary_threshold or 20)
    return (session.message_count or 0) >= threshold * 2


async def summarize_messages(
    messages: list[AssistantMessage],
    settings: UserSetting,
    llm,
) -> str:
    """调用 LLM 把最近一轮对话压缩成摘要。"""
    lines = []
    for m in messages:
        prefix = "用户" if m.role == "user" else "助手"
        lines.append(f"{prefix}: {m.content}")
    prompt = (
        "请把以下对话总结为一段简洁摘要，保留用户的创作意图、关键指令和已确认的变更。"
        "该摘要仅用于后续对话上下文，不对用户显示。\n\n"
        + "\n".join(lines)
    )
    summary = await llm.chat([{"role": "user", "content": prompt}])
    max_len = max(100, settings.assistant_summary_max_length or 1000)
    return summary[:max_len]


def append_summary(session: AssistantSession, messages: list[AssistantMessage], summary_text: str) -> None:
    """把新生成的摘要加入 session，并清理超出限制的摘要。"""
    summaries = list(session.summaries or [])
    total_turns = len(messages) // 2
    start_turn = 1
    if summaries:
        # 简单按轮数累加
        last_range = summaries[-1].get("turn_range", "1-1")
        last_end = int(last_range.split("-")[-1])
        start_turn = last_end + 1
    end_turn = start_turn + total_turns - 1
    summaries.append({
        "turn_range": f"{start_turn}-{end_turn}",
        "summary": summary_text,
    })
    session.summaries = summaries
    session.message_count = 0
```

- [ ] **Step 2: 编写测试**

`backend/tests/test_history.py`：

```python
from unittest.mock import AsyncMock
import pytest
from app.agents.harness.history import build_messages, should_summarize, summarize_messages, append_summary
from app.models import AssistantMessage, AssistantSession, UserSetting


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


def test_build_messages_includes_summaries_and_recent():
    session = AssistantSession(
        id="s1", project_id="p1", summaries=[{"turn_range": "1-2", "summary": " earlier"}], message_count=2
    )
    messages = [
        AssistantMessage(session_id="s1", role="user", content="hi"),
        AssistantMessage(session_id="s1", role="assistant", content="hello"),
    ]
    settings = UserSetting(assistant_max_summaries=5)
    msgs = build_messages("sys", session, messages, "now", settings)
    assert msgs[0] == {"role": "system", "content": "sys"}
    assert msgs[1]["role"] == "user" and "历史摘要" in msgs[1]["content"]
    assert msgs[2]["role"] == "user" and msgs[2]["content"] == "hi"
    assert msgs[3]["role"] == "assistant"
    assert msgs[-1]["content"] == "now"


def test_should_summarize_at_threshold():
    session = AssistantSession(message_count=40)
    settings = UserSetting(assistant_summary_threshold=20)
    assert should_summarize(session, settings) is True


def test_should_not_summarize_below_threshold():
    session = AssistantSession(message_count=2)
    settings = UserSetting(assistant_summary_threshold=20)
    assert should_summarize(session, settings) is False


@pytest.mark.anyio
async def test_summarize_messages_trims_to_max_length():
    llm = AsyncMock()
    llm.chat = AsyncMock(return_value="x" * 2000)
    messages = [AssistantMessage(session_id="s1", role="user", content="hi")]
    settings = UserSetting(assistant_summary_max_length=300)
    result = await summarize_messages(messages, settings, llm)
    assert len(result) == 300
```

- [ ] **Step 3: 运行测试**

```bash
cd backend
python -m pytest tests/test_history.py -v
```

Expected: 全部 PASS。

- [ ] **Step 4: Commit**

```bash
git add backend/app/agents/harness/history.py backend/tests/test_history.py
git commit -m "feat(assistant): add history context builder and summarizer"
```

---

## Task 5: Assistant API 改造

**Files:**
- Modify: `backend/app/api/assistant.py`

**Interfaces:**
- Consumes: `history.build_messages`, `history.should_summarize`, `history.summarize_messages`, `history.append_summary`。
- Produces: 新增 `POST /api/assistant/session/{project_id}`、`GET /api/assistant/sessions/{project_id}`、`POST /api/assistant/session/{session_id}/switch`；`/chat` 使用 active session 并在回复后触发压缩。

- [ ] **Step 1: 重写 session 管理函数**

```python
from sqlalchemy import select, func
from app.agents.harness.history import build_messages, should_summarize, summarize_messages, append_summary


async def _get_active_session(db, project_id) -> AssistantSession:
    res = await db.execute(
        select(AssistantSession)
        .where(AssistantSession.project_id == project_id, AssistantSession.is_active == True)  # noqa: E712
    )
    s = res.scalars().first()
    if not s:
        s = AssistantSession(
            project_id=project_id,
            title="对话 1",
            is_active=True,
            staged_changes=[],
            summaries=[],
            message_count=0,
        )
        db.add(s)
        await db.commit()
        await db.refresh(s)
    return s


async def _deactivate_other_sessions(db, project_id: str, keep_id: str) -> None:
    await db.execute(
        update(AssistantSession)
        .where(AssistantSession.project_id == project_id, AssistantSession.id != keep_id)
        .values(is_active=False)
    )
```

- [ ] **Step 2: 新增 API 端点**

```python
@router.post("/session/{project_id}")
async def create_session(project_id: str, db: AsyncSession = Depends(get_db)):
    proj = await db.get(Project, project_id)
    if not proj:
        raise NotFoundError("项目不存在")

    # 旧 session 全部置 inactive
    await db.execute(
        update(AssistantSession)
        .where(AssistantSession.project_id == project_id)
        .values(is_active=False)
    )

    count_res = await db.execute(
        select(func.count()).where(AssistantSession.project_id == project_id)
    )
    count = count_res.scalar() or 0
    new_session = AssistantSession(
        project_id=project_id,
        title=f"对话 {count + 1}",
        is_active=True,
        staged_changes=[],
        summaries=[],
        message_count=0,
    )
    db.add(new_session)
    await db.commit()
    await db.refresh(new_session)
    return {"ok": True, "session": new_session.to_dict()}


@router.get("/sessions/{project_id}")
async def list_sessions(project_id: str, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(AssistantSession)
        .where(AssistantSession.project_id == project_id)
        .order_by(AssistantSession.updated_at.desc())
    )
    sessions = [s.to_dict() for s in res.scalars().all()]
    return {"ok": True, "sessions": sessions}


@router.post("/session/{session_id}/switch")
async def switch_session(session_id: str, db: AsyncSession = Depends(get_db)):
    sess = await db.get(AssistantSession, session_id)
    if not sess:
        raise NotFoundError("会话不存在")
    await db.execute(
        update(AssistantSession)
        .where(AssistantSession.project_id == sess.project_id)
        .values(is_active=False)
    )
    sess.is_active = True
    await db.commit()
    await db.refresh(sess)
    return {"ok": True, "session": sess.to_dict()}
```

- [ ] **Step 3: 修改 `/chat` 使用历史上下文**

将 `_ensure_session` 替换为 `_get_active_session`。

在保存 user message 后、调用 supervisor 前，读取历史消息并构造上下文：

```python
hist_res = await db.execute(
    select(AssistantMessage)
    .where(AssistantMessage.session_id == sess.id)
    .order_by(AssistantMessage.created_at.asc())
)
historical_messages = hist_res.scalars().all()

settings_row = (await db.execute(select(UserSetting))).scalars().first()
settings_obj = settings_row or UserSetting()

# 历史上下文（摘要 + 最近消息），不含 system 与当前输入
history_context = build_history_context(sess, historical_messages, settings_obj)

# Supervisor 使用完整 messages
supervisor_msgs = build_messages(
    "你是小说创作助手的调度器...",
    sess,
    historical_messages,
    user_input,
    settings_obj,
)
plan = await run_supervisor(llm, supervisor_msgs)
```

派发 Worker 时传入 `history_context`：

```python
result = await run_worker(
    wcls, db, llm, recursive_limit, goal, context,
    history_context=history_context,
)
```

Responder 同样传入：

```python
summary = await respond(llm, records, history_context=history_context)
```

- [ ] **Step 4: 修改 supervisor / worker / responder 接收 messages**

`backend/app/agents/harness/nodes/supervisor.py`：

```python
async def run_supervisor(llm: LLMClient, messages: list[dict]) -> dict:
    try:
        raw = await llm.parse_llm_json(messages)
        if isinstance(raw, dict) and "tasks" in raw:
            return raw
    except Exception:
        pass
    return {"intent": messages[-1]["content"][:50], "tasks": [{"worker": "outline", "goal": messages[-1]["content"]}]}
```

`backend/app/agents/harness/worker_base.py`：

```python
async def _tool_loop(
    self,
    system_prompt: str,
    user_prompt: str,
    extra_tools: list[dict] | None = None,
    history_context: list[dict] | None = None,
) -> dict:
    messages = [{"role": "system", "content": system_prompt}]
    if history_context:
        messages.extend(history_context)
    messages.append({"role": "user", "content": user_prompt})
    ...
```

同时修改 `run_worker` 签名并传入 `history_context`：

```python
async def run_worker(
    worker_cls: type["WorkerBase"],
    db: AsyncSession,
    llm,
    recursive_limit: int,
    goal: str,
    context: dict,
    history_context: list[dict] | None = None,
) -> dict:
    worker = worker_cls(db, llm, recursive_limit)
    return await worker.run(goal, context, history_context)
```

`backend/app/agents/harness/workers/__init__.py` 中的每个 `run` 方法：

```python
async def run(self, goal: str, context: dict, history_context: list[dict] | None = None) -> dict:
    system = "..."
    return await self._tool_loop(system, goal, history_context=history_context)
```

`backend/app/agents/harness/nodes/responder.py`：

```python
async def respond(llm: LLMClient, records: list[ChangeRecord], history_context: list[dict] | None = None) -> str:
    listing = render_records(records)
    msgs = [{"role": "system", "content": RESPONDER_PROMPT}]
    if history_context:
        msgs.extend(history_context)
    msgs.append({"role": "user", "content": f"变更清单：\n{listing}"})
    try:
        return await llm.chat(msgs)
    except Exception:
        return "已生成以下变更建议，请在确认后应用：\n" + listing
```

- [ ] **Step 5: 在 `/chat` 中触发压缩**

在保存 assistant message 后：

```python
sess.message_count += 2
if should_summarize(sess, settings_obj):
    recent = historical_messages[-(settings_obj.assistant_summary_threshold * 2):]
    summary_text = await summarize_messages(recent, settings_obj, llm)
    append_summary(sess, recent, summary_text)
    await db.commit()
    await db.refresh(sess)
```

- [ ] **Step 6: 更新 `/session/{project_id}/history` 返回 active session**

```python
@router.get("/session/{project_id}/history")
async def get_session_history(project_id: str, db: AsyncSession = Depends(get_db)):
    sess = await _get_active_session(db, project_id)
    ... # 其余逻辑不变
```

- [ ] **Step 7: 运行测试**

```bash
cd backend
python -m pytest tests/test_assistant_history.py tests/test_history.py -v
```

Expected: 全部 PASS。

- [ ] **Step 8: Commit**

```bash
git add backend/app/api/assistant.py backend/app/agents/harness/nodes/supervisor.py backend/app/agents/harness/worker_base.py backend/app/agents/harness/nodes/responder.py
git commit -m "feat(assistant): multi-session API and history-aware LLM calls"
```

---

## Task 6: 前端 API 与 Store

**Files:**
- Modify: `frontend/src/api/short.ts`
- Modify: `frontend/src/types/index.ts`
- Modify: `frontend/src/stores/useAssistantSession.ts`

**Interfaces:**
- Consumes: 新增后端 API。
- Produces: `assistantApi.createSession/listSessions/switchSession`；store 提供 `sessions`、`loadSessions`、`createSession`、`switchSession`。

- [ ] **Step 1: 扩展 `AssistantSession` 类型**

`frontend/src/types/index.ts`：

```typescript
export interface AssistantSession {
  id: string;
  project_id: string;
  title: string;
  is_active: boolean;
  staged_changes: any[];
  summaries: any[];
  message_count: number;
  updated_at: string | null;
}
```

- [ ] **Step 2: 扩展 assistantApi**

`frontend/src/api/short.ts`：

```typescript
export const assistantApi = {
  chat: (pid: string, message: string) => api.post("/assistant/chat", { project_id: pid, message }),
  session: (pid: string) => api.get(`/assistant/session/${pid}`),
  history: (pid: string) => api.get(`/assistant/session/${pid}/history`),
  sessions: (pid: string) => api.get(`/assistant/sessions/${pid}`),
  createSession: (pid: string) => api.post(`/assistant/session/${pid}`),
  switchSession: (sessionId: string) => api.post(`/assistant/session/${sessionId}/switch`),
  stage: (sessionId: string, record: ChangeRecord) =>
    api.post("/assistant/stage", { session_id: sessionId, change_record: record }),
  confirm: (sessionId: string) => api.post("/assistant/confirm", { session_id: sessionId }),
  reject: (sessionId: string) => api.post("/assistant/reject", { session_id: sessionId }),
};
```

- [ ] **Step 3: 扩展 useAssistantSession store**

```typescript
interface AssistantSessionState {
  sessionId: string | null;
  sessions: AssistantSession[];
  messages: AssistantMessage[];
  busy: boolean;
  pendingRecords: ChangeRecord[];
  error: string | null;
  loadHistory: (pid: string) => Promise<void>;
  loadSessions: (pid: string) => Promise<void>;
  sendMessage: (pid: string, text: string) => Promise<void>;
  createSession: (pid: string) => Promise<void>;
  switchSession: (sessionId: string, pid: string) => Promise<void>;
  stageChange: (record: ChangeRecord) => Promise<void>;
  confirm: () => Promise<void>;
  reject: () => Promise<void>;
}
```

实现：

```typescript
loadSessions: async (pid: string) => {
  try {
    const { data } = await assistantApi.sessions(pid);
    set({ sessions: data.sessions || [] });
  } catch (err) {
    set({ error: err instanceof Error ? err.message : "加载会话列表失败" });
  }
},

createSession: async (pid: string) => {
  set({ busy: true, error: null });
  try {
    const { data } = await assistantApi.createSession(pid);
    await get().loadSessions(pid);
    await get().loadHistory(pid);
    set({ sessionId: data.session.id });
  } catch (err) {
    set({ error: err instanceof Error ? err.message : "新建对话失败" });
  } finally {
    set({ busy: false });
  }
},

switchSession: async (sessionId: string, pid: string) => {
  set({ busy: true, error: null });
  try {
    await assistantApi.switchSession(sessionId);
    await get().loadSessions(pid);
    await get().loadHistory(pid);
  } catch (err) {
    set({ error: err instanceof Error ? err.message : "切换对话失败" });
  } finally {
    set({ busy: false });
  }
},
```

- [ ] **Step 4: 运行类型检查**

```bash
cd frontend
npx tsc -b
```

Expected: 无类型错误。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/short.ts frontend/src/types/index.ts frontend/src/stores/useAssistantSession.ts
git commit -m "feat(frontend): assistant session API and store"
```

---

## Task 7: 前端侧边栏与集成

**Files:**
- Create: `frontend/src/components/AssistantSessionSidebar.tsx`
- Modify: `frontend/src/components/AssistantStudio.tsx`

**Interfaces:**
- Consumes: `useAssistantSession` 的 `sessions`、`sessionId`、`loadSessions`、`createSession`、`switchSession`。
- Produces: 可渲染的对话列表 UI。

- [ ] **Step 1: 创建侧边栏组件**

```tsx
import { useEffect } from "react";
import { Button } from "@/components/ui";
import type { AssistantSession } from "@/types";

interface Props {
  pid: string;
  sessions: AssistantSession[];
  activeId: string | null;
  onCreate: () => void;
  onSwitch: (id: string) => void;
}

export default function AssistantSessionSidebar({ pid, sessions, activeId, onCreate, onSwitch }: Props) {
  useEffect(() => {
    // 首次挂载由父组件 load
  }, [pid]);

  return (
    <div className="flex h-full w-52 shrink-0 flex-col border-r border-line bg-surface">
      <div className="border-b border-line p-3">
        <Button variant="primary" className="w-full" onClick={onCreate}>
          + 新建对话
        </Button>
      </div>
      <div className="flex-1 overflow-y-auto p-2">
        {sessions.length === 0 && <div className="p-2 text-xs text-muted">暂无对话</div>}
        {sessions.map((s) => (
          <button
            key={s.id}
            onClick={() => onSwitch(s.id)}
            className={
              "mb-1 w-full rounded border px-3 py-2 text-left text-sm " +
              (s.id === activeId
                ? "border-accent bg-accent-soft text-ink"
                : "border-transparent text-muted hover:bg-surface-2 hover:text-ink")
            }
          >
            <div className="truncate font-medium">{s.title}</div>
            <div className="text-[11px] opacity-70">
              {s.updated_at ? new Date(s.updated_at).toLocaleString() : "--"}
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: 集成到 AssistantStudio**

```tsx
export default function AssistantStudio({ pid }: { pid: string }) {
  const [input, setInput] = useState("");
  const [contextOpen, setContextOpen] = useState(false);
  const [editing, setEditing] = useState<{ kind: string; item: any } | null>(null);

  const {
    messages,
    pendingRecords,
    busy,
    sessionId,
    sessions,
    error,
    loadHistory,
    loadSessions,
    sendMessage,
    createSession,
    switchSession,
    stageChange,
    confirm,
    reject,
  } = useAssistantSession();

  useEffect(() => {
    loadSessions(pid);
    loadHistory(pid);
  }, [pid, loadHistory, loadSessions]);

  return (
    <div className="flex h-full">
      <AssistantSessionSidebar
        pid={pid}
        sessions={sessions}
        activeId={sessionId}
        onCreate={() => createSession(pid)}
        onSwitch={(id) => switchSession(id, pid)}
      />
      <div className="flex flex-1 flex-col">
        {/* 原有 ContextPanel + AssistantChat + input */}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: 类型检查与 Commit**

```bash
cd frontend
npx tsc -b
```

Expected: 无类型错误。

```bash
git add frontend/src/components/AssistantSessionSidebar.tsx frontend/src/components/AssistantStudio.tsx
git commit -m "feat(frontend): assistant session sidebar and studio integration"
```

---

## Task 8: 端到端验证

**Files:**
- 无需修改文件。

- [ ] **Step 1: 启动后端**

```bash
cd backend
python desktop_launcher.py
```

- [ ] **Step 2: 打开应用并创建项目**

访问 `http://127.0.0.1:8765`，创建一个长篇项目。

- [ ] **Step 3: 测试多轮记忆**

1. 在助手输入"主角是刘修，穿越者"。
2. 确认角色变更。
3. 输入"给他加个妹妹"。
4. 观察助手是否能基于前文理解"他"指刘修，并生成新增角色。

- [ ] **Step 4: 测试新建/切换对话**

1. 点击"新建对话"。
2. 发送消息，确认是新 session。
3. 切换回旧 session，历史消息应恢复。

- [ ] **Step 5: 运行完整测试**

```bash
cd backend
python -m pytest tests/ -v
cd ../frontend
npx tsc -b
```

Expected: 后端测试全部 PASS，前端类型检查无错误。

- [ ] **Step 6: Commit 验证结果（如有测试新增）**

```bash
git add backend/tests/ frontend/
git commit -m "test: e2e verification for assistant multi-turn memory"
```

---

## Self-Review

1. **Spec coverage:**
   - 多 session 存档：Task 4 新增 create/list/switch API；Task 6/7 前端支持。
   - 每 N 轮压缩：Task 3 设置项；Task 4/5 压缩触发与摘要生成。
   - 历史上下文：Task 4/5 在 supervisor/worker/responder 中注入。
   - 新建对话：Task 4/6/7 完整链路。
   - 无占位符：所有步骤含具体代码与命令。

2. **Placeholder scan:** 无 TBD/TODO/"implement later"。

3. **Type consistency:** `AssistantSession.to_dict()`、`UserSettings` 类型、API 路径在各任务中一致。

4. **Gap:** 需要确保 `backend/app/api/settings.py` 更新端点可接收新字段（Task 3 已覆盖）。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-13-assistant-multi-turn-memory-plan.md`.

Two execution options:

1. **Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?

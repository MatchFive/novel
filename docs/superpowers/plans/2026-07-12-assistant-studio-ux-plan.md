# 长篇创作助手 UX 重设计 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `LongWorkspace` 的“创作助手”标签页从上下堆叠的 JSON 列表演进为以连续对话为核心的折叠式工作室，支持项目素材上下文、多轮消息历史、逐条变更卡片审核。

**Architecture:** 后端新增 `AssistantMessage` 表持久化聊天流，并新增 `/assistant/session/{project_id}/history` 与 `/assistant/stage` 两个接口；前端用 Zustand store 管理会话状态，新增 `AssistantStudio` 组件替换原 `AssistantPanel`，变更展示统一走 `ChangeRecordCard`。

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2.0 async, SQLite, React 19, TypeScript, Vite, Tailwind, Zustand, pytest（后端测试）

## Global Constraints

- 仅修改 `LongWorkspace` 的“创作助手”标签页；不改短篇 `ShortStudio`。
- 后端实体写入仍必须经过 `services/change_apply.py`；上下文面板的人工编辑也先生成 `ChangeRecord` 并走确认流。
- 视觉风格保持现有 Lovart 暖褐书香主题，不引入新主色。
- 聊天仍用同步 POST，不引入 WebSocket/SSE。
- SQLite 无 Alembic；新增表由 SQLAlchemy `create_all()` 自动创建。若本地已有 `data/novel.db`，需要删除后重建或手动迁移。
- 当前仓库不是 git 仓库；commit 步骤在启用 git 后执行，否则可跳过。

---

### Task 1: Add `AssistantMessage` model

**Files:**
- Modify: `backend/app/models.py`

**Interfaces:**
- Produces: `AssistantMessage` SQLAlchemy model with columns `id`, `session_id`, `role`, `content`, `metadata`, `created_at`.

- [ ] **Step 1: Add the model**

```python
class AssistantMessage(Base):
    __tablename__ = "assistant_messages"

    id = Column(CHAR(36), primary_key=True, default=_uuid)
    session_id = Column(CHAR(36), ForeignKey("assistant_sessions.id"), nullable=False, index=True)
    role = Column(String(32), nullable=False)
    content = Column(Text, default="")
    metadata_ = Column("metadata", JSON, default=dict)
    created_at = Column(DateTime, default=_now, nullable=False)
```

Place it immediately after `AssistantSession` in `backend/app/models.py`.

- [ ] **Step 2: Syntax check**

Run: `cd backend && python -m compileall app`
Expected: `Compiling '...'...` with no errors.

- [ ] **Step 3: Verify table creation**

Run: `cd backend && python -c "from app.database import create_all; import asyncio; asyncio.run(create_all())"`
Expected: command exits with code 0. If `data/novel.db` already exists and lacks the new table, delete it first: `rm data/novel.db`.

- [ ] **Step 4: Commit**

```bash
git add backend/app/models.py
git commit -m "feat(models): add AssistantMessage table"
```

---

### Task 2: Persist user and assistant messages in `/assistant/chat`

**Files:**
- Modify: `backend/app/api/assistant.py`
- Modify: `backend/requirements.txt`
- Create: `backend/tests/test_assistant_history.py`

**Interfaces:**
- Consumes: `AssistantMessage` model from Task 1.
- Produces: `POST /assistant/chat` writes `user` and `assistant` rows and returns `message_id` in the response.

- [ ] **Step 1: Add pytest dependencies**

Append to `backend/requirements.txt`:

```text
pytest==8.3.3
pytest-asyncio==0.24.0
```

Run: `cd backend && pip install -r requirements.txt`

- [ ] **Step 2: Write failing test**

Create `backend/tests/test_assistant_history.py`:

```python
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.database import create_all, engine


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module", autouse=True)
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: None)
    await create_all()
    yield


@pytest.mark.anyio
async def test_chat_persists_messages():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # ensure project exists
        r = await ac.post("/api/projects", json={"type": "long", "title": "test", "description": ""})
        assert r.status_code == 200
        pid = r.json()["id"]

        r = await ac.post("/api/assistant/chat", json={"project_id": pid, "message": "hello"})
        assert r.status_code == 200
        body = r.json()
        assert "message_id" in body
        assert body["ok"] is True

        r = await ac.get(f"/api/assistant/session/{pid}/history")
        assert r.status_code == 200
        msgs = r.json()["messages"]
        assert any(m["role"] == "user" for m in msgs)
        assert any(m["role"] == "assistant" for m in msgs)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && pytest tests/test_assistant_history.py -v`
Expected: FAIL because `/assistant/session/{pid}/history` does not exist yet (Task 3).

- [ ] **Step 4: Persist messages in `/chat`**

Modify `backend/app/api/assistant.py`:

```python
from app.models import AssistantSession, Project, UserSetting, AssistantMessage
```

Inside `chat`, before running supervisor:

```python
    sess = await _ensure_session(db, project_id)
    user_msg = AssistantMessage(
        session_id=sess.id,
        role="user",
        content=user_input,
        metadata_={},
    )
    db.add(user_msg)
    await db.flush()
```

After `summary = await respond(...)`, before returning:

```python
    records_data = [r.model_dump() for r in records]
    assistant_msg = AssistantMessage(
        session_id=sess.id,
        role="assistant",
        content=summary,
        metadata_={
            "intent": plan.get("intent"),
            "change_record_ids": [r.get("id") for r in records_data],
        },
    )
    db.add(assistant_msg)
    await db.commit()
    await db.refresh(assistant_msg)
```

Update the return dict to include `message_id`:

```python
    return {
        "ok": True,
        "session_id": sess.id,
        "message_id": assistant_msg.id,
        "intent": plan.get("intent"),
        "change_records": records_data,
        "summary": summary,
    }
```

- [ ] **Step 5: Run test**

Run: `cd backend && pytest tests/test_assistant_history.py::test_chat_persists_messages -v`
Expected: PASS once Task 3 endpoint is implemented. If running before Task 3, it will still fail on history; proceed to Task 3 then rerun.

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/assistant.py backend/requirements.txt backend/tests/test_assistant_history.py
git commit -m "feat(assistant): persist chat messages and return message_id"
```

---

### Task 3: Add `GET /assistant/session/{project_id}/history`

**Files:**
- Modify: `backend/app/api/assistant.py`

**Interfaces:**
- Consumes: `AssistantMessage` model.
- Produces: `GET /assistant/session/{project_id}/history` returns `{ ok: true, messages: AssistantMessage[], session_id: string }`.

- [ ] **Step 1: Add endpoint**

Add to `backend/app/api/assistant.py`:

```python
@router.get("/session/{project_id}/history")
async def get_session_history(project_id: str, db: AsyncSession = Depends(get_db)):
    sess = await _ensure_session(db, project_id)
    res = await db.execute(
        select(AssistantMessage)
        .where(AssistantMessage.session_id == sess.id)
        .order_by(AssistantMessage.created_at.asc())
    )
    messages = [
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "metadata": m.metadata_ or {},
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in res.scalars().all()
    ]
    return {"ok": True, "session_id": sess.id, "messages": messages, "staged_changes": sess.staged_changes or []}
```

- [ ] **Step 2: Run test**

Run: `cd backend && pytest tests/test_assistant_history.py::test_chat_persists_messages -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add backend/app/api/assistant.py
git commit -m "feat(assistant): add session history endpoint"
```

---

### Task 4: Add `POST /assistant/stage`

**Files:**
- Modify: `backend/app/api/assistant.py`
- Modify: `backend/tests/test_assistant_history.py`

**Interfaces:**
- Consumes: `ChangeRecord` shaped JSON from frontend.
- Produces: `POST /assistant/stage` appends the record to `AssistantSession.staged_changes` and returns the updated staged list.

- [ ] **Step 1: Add endpoint**

Add to `backend/app/api/assistant.py`:

```python
@router.post("/stage")
async def stage_change(body: dict, db: AsyncSession = Depends(get_db)):
    session_id = body.get("session_id")
    record = body.get("change_record")
    if not session_id or not record:
        raise ValidationError("session_id 与 change_record 必填")
    sess = await db.get(AssistantSession, session_id)
    if not sess:
        raise NotFoundError("会话不存在")
    staged = list(sess.staged_changes or [])
    staged.append(record)
    sess.staged_changes = staged
    await db.commit()
    return {"ok": True, "staged_changes": staged}
```

- [ ] **Step 2: Add test**

Append to `backend/tests/test_assistant_history.py`:

```python
@pytest.mark.anyio
async def test_stage_change():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post("/api/projects", json={"type": "long", "title": "stage", "description": ""})
        pid = r.json()["id"]
        r = await ac.get(f"/api/assistant/session/{pid}/history")
        session_id = r.json()["session_id"]

        record = {
            "id": "test-record-1",
            "project_id": pid,
            "action": "update",
            "entity_type": "character",
            "entity_id": "char-1",
            "before": {"name": "Alice"},
            "after": {"name": "Alice2"},
            "requires_confirmation": True,
        }
        r = await ac.post("/api/assistant/stage", json={"session_id": session_id, "change_record": record})
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert any(rec["id"] == "test-record-1" for rec in r.json()["staged_changes"])
```

- [ ] **Step 3: Run test**

Run: `cd backend && pytest tests/test_assistant_history.py::test_stage_change -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/assistant.py backend/tests/test_assistant_history.py
git commit -m "feat(assistant): add stage endpoint for manual change records"
```

---

### Task 5: Update confirm/reject to mark message status

**Files:**
- Modify: `backend/app/api/assistant.py`
- Modify: `backend/app/services/change_apply.py` (if needed)

**Interfaces:**
- Consumes: `confirm_session` / `reject_session` results.
- Produces: Latest assistant message for the session gets `metadata.status` = `applied` or `rejected` and `metadata.applied_count` / `rejected_count`.

- [ ] **Step 1: Add helper to update latest assistant message**

Add to `backend/app/api/assistant.py`:

```python
async def _mark_latest_assistant_message(db, session_id: str, status: str, count: int = 0):
    res = await db.execute(
        select(AssistantMessage)
        .where(AssistantMessage.session_id == session_id, AssistantMessage.role == "assistant")
        .order_by(AssistantMessage.created_at.desc())
        .limit(1)
    )
    msg = res.scalars().first()
    if msg:
        meta = dict(msg.metadata_ or {})
        meta["status"] = status
        meta[f"{status}_count"] = count
        msg.metadata_ = meta
        await db.commit()
```

- [ ] **Step 2: Update confirm endpoint**

```python
@router.post("/confirm")
async def confirm(body: dict, db: AsyncSession = Depends(get_db)):
    session_id = body.get("session_id")
    if not session_id:
        raise ValidationError("session_id 必填")
    result = await confirm_session(db, session_id)
    await _mark_latest_assistant_message(
        db, session_id, "applied", len(result.get("applied", []))
    )
    return result
```

- [ ] **Step 3: Update reject endpoint**

```python
@router.post("/reject")
async def reject(body: dict, db: AsyncSession = Depends(get_db)):
    session_id = body.get("session_id")
    if not session_id:
        raise ValidationError("session_id 必填")
    result = await reject_session(db, session_id)
    await _mark_latest_assistant_message(
        db, session_id, "rejected", result.get("rejected_count", 0)
    )
    return result
```

- [ ] **Step 4: Update `reject_session` to return `rejected_count`**

Modify `backend/app/services/change_apply.py`:

```python
async def reject_session(db: AsyncSession, session_id: str) -> dict:
    res = await db.execute(select(AssistantSession).where(AssistantSession.id == session_id))
    sess = res.scalars().first()
    if not sess:
        raise NotFoundError("会话不存在")
    rejected_count = len(sess.staged_changes or [])
    sess.staged_changes = []
    await db.commit()
    return {"ok": True, "rejected_count": rejected_count}
```

- [ ] **Step 5: Manual smoke test**

1. Start backend: `cd backend && uvicorn app.main:app --port 8765`
2. Create a project via `POST /api/projects`.
3. Send a chat message via `POST /api/assistant/chat`.
4. Confirm via `POST /api/assistant/confirm`.
5. Fetch history via `GET /api/assistant/session/{project_id}/history` and verify the latest assistant message has `metadata.status == "applied"`.

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/assistant.py backend/app/services/change_apply.py
git commit -m "feat(assistant): mark latest message status on confirm/reject"
```

---

### Task 6: Frontend types and API updates

**Files:**
- Modify: `frontend/src/types/index.ts`
- Modify: `frontend/src/api/short.ts`

**Interfaces:**
- Produces: `AssistantMessage` interface; `assistantApi.history` and `assistantApi.stage` methods.

- [ ] **Step 1: Add types**

Append to `frontend/src/types/index.ts`:

```ts
export interface AssistantMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  metadata?: {
    intent?: string;
    change_record_ids?: string[];
    status?: "applied" | "rejected";
    applied_count?: number;
    rejected_count?: number;
  };
  created_at: string | null;
}
```

- [ ] **Step 2: Add API methods**

Modify `frontend/src/api/short.ts`:

```ts
import type { ChangeRecord } from "@/types";

export const assistantApi = {
  chat: (pid: string, message: string) => api.post("/assistant/chat", { project_id: pid, message }),
  session: (pid: string) => api.get(`/assistant/session/${pid}`),
  history: (pid: string) => api.get(`/assistant/session/${pid}/history`),
  stage: (sessionId: string, record: ChangeRecord) =>
    api.post("/assistant/stage", { session_id: sessionId, change_record: record }),
  confirm: (sessionId: string) => api.post("/assistant/confirm", { session_id: sessionId }),
  reject: (sessionId: string) => api.post("/assistant/reject", { session_id: sessionId }),
};
```

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npx tsc -b`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types/index.ts frontend/src/api/short.ts
git commit -m "feat(frontend): add AssistantMessage type and assistant API methods"
```

---

### Task 7: Frontend `useAssistantSession` store

**Files:**
- Create: `frontend/src/stores/useAssistantSession.ts`

**Interfaces:**
- Consumes: `assistantApi` and types from Task 6.
- Produces: Zustand store exposing `sessionId`, `messages`, `busy`, `pendingRecords`, `loadHistory`, `sendMessage`, `stageChange`, `confirm`, `reject`.

- [ ] **Step 1: Implement store**

Create `frontend/src/stores/useAssistantSession.ts`:

```ts
import { create } from "zustand";
import { assistantApi } from "@/api/short";
import type { AssistantMessage, ChangeRecord } from "@/types";

interface AssistantSessionState {
  sessionId: string | null;
  messages: AssistantMessage[];
  busy: boolean;
  pendingRecords: ChangeRecord[];
  loadHistory: (pid: string) => Promise<void>;
  sendMessage: (pid: string, text: string) => Promise<void>;
  stageChange: (record: ChangeRecord) => Promise<void>;
  confirm: () => Promise<void>;
  reject: () => Promise<void>;
}

export const useAssistantSession = create<AssistantSessionState>((set, get) => ({
  sessionId: null,
  messages: [],
  busy: false,
  pendingRecords: [],

  loadHistory: async (pid: string) => {
    const { data } = await assistantApi.history(pid);
    set({
      sessionId: data.session_id,
      messages: data.messages || [],
      pendingRecords: data.staged_changes || [],
    });
  },

  sendMessage: async (pid: string, text: string) => {
    set({ busy: true });
    try {
      const userMsg: AssistantMessage = {
        id: `local-${Date.now()}`,
        role: "user",
        content: text,
        created_at: new Date().toISOString(),
      };
      set((s) => ({ messages: [...s.messages, userMsg] }));
      const { data } = await assistantApi.chat(pid, text);
      const assistantMsg: AssistantMessage = {
        id: data.message_id,
        role: "assistant",
        content: data.summary,
        metadata: {
          intent: data.intent,
          change_record_ids: (data.change_records || []).map((r: ChangeRecord) => r.id),
        },
        created_at: new Date().toISOString(),
      };
      set((s) => ({
        sessionId: data.session_id,
        messages: [...s.messages, assistantMsg],
        pendingRecords: [...s.pendingRecords, ...(data.change_records || [])],
      }));
    } finally {
      set({ busy: false });
    }
  },

  stageChange: async (record: ChangeRecord) => {
    const sessionId = get().sessionId;
    if (!sessionId) return;
    await assistantApi.stage(sessionId, record);
    set((s) => ({
      pendingRecords: [...s.pendingRecords, record],
    }));
  },

  confirm: async () => {
    const sessionId = get().sessionId;
    if (!sessionId) return;
    set({ busy: true });
    try {
      const { data } = await assistantApi.confirm(sessionId);
      set((s) => {
        const messages = [...s.messages];
        let lastAssistant = -1;
        for (let i = messages.length - 1; i >= 0; i--) {
          if (messages[i].role === "assistant") {
            lastAssistant = i;
            break;
          }
        }
        if (lastAssistant >= 0) {
          messages[lastAssistant] = {
            ...messages[lastAssistant],
            metadata: {
              ...messages[lastAssistant].metadata,
              status: "applied",
              applied_count: (data.applied || []).length,
            },
          };
        }
        return { messages, pendingRecords: [] };
      });
    } finally {
      set({ busy: false });
    }
  },

  reject: async () => {
    const sessionId = get().sessionId;
    if (!sessionId) return;
    set({ busy: true });
    try {
      const { data } = await assistantApi.reject(sessionId);
      set((s) => {
        const messages = [...s.messages];
        let lastAssistant = -1;
        for (let i = messages.length - 1; i >= 0; i--) {
          if (messages[i].role === "assistant") {
            lastAssistant = i;
            break;
          }
        }
        if (lastAssistant >= 0) {
          messages[lastAssistant] = {
            ...messages[lastAssistant],
            metadata: {
              ...messages[lastAssistant].metadata,
              status: "rejected",
              rejected_count: data.rejected_count || 0,
            },
          };
        }
        return { messages, pendingRecords: [] };
      });
    } finally {
      set({ busy: false });
    }
  },
}));
```

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc -b`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/stores/useAssistantSession.ts
git commit -m "feat(frontend): add useAssistantSession zustand store"
```

---

### Task 8: `ChangeRecordCard` component

**Files:**
- Create: `frontend/src/components/ChangeRecordCard.tsx`

**Interfaces:**
- Consumes: `ChangeRecord` type.
- Produces: A visual card showing action, entity type, and field differences.

- [ ] **Step 1: Implement component**

Create `frontend/src/components/ChangeRecordCard.tsx`:

```tsx
import { Card, Tag } from "@/components/ui";
import type { ChangeRecord } from "@/types";

const ACTION_LABELS: Record<string, string> = {
  add: "新增",
  update: "修改",
  delete: "删除",
};

const ENTITY_LABELS: Record<string, string> = {
  character: "角色",
  outline: "大纲",
  foreshadow: "伏笔",
  world: "世界观",
  plot: "剧情节点",
  chapter: "章节",
};

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return v || "—";
  return JSON.stringify(v);
}

function diffFields(before: any, after: any): { key: string; before: string; after: string }[] {
  const keys = new Set([...Object.keys(before || {}), ...Object.keys(after || {})]);
  return Array.from(keys)
    .filter((k) => before?.[k] !== after?.[k])
    .map((k) => ({
      key: k,
      before: formatValue(before?.[k]),
      after: formatValue(after?.[k]),
    }));
}

export default function ChangeRecordCard({ record }: { record: ChangeRecord }) {
  const action = ACTION_LABELS[record.action] || record.action;
  const entity = ENTITY_LABELS[record.entity_type] || record.entity_type;
  const diffs = diffFields(record.before, record.after);

  return (
    <Card className="p-3 text-sm">
      <div className="mb-2 flex items-center gap-2">
        <Tag>{action}</Tag>
        <span className="font-medium text-ink">{entity}</span>
        <span className="text-xs text-muted">{record.entity_id || "（新增）"}</span>
      </div>
      <div className="space-y-1">
        {diffs.length === 0 && (
          <div className="text-xs text-muted">无字段变化</div>
        )}
        {diffs.map((d) => (
          <div key={d.key} className="grid grid-cols-[80px_1fr] gap-2 text-xs">
            <span className="text-muted">{d.key}</span>
            <div className="space-y-0.5">
              {record.action !== "add" && (
                <div className="line-through text-muted">{d.before}</div>
              )}
              {record.action !== "delete" && (
                <div className="text-ink">{d.after}</div>
              )}
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}
```

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc -b`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/ChangeRecordCard.tsx
git commit -m "feat(frontend): add ChangeRecordCard component"
```

---

### Task 9: `AssistantChat` component

**Files:**
- Create: `frontend/src/components/AssistantChat.tsx`

**Interfaces:**
- Consumes: `AssistantMessage`, `ChangeRecord` types; `ChangeRecordCard`.
- Produces: Scrollable chat stream with user/assistant messages and embedded change cards/status.

- [ ] **Step 1: Implement component**

Create `frontend/src/components/AssistantChat.tsx`:

```tsx
import { useEffect, useRef } from "react";
import { Button } from "@/components/ui";
import ChangeRecordCard from "./ChangeRecordCard";
import type { AssistantMessage, ChangeRecord } from "@/types";

interface AssistantChatProps {
  messages: AssistantMessage[];
  pendingRecords: ChangeRecord[];
  busy: boolean;
  onConfirm: () => void;
  onReject: () => void;
}

function StatusBadge({ metadata }: { metadata?: AssistantMessage["metadata"] }) {
  if (!metadata?.status) return null;
  if (metadata.status === "applied") {
    return <span className="text-xs text-accent">✓ 已应用 {metadata.applied_count || 0} 条</span>;
  }
  return <span className="text-xs text-muted">✗ 已拒绝</span>;
}

export default function AssistantChat({
  messages,
  pendingRecords,
  busy,
  onConfirm,
  onReject,
}: AssistantChatProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, pendingRecords]);

  let lastAssistantIndex = -1;
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === "assistant") {
      lastAssistantIndex = i;
      break;
    }
  }
  const lastAssistant = lastAssistantIndex >= 0 ? messages[lastAssistantIndex] : undefined;
  const showActions = pendingRecords.length > 0 && lastAssistant && !lastAssistant.metadata?.status;

  return (
    <div className="flex-1 space-y-4 overflow-y-auto px-6 py-4">
      {messages.length === 0 && !busy && (
        <div className="py-10 text-center text-sm text-muted">
          描述你的创作意图，例如“为主角增加一个宿敌角色”。
        </div>
      )}

      {messages.map((m) => (
        <div
          key={m.id}
          className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}
        >
          <div
            className={`max-w-[80%] space-y-2 ${
              m.role === "user" ? "bg-accent text-paper" : "bg-surface border border-line"
            } p-3 text-sm`}
          >
            <div className="whitespace-pre-wrap">{m.content}</div>
            {m.role === "assistant" && (
              <div className="flex items-center justify-between gap-4 pt-1">
                <StatusBadge metadata={m.metadata} />
              </div>
            )}
          </div>
        </div>
      ))}

      {showActions && (
        <div className="space-y-3 rounded-none border border-line bg-surface p-4">
          <div className="text-sm font-medium text-ink">待确认变更（{pendingRecords.length}）</div>
          <div className="space-y-2">
            {pendingRecords.map((r) => (
              <ChangeRecordCard key={r.id} record={r} />
            ))}
          </div>
          <div className="flex gap-2">
            <Button variant="primary" onClick={onConfirm} disabled={busy}>
              确认应用
            </Button>
            <Button variant="ghost" onClick={onReject} disabled={busy}>
              拒绝
            </Button>
          </div>
        </div>
      )}

      {busy && (
        <div className="text-xs text-muted">助手思考中…</div>
      )}

      <div ref={bottomRef} />
    </div>
  );
}
```

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc -b`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/AssistantChat.tsx
git commit -m "feat(frontend): add AssistantChat component"
```

---

### Task 10: `ContextPanel` component

**Files:**
- Create: `frontend/src/components/ContextPanel.tsx`
- Modify: `frontend/src/api/long.ts` (if needed)

**Interfaces:**
- Consumes: project data lists from `longApi`.
- Produces: collapsible panel with category tabs and item lists; emits `onQuote` and `onEdit` events.

- [ ] **Step 1: Ensure longApi lists exist**

Check `frontend/src/api/long.ts` for methods matching `characters`, `foreshadows`, `world`, `plot`, `chapters`, `outlines`. If any are missing, add them.

- [ ] **Step 2: Implement component**

Create `frontend/src/components/ContextPanel.tsx`:

```tsx
import { useEffect, useState } from "react";
import { longApi } from "@/api/long";
import { Button, Card } from "@/components/ui";

const CATEGORIES = [
  { key: "outline", label: "大纲", list: longApi.outlines, nameField: "title" },
  { key: "character", label: "角色", list: longApi.characters, nameField: "name" },
  { key: "foreshadow", label: "伏笔", list: longApi.foreshadows, nameField: "title" },
  { key: "world", label: "世界观", list: longApi.world, nameField: "category" },
  { key: "plot", label: "剧情节点", list: longApi.plot, nameField: "title" },
  { key: "chapter", label: "章节", list: longApi.chapters, nameField: "title" },
];

interface ContextPanelProps {
  pid: string;
  open: boolean;
  onToggle: () => void;
  onQuote: (prefix: string, name: string) => void;
  onEdit: (kind: string, item: any) => void;
}

export default function ContextPanel({ pid, open, onToggle, onQuote, onEdit }: ContextPanelProps) {
  const [active, setActive] = useState("character");
  const [items, setItems] = useState<any[]>([]);

  useEffect(() => {
    if (!open) return;
    const category = CATEGORIES.find((c) => c.key === active);
    if (!category) return;
    category.list(pid).then(({ data }) => setItems(data || []));
  }, [pid, active, open]);

  const activeCategory = CATEGORIES.find((c) => c.key === active)!;

  return (
    <div className="border-b border-line bg-surface">
      <button
        onClick={onToggle}
        className="flex w-full items-center justify-between px-4 py-2 text-sm font-medium text-ink hover:bg-surface-2"
      >
        <span>上下文 {open ? "▼" : "▶"}</span>
        <span className="text-xs text-muted">大纲 · 角色 · 伏笔 · 世界观 · 剧情节点 · 章节</span>
      </button>

      {open && (
        <div className="border-t border-line p-4">
          <div className="mb-3 flex gap-2 overflow-x-auto">
            {CATEGORIES.map((c) => (
              <button
                key={c.key}
                onClick={() => setActive(c.key)}
                className={`whitespace-nowrap border px-2 py-1 text-xs ${
                  active === c.key ? "border-accent bg-accent-soft text-accent-strong" : "border-line text-muted"
                }`}
              >
                {c.label}
              </button>
            ))}
          </div>

          <div className="grid max-h-48 grid-cols-1 gap-2 overflow-y-auto sm:grid-cols-2 lg:grid-cols-3">
            {items.map((it) => {
              const name = it[activeCategory.nameField] || "（未命名）";
              return (
                <Card key={it.id} className="p-2 text-xs">
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate font-medium text-ink">{name}</span>
                    <div className="flex shrink-0 gap-1">
                      <Button
                        variant="ghost"
                        className="h-6 px-1.5 text-[11px]"
                        onClick={() => onQuote(activeCategory.key, name)}
                      >
                        引用
                      </Button>
                      <Button
                        variant="ghost"
                        className="h-6 px-1.5 text-[11px]"
                        onClick={() => onEdit(activeCategory.key, it)}
                      >
                        编辑
                      </Button>
                    </div>
                  </div>
                </Card>
              );
            })}
            {items.length === 0 && <div className="text-xs text-muted">暂无{activeCategory.label}</div>}
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npx tsc -b`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/ContextPanel.tsx frontend/src/api/long.ts
git commit -m "feat(frontend): add ContextPanel component"
```

---

### Task 11: `ContextEntityEditor` component

**Files:**
- Create: `frontend/src/components/ContextEntityEditor.tsx`

**Interfaces:**
- Consumes: entity `kind` and `item`.
- Produces: a modal/panel form that constructs a `ChangeRecord` and calls `onSave`.

- [ ] **Step 1: Implement component**

Create `frontend/src/components/ContextEntityEditor.tsx`:

```tsx
import { useState } from "react";
import { Button, Input, Textarea, Card } from "@/components/ui";
import type { ChangeRecord } from "@/types";

interface ContextEntityEditorProps {
  kind: string;
  item: any;
  onSave: (record: ChangeRecord) => void;
  onCancel: () => void;
}

const FIELD_CONFIG: Record<string, { key: string; label: string; multiline?: boolean }[]> = {
  character: [
    { key: "name", label: "名称" },
    { key: "traits", label: "性格", multiline: true },
    { key: "ability", label: "能力", multiline: true },
    { key: "status", label: "状态" },
  ],
  foreshadow: [
    { key: "title", label: "标题" },
    { key: "content", label: "内容", multiline: true },
    { key: "state", label: "状态" },
  ],
  world: [
    { key: "category", label: "分类" },
    { key: "content", label: "内容", multiline: true },
  ],
  plot: [
    { key: "title", label: "标题" },
    { key: "summary", label: "概要", multiline: true },
    { key: "timeline_pos", label: "时间位置" },
  ],
  outline: [
    { key: "title", label: "标题" },
    { key: "content", label: "内容", multiline: true },
  ],
  chapter: [
    { key: "title", label: "标题" },
    { key: "content", label: "内容", multiline: true },
  ],
};

const ENTITY_LABELS: Record<string, string> = {
  character: "角色",
  outline: "大纲",
  foreshadow: "伏笔",
  world: "世界观",
  plot: "剧情节点",
  chapter: "章节",
};

export default function ContextEntityEditor({ kind, item, onSave, onCancel }: ContextEntityEditorProps) {
  const fields = FIELD_CONFIG[kind] || [];
  const [after, setAfter] = useState<Record<string, string>>(() => {
    const initial: Record<string, string> = {};
    fields.forEach((f) => (initial[f.key] = item[f.key] || ""));
    return initial;
  });

  const handleSave = () => {
    const before: Record<string, string> = {};
    const afterClean: Record<string, string> = {};
    fields.forEach((f) => {
      before[f.key] = item[f.key] || "";
      afterClean[f.key] = after[f.key];
    });
    const record: ChangeRecord = {
      id: `manual-${Date.now()}`,
      project_id: item.project_id,
      action: "update",
      entity_type: kind,
      entity_id: item.id,
      before,
      after: afterClean,
      requires_confirmation: true,
    };
    onSave(record);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/50 p-4">
      <Card className="w-full max-w-lg p-4">
        <div className="mb-3 text-sm font-medium text-ink">编辑{ENTITY_LABELS[kind] || kind}</div>
        <div className="space-y-3">
          {fields.map((f) => (
            <div key={f.key}>
              <label className="mb-1 block text-xs text-muted">{f.label}</label>
              {f.multiline ? (
                <Textarea
                  value={after[f.key] || ""}
                  onChange={(e) => setAfter({ ...after, [f.key]: e.target.value })}
                  rows={4}
                />
              ) : (
                <Input
                  value={after[f.key] || ""}
                  onChange={(e) => setAfter({ ...after, [f.key]: e.target.value })}
                />
              )}
            </div>
          ))}
        </div>
        <div className="mt-4 flex gap-2">
          <Button variant="primary" onClick={handleSave}>
            保存为变更建议
          </Button>
          <Button variant="ghost" onClick={onCancel}>
            取消
          </Button>
        </div>
      </Card>
    </div>
  );
}
```

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc -b`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/ContextEntityEditor.tsx
git commit -m "feat(frontend): add ContextEntityEditor component"
```

---

### Task 12: `AssistantStudio` integration

**Files:**
- Create: `frontend/src/components/AssistantStudio.tsx`
- Modify: `frontend/src/pages/LongWorkspace.tsx`

**Interfaces:**
- Consumes: `useAssistantSession`, `AssistantChat`, `ContextPanel`, `ContextEntityEditor`.
- Produces: Replaces the inline `AssistantPanel` in `LongWorkspace`.

- [ ] **Step 1: Implement AssistantStudio**

Create `frontend/src/components/AssistantStudio.tsx`:

```tsx
import { useEffect, useState } from "react";
import { useAssistantSession } from "@/stores/useAssistantSession";
import { Button, Input } from "@/components/ui";
import AssistantChat from "./AssistantChat";
import ContextPanel from "./ContextPanel";
import ContextEntityEditor from "./ContextEntityEditor";
import type { ChangeRecord } from "@/types";

export default function AssistantStudio({ pid }: { pid: string }) {
  const [input, setInput] = useState("");
  const [contextOpen, setContextOpen] = useState(false);
  const [editing, setEditing] = useState<{ kind: string; item: any } | null>(null);

  const {
    messages,
    pendingRecords,
    busy,
    sessionId,
    loadHistory,
    sendMessage,
    stageChange,
    confirm,
    reject,
  } = useAssistantSession();

  useEffect(() => {
    loadHistory(pid);
  }, [pid, loadHistory]);

  const handleSend = async () => {
    if (!input.trim()) return;
    const text = input;
    setInput("");
    await sendMessage(pid, text);
  };

  const handleQuote = (kind: string, name: string) => {
    const prefix = kind === "character" ? "@" : "#";
    setInput((prev) => `${prev}${prefix}${name} `);
  };

  const handleEditSave = async (record: ChangeRecord) => {
    await stageChange(record);
    setEditing(null);
  };

  return (
    <div className="flex h-full flex-col">
      <ContextPanel
        pid={pid}
        open={contextOpen}
        onToggle={() => setContextOpen((v) => !v)}
        onQuote={handleQuote}
        onEdit={(kind, item) => setEditing({ kind, item })}
      />

      <AssistantChat
        messages={messages}
        pendingRecords={pendingRecords}
        busy={busy}
        onConfirm={confirm}
        onReject={reject}
      />

      <div className="shrink-0 border-t border-line bg-surface p-4">
        <div className="flex gap-2">
          <Input
            placeholder="描述创作意图，Enter 发送，Shift+Enter 换行"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                handleSend();
              }
            }}
            disabled={busy}
          />
          <Button variant="primary" onClick={handleSend} disabled={busy || !input.trim()}>
            发送
          </Button>
        </div>
      </div>

      {editing && (
        <ContextEntityEditor
          kind={editing.kind}
          item={editing.item}
          onSave={handleEditSave}
          onCancel={() => setEditing(null)}
        />
      )}
    </div>
  );
}
```

- [ ] **Step 2: Replace AssistantPanel in LongWorkspace**

Modify `frontend/src/pages/LongWorkspace.tsx`:

1. Remove the local `AssistantPanel` function definition (lines ~197-272).
2. Add import at the top:

```tsx
import AssistantStudio from "@/components/AssistantStudio";
```

3. Replace the tab render:

```tsx
{tab === "assistant" && <AssistantStudio pid={id!} />}
```

- [ ] **Step 3: Typecheck and build**

Run: `cd frontend && npx tsc -b && npm run build`
Expected: no TypeScript errors and build succeeds.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/AssistantStudio.tsx frontend/src/pages/LongWorkspace.tsx
git commit -m "feat(frontend): integrate AssistantStudio into LongWorkspace"
```

---

### Task 13: End-to-end verification

**Files:**
- All of the above.

- [ ] **Step 1: Backend tests**

Run: `cd backend && pytest tests/test_assistant_history.py -v`
Expected: all tests pass.

- [ ] **Step 2: Backend smoke test**

Run: `cd backend && uvicorn app.main:app --port 8765`

In another terminal:

```bash
curl http://127.0.0.1:8765/health
```

Expected: `{"ok":true,...}`

Create a long project and use the assistant page:
1. Open the desktop client or `http://127.0.0.1:8765`.
2. Create a long novel project.
3. Go to“创作助手”，发送“为主角增加一个宿敌角色”。
4. Verify assistant message appears with change cards.
5. Click“确认应用” and verify the message status changes to“已应用 N 条”。
6. Refresh the page and verify chat history reloads.

- [ ] **Step 3: Frontend build**

Run: `cd frontend && npm run build`
Expected: `dist/` is updated with no errors.

- [ ] **Step 4: Final commit**

```bash
git add .
git commit -m "feat: redesign long-form assistant UX as studio with context and history"
```

---

## Self-Review

**Spec coverage:**
- 折叠式工作室布局 → Task 12 `AssistantStudio`.
- 后端持久化消息历史 → Tasks 1-3.
- 上下文面板 → Task 10.
- 上下文引用 → Task 12 `handleQuote`.
- 上下文直接编辑并走变更流 → Tasks 11-12.
- 逐条变更卡片 → Task 8.
- 聊天流内状态标记 → Task 9 `StatusBadge`.
- 范围外约束（不改短篇、不改其他标签页、不写 Neo4j/SSE） → respected.

**Placeholder scan:** None found; every step includes exact code, paths, and expected outputs.

**Type consistency:**
- `ChangeRecord` type is shared across `ChangeRecordCard`, `ContextEntityEditor`, and `useAssistantSession`.
- `AssistantMessage` type is used in store and chat component.
- `assistantApi.stage` receives `ChangeRecord`.

**Potential gaps fixed:**
- Added `/assistant/stage` so manual edits from context panel can enter the same staged/confirm flow.
- Added `message_id` return from `/chat` so the latest assistant message can be marked on confirm/reject.
- Noted SQLite migration approach.

# 长篇创作助手 UX 重设计

**日期**：2026-07-12  
**范围**：仅 `LongWorkspace` 中的“创作助手”标签页（`frontend/src/pages/LongWorkspace.tsx` 的 `AssistantPanel`）。  
**目标**：解决当前助手页面信息分散、缺乏上下文的问题，提供以连续对话为核心、可引用项目素材、可审核变更的工作室体验。

## 背景

当前 `AssistantPanel` 采用上下堆叠结构：输入框 → 摘要卡片 → 原始 JSON 变更列表 → 确认/拒绝按钮 → 日志。存在以下问题：

- 每次请求是独立的，没有对话历史，用户无法连续追问或修正。
- 变更建议以原始 `ChangeRecord.after` JSON 展示，难以快速理解。
- 用户与助手聊天时无法参考项目已有素材（角色、伏笔、世界观等）。

## 设计决策

| 维度 | 决策 |
|------|------|
| 整体形态 | 折叠式工作室：聊天流占主空间，上下文面板可折叠展开 |
| 对话历史 | 后端持久化完整消息流，刷新可恢复 |
| 上下文面板 | 展示项目素材，支持引用到输入框，支持发起编辑并走变更确认流 |
| 变更审核 | 逐条变更卡片，展示操作、实体类型、关键字段差异 |
| 历史/审计 | 状态标记嵌入聊天流，不单独抽屉 |

## 布局

```
┌─────────────────────────────────────────────────────────────────────┐
│  LongWorkspace 左侧导航（ outline | character | ... | assistant ）   │
├─────────────────────────────────────────────────────────────────────┤
│  [上下文 ▼]  大纲 · 角色 · 伏笔 · 世界观 · 剧情节点 · 章节           │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│   用户：帮我把主角加一个宿敌                                         │
│                                                                     │
│   ┌─────────────────────────────────────────────────────────────┐  │
│   │ 助手：好的，我建议新增角色“黑影”，并在大纲里埋一条对立线。  │  │
│   │                                                             │  │
│   │ [新增角色] 黑影                                             │  │
│   │   性格：冷酷、偏执                                          │  │
│   │   能力：影遁                                                │  │
│   │ [修改大纲] 第一章 → 新增冲突节点                            │  │
│   │                                                             │  │
│   │ [确认应用]  [拒绝]                                          │  │
│   └─────────────────────────────────────────────────────────────┘  │
│                                                                     │
│   用户：再把这个角色写得更偏执一点                                 │
│                                                                     │
│   ┌─────────────────────────────────────────────────────────────┐  │
│   │ 助手：已调整。                                              │  │
│   │ [修改角色] 黑影                                             │  │
│   │   性格：冷酷、偏执 → 冷酷、偏执、病态掌控欲                 │  │
│   │ ✓ 已应用 1 条                                               │  │
│   └─────────────────────────────────────────────────────────────┘  │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│  [消息输入框，Enter 发送，Shift+Enter 换行]                        │
└─────────────────────────────────────────────────────────────────────┘
```

- 顶部“上下文条”默认折叠，点击展开后按分类展示项目素材。
- 聊天流居中，消息气泡区分用户与助手。
- 输入框固定在底部，始终可输入。

## 组件拆分

### 新增/重命名前端组件

| 组件 | 路径 | 职责 |
|------|------|------|
| `AssistantStudio` | `frontend/src/components/AssistantStudio.tsx` | 替换原 `AssistantPanel`，管理布局、消息历史、上下文面板状态 |
| `AssistantChat` | `frontend/src/components/AssistantChat.tsx` | 渲染消息流，包含用户消息、助手消息、变更卡片、状态标记 |
| `ChangeRecordCard` | `frontend/src/components/ChangeRecordCard.tsx` | 单条变更的可视化卡片 |
| `ContextPanel` | `frontend/src/components/ContextPanel.tsx` | 可折叠的项目素材面板 |
| `ContextEntityEditor` | `frontend/src/components/ContextEntityEditor.tsx` | 在上下文里编辑素材并生成 ChangeRecord |
| `useAssistantSession` | `frontend/src/stores/useAssistantSession.ts` | 管理当前会话 ID、消息列表、加载态 |

### UI 组件复用

- `Button`, `Input`, `Textarea`, `Card`, `SectionTitle`, `Tag`, `Empty` 来自 `frontend/src/components/ui.tsx`。
- 视觉风格保持现有 Lovart 暖褐书香主题，不引入新主色。

## 数据模型与 API

### 后端新增模型

在 `backend/app/models.py` 新增：

```python
class AssistantMessage(Base):
    __tablename__ = "assistant_messages"

    id = Column(CHAR(36), primary_key=True, default=_uuid)
    session_id = Column(CHAR(36), ForeignKey("assistant_sessions.id"), nullable=False, index=True)
    role = Column(String(32), nullable=False)  # user / assistant / system
    content = Column(Text, default="")
    metadata = Column(JSON, default=dict)      # intent, change_record_ids, status 等
    created_at = Column(DateTime, default=_now, nullable=False)
```

### 后端接口变更

1. **`POST /api/assistant/chat`**
   - 在返回前，把用户输入和助手回复写入 `assistant_messages`。
   - 返回体增加 `message_id`（助手消息 ID）以便前端更新状态。

2. **`GET /api/assistant/session/{project_id}/history`**
   - 返回该项目的消息历史（按 `created_at` 正序）。
   - 如果没有会话则自动创建一个空会话。

3. **`POST /api/assistant/stage`**
   - 接收 `{ session_id, change_record }`。
   - 把用户手动构造的 `ChangeRecord` 追加到 `AssistantSession.staged_changes`。
   - 返回追加后的变更列表，前端将其展示在聊天流最后一条助手消息的待确认区，或新增一条系统/助手占位消息。

4. **`POST /api/assistant/confirm` / `reject`**
   - 保持现有逻辑，但成功后更新对应助手消息 `metadata.status` = `applied` / `rejected`。

### 前端 API 封装

在 `frontend/src/api/short.ts` 的 `assistantApi` 中新增：

```ts
history: (pid: string) => api.get(`/assistant/session/${pid}/history`),
stage: (sessionId: string, record: ChangeRecord) =>
  api.post("/assistant/stage", { session_id: sessionId, change_record: record }),
```

## 关键交互流程

### 首次进入助手页

1. 调用 `assistantApi.session(pid)` 获取或创建会话。
2. 调用 `assistantApi.history(pid)` 加载历史消息。
3. 渲染聊天流；历史中没有变更卡片的只展示文本摘要。

### 发送消息

1. 前端先把用户消息追加到本地列表，调用 `assistantApi.chat(pid, msg)`。
2. 收到响应后：
   - 追加助手消息，包含 `summary` 和 `change_records`。
   - 为每条 `ChangeRecord` 渲染 `ChangeRecordCard`。
   - 显示“确认应用”/“拒绝”按钮。

### 上下文引用

1. 用户展开上下文条，浏览项目素材。
2. 点击素材上的“引用”按钮，在输入框末尾追加 `@角色名 ` 或 `#章节标题 `。
3. 发送时，引用标记随消息一起传给后端（后端可选择在 prompt 里解析，当前版本可仅作展示）。

### 上下文直接编辑

1. 用户在上下文面板点击素材的“编辑”。
2. 弹出 `ContextEntityEditor`，修改字段。
3. 点击“保存为变更建议”：前端构造一条 `ChangeRecord`（`action=update`, `entity_type=character`, `entity_id=...`, `before`, `after`），调用 `assistantApi.stage(sessionId, record)` 写入后端会话的 `staged_changes`。
4. 该变更进入当前会话的待确认区，与助手生成的变更一起展示。

### 确认/拒绝变更

1. 用户点击“确认应用”：调用 `assistantApi.confirm(sessionId)`。
2. 成功后，前端把对应助手消息状态标记为 `已应用 N 条`。
3. 用户点击“拒绝”：调用 `assistantApi.reject(sessionId)`，标记为 `已拒绝`。

## 状态管理

使用 Zustand store：`frontend/src/stores/useAssistantSession.ts`

```ts
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
```

## 错误处理

- 沿用现有 `AppError` 子类，前端统一捕获并在消息气泡内显示错误文本。
- 后端写入消息历史失败不应影响 `/chat` 主响应，记录日志即可。

## 边界情况

- 空历史：显示欢迎提示和示例快捷指令（如“帮我设计一个反派角色”）。
- 无变更的助手回复：只显示文本摘要，不渲染变更卡片。
- 已确认/拒绝后：输入框保持可用，用户可继续追问。
- 上下文编辑生成的变更未确认前，不能再次编辑同一素材，避免冲突。

## 不做的范围

- 不改短篇 `ShortStudio` 的创作助手。
- 不改 `LongWorkspace` 其他标签页的编辑逻辑。
- 不引入真实 diff 算法；变更卡片只展示关键字段的 `before` / `after` 文本。
- 不实现 WebSocket/SSE 聊天流；仍用同步 POST。

## 实现顺序建议

1. 后端新增 `AssistantMessage` 模型；运行后 `create_all()` 会自动创建新表（如本地已有 `data/novel.db`，需删除后重建或手动迁移，当前项目未配置 Alembic）。
2. 后端 `/chat` 写消息历史；新增 `/session/{project_id}/history` 和 `/assistant/stage`。
3. 前端 `useAssistantSession` store 与 API 封装。
4. 前端 `AssistantStudio` 布局与 `AssistantChat` 消息流。
5. 前端 `ChangeRecordCard` 替换原始 JSON 展示。
6. 前端 `ContextPanel` + 引用功能。
7. 前端 `ContextEntityEditor` + 调用 `/assistant/stage` 生成变更建议。
8. 联调确认/拒绝后的状态标记。

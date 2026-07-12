# Assistant 多轮记忆与多 Session 设计

## 背景

当前 `/api/assistant/chat` 调用 LLM 时只传入 `user_input` 与项目 `context`，没有把历史对话传给模型。用户希望：

1. 助手具备多轮记忆。
2. 每 N 轮对话自动压缩为摘要（对 LLM 可见，不对用户显示）。
3. 支持新建对话，且旧对话保留可切换（多 session 存档）。

## 目标

- 让 supervisor / worker / responder 在决策时能看到历史上下文。
- 控制上下文长度，避免 token 无限增长。
- 一个项目支持多个 AssistantSession，用户可新建/切换。

## 非目标

- 不支持跨项目共享 session。
- 不支持对话内容的搜索/导出。
- 摘要生成不阻塞用户回复，采用异步触发。

## 数据模型变更

### `AssistantSession`

移除 `project_id` 的唯一约束，允许一个项目存在多个 session。

新增字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `title` | `str` | 对话标题，默认自动生成，如"对话 1"。 |
| `is_active` | `bool` | 一个 project 下仅有一个 `true`。 |
| `summaries` | `JSON` / `list[dict]` | 压缩摘要数组，结构见下。 |
| `message_count` | `int` | 自上次压缩以来累计的消息条数（user + assistant 各算 1）。 |

`summaries` 条目结构：

```json
{
  "turn_range": "1-20",
  "summary": "用户希望为项目增加主角刘修，并设定其为穿越者...",
  "created_at": "2026-07-13T10:00:00"
}
```

### `AssistantMessage`

无变更，继续通过 `session_id` 关联。

### `UserSetting`

新增设置项：

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `assistant_summary_threshold` | `int` | 20 | 每多少轮触发一次压缩。 |
| `assistant_max_summaries` | `int` | 5 | 最多保留几条历史摘要。 |
| `assistant_summary_max_length` | `int` | 1000 | 单条摘要最大字符数。 |

## API 变更

### 新增

- `POST /api/assistant/session/{project_id}`
  - 创建新 session。
  - 将同一 project 下其他 session 的 `is_active` 置为 `false`。
  - 返回新 session 的 `id`、`title`、`created_at`。

- `GET /api/assistant/sessions/{project_id}`
  - 返回该项目所有 session 列表，按 `updated_at` 倒序。
  - 字段：`id`、`title`、`is_active`、`created_at`、`updated_at`。

- `POST /api/assistant/session/{session_id}/switch`
  - 将指定 session 设为 active，其余 inactive。
  - 返回当前 active session 的摘要信息。

### 修改

- `GET /api/assistant/session/{project_id}/history`
  - 保持接口签名不变。
  - 内部改为返回当前 **active session** 的历史消息与 staged_changes。

- `POST /api/assistant/chat`
  - 内部改为向当前 active session 追加消息。
  - 回复生成后触发压缩检查。

## 压缩摘要流程

### 触发时机

每次 assistant 回复完成后：

1. `session.message_count += 2`（计入 user + assistant 各一条）。
2. 若 `session.message_count >= assistant_summary_threshold * 2`：
   - 取最近 `threshold * 2` 条消息。
   - 调用 LLM 生成摘要。
   - 将摘要写入 `session.summaries`。
   - `session.message_count = 0`。

### 摘要 Prompt

```text
请把以下 N 轮对话总结为一段简洁的摘要，保留用户的创作意图、关键指令和已确认的变更。该摘要仅用于后续对话的上下文，不对用户显示。

对话记录：
...
```

### 摘要清理

- 若 `len(summaries) > assistant_max_summaries`，删除最老的一条。
- 单条摘要超长时截断至 `assistant_summary_max_length`。

## LLM 上下文组装

每一轮发送给 LLM 的 messages 数组结构：

```python
[
    # 1. 系统 prompt（保持各节点原有 prompt）
    {"role": "system", "content": "..."},

    # 2. 历史摘要（对 LLM 可见）
    *[
        {"role": "user", "content": f"[历史摘要 {i+1}（{s['turn_range']}）] {s['summary']}"}
        for i, s in enumerate(session.summaries)
    ],

    # 3. 最近未满阈值的具体消息
    *[
        {"role": m.role, "content": m.content}
        for m in recent_messages
    ],

    # 4. 当前用户输入
    {"role": "user", "content": user_input},
]
```

说明：

- 摘要放在 `user` 角色消息中，避免改变 system prompt 语义。
- 若刚触发压缩，`recent_messages` 可能为空。
- Worker 的 tool loop 中也按同样结构注入摘要和最近消息。

## 前端 UI 变更

### 新增 `AssistantSessionSidebar` 组件

- 位于助手面板左侧。
- 展示当前项目的 session 列表。
- 高亮当前 active session。
- 每个 item 显示标题和最后更新时间。

### 新增操作

- **新建对话**：调用 `POST /api/assistant/session/{project_id}`，成功后切到新 session。
- **切换对话**：点击 session item，调用 `POST /api/assistant/session/{id}/switch`。

### 调整 store

`useAssistantSession` 新增：

- `sessions: AssistantSession[]`
- `loadSessions(pid): Promise<void>`
- `createSession(pid): Promise<void>`
- `switchSession(sessionId): Promise<void>`

### 入口调整

进入 `LongWorkspace` 时：

1. 调用 `loadSessions(pid)`。
2. 调用 `loadHistory(pid)`（自动使用 active session）。

### 样式

保持项目当前视觉风格，不做 Lovart 风格切换。

## Settings 页面

新增"助手"分组，包含三个数字输入框：

- 对话压缩阈值（轮）
- 最大保留摘要数
- 单条摘要最大长度（字符）

## 测试策略

1. 单元测试：`AssistantSession` 模型支持多 session 约束。
2. 单元测试：压缩触发逻辑在满阈值时生成摘要。
3. 单元测试：上下文组装包含 summaries 和 recent messages。
4. 集成测试：新建 session 后旧 session 历史不再出现在当前对话。
5. 集成测试：切换 session 后前端正确加载对应历史。

## 依赖与风险

- 需要修改现有 `AssistantSession` 表结构，需使用 Alembic 或手动迁移。
- 当前无 Alembic，新增字段需要删除旧表由 SQLAlchemy 重新创建，或写迁移脚本。
- 摘要生成额外调用一次 LLM，会增加 token 消耗和延迟；阈值可配置以平衡。

## 后续可扩展

- 允许用户手动编辑 session 标题。
- 对话历史导出。
- 基于摘要的跨会话搜索。

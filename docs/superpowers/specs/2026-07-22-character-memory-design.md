# 角色记忆管理功能设计文档

**日期**：2026-07-22  
**主题**：长篇小说角色已知信息记忆提取、管理与 Worker 查询  
**状态**：待实现

---

## 1. 背景与问题

当前正文生成 Prompt 对主角认知边界的描述为：

> 主角只能基于本章正文、前文尾部和本章已呈现信息做判断，禁止开头就知道尚未交代的设定。

这条规则过于严格，导致长篇小说中角色出现"金鱼记忆"：主角会忘记在前文已经确认获得的信息、经历和能力。根本原因：

1. 系统没有角色级别的记忆库；
2. Worker 生成正文时无法精确获知每个角色截至当前章节的已知信息；
3. 前文尾部摘要的召回精度随篇幅增长而下降。

本设计引入**角色记忆管理**，允许用户在完成章节校阅后点击"更新记忆"，由 LLM 自动提取角色已知信息，经用户确认后写入结构化记忆库，供后续 Worker 按重要性、时效性、关联关系查询。

---

## 2. 设计目标

1. 为每个角色维护独立的已知信息记忆库。
2. 记忆按**重要性**和**时效性**分类，减少 Worker 查询噪声。
3. 支持**来源章节溯源**和**关联角色/伏笔**召回。
4. 用户可手动增删改记忆。
5. 记忆提取结果需要用户确认，确认体验与 AI Chat 的 staged changes 一致。
6. 正本生成 Prompt 允许主角使用"截至本章已确认的记忆"，但禁止提前知道未揭露设定。

---

## 3. 数据模型

### 3.1 `LongCharacterMemory`（角色记忆表）

存储已确认的角色已知信息。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | CHAR(36) PK | UUID |
| `project_id` | CHAR(36), FK → projects.id | 项目 |
| `character_id` | CHAR(36), FK → long_characters.id | 所属角色 |
| `content` | Text | 记忆文本片段（自由文本） |
| `importance` | String | `core` / `major` / `minor` |
| `ttl` | String | `permanent` / `long` / `arc` / `scene` |
| `source_chapter_id` | CHAR(36), FK → long_chapters.id, nullable | 来源章节（手动修改可为 null） |
| `source_type` | String | `auto`（LLM 提取）/ `manual`（用户手动） |
| `related_character_ids` | JSON list | 关联角色 id 列表 |
| `related_foreshadow_ids` | JSON list | 关联伏笔 id 列表 |
| `created_at` | DateTime | 创建时间 |
| `updated_at` | DateTime | 更新时间 |

### 3.2 `LongCharacterMemoryDraft`（记忆提取草稿）

暂存 LLM 一次提取产生的候选记忆变更，等待用户确认。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | CHAR(36) PK | UUID |
| `project_id` | CHAR(36), FK | 项目 |
| `chapter_id` | CHAR(36), FK → long_chapters.id | 来源章节 |
| `character_id` | CHAR(36), FK → long_characters.id | 目标角色 |
| `action` | String | `add` / `update` / `delete` |
| `target_memory_id` | CHAR(36), FK → long_character_memories.id, nullable | update/delete 时指向现有记忆 |
| `content` | Text | 记忆文本 |
| `importance` | String | `core` / `major` / `minor` |
| `ttl` | String | `permanent` / `long` / `arc` / `scene` |
| `related_character_ids` | JSON list | 关联角色 |
| `related_foreshadow_ids` | JSON list | 关联伏笔 |
| `created_at` | DateTime | 提取时间 |

### 3.3 `LongChapterMemoryExtraction`（章节记忆提取记录）

记录每章上次成功提取记忆的状态，用于判断是否需要重新提取。

| 字段 | 类型 | 说明 |
|---|---|---|
| `chapter_id` | PK, FK → long_chapters.id | 章节 id |
| `extracted_at` | DateTime | 上次成功提取时间 |
| `content_hash` | String | 提取时正文 hash（SHA-256 前 16 位） |
| `memory_count` | Integer | 本次提取产出的记忆条数 |

### 3.4 与现有模型的关系

- 记忆不通过 `change_apply.py` 的 `_ENTITY_REPO` 写入，因为记忆不经过 staged_changes 确认流程；它由专用 API 直接落库。
- 手动修改的记忆 `source_type = manual`，`source_chapter_id = null`。
- 自动提取的记忆 `source_type = auto`，`source_chapter_id` 指向来源章节。

---

## 4. API 设计

新增 `backend/app/api/long_memory.py`，在 `main.py` 中以 `/api/long` 前缀注册。

### 4.1 路由列表

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/long/chapters/{chapter_id}/extract-memory` | 提取本章记忆候选（不落库） |
| GET | `/long/chapters/{chapter_id}/memory-drafts` | 获取本章的记忆候选 |
| POST | `/long/memory-drafts/apply` | 批量确认应用候选 |
| POST | `/long/memory-drafts/discard` | 丢弃本章候选 |
| GET | `/long/characters/{character_id}/memories` | 获取角色已确认记忆 |
| POST | `/long/characters/{character_id}/memories` | 手动新增记忆 |
| PUT | `/long/characters/{character_id}/memories/{memory_id}` | 手动修改记忆 |
| DELETE | `/long/characters/{character_id}/memories/{memory_id}` | 删除记忆 |

### 4.2 `POST /long/chapters/{chapter_id}/extract-memory`

**流程**：

1. 读取章节，校验 `project_id`；
2. 计算当前 `content` 的 hash；
3. 查 `LongChapterMemoryExtraction`：
   - 若记录存在且 `content_hash` 相同，返回 `{ok: true, skipped: true, message: "本章记忆已是最新，是否重新提取？"}`；
   - 否则继续；
4. 删除本章旧的 `LongCharacterMemoryDraft`；
5. 识别本章出场角色（基于正文/细纲 + 角色姓名匹配）；
6. 对每个角色，读取其现有 `LongCharacterMemory`；
7. 调用 LLM 提取候选记忆变更；
8. 将候选写入 `LongCharacterMemoryDraft`；
9. 返回 `{ok: true, drafts: [...], grouped_by_character: {...}}`。

**LLM 输入**：本章正文、目标角色现有记忆、项目角色列表、项目伏笔列表。

**LLM 输出格式**：

```json
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
```

### 4.3 `POST /long/memory-drafts/apply`

**请求体**：`{ chapter_id: string }`

**流程**：

1. 读取 `chapter_id` 对应的所有 drafts；
2. 事务处理：
   - `add` → 插入 `LongCharacterMemory`（`source_type=auto`，`source_chapter_id=chapter_id`）；
   - `update` → 更新对应 memory 的 `content` / `importance` / `ttl` / `related_*`；
   - `delete` → 删除对应 memory；
3. 写入/更新 `LongChapterMemoryExtraction`（记录 `extracted_at` 和 `content_hash`）；
4. 清空本章 drafts；
5. 返回 `{ok: true, applied: {created: n, updated: n, deleted: n}}`。

### 4.4 `POST /long/memory-drafts/discard`

**请求体**：`{ chapter_id: string }`

**流程**：删除本章所有 drafts，不更新 extraction 记录。

### 4.5 手动记忆 CRUD

- 手动新增：`source_type=manual`，`source_chapter_id=null`；
- 手动修改：更新字段，`source_type` 保持 `manual`；
- 删除：直接删除记录。

---

## 5. Worker 集成

### 5.1 新增只读工具

在 `app/agents/tools/__init__.py` 注册：

```python
async def read_character_memories(
    db: AsyncSession,
    character_id: str,
    importance: str | None = None,
    ttl: str | None = None,
    related_foreshadow_id: str | None = None,
    limit: int = 20,
) -> list[dict]:
    ...
```

工具描述：按角色查询已确认记忆，支持按 importance / ttl 过滤、按伏笔 id 召回相关记忆。

### 5.2 Worker 调用点

| Worker | 用法 |
|---|---|
| `ChapterTextWorker` | 生成正文前，为每个主要出场角色查询记忆，注入 prompt |
| `ChapterOutlineWorker` | 生成细纲时，为聚焦角色查询核心/长期记忆 |
| `PlotNodesWorker` | 设计剧情节点时，查询与伏笔相关的记忆 |
| `BroadOutlineWorker` | 查询核心记忆，避免总纲与角色已知信息冲突 |

### 5.3 Prompt 调整

#### 5.3.1 正本生成 Prompt（`chapter_text_prompt`）

将现有规则：

> 主角只能知道本章正文、前文尾部以及本章内已呈现信息。严禁在开头就让主角知道尚未交代的设定...

替换为：

> **主角认知边界**：主角知道的内容包括：
> 1. 本章正文内已呈现的信息；
> 2. 前文尾部信息；
> 3. 截至本章已由作者确认的角色记忆中标记为 `permanent` / `long` / `arc`（且来源章节早于或等于本章）的信息；
> 4. 与当前活跃伏笔相关、且已确认的记忆。
>
> 严禁主角知道以下信息：
> - 本章尚未呈现的全新设定；
> - 仅在未来章节才会揭露的内容；
> - 虽在角色记忆中但明确与当前场景无关且已过时的 `scene` 级记忆；
> - 其他角色知道但本角色尚未获得途径知晓的信息。

#### 5.3.2 新增记忆提取 Prompt

新增 `memory_extraction_prompt`，核心规则：

- 只提取角色在本章中实际获得的信息；
- 区分事实与推断，统一以自由文本表达；
- 标注 `importance` 和 `ttl`；
- 识别关联角色和关联伏笔；
- 现有记忆被推翻时输出 `update`；
- 现有记忆过时时输出 `delete`。

---

## 6. 前端界面

### 6.1 章节编辑器（`ChapterEditor.tsx`）

在顶部工具栏新增按钮：

```tsx
<Button variant="ghost" onClick={handleExtractMemory}>更新记忆</Button>
```

点击后：
1. 调用 `POST /long/chapters/{chapter_id}/extract-memory`；
2. 若返回 `skipped: true`，弹窗提示"本章记忆已是最新，是否重新提取？"；
3. 否则进入"记忆候选"预览面板。

### 6.2 记忆候选预览面板

在 `ChapterEditor` 下方展开一个面板，按角色分组显示候选变更：

```
【第 N 章 记忆候选】

角色：李雷
  [新增] 他知道韩梅梅会武功（importance: major, ttl: long）
  [修改] "他认为韩梅梅只是普通人" → "他知道韩梅梅其实会武功"

角色：韩梅梅
  [新增] 她在本章暴露了武功

[确认应用] [取消]
```

复用现有的 `ChangeRecordCard` 样式风格（黑白灰、1px 边框、零圆角）。

### 6.3 角色详情页

在角色编辑区域新增"记忆"标签页：

- 列出该角色所有已确认记忆；
- 每条记忆显示：content、importance / ttl 标签、来源标识、关联角色/伏笔；
- 来源显示规则：
  - `auto` + `source_chapter_id` → "第 N 章自动提取"
  - `manual` → "用户手动修改"
- 支持手动新增、编辑、删除记忆。

### 6.4 API 封装

在 `frontend/src/api/long.ts` 新增：

```ts
extractMemory: (chapterId: string) => api.post(`/long/chapters/${chapterId}/extract-memory`),
memoryDrafts: (chapterId: string) => api.get(`/long/chapters/${chapterId}/memory-drafts`),
applyMemoryDrafts: (chapterId: string) => api.post(`/long/memory-drafts/apply`, { chapter_id: chapterId }),
discardMemoryDrafts: (chapterId: string) => api.post(`/long/memory-drafts/discard`, { chapter_id: chapterId }),
characterMemories: (characterId: string) => api.get(`/long/characters/${characterId}/memories`),
addCharacterMemory: (characterId: string, data: any) => api.post(`/long/characters/${characterId}/memories`, data),
updateCharacterMemory: (characterId: string, memoryId: string, data: any) => api.put(`/long/characters/${characterId}/memories/${memoryId}`, data),
deleteCharacterMemory: (characterId: string, memoryId: string) => api.delete(`/long/characters/${characterId}/memories/${memoryId}`),
```

---

## 7. 边界处理

| 场景 | 处理 |
|---|---|
| 本章没有出场角色 | 返回 `drafts: []`，清空旧 drafts，更新 extraction 记录 |
| LLM 输出格式错误 | 捕获异常，返回错误信息，不写 drafts |
| 部分角色提取失败 | 记录日志，只返回成功角色的 drafts |
| 用户拒绝/取消 | 调用 discard，清空本章 drafts，不更新 extraction |
| 重复提取同一章 | hash 相同则提示跳过；hash 不同则重新生成 drafts 并覆盖 |
| 手动修改后再自动提取 | 手动记忆不会被 LLM 自动覆盖，除非 LLM 明确输出 update/delete |
| 角色删除 | 级联删除该角色所有 memory 和 draft |
| 章节删除 | 级联删除该章节相关的 memory 来源标识、drafts、extraction 记录 |

---

## 8. 实现顺序

1. **数据层**：新增 `LongCharacterMemory`、`LongCharacterMemoryDraft`、`LongChapterMemoryExtraction` 模型；
2. **服务层**：实现记忆提取服务（LLM 调用 + drafts 写入）；
3. **API 层**：实现 `extract-memory`、`memory-drafts`、`apply/discard`、`character memories CRUD`；
4. **后端 Worker 工具**：新增 `read_character_memories`；
5. **Prompt 调整**：更新 `chapter_text_prompt`，新增 `memory_extraction_prompt`；
6. **Worker 集成**：在 `ChapterTextWorker` 等 worker 中查询并注入记忆；
7. **前端**：章节编辑器加按钮 + 候选预览面板；
8. **前端**：角色详情页记忆管理。

---

## 9. 验证方式

- 后端语法检查：`cd backend && python -m compileall app`
- 前端类型检查：`cd frontend && npx tsc -b`
- 功能验证：
  1. 生成第 N 章正文；
  2. 点击"更新记忆"，确认候选；
  3. 生成第 N+1 章正文，观察主角是否正确引用第 N 章已确认的记忆；
  4. 手动修改某条记忆，验证来源显示为"用户手动修改"。

---

## 10. 未纳入本次范围

- 记忆的向量语义检索（当前按结构化字段过滤 + 关联关系召回，已满足需求）；
- 角色之间的"共同记忆"或"秘密"权限控制；
- 记忆的自动过期/遗忘机制（通过 `ttl=scene/arc` + Worker 过滤实现）。

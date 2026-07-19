# 长篇工作台重做 / 助手窗口缩放增强 / 确认去重 — 设计文档

日期：2026-07-19
状态：已获用户方向确认（三栏工作台 / 八方向拖拽 / 自动转为更新）

## 背景与问题

1. **长篇项目界面**：`LongWorkspace.tsx` 中角色/伏笔/世界观/剧情节点均为平铺卡片列表，只能新增和删除，不能直接编辑（后端 PUT 路由和前端 `longApi.update*` 都已存在但未接入 UI）；世界观数据有 `category` 字段但 UI 未按分类展示，缺少分类/分组能力。
2. **AI 助手窗口**：`FloatingAssistant.tsx` 只有右下角一个 16px 的拖拽手柄，不易发现，用户感知为"只有全屏和小窗两种模式"。
3. **确认添加重复**：`change_apply.apply_change` 的 `action="add"` 无条件插入，AI 助手暂存的变更确认入库时可能出现同名角色等重复条目。

三个需求相互独立，可分别实施、分别验证。

## 需求一：长篇工作台三栏重做

### 布局

左侧分类导航保持现有结构（大纲树 / 角色 / 伏笔 / 世界观 / 剧情节点 / 章节 / 图谱）。除章节、图谱外，每个分类页从"平铺卡片"改为**双栏工作台**：

```
┌──────────┬───────────────────┬──────────────────────┐
│ 分类导航  │ 条目列表（中栏）    │ 编辑面板（右栏）        │
│ (现有)    │ 搜索框 + 分组列表   │ 选中条目的可编辑表单    │
└──────────┴───────────────────┴──────────────────────┘
```

### 组件设计

新增通用组件 `EntityWorkbench`（`frontend/src/components/EntityWorkbench.tsx`），通过配置驱动复用于角色/伏笔/世界观/剧情节点/大纲：

```ts
interface EntityWorkbenchConfig {
  kind: string;                       // 实体类型，对应 KIND_API
  label: string;                      // 显示名，如 "角色"
  fields: { key: string; label: string; multiline?: boolean }[];
  titleOf: (item: any) => string;     // 列表行标题
  subtitleOf?: (item: any) => string; // 列表行副标题（摘要预览）
  groupBy?: (item: any) => string;    // 分组键，返回分组名
  groupOrder?: string[];              // 分组排序（可选）
  searchKeys: string[];               // 参与搜索的字段
}
```

- **中栏列表**：顶部搜索框（按 `searchKeys` 过滤，大小写不敏感）+「+ 新增」按钮；条目按 `groupBy` 分组渲染，组头显示组名和条数；单击选中载入右栏。
- **右栏编辑面板**：按 `fields` 渲染表单（`multiline` 用 Textarea，其余 Input）；「保存」调用对应 `longApi.update*`；「删除」加 `window.confirm` 确认后调用 `delete*` 并清空选中。
- **新增**：中栏「+ 新增」在右栏打开空白表单，保存时调用 `add*`（POST），成功后选中新条目。
- 未选中任何条目时右栏显示空状态提示（"从左侧选择或新建条目"）。

### 各分类配置

| 分类 | 分组键 | 搜索键 | 说明 |
|---|---|---|---|
| 角色 | `status`（存活/死亡等） | `name`, `traits` | 编辑字段：name, traits, ability, status |
| 伏笔 | `state`（pending/revealed/abandoned，固定顺序） | `title`, `content` | state 用下拉选择 |
| 世界观 | `category`（空值归入"未分类"） | `category`, `content` | 编辑字段：category, content |
| 剧情节点 | 不分组，按 `timeline_pos` + `order` 排序 | `title`, `summary` | 编辑字段：title, summary, timeline_pos |
| 大纲 | 不分组，按 `order` 排序 | `title`, `content` | 编辑字段：title, content；保留"复制为新版"按钮（在编辑器内） |

### 范围外（YAGNI）

- 章节 tab 保持现状（已是双栏 ChapterList + ChapterEditor）。
- 图谱 tab 保持现状。
- 角色的 `relations`（JSON）、`importance` 字段本期不做编辑 UI，列表/编辑均不涉及。
- 不做拖拽排序、不做大纲树形缩进展示（`parent_id` 层级维持现状的平铺+排序）。
- 视觉风格保持现有体系（沿用 `ui.tsx` 的 Card/Button/Input/SectionTitle 与现有配色/圆角），不引入新设计系统。

### 改动文件

- 新增 `frontend/src/components/EntityWorkbench.tsx`
- 重写 `frontend/src/pages/LongWorkspace.tsx` 中的 `OutlinePanel`、`CrudPanel`（由 `EntityWorkbench` 配置实例替换）；`ChapterPanel`、`GraphPanel`、左侧导航不动。

## 需求二：AI 助手窗口八方向缩放

### 设计

升级 `frontend/src/hooks/useResizable.ts`（或在其上扩展），支持八方向拖拽：

- **拖拽热区**：四条边（上/下/左/右）+ 四个角，每个热区约 6px 宽的透明绝对定位条。
- **方向语义**：面板锚定在视口右下（`fixed bottom-4 right-4`），宽度增长向左扩展、高度增长向上扩展。因此**主可见手柄放在左上角**（cursor `nwse-resize`，向左上拖拽即放大），替代现有右下角手柄——现有右下角手柄拖拽方向与面板实际增长方向相反，是"不好用"的根因之一。右边/下边热区向内拖用于缩小。
- **约束**：`min {320, 400}`、`max {1200, 900}`，且钳制不超过当前视口宽高减 32px 边距。
- **持久化**：沿用现有 localStorage（`novel-assistant-panel-size`），读取时同样钳制。
- **最大化**：`isMaximized` 时所有拖拽热区禁用；还原后恢复拖拽前记忆的尺寸（现有逻辑已满足）。
- 光标样式按方向设置（`ew/ns/nwse/nesw-resize`）。

### 改动文件

- `frontend/src/hooks/useResizable.ts`：`startResize` 接受方向参数。
- `frontend/src/components/FloatingAssistant.tsx`：渲染八个热区；可见手柄从右下角移至左上角并放大。

## 需求三：确认时去重 — 自动转为更新

### 设计

在 `backend/app/services/change_apply.py` 的 `apply_change` 中，`action == "add"` 分支先查重：

**匹配键**（同 `project_id` 内，`strip()` 后大小写不敏感比较）：

| 实体 | 匹配字段 | 命中后行为 |
|---|---|---|
| character | `name` | 转为 update：将 `after` 中非空字段合并更新到已存在条目 |
| foreshadow | `title` | 同上 |
| plot | `title` | 同上 |
| outline | `title` | 同上 |
| world | `category + content` 完全相同 | 视为重复，跳过（不新增不更新），返回 `skipped_duplicate` |
| chapter | 不去重 | 允许同名章节，直接新增 |

**合并语义**（转 update 时）：

- `after` 中值为非空字符串 / 非空值的字段覆盖已有条目对应字段；空字符串、`None`、缺失键不覆盖（避免 AI 生成的部分字段清空已有数据）。
- 若 `after` 经清洗后没有任何非空字段，则视为无操作，返回 `skipped_duplicate`。
- `LongChangeRecord` 照常记录：`entity_id` 为被合并的已有条目 id，`before` 取合并前该条目快照，`after` 为实际应用的字段子集，`status="applied"`。
- `apply_change` 返回值增加可选键：`{"ok": True, "merged_into": <id>}` 或 `{"ok": True, "skipped_duplicate": True, "entity_id": <id>}`，供上层统计。

**确认结果反馈**：

- `confirm_session` 的返回中，`applied` 列表元素带上 `merged_into` / `skipped_duplicate` 标记（透传）。
- 前端 `useAssistantSession.confirm` 收到结果后，若存在 merged/skipped 条目，在聊天区显示一条系统提示，如"3 条已应用，其中 1 条合并到现有条目「张三」，1 条重复已跳过"。
- 同批 staged 中两条同名新增自然收敛：第一条落库后，第二条查到它并转为更新。

**查重实现**：新增 `_find_duplicate(db, entity_type, project_id, after)` 辅助函数，用 SQLAlchemy `select` 按 project_id 拉取后在 Python 侧做规范化比较（数据量为单项目级，可接受；避免数据库层面大小写不敏感比较的方言差异）。

### 改动文件

- `backend/app/services/change_apply.py`：`_find_duplicate` + add 分支改造。
- `frontend/src/stores/useAssistantSession.ts`：confirm 后汇总提示（merged/skipped 计数）。
- `frontend/src/components/AssistantChat.tsx`（或消息列表处）：渲染该系统提示（若无合适位置则以一条本地消息形式插入）。

## 错误处理

- 前端工作台：列表/保存/删除失败时在面板顶部显示错误条（沿用 ChapterPanel 的 error 条模式）。
- 后端查重：任何查重异常不阻断新增（降级为直接插入并记 warning 日志），避免因查重 bug 阻塞确认流程。
- 合并更新复用现有 update 路径，Neo4j 镜像同步逻辑不变。

## 验证方式

- `cd backend && python -m compileall app`
- `cd frontend && npx tsc -b && npm run build`
- 手动冒烟：
  1. 长篇项目 → 角色页：搜索、分组、选中编辑保存、新增、删除。
  2. 助手窗口：各边/角拖拽、最大化/还原、刷新后尺寸保持。
  3. 通过助手暂存一条与现有角色同名的新增 → 确认 → 断言数据库中角色数不增加、原条目字段被合并、聊天区出现合并提示。

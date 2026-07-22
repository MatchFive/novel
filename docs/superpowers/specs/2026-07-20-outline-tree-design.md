# 长篇大纲多级树设计（总纲 → 时期 → 卷）

日期：2026-07-20
状态：已确认（方案 A）

## 背景与目标

长篇大纲目前是平铺单条记录：一个时期的所有卷内容塞在一个条目的 `content` 里，导致：

- 大纲页展示/编辑体验差（整坨文本）
- 下游消费（章节生成、续写）无法按卷选取相关大纲，只能整段截断

目标：大纲改为**固定三级树**（总纲 `broad` → 时期 `period` → 卷 `volume`），卷节点记录章节范围，下游按章号命中所属卷取用大纲；现有整坨数据通过 AI 一键拆分迁移；AI 生成大纲同步升级为直接产出树结构。

### 已确认的决策

| 问题 | 决策 |
|---|---|
| 层级结构 | 固定三级：总纲 → 时期 → 卷 |
| 现有整坨数据 | AI 一键拆分（LLM 按内容拆成树，确认流生效） |
| 章节 ↔ 卷关联 | 卷节点记录章节范围（chapter_start/end，1-based 章号） |
| AI 生成大纲 | 一起升级，直接产出嵌套树 |

## 方案概述（方案 A）

复用现有 `long_outlines` 表：`parent_id` 回归树父级语义，`type` 区分三级，新增 `chapter_start` / `chapter_end` 两列。所有写操作继续走单一写入口 `change_apply`；AI 拆分/生成走 staged-changes 确认流。

不采用的备选：B（标题约定伪层级，层级是假的、解析脆弱）；C（新建树表，版本链/审计/镜像全部重做，成本翻倍）。

## §1 数据模型与迁移

### `long_outlines` 表变更

| 列 | 变化 | 说明 |
|---|---|---|
| `type` | 扩展语义 | `broad`（总纲，根级）/ `period`（时期）/ `volume`（卷）。现有行全是 `broad`，无需数据迁移 |
| `parent_id` | 语义修正 | 纯树父级。删除 `api/long_outline.py` add_outline 中"parent_id 继承 version_chain"的旧逻辑；`version_chain` 是独立列，「复制为新版」继续走它，两者脱钩 |
| `chapter_start` / `chapter_end` | 新增列 | 可空整数，仅卷节点使用，1-based 章号（章节 `order` 为 0-based，匹配时 +1）。走启动时幂等迁移，沿用现有"ALTER TABLE 存在即跳过"模式 |

### 层级约束（API 与 `change_apply` 双侧校验）

- `broad`：`parent_id` 必须为空；`period`：父必须是 `broad`；`volume`：父必须是 `period`
- update 移动节点时禁止移到自身后代下（环检测）
- 删除有子级的节点 → 拒绝并提示先处理子级
- 卷的 `chapter_start <= chapter_end`；允许留空（未指定范围）

### AI 拆分对原条目的处理

不删除原条目，而是**原条目升级为父节点**：如"开荒期·百废待兴"整坨条目拆分后本身变为 `period` 节点（content 由 LLM 改写为时期概述），各卷为其新增 volume 子节点。版本链历史自然保留。

## §2 后端消费逻辑

- **章节生成**（`agents/harness/workers/chapter_workers.py`）
  - `_broad_outline_text` 只取 `broad` 节点（现状不变）
  - 新增 `_volume_outline_text(outlines, chapter_order)`：按章号命中 `chapter_start/end` 找到所属卷，返回卷大纲全文 + 父时期概述
  - `chapter_outline` 与 `chapter_text` 两个 worker 的 prompt context 增加 `volume_outline` 字段，模板各加一节「本卷大纲」
  - 命中不到卷时回退为时期标题列表，不报错
- **续写**（`api/long_continue.py` assemble_context）：总纲全量（截断）+ 最后一章所属卷的完整大纲 + 其余时期/卷只列标题（替换现在"前 10 条各截 200 字"）
- **导出**（`services/export.py`）：大纲部分按树缩进输出（总纲 → 时期 → 卷）
- **ContextBuilder / 图谱**：outline 标题文本匹配逻辑不变；`parent_id` / `type` / `chapter_start` / `chapter_end` 随 `_sanitize_fields` 自动进入 Neo4j 镜像

## §3 AI 拆分与 AI 生成升级

### AI 拆分（新增 `OutlineSplitWorker`）

- 入口：大纲树节点的「AI 拆分」按钮 → 复用 assistant session（同 ChapterPanel `sendMessage(pid, text, context)` 模式），context 带 `{entity_type: "outline", entity_id}`；supervisor 增加 `outline_split` 意图分类
- Worker 读目标条目 content，LLM 输出 JSON：
  - 目标含多个时期（总纲）→ `{periods: [{title, summary, volumes: [...]}]}`
  - 目标是单个时期 → `{summary, volumes: [{title, content, chapter_start, chapter_end}]}`
- 产出 ChangeRecords：**update 原条目**（type 升级 + content 改写为概述）+ **add 各子节点**。原条目已存在，子节点 `parent_id` 直接用真实 id，无需 temp_id
- 章号范围由 LLM 按卷内章节规划推断，允许留空，用户可在编辑器手改

### AI 生成升级（改造 `BroadOutlineWorker`）

- prompt 改为输出嵌套 JSON：`{broad: {title, content}, periods: [{title, content, volumes: [{title, content, chapter_start, chapter_end}]}]}`
- aggregator 展开为多条 ChangeRecord，树内引用用 `temp_id`：add 总纲（`temp:1`）→ add 时期（parent=`temp:1`，`temp:2`）→ add 卷（parent=`temp:2`）

### `confirm_session` temp_id 重写（`services/change_apply.py`）

- add 类 ChangeRecord 可携带 `temp_id`；应用成功后记录 `temp→真实 id` 映射
- 应用后续变更前，将 `after.parent_id` 中的 temp 引用重写为真实 id
- 父级应用失败 → 引用它的子级报结构化错误（`PARENT_FAILED`），进入 errors，不静默跳过；逐条独立 commit 语义不变

## §4 前端树 UI

大纲 tab 不再走 `EntityWorkbench` 平铺，新建 `OutlinePanel.tsx`（其他实体页不变）：

- **左栏树**：缩进 + 展开/折叠箭头；节点带类型徽标（总纲/时期/卷）；搜索时显示命中节点及其祖先链；`order` 排序不变
- **新增**：根级「新增总纲」；选中节点后「新增子级」（broad→period、period→volume，volume 无子级按钮）
- **右栏编辑器**：复用现有表单样式——标题 + 内容（`fill` 撑满）；类型只读标签；卷节点额外两个数字输入（起始章/结束章）；「复制为新版」保留；「AI 拆分」按钮（仅 broad/period 节点显示）
- **删除**：后端拒绝有子级节点，错误消息原样展示
- `src/api/long.ts` outline payload 类型补 `type` / `parent_id` / `chapter_start` / `chapter_end`

## 错误处理

- 层级约束违反：API 抛 `ValidationError`，change_apply 走结构化错误返回
- AI 拆分/生成 JSON 解析失败：沿用 `_generate_json` 现有失败处理（返回 error 字段，不产生变更）
- temp_id 父级失败：子级 `PARENT_FAILED` 结构化错误

## 验证

项目无测试 runner，采用：

1. `cd backend && python -m compileall app`
2. `cd frontend && npx tsc -b && npm run build`
3. 启动后端，curl 走一遍：建树（三级）→ 层级约束违反场景 → AI 拆分确认流 → temp_id 链确认流
4. 重启桌面客户端人工确认大纲树 UI

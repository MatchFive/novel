# AI 创作生成流水线增强 — 设计文档

日期：2026-07-19
状态：已获用户方向确认（可配尺度等级+自动改写 / 可配置章节字数 / 直接写入+可撤销）
关联：与《2026-07-19-long-workspace-assistant-ux-design.md》相互独立，可并行实施

## 背景与现状

harness 已具备多阶段生成管线：`broad_outline → plot_nodes → assignment → chapter_outline → chapter_text`，由 supervisor 按聊天指令派发，所有产出暂存为 ChangeRecord 等用户确认。章节编辑器已有「生成细纲/生成正文」按钮（通过聊天带 entity 上下文触发）。

现有缺口：

1. **尺度检查缺失**：`chapter_review_prompt` 只查物理逻辑/人物一致性/伏笔，不查内容尺度（色情、血腥、暴力等）。
2. **章节长度失控**：assignment 不按字数预算拆分剧情；chapter_text 一次性生成整章，长剧情会截断或一章过长。
3. **落地体验差**：整章正文作为待确认 ChangeRecord 塞在聊天气泡里，长文本无法有效预览，确认流程形同虚设。
4. **上下文连贯性有限**：正文生成只参考前一章尾部 800 字，缺乏跨章节的摘要链。

## 目标流程

用户在 AI 助手中聊天 → 依次/一次性生成可用的世界观、角色、剧情节点、总纲、章节细纲（实体类变更保持现有暂存+确认流程）→ 在章节列表/编辑器点击「生成正文」→ 后端完成**分段生成 → 一致性审校 → 尺度检查/自动改写** → **直接写入章节** → 聊天中返回摘要 + 「撤销」入口。

## 一、新增用户设置

`UserSetting` 增加两列（沿用 `scripts/migrate.py` 的 `ALTER TABLE ADD COLUMN` 列表模式追加）：

| 列 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `content_rating` | String(16) | `"standard"` | 尺度等级：`loose` / `standard` / `strict` |
| `chapter_target_words` | Integer | `2500` | 每章目标字数，用于拆分预算与分段生成终止条件 |

- `to_dict`、`schemas/setting.py`、设置 API、`frontend/src/api/settings.ts`、`SettingsPage.tsx` 同步增加：尺度等级用三选一控件，目标字数用数字输入（钳制 1000–8000）。
- LLM worker 读取方式：与现有 `recursive_limit` 相同，从 `UserSetting` 首行读取。

## 二、尺度检查（可配等级 + 自动改写）

新增 `CHAPTER_RATING_PROMPT`（`prompts/chapter_generation.py`）：

- 输入：章节正文、等级（loose/standard/strict 的判定标准写进 prompt）。
- 输出：`{"ok": true}` 或 `{"ok": false, "issues": [{"excerpt": "问题段落摘录", "problem": "问题描述", "suggestion": "改写建议"}]}`。
- 判定标准：
  - `strict`：不允许明确性描写、露骨血腥；亲密/暴力仅可暗示性带过。
  - `standard`：允许紧张暴力与含蓄亲密描写，不允许露骨性描写与细致酷刑/血腥。
  - `loose`：仅拦截违法与极端内容（未成年人相关、教唆犯罪等），其余放行。

`ChapterTextWorker.run` 流程改为：

```
分段生成正文（见第三节）
  → 一致性审校（现有 chapter_review，发现问题带反馈重生成 1 次）
  → 尺度检查（按 content_rating）
      有问题 → 携带 issues 反馈改写（最多 1 次），改写后再查一次
      仍有问题 → 使用最后一次文本，结果中记录 rating_flags 清单
  → 返回 {changes, notes}
```

- 自动改写的反馈格式与现有一致性审校重试相同（追加【审校反馈】到 user prompt）。
- 尺度检查/改写任何一步异常 → 降级：跳过检查直接采用原文，记 warning 日志，不阻断生成。
- worker 返回新增 `notes` 字段（如 `"已按「标准」尺度自动调整 2 处"`、`"尺度复核仍有 1 处待人工确认"`），经 responder 呈现在聊天摘要中。

## 三、章节长度控制（可配置字数 + 拆分 + 分段生成）

### 3.1 分配阶段按字数预算拆分

`ASSIGNMENT_PROMPT` 注入 `chapter_target_words`：

- 规则：每章剧情容量按目标字数估算（约每 800–1000 字容纳一个剧情节点）；若未分配节点总量超出已有章节容量，新建足量占位章节；单章节点过多时拆到后续章节。
- 用户说"规划前 N 章"时严格生成 N 个占位章节。

### 3.2 细纲阶段感知字数

`CHAPTER_OUTLINE_PROMPT` 注入目标字数，要求细纲场景规模与目标字数匹配（场景数 ≈ 目标字数 / 600，超出则提示拆分建议写入 notes——不强制）。

### 3.3 正文分段生成

`ChapterTextWorker` 正文生成从单次调用改为分段循环：

- 每段目标 ≈ 800–1200 字；总目标 = `chapter_target_words`（允许 ±20% 浮动）。
- 段 i 的输入：章节目标、细纲、分配的剧情节点、角色/世界观/伏笔、**上一段尾部 300 字**、已累计字数与剩余预算、第一章时附前一章尾部（现有逻辑保留）。
- 终止条件：达到目标字数，或 LLM 返回 `{"finished": true}` 表示细纲内容已写完。
- 段数上限：`ceil(target / 800) + 2`，防止死循环。
- 每段输出 JSON：`{"text": "...", "finished": bool}`；拼接后进入审校与尺度检查。
- 单段生成失败：重试 1 次；仍失败则保留已累计文本，notes 记录"生成中断于 X 字"，继续后续检查流程（不丢弃半成品）。

### 3.4 跨章连贯性增强

- 新增"前章摘要链"：目标章节之前的所有章节，各取 `detailed_outline` 或 `content` 前 200 字生成一行摘要，总量钳制 2000 字，注入首段生成 prompt（替代/补充现有仅前一章的摘要）。
- 前一章正文尾部 800 字逻辑保留不变。

## 四、直接写入 + 可撤销

### 4.1 自动应用正文/细纲变更

`/chat` 聚合后，对 ChangeRecord 分流：

- `entity_type="chapter"` 且 `action="update"` 且变更字段为 `content` 和/或 `detailed_outline`（即 chapter_text / chapter_outline worker 产出）→ **立即经 `change_apply.apply_change` 应用**，并在调用处（assistant.py，参照 `confirm_session` 的写法）写入 `LongChangeRecord`（含 before 快照、`source="auto"`，见 4.3），不进 staged_changes。
- 其余实体（角色/世界观/大纲/剧情/伏笔）维持现有暂存+确认流程不变。
- 应用失败（异常）→ 降级回 staged，聊天里说明原因。
- 响应 JSON 增加 `auto_applied: [{change_id, entity_id, entity_type, notes}]`，前端据此前置刷新章节数据并在聊天中显示"已写入第 X 章《标题》+ 尺度调整说明 + [撤销]"。

### 4.2 撤销

- 新端点 `POST /api/assistant/undo`：`{project_id, entity_type, entity_id}` → 查该项目该实体最近一条 `status="applied"` 且来源为自动应用的 `LongChangeRecord`，把 `before` 快照写回（经 `apply_change` update 路径），该记录标记 `status="undone"`，并新增一条 undo 审计记录。
- 只支持撤销最近一次自动应用（单级撤销）；无记录返回 `{"ok": false, "message": "没有可撤销的自动生成"}`。
- 前端两处入口：聊天中 auto_applied 消息上的「撤销」按钮；章节编辑器工具栏「撤销生成」按钮。按钮可用性由新端点 `GET /api/assistant/undoable/{chapter_id}` 判断（返回 `{"undoable": bool}`：该章节存在未撤销的 `source="auto"` 应用记录时为 true）。

### 4.3 ChangeRecord 扩展

自动应用的记录在 `LongChangeRecord` 增加可识别标记：复用现有字段，在 `after` JSON 外层不加字段；改为 `LongChangeRecord` 增加 `source` 列（`"staged"` / `"auto"`，默认 `"staged"`，migrate.py 追加）。undo 只匹配 `source="auto"`。

## 五、前端交互

1. **章节列表**（ChapterList）：每行增加「生成正文」按钮（已有细纲的章显示，无细纲的章显示「生成细纲」）；生成期间该行显示转圈禁用态。点击后复用现有机制（`sendMessage` 带 `entity_type/entity_id` 上下文），助手面板自动打开显示进度。
2. **聊天区**：auto_applied 消息渲染为带边框的摘要卡：章节名、字数、尺度调整 notes、「查看章节」链接（跳转章节 tab 并选中）、「撤销」按钮。
3. **章节编辑器**：工具栏增加「撤销生成」（按 4.2 可用性）；保存按钮逻辑不变。
4. **设置页**：新增「每章目标字数」（数字，默认 2500）与「内容尺度等级」（宽松/标准/严格，默认标准）。
5. 生成是长耗时操作（多段+两次检查）：前端 `busy` 期间聊天输入禁用（现有），章节列表行内 spinner；不做 SSE 流式（见范围外）。

## 六、错误处理与降级

| 场景 | 行为 |
|---|---|
| 尺度检查 LLM 异常 | 跳过检查采用原文，warning 日志 |
| 自动改写后仍不合规 | 采用最后文本，notes 标注待人工确认 |
| 分段中某段失败重试无果 | 保留已生成部分继续，notes 标注中断位置 |
| 自动应用 DB 失败 | 降级为 staged 待确认，聊天说明 |
| undo 无可撤销记录 | 返回 ok:false + 提示，前端禁用按钮态兜底 |

## 范围外（YAGNI）

- 不做 SSE 流式生成展示（长耗时以 spinner + busy 态表达，后续可独立迭代）。
- 不做多级撤销历史（单级，可后续扩展为版本列表）。
- 尺度检查不做本地敏感词库，纯 LLM 判定。
- 短篇（short）流程不改。
- 批量"一键生成全部章节正文"不做（逐章触发，避免失控成本）。

## 改动文件清单

**后端**
- `app/models.py`：UserSetting 两列、LongChangeRecord.source 列
- `scripts/migrate.py`：追加三条 ALTER
- `app/schemas/setting.py`、`app/api/settings.py`：设置读写
- `app/agents/harness/prompts/chapter_generation.py`：rating prompt；assignment/outline/text prompt 注入字数与摘要链
- `app/agents/harness/workers/chapter_workers.py`：分段生成循环、尺度检查+改写、notes
- `app/api/assistant.py`：auto-apply 分流、undo 端点、响应带 auto_applied
- `app/services/change_apply.py`：apply_change 接受 `source` 标记写入 LongChangeRecord（confirm_session 路径标 staged）

**前端**
- `src/types/index.ts`、`src/api/settings.ts`、`src/pages/SettingsPage.tsx`：新设置项
- `src/api/`：undo API 封装
- `src/stores/useAssistantSession.ts`：处理 auto_applied（刷新、消息卡）
- `src/components/AssistantChat.tsx` / `ChangeRecordCard.tsx`：auto_applied 摘要卡 + 撤销
- `src/components/chapter/ChapterList.tsx`、`ChapterEditor.tsx`：生成按钮状态、撤销生成
- `src/pages/LongWorkspace.tsx`：生成完成后刷新章节详情的联动

## 验证方式

- `cd backend && python -m compileall app`；`python scripts/migrate.py` 后旧库正常启动
- `cd frontend && npx tsc -b && npm run build`
- 手动冒烟：
  1. 新项目聊天"生成世界观+主角+前5章规划"→ 实体暂存确认入库 → 章节占位生成。
  2. 章节列表点「生成正文」→ 字数接近目标值 → 聊天出现 auto_applied 卡 → 章节内容已直接写入。
  3. 点「撤销」→ 章节恢复之前内容。
  4. 设置改 strict → 重新生成一章 → 尺度 notes 出现/内容调淡。
  5. 目标字数改 1500 → 重新生成 → 章节明显变短且剧情节点被拆到更多章。

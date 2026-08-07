# Novel Studio 配置增强设计

**日期：** 2026-08-07  
**状态：** 已确认，待实施  
**范围：** 文风配置、大纲变更类型增强、模型配置 UI 重设计、章节生成配置下沉到小说

## 1. 背景与目标

当前 Novel Studio 存在以下问题：

1. **文风无法配置**：项目模型没有风格字段，章节生成提示词无法消费风格信息。
2. **总纲修改容易被替换**：当前变更类型只有 `add` / `update` / `delete`，大模型修改总纲时若返回内容被省略，`update` 会把未提及字段覆盖为空。
3. **模型配置体验差**：LLM 与 Embedding 模型混在一起测试，模型名需手动输入，默认预设过多。
4. **章节生成配置全局唯一**：`chapter_target_words` 和 `content_rating` 存储在 `UserSetting`，无法针对不同小说单独配置。

本设计按「最小改动扩展现有表」的整体方案（方案 A）解决上述问题。

## 2. 整体方案

采用**最小改动扩展现有表**：

- `Project` 表新增 `writing_style` 和 `generation_config` 两个 JSON 字段。
- `change_apply.py` 新增三种变更类型。
- 前端模型配置页重设计为分类卡片 + 拉取模型 + 差异化测试。

不引入新的独立配置表，保持与现有 `UserSetting` 强类型风格一致。

## 3. 详细设计

### 3.1 文风配置

#### 数据模型

`Project` 表新增字段：

```python
writing_style: Mapped[dict] = mapped_column(JSON, default=dict)
```

结构：

```json
{
  "perspective": "第三人称限知",
  "language_style": "古风文言",
  "pace": "紧凑",
  "tone": "沉郁",
  "custom_note": "对话短促，少用心理描写，多用动作和环境暗示情绪。"
}
```

#### 后端

- 新增 `WritingStyleSchema` Pydantic 模型，用于 API 校验。
- 在 `Project` 的创建/更新 schema 中暴露 `writing_style`。
- 章节生成工作流 `chapter_generation.py` 的 `load_context` 将 `project.writing_style` 注入 prompt。
- `CHAPTER_TEXT_PROMPT` / `CHAPTER_OUTLINE_PROMPT` 增加引导语：
  > 请遵循以下文风设定：{style}。若风格为空，则不附加本段。

#### 前端

- 在 `LongWorkspace` 增加「项目设置」入口。
- 表单包含 4 个结构化字段 + 1 个自由文本框：
  - 叙事视角（下拉）
  - 语言风格（下拉）
  - 节奏（下拉）
  - 情感基调（下拉）
  - 自定义补充（文本域）
- 新建项目时字段为空，不干扰生成。

---

### 3.2 新增变更类型

#### 新增类型

在现有 `add` / `update` / `delete` 基础上，增加：

| 类型 | 语义 | 适用场景 |
|---|---|---|
| `partial_update` | 只更新 `after` 中显式字段，未出现字段保持原值 | 改标题、改某一段 |
| `append` | 在长文本字段末尾追加内容 | 扩展总纲、补充设定 |
| `patch` | 通过 `search` + `replace` 定位替换片段 | 精确修改某一句话 |

#### 变更记录格式

```json
// partial_update
{
  "action": "partial_update",
  "entity_type": "outline",
  "entity_id": "...",
  "after": {"title": "新标题"}
}

// append
{
  "action": "append",
  "entity_type": "outline",
  "entity_id": "...",
  "after": {"content": "\n\n新增段落..."}
}

// patch
{
  "action": "patch",
  "entity_type": "outline",
  "entity_id": "...",
  "after": {"search": "旧文本", "replace": "新文本"}
}
```

#### 后端处理

- `change_apply.apply_change` 增加分支逻辑：
  - `partial_update`：读取原实体，用 `after` 字段覆盖，其余保留。
  - `append`：对文本字段执行 `原值 + after[field]`。
  - `patch`：要求 `search` 唯一命中，执行替换；否则返回结构化错误。
- `_validate_outline_change` 同步支持新类型。
- `OutlineWorker` / `BroadOutlineWorker` 的 prompt 增加说明：
  - 长文本修改优先使用 `partial_update` / `append` / `patch`。
  - 只有确实需要整段重写时才用 `update`。

#### 错误处理

| 错误码 | 场景 |
|---|---|
| `PATCH_NOT_FOUND` | `search` 在原文中未找到 |
| `PATCH_AMBIGUOUS` | `search` 在原文中出现多次 |

---

### 3.3 模型配置 UI 重设计

#### 默认模型调整

- 种子数据只保留一个 DeepSeek 文本模型。
- 不再默认创建 OpenAI / SiliconFlow / Moonshot。
- 向量模型未配置时，回退到 `.env` 的 `LLM_EMBEDDING_MODEL`。

#### 后端 API 增强

- `GET /models` 拉取：当前在 `/settings/models` 已有，保持不变。
- 新增 `GET /settings/models/fetch?base_url=...&api_key=...`：调用 OpenAI 兼容 `/models` 接口，返回可用模型列表。
- `POST /settings/models/test` 增强：
  - 文本模型：发送短对话，校验返回非空。
  - 向量模型：调用 embedding 接口，校验返回维度与配置一致。
- `ModelConfig` 表新增 `temperature` 字段，仅文本模型使用。
- LLM 客户端调用时优先使用模型配置的温度，未设置时回退到 `.env` 的 `LLM_TEMPERATURE`。

#### 前端设计

- 模型按「文本模型」和「向量模型」分类展示为卡片。
- 每张卡片显示：名称、模型、温度（文本模型）、维度（向量模型）、base_url。
- 操作按钮：编辑、测试、删除、设为默认。
- 底部「+ 新增模型」按钮，点击后展开表单。
- 新增模型表单：
  - 名称
  - 类型（文本模型 / 向量模型）
  - base_url
  - api_key
  - 模型下拉（从 base_url 拉取）
  - 温度（仅文本模型）
  - 向量维度（仅向量模型）
  - 刷新模型列表 / 测试连接 / 保存 / 取消

---

### 3.4 章节生成配置下沉到小说

#### 数据模型

`Project` 表新增字段：

```python
generation_config: Mapped[dict] = mapped_column(JSON, default=dict)
```

结构：

```json
{
  "chapter_target_words": 8000,
  "content_rating": "standard"
}
```

#### 读取优先级

1. 项目 `generation_config`。
2. 若缺失，回退到全局 `UserSetting`。
3. 若全局也缺失，使用默认值（`8000` / `standard`）。

#### 后端

- `Project` schema 暴露 `generation_config`。
- 章节生成工作流 `chapter_generation.py` 的 `load_context` 改为从项目读取配置。
- `_chapter_utils.generation_settings()` 增加 `project_id` 参数，按优先级读取。

#### 前端

- 项目设置中增加「章节生成」分组：
  - 每章目标字数（1000-8000）
  - 内容尺度等级（宽松 / 标准 / 严格）
- `LongWorkspace` 章节生成入口旁增加快捷按钮，打开项目设置并定位到该分组。
- 全局设置页保留这两个字段，但文案改为「新建项目默认值」。

#### 迁移策略

- 已有项目：不强制回填，读取时回退全局设置。
- 新建项目：创建时把当前全局值复制到项目配置。

## 4. 数据流

### 4.1 文风注入流程

```
用户填写文风 → Project.writing_style → API 保存
                      ↓
            chapter_generation.load_context
                      ↓
            prompt 中附加文风引导
```

### 4.2 新增变更类型流程

```
LLM 输出 change → OutlineWorker._normalize_outline_changes
                      ↓
            change_apply.apply_change
                      ↓
            按 action 分支处理
```

### 4.3 模型拉取流程

```
用户填写 base_url + api_key → 前端调用 /settings/models/fetch
                                          ↓
                              后端调用 provider /models
                                          ↓
                              返回模型列表供下拉选择
```

### 4.4 章节生成配置读取流程

```
chapter_generation.load_context(project_id)
            ↓
    读取 Project.generation_config
            ↓
    缺失 → 读取 UserSetting 全局配置
            ↓
    注入 prompt
```

## 5. 错误处理

- 所有 LLM 调用错误保持现有 `AppError` 结构。
- `patch` 类型变更失败时返回明确错误码。
- 模型拉取失败时前端显示友好提示，允许手动填写。

## 6. 测试计划

- 后端编译检查：`cd backend && python -m compileall app`
- 前端类型检查：`cd frontend && npx tsc -b`
- 手动验证：
  1. 创建项目，设置文风，触发章节生成，观察 prompt 是否包含文风。
  2. 让 AI 修改总纲，验证 `partial_update` / `append` / `patch` 不丢失未提及内容。
  3. 在模型配置页添加 DeepSeek，测试连接成功；添加向量模型，测试维度校验。
  4. 修改项目级目标字数，验证章节生成使用新项目值；清空项目配置后回退全局值。

## 7. 依赖与风险

- 项目使用 SQLAlchemy `create_all()` 而非 Alembic。新字段会在新数据库上自动创建，已有 `data/novel.db` 需要通过一次性的 `ALTER TABLE ADD COLUMN` 脚本或启动时自动检测补列来迁移。
- `ModelConfig` 新增 `temperature` 字段，需同步 schema 和前端。
- 拉取 `/models` 接口依赖提供商支持；需优雅降级到手动输入。

## 8. 待办清单（实施阶段）

1. 数据库迁移：`Project` 新增 `writing_style`、`generation_config`；`ModelConfig` 新增 `temperature`。已有数据库通过启动时自动检测补列或一次性脚本迁移。
2. 后端 schema 与 API：文风、项目生成配置、模型拉取、测试连接增强。
3. 后端业务逻辑：`change_apply` 新类型、`chapter_generation` 配置读取优先级、LLM 温度读取。
4. 前端：项目设置页、模型配置页重设计、`LongWorkspace` 快捷入口。
5. 验证与提交。

# Agent Harness 重构设计

日期：2026-07-29
状态：设计已确认，待编写实现计划

## 1. 背景与目标

当前 `backend/app/api/assistant.py` 中的 agent harness 存在以下问题：

- Worker 全部挤在 `workers/__init__.py` 和 `chapter_workers.py`，职责不清晰。
- Worker 提示词、能力描述、可用工具全部硬编码在 Python 中，难以配置和迭代。
- Worker 注册与实例化逻辑散落在 `assistant.py`，没有统一管理层。
- Supervisor 输出的是顺序 task list，无法表达并行与依赖关系。
- 没有运行时状态机，`HarnessState` 虽已定义但未真正使用。
- 网文创作知识未系统化，无法以 skill / RAG 形式增强 prompt。
- 固定流程（如记忆更新）与开放推理任务混在一起。
- 缺少 confirm/reject 后的经验总结闭环。

本设计文档给出**总体架构**与**第一阶段落地范围**，后续阶段在单独计划中细化。

## 2. 总体架构

把 harness 重构成一个**状态机驱动的运行时**：

```
User Input
    │
    ▼
┌─────────────┐
│   Analyze   │  意图分析、历史摘要检索
└──────┬──────┘
       ▼
┌─────────────┐
│  Supervisor │  读取 WorkerManager 元数据，生成 ExecutionPlan（Task DAG）
└──────┬──────┘
       ▼
┌─────────────┐
│   Executor  │  DAG Executor：按依赖并行调度 Worker
└──────┬──────┘
       ▼
┌─────────────┐
│  Aggregator │  WorkerResult → ChangeRecord[]
└──────┬──────┘
       ▼
┌─────────────┐
│  Responder  │  生成用户回复
└──────┬──────┘
       ▼
┌─────────────┐
│    Commit   │  写入 staged_changes / auto-apply
└─────────────┘
```

核心组件：

- **WorkerManager（单例）**：扫描 JSON 配置自动发现 worker，提供元数据给 supervisor，负责实例化。
- **Worker**：每个 worker 一个独立 py 文件 + 一个 JSON 配置。继承 `WorkerBase`，可覆盖特殊行为。
- **ExecutionPlan / Task**：supervisor 输出的 DAG 任务图，带依赖与 artifact 传递。
- **DagExecutor**：拓扑调度，能并行的自动并行。
- **HarnessRuntime / HarnessState**：运行时状态机，按阶段推进。
- **Nodes**：`analyze`、`supervisor`、`executor`、`aggregator`、`responder`、`commit` 分别对应一个阶段。

变更写入继续走现有约束：Worker 只读 → `ChangeRecord` → `AssistantSession.staged_changes` → `change_apply.confirm_session/reject_session`。

## 3. 第一阶段范围

第一阶段只落地 harness 骨架，**不引入**网文知识增强、固定 workflow 剥离、经验总结闭环。

包含：

1. Worker 独立 py 文件 + JSON 配置。
2. WorkerManager 单例 + 自动发现 + 代码覆盖机制。
3. Task DAG 模型 + DAG Executor。
4. HarnessState 状态机 + HarnessRuntime。
5. Node 化重构：`supervisor.py`、`executor.py`、`aggregator.py`、`responder.py`、`commit.py`。
6. 保持 `/assistant/chat`、`/confirm`、`/reject` 接口不变。
7. 功能等价性验证。

## 4. Worker 与 WorkerManager

### 4.1 文件组织

```
backend/app/agents/harness/workers/
├── __init__.py              # 导出 registry 与 run_worker
├── worker_base.py           # WorkerBase（保持现有 tool loop）
├── character_worker.py
├── world_worker.py
├── outline_worker.py
├── plot_worker.py
├── foreshadow_worker.py
├── outline_split_worker.py
├── broad_outline_worker.py
├── plot_nodes_worker.py
├── assignment_worker.py
├── chapter_outline_worker.py
├── chapter_text_worker.py
└── configs/
    ├── character.json
    ├── world.json
    ├── outline.json
    ├── plot.json
    ├── foreshadow.json
    ├── outline_split.json
    ├── broad_outline.json
    ├── plot_nodes.json
    ├── assignment.json
    ├── chapter_outline.json
    └── chapter_text.json
```

### 4.2 Worker 类约定

- 每个 worker 是一个继承 `WorkerBase` 的类，一个文件一个类。
- 默认实现：
  ```python
  async def run(self, task: Task, context: HarnessContext) -> WorkerResult
  ```
- 从 JSON 读取 system prompt、tools、output schema、模型等级、temperature。
- 特殊行为（如 `ChapterTextWorker` 的多段生成、一致性 review）在子类覆盖 `run` 或拆私有方法；JSON 只负责可配置部分。

### 4.3 WorkerManager 职责

- **单例**：启动时扫描 `configs/*.json` 自动注册。
- **发现**：
  - 读取 JSON 中的 `worker_name`。
  - 尝试导入同名 py class（如 `character_worker.CharacterWorker`），若存在则覆盖默认生成的 worker。
  - 若不存在，则基于 JSON 配置动态生成一个默认 worker 类。
- **元数据输出**：
  ```python
  def list_workers(self) -> list[WorkerMetadata]
  ```
  返回每个 worker 的 `name`、`description`、`capabilities`、`input_schema`、`output_schema`，供 supervisor system prompt 使用（类似 MCP server 的 `tools/list`）。
- **实例化**：
  ```python
  def create_worker(self, name: str, db, llm, recursive_limit: int) -> WorkerBase
  ```

### 4.4 JSON 配置 schema

```json
{
  "worker_name": "character",
  "description": "负责角色设定、关系、记忆等变更建议",
  "system_prompt": "你是小说创作助手中的角色专家。...",
  "tools": [
    "read_characters",
    "read_character",
    "read_character_memories",
    "propose_add_character",
    "propose_update_character"
  ],
  "input_schema": {
    "type": "object",
    "properties": {
      "goal": {"type": "string"},
      "input_artifacts": {"type": "object"}
    }
  },
  "output_schema": {
    "type": "object",
    "properties": {
      "summary": {"type": "string"},
      "changes": {"type": "array", "items": {"$ref": "#/$defs/change_record"}},
      "artifacts": {"type": "object"}
    },
    "required": ["summary", "changes"]
  },
  "model_level": "default",
  "temperature": 0.7,
  "timeout": 60.0,
  "recursive_limit": 8
}
```

`WorkerBase` 负责把 `output_schema` 注入 system prompt，并要求 LLM 输出合法 JSON。

## 5. Task DAG 与调度器

### 5.1 Task 模型

```python
class Task(BaseModel):
    id: str
    worker: str
    goal: str
    input_artifacts: dict[str, str] = {}   # key -> upstream task_id
    output_artifacts: list[str] = []
    deps: list[str] = []
    meta: dict = {}                        # 模型等级/timeout覆盖等

class ExecutionPlan(BaseModel):
    intent: str
    tasks: list[Task]
    global_context: dict = {}
```

### 5.2 Supervisor

Supervisor system prompt 由 `WorkerManager.list_workers()` 动态构造，包含：

- 所有 worker 的 `description`。
- 每个 worker 能处理的实体类型。
- 输入/输出 schema。
- 依赖规划规则：
  - 独立任务给相同 `deps`，让 executor 自动并行。
  - 下游需要上游产物的，用 `input_artifacts` 显式引用。

Supervisor 必须输出 `ExecutionPlan`。若解析失败，fallback 为顺序执行（所有 `deps` 为空）。

### 5.3 DAG Executor

```python
class DagExecutor:
    async def execute(
        self,
        plan: ExecutionPlan,
        context: HarnessContext,
        runtime: HarnessRuntime,
    ) -> dict[str, WorkerResult]
```

行为：

- 维护就绪队列：所有 `deps` 已完成的 task 进入队列。
- 使用 `asyncio.gather` 并行执行就绪 task。
- Worker 读取 `input_artifacts` 从 `context.artifacts` 取数据。
- Worker 返回的 `artifacts` 写入 `context.artifacts[task.id]`。
- 失败 task 标记为 `FAILED`；其下游 task 标记为 `SKIPPED_DUE_TO_ERROR`。
- 超时与递归限制由 `WorkerBase` 负责，Executor 只捕获异常。

### 5.4 Artifact 传递

Worker 产出除了 `changes` 和 `summary`，还可以产出 `artifacts`：

```json
{
  "summary": "...",
  "changes": [...],
  "artifacts": {
    "outline_draft": {...},
    "character_relationship_graph": {...}
  }
}
```

下游 task 通过 `input_artifacts: {"outline_draft": "task_abc"}` 引用。这样依赖关系在 plan 阶段就显式化，而不是靠运行时 context 隐式推断。

## 6. HarnessState 状态机

### 6.1 状态模型

```python
class HarnessStage(str, Enum):
    INIT = "init"
    ANALYZE = "analyze"
    PLAN = "plan"
    EXECUTE = "execute"
    AGGREGATE = "aggregate"
    RESPOND = "respond"
    COMMIT = "commit"
    DONE = "done"
    ERROR = "error"

class HarnessState(BaseModel):
    project_id: str
    session_id: str
    user_input: str
    stage: HarnessStage = HarnessStage.INIT
    context: HarnessContext
    plan: ExecutionPlan | None = None
    results: dict[str, WorkerResult] = {}
    change_records: list[ChangeRecord] = []
    summary: str = ""
    error: HarnessError | None = None
```

### 6.2 Runtime

```python
class HarnessRuntime:
    def __init__(
        self,
        state: HarnessState,
        manager: WorkerManager,
        llm_factory,
        db,
    ):
        ...

    async def run(self) -> HarnessState:
        """顺序推进到 DONE 或 ERROR。"""

    async def step(self) -> HarnessState:
        """单步推进，用于未来流式 UI 或调试。"""
```

每个阶段对应 `nodes/` 下的一个 node 函数：

| 阶段 | Node 文件 | 职责 |
|---|---|---|
| INIT | runtime 内部 | 初始化 state |
| ANALYZE | `nodes/analyze.py` | 意图分析、历史摘要检索 |
| PLAN | `nodes/supervisor.py` | 生成 ExecutionPlan |
| EXECUTE | `nodes/executor.py` | DAG Executor 执行 |
| AGGREGATE | `nodes/aggregator.py` | WorkerResult → ChangeRecord |
| RESPOND | `nodes/responder.py` | 生成回复 |
| COMMIT | `nodes/commit.py` | 写入 staged_changes / auto-apply |
| ERROR | runtime 内部 | 记录错误并尝试生成说明 |

### 6.3 错误处理

- Worker 内部异常由 `WorkerBase` 捕获，返回 `WorkerResult(status="error", error=...)`。
- Executor 失败不抛异常，而是把失败 task 与下游标记为跳过。
- Runtime 进入 `ERROR` 阶段后，responder 仍尝试生成说明性回复。
- 网络/LLM 异常保持 `AppError` 子类，由 FastAPI handler 返回 `{"ok": false, ...}`。

## 7. 接口兼容

第一阶段不修改外部 API：

- `/assistant/chat` 请求/响应格式不变。
- `/assistant/confirm` 与 `/assistant/reject` 仍走 `change_apply`。
- 内部改为 `HarnessRuntime.run()` 驱动。

保留 `_detect_chapter_generation_intent` 等规则作为 safety net，直到 supervisor 稳定输出 DAG。

## 8. 迁移步骤（第一阶段）

1. 新增 `HarnessState`、`ExecutionPlan`、`Task`、`WorkerResult`、`HarnessContext` 等模型。
2. 新建 `WorkerManager` 与 `workers/configs/` 目录，把现有 worker 配置抽成 JSON。
3. 拆分 `workers/__init__.py` 到独立 py 文件。
4. 实现 `DagExecutor`。
5. 实现 `HarnessRuntime` 与 `nodes/` 下各 node。
6. 重写 `assistant.py` 主流程，接入 runtime。
7. 跑 smoke test：`/health`、`/assistant/chat` 基本对话、confirm/reject 流程。

## 9. 后续阶段详细规划

### 9.1 Phase 2：网文知识与 Skills

目标：把网文创作方法论系统化，以 skill / RAG 形式注入 worker prompt，提升 agent 创作能力。

#### 9.1.1 Skill 体系

新增目录：

```
backend/app/agents/skills/
├── __init__.py
├── skill_manager.py
├── registry/
│   ├── plot_design.md
│   ├── character_arc.md
│   ├── world_building.md
│   ├── foreshadowing.md
│   ├── pacing.md
│   ├── dialogue.md
│   └── hook_opening.md
└── rag/
    ├── chunks/
    └── index.py
```

两种 skill 形态：

| 形态 | 用途 | 存储 |
|---|---|---|
| **Inline Skill** | 短规则、模板、checklist，直接拼入 system prompt | Markdown 文件 |
| **RAG Chunk** | 长文指南、案例分析，按需检索后注入 | Markdown + 向量索引 |

#### 9.1.2 Skill Schema

```json
{
  "skill_name": "plot_design",
  "description": "网络小说情节设计方法论",
  "type": "inline",
  "triggers": ["outline", "plot", "foreshadow"],
  "content_file": "registry/plot_design.md",
  "priority": 1
}
```

```json
{
  "skill_name": "wangwenclub_case",
  "description": "网文俱乐部创作案例库",
  "type": "rag",
  "source_url": "https://www.wangwenclub.com/handbook/category/%E5%88%9B%E4%BD%9C%E6%8C%87%E5%8D%97",
  "chunks_dir": "rag/chunks/wangwenclub/",
  "index_file": "rag/index/wangwenclub.json",
  "embedding_model": "...",
  "top_k": 3
}
```

#### 9.1.3 SkillManager 职责

- 加载所有 skill 配置。
- 根据 worker 名称或任务目标返回应注入的 skill 文本列表。
- 对 RAG skill：接收用户输入或任务目标，检索 top-k chunk，拼接后返回。
- 提供 `list_skills()` 供 supervisor 选择（未来可让 supervisor 主动决定调用哪些 skill）。

#### 9.1.4 Worker JSON 集成

Worker JSON 增加 `skills` 字段：

```json
{
  "worker_name": "outline",
  "skills": ["plot_design", "pacing"],
  "rag_skills": ["wangwenclub_case"]
}
```

`WorkerBase` 在构造 system prompt 时：

1. 读取 `skills` 对应的 inline skill 文本。
2. 若配置了 `rag_skills`，用 task.goal 做 query 检索 chunk。
3. 把所有文本按优先级排序，拼到 system prompt 末尾。

#### 9.1.5 参考 wangwenclub 的处理方式

- 初期允许手动把指南内容整理成 Markdown 放入 `registry/` 和 `rag/chunks/`。
- 未来可扩展一个抓取/同步工具，但第一阶段不实现自动抓取。
- 每个 chunk 要有明确标签（如 `category=情节设计`, `topic=伏笔回收`），方便按 worker 类型过滤。

### 9.2 Phase 3：固定 Workflow 剥离

目标：把“前端按钮 + 后端固定流程”的任务从 harness 中剥离，减少 harness 负担，提升可预测性。

#### 9.2.1 哪些流程应该剥离

| 流程 | 当前位置 | 剥离后 |
|---|---|---|
| **角色记忆更新** | `api/long_memory.py` | 已是独立 workflow，保留并可能改名为 `workflow/memory_update` |
| **章节生成**（多段生成 + review + rating） | `workers/chapter_workers.py` 内 | 独立为 `workflow/chapter_generation` |
| **世界观一致性检查** | 当前无 | 未来新增 `workflow/world_consistency_check` |
| **伏笔回收检查** | 当前无 | 未来新增 `workflow/foreshadow_audit` |

#### 9.2.2 Workflow 统一抽象

新增 `backend/app/agents/workflows/`：

```python
class WorkflowDefinition(BaseModel):
    name: str
    description: str
    steps: list[WorkflowStep]
    input_schema: dict
    output_schema: dict

class WorkflowStep(BaseModel):
    name: str
    fn: str          # 对应一个 Python 函数
    depends_on: list[str] = []
    condition: str | None = None   # 可选条件执行
```

每个 workflow 是一个固定的有向图，不由 LLM 动态规划，但可以包含 LLM 调用节点。

#### 9.2.3 前端按钮

在 LongWorkspace 中增加“工具箱”或“快捷操作”区域：

- “提取本章记忆” → 调用 `/api/workflow/chapter/:id/extract-memory`
- “生成下一章” → 调用 `/api/workflow/project/:id/generate-chapter`
- “检查伏笔” → 调用 `/api/workflow/project/:id/audit-foreshadows`

这些操作有明确入口，不需要通过 `/assistant/chat` 触发。

#### 9.2.4 与 Harness 的关系

- Workflow 内部可以调用 worker/harness 的能力，但 workflow 本身是编排层。
- Harness 只处理用户自然语言输入的开放推理任务。
- 某些 workflow 的结果（如生成的章节）仍可能进入 `staged_changes` 等待确认。

### 9.3 Phase 4：经验总结闭环

目标：从用户的 confirm/reject/accept-after-adjust 行为中学习，沉淀项目级经验，优化后续 agent 行为。

#### 9.3.1 触发时机

| 用户行为 | 总结重点 |
|---|---|
| 直接 confirm | 记录成功模式：用户偏好、变更类型、上下文 |
| reject | 记录失败原因：方向错误、理解偏差、质量不达标 |
| reject 后调整再 confirm | **重点总结**：最初为什么错、调整后为什么对、规则提炼 |

#### 9.3.2 数据模型

新增 `ProjectExperience`（存储在 SQLite，可选 Neo4j）：

```python
class ProjectExperience(BaseModel):
    id: str
    project_id: str
    trigger_turn_id: str          # 关联哪一次对话
    experience_type: str          # "success" | "failure" | "adjustment"
    original_input: str
    original_plan: dict
    final_change_records: list[ChangeRecord]
    reflection_text: str          # LLM 生成的经验总结
    rules: list[str]              # 提炼出的规则/约束
    embedding: list[float] | None
    created_at: datetime
```

#### 9.3.3 Reflection Node

新增 `nodes/reflection.py`：

- 输入：user_input、原始 plan、worker_results、最终变更记录、用户反馈（confirm/reject/adjust）。
- 调用 LLM 生成 `reflection_text` 和 `rules`。
- 对 adjust 场景，重点分析 diff：用户改了什么、为什么改。

#### 9.3.4 经验注入策略

- 每次 `/assistant/chat` 的 analyze 阶段，用 user_input 向量检索 `ProjectExperience`，取 top-k 条。
- 把相关经验拼入 supervisor 和 worker 的 system prompt（如“注意：该项目历史上用户倾向于……”）。
- 避免过度拟合：设置相似度阈值和最大注入条数。

#### 9.3.5 与 Skill 的区别

| | Skill | ProjectExperience |
|---|---|---|
| 来源 | 通用网文知识 | 该项目历史交互 |
| 更新频率 | 手动维护 | 自动沉淀 |
| 作用范围 | 特定 worker | 全局 supervisor + worker |
| 形态 | Markdown / RAG chunk | 结构化记录 + 向量检索 |

## 10. 未解决问题（第一阶段不实现，但已在 Phase 2/3/4 规划）

以下功能已在第 9 节给出架构规划，第一阶段不编写实现代码：

- Skill / RAG 的向量索引构建脚本与抓取工具（Phase 2 实现）。
- Workflow 的前端 UI 按钮与具体 API 路由（Phase 3 实现）。
- `ProjectExperience` 数据表迁移与 reflection node（Phase 4 实现）。

第一阶段完成后，这些规划将直接作为后续实现计划的输入。

## 11. 验收标准

第一阶段完成后：

- [ ] 所有 worker 都有独立的 py 文件和 JSON 配置。
- [ ] WorkerManager 能自动发现 worker 并输出元数据。
- [ ] Supervisor 能生成带依赖的 ExecutionPlan，DAG Executor 能按依赖并行执行。
- [ ] HarnessRuntime 能把一次 `/assistant/chat` 完整推进到响应。
- [ ] 现有 `/assistant/chat`、`/confirm`、`/reject` 接口行为不变。
- [ ] `backend` 语法检查通过，`npx tsc -b` 通过（前端无改动也应通过）。

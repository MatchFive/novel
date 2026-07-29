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

## 9. 后续阶段（简述）

### Phase 2：网文知识与 Skills

- 新增 `backend/app/agents/skills/` 目录与 `SkillManager`。
- 参考 wangwenclub 创作指南，沉淀为可复用 skill / RAG chunk。
- Worker JSON 支持 `"skills": ["plot_design", "character_arc"]`，运行时把 skill 文本注入 system prompt。

### Phase 3：固定 Workflow 剥离

- 把 `backend/app/api/long_memory.py` 的 extract-confirm-apply 流程改造成显式 workflow endpoint。
- 把 chapter generation 中固定分段、review、rating 流程剥离成 workflow。
- Harness 只负责需要 LLM 开放推理的复杂任务。

### Phase 4：经验总结闭环

- 在 `/confirm` 和 `/reject` 后增加 reflection node。
- 区分“直接接受”与“先拒绝再调整后接受”两种模式，后者重点总结。
- 生成项目级 `ProjectExperience` 记录，影响后续 supervisor 和 worker prompt。

## 10. 未解决问题（第一阶段不处理）

- Skill / RAG 的具体 schema 和存储方式（Phase 2 再定）。
- 固定 workflow 的 UI 按钮与后端 API 细节（Phase 3 再定）。
- 经验总结的存储格式、检索方式、注入策略（Phase 4 再定）。

## 11. 验收标准

第一阶段完成后：

- [ ] 所有 worker 都有独立的 py 文件和 JSON 配置。
- [ ] WorkerManager 能自动发现 worker 并输出元数据。
- [ ] Supervisor 能生成带依赖的 ExecutionPlan，DAG Executor 能按依赖并行执行。
- [ ] HarnessRuntime 能把一次 `/assistant/chat` 完整推进到响应。
- [ ] 现有 `/assistant/chat`、`/confirm`、`/reject` 接口行为不变。
- [ ] `backend` 语法检查通过，`npx tsc -b` 通过（前端无改动也应通过）。

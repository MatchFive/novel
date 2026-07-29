# Agent Harness Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the agent harness into a state-machine-driven runtime with JSON-configured workers, a `WorkerManager` singleton, and a DAG task executor, while preserving existing `/assistant/chat`, `/confirm`, and `/reject` behavior.

**Architecture:** Introduce `HarnessState`/`HarnessRuntime` to drive `analyze → plan → execute → aggregate → respond → commit` stages. Workers become one-class-per-file with JSON configs discovered by `WorkerManager`. The supervisor outputs an `ExecutionPlan` of `Task` nodes with dependencies and artifacts; `DagExecutor` runs them in topological order with parallelism.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2.0 async, Pydantic v2, asyncio.

## Global Constraints

- All persistent mutations continue to go through `app/services/change_apply.py`.
- Workers remain read-only; writes are expressed as `ChangeRecord` drafts staged on `AssistantSession`.
- External API compatibility: `/assistant/chat`, `/assistant/confirm`, `/assistant/reject` request/response shapes must not change.
- Use Pydantic v2 `BaseModel` for all new data structures.
- Follow absolute imports from the `backend/app` package root; run backend commands with `cwd=backend`.
- Use `python -m unittest` for tests (pytest is not configured in this repo).
- Commit after each independently testable task.

## File Structure

### New files

| File | Responsibility |
|---|---|
| `app/agents/harness/models.py` | Core data models: `Task`, `ExecutionPlan`, `WorkerResult`, `HarnessContext`, `HarnessError`, `WorkerMetadata` |
| `app/agents/harness/worker_manager.py` | Singleton manager: scan JSON configs, discover/override worker classes, list metadata, instantiate workers |
| `app/agents/harness/dag_executor.py` | `DagExecutor`: topological task scheduling with parallel execution and artifact propagation |
| `app/agents/harness/runtime.py` | `HarnessRuntime` + `HarnessStage` enum: state-machine driver with `run()` and `step()` |
| `app/agents/harness/nodes/analyze.py` | Analyze node: intent analysis and historical summary retrieval |
| `app/agents/harness/nodes/commit.py` | Commit node: auto-apply chapter changes and stage remaining records |
| `app/agents/harness/workers/configs/*.json` | Per-worker JSON configuration: prompt, tools, schemas, model level |
| `app/agents/harness/workers/character_worker.py` | `CharacterWorker` class |
| `app/agents/harness/workers/world_worker.py` | `WorldWorker` class |
| `app/agents/harness/workers/outline_worker.py` | `OutlineWorker` class |
| `app/agents/harness/workers/plot_worker.py` | `PlotWorker` class |
| `app/agents/harness/workers/foreshadow_worker.py` | `ForeshadowWorker` class |
| `app/agents/harness/workers/outline_split_worker.py` | `OutlineSplitWorker` class |
| `app/agents/harness/workers/broad_outline_worker.py` | `BroadOutlineWorker` class |
| `app/agents/harness/workers/plot_nodes_worker.py` | `PlotNodesWorker` class |
| `app/agents/harness/workers/assignment_worker.py` | `AssignmentWorker` class |
| `app/agents/harness/workers/chapter_outline_worker.py` | `ChapterOutlineWorker` class |
| `app/agents/harness/workers/chapter_text_worker.py` | `ChapterTextWorker` class |
| `tests/agents/harness/test_models.py` | Unit tests for `ExecutionPlan`, `Task`, `DagExecutor` models |
| `tests/agents/harness/test_dag_executor.py` | Unit tests for `DagExecutor` |
| `tests/agents/harness/test_worker_manager.py` | Unit tests for `WorkerManager` discovery and metadata |

### Modified files

| File | Change |
|---|---|
| `app/agents/harness/state.py` | Keep `ChangeRecord`/`make_change`; update `HarnessState` to new stage enum and fields |
| `app/agents/harness/worker_base.py` | Change `run()` signature to accept `Task` + `HarnessContext`; load config from JSON; keep tool loop |
| `app/agents/harness/workers/__init__.py` | Replace class definitions with registry imports + `run_worker` helper |
| `app/agents/harness/workers/chapter_workers.py` | Deprecated; contents moved to separate files and then deleted |
| `app/agents/harness/nodes/supervisor.py` | Refactor to produce `ExecutionPlan` using `WorkerManager` metadata |
| `app/agents/harness/nodes/executor.py` | New wrapper node invoking `DagExecutor` |
| `app/agents/harness/nodes/aggregator.py` | Update to accept `dict[str, WorkerResult]` and preserve `worker` name |
| `app/agents/harness/nodes/responder.py` | Update signature to accept `HarnessState` |
| `app/api/assistant.py` | Replace imperative flow with `HarnessRuntime.run()`; keep endpoints and session logic |

---

## Task 1: Expand harness state models

**Files:**
- Create: `app/agents/harness/models.py`
- Modify: `app/agents/harness/state.py`

**Interfaces:**
- Consumes: existing `ChangeRecord` and `make_change` from `state.py`
- Produces: `Task`, `ExecutionPlan`, `WorkerResult`, `HarnessContext`, `HarnessError`, `HarnessStage`, `HarnessState` (updated)

- [ ] **Step 1: Create `app/agents/harness/models.py`**

```python
"""Harness runtime data models."""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class Task(BaseModel):
    id: str
    worker: str
    goal: str
    input_artifacts: dict[str, str] = {}
    output_artifacts: list[str] = []
    deps: list[str] = []
    meta: dict[str, Any] = {}


class ExecutionPlan(BaseModel):
    intent: str
    tasks: list[Task] = []
    global_context: dict[str, Any] = {}


class WorkerResult(BaseModel):
    worker: str = ""
    task_id: str = ""
    status: str = "completed"  # completed | error
    summary: str = ""
    changes: list[dict] = []
    artifacts: dict[str, Any] = {}
    notes: list[str] = []
    error: str | None = None
    stage: str = ""

    @classmethod
    def from_raw(cls, worker: str, task_id: str, raw: dict) -> "WorkerResult":
        changes = raw.get("changes") or []
        if isinstance(changes, str):
            import json
            try:
                changes = json.loads(changes)
            except Exception:
                changes = []
        return cls(
            worker=worker,
            task_id=task_id,
            status="error" if raw.get("error") else "completed",
            summary=raw.get("summary", ""),
            changes=changes,
            artifacts=raw.get("artifacts", {}),
            notes=raw.get("notes", []),
            error=raw.get("error"),
            stage=raw.get("stage", worker),
        )


class HarnessContext(BaseModel):
    project_id: str | None = None
    user_input: str = ""
    project_summary: str = ""
    entities: dict[str, list[dict]] = Field(default_factory=dict)
    session_context: dict[str, Any] = Field(default_factory=dict)
    artifacts: dict[str, dict[str, Any]] = Field(default_factory=dict)

    def entity_list(self, entity_type: str) -> list[dict]:
        return self.entities.get(entity_type, [])


class HarnessError(BaseModel):
    stage: str
    message: str
    details: dict[str, Any] = {}


class WorkerMetadata(BaseModel):
    worker_name: str
    description: str
    system_prompt: str
    tools: list[str]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    model_level: str = "default"
    temperature: float = 0.7
    timeout: float = 60.0
    recursive_limit: int | None = None


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
```

- [ ] **Step 2: Update `app/agents/harness/state.py`**

Replace the `HarnessState` class with:

```python
from app.agents.harness.models import (
    ExecutionPlan,
    HarnessContext,
    HarnessError,
    HarnessStage,
    WorkerResult,
)


class HarnessState(BaseModel):
    project_id: str | None = None
    session_id: str = ""
    user_input: str = ""
    stage: HarnessStage = HarnessStage.INIT
    context: HarnessContext = Field(default_factory=HarnessContext)
    plan: ExecutionPlan | None = None
    results: dict[str, WorkerResult] = Field(default_factory=dict)
    change_records: list[ChangeRecord] = Field(default_factory=list)
    staged_records: list[ChangeRecord] = Field(default_factory=list)
    summary: str = ""
    error: HarnessError | None = None
    auto_applied: list[dict] = Field(default_factory=list)

    def add_change(self, cr: ChangeRecord) -> None:
        self.change_records.append(cr)
```

Keep `ChangeRecord` and `make_change` unchanged.

- [ ] **Step 3: Run backend compile check**

```bash
cd backend && python -m compileall app/agents/harness/models.py app/agents/harness/state.py
```

Expected: no syntax errors.

- [ ] **Step 4: Commit**

```bash
git add app/agents/harness/models.py app/agents/harness/state.py
git commit -m "feat(harness): add Task, ExecutionPlan, WorkerResult, HarnessContext models"
```

---

## Task 2: Create worker JSON configs (domain workers)

**Files:**
- Create: `app/agents/harness/workers/configs/character.json`
- Create: `app/agents/harness/workers/configs/world.json`
- Create: `app/agents/harness/workers/configs/outline.json`
- Create: `app/agents/harness/workers/configs/plot.json`
- Create: `app/agents/harness/workers/configs/foreshadow.json`
- Create: `app/agents/harness/workers/configs/outline_split.json`

**Interfaces:**
- Consumes: existing worker prompts from `app/agents/harness/workers/__init__.py`
- Produces: JSON config files loaded by `WorkerManager` in Task 7

- [ ] **Step 1: Create the configs directory**

```bash
mkdir -p backend/app/agents/harness/workers/configs
```

- [ ] **Step 2: Create `app/agents/harness/workers/configs/character.json`**

Copy the system prompt text exactly from `CharacterWorker.run()` in `app/agents/harness/workers/__init__.py` (lines 58-70) into the `system_prompt` field.

```json
{
  "worker_name": "character",
  "description": "角色设计师：负责角色设定、关系、能力、地位等变更建议",
  "system_prompt": "PASTE_EXISTING_CHARACTER_PROMPT_HERE",
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
      "changes": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "action": {"type": "string", "enum": ["add", "update"]},
            "entity_id": {"type": ["string", "null"]},
            "entity_type": {"type": "string"},
            "fields": {"type": "object"},
            "temp_id": {"type": "string"}
          },
          "required": ["action", "fields"]
        }
      },
      "artifacts": {"type": "object"}
    },
    "required": ["changes"]
  },
  "model_level": "default",
  "temperature": 0.7,
  "timeout": 60.0,
  "recursive_limit": 8
}
```

- [ ] **Step 3: Create the remaining 5 domain worker configs**

Repeat the pattern for `world`, `outline`, `plot`, `foreshadow`, `outline_split`. For each:

1. Use `worker_name` matching the class name snake_case.
2. Copy the existing `system_prompt` from the corresponding class in `app/agents/harness/workers/__init__.py`.
3. List only the tools that worker actually calls.
4. Keep the same output schema shape.

Example for `world.json`:

```json
{
  "worker_name": "world",
  "description": "世界观设定师：负责世界观分类与内容变更",
  "system_prompt": "PASTE_EXISTING_WORLD_PROMPT_HERE",
  "tools": ["read_world", "propose_update_world", "propose_add_world"],
  ...
}
```

- [ ] **Step 4: Validate JSON files load**

```python
# backend/tests/agents/harness/test_worker_configs.py
import json
import unittest
from pathlib import Path

CONFIG_DIR = Path(__file__).parent.parent.parent.parent / "app" / "agents" / "harness" / "workers" / "configs"

class TestWorkerConfigs(unittest.TestCase):
    def test_all_configs_are_valid_json(self):
        for path in CONFIG_DIR.glob("*.json"):
            with self.subTest(config=path.name):
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertIn("worker_name", data)
                self.assertIn("system_prompt", data)
                self.assertIsInstance(data["tools"], list)

if __name__ == "__main__":
    unittest.main()
```

Run:

```bash
cd backend && python -m unittest tests.agents.harness.test_worker_configs -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/agents/harness/workers/configs/character.json app/agents/harness/workers/configs/world.json app/agents/harness/workers/configs/outline.json app/agents/harness/workers/configs/plot.json app/agents/harness/workers/configs/foreshadow.json app/agents/harness/workers/configs/outline_split.json tests/agents/harness/test_worker_configs.py
git commit -m "feat(harness): add JSON configs for domain workers"
```

---

## Task 3: Create worker JSON configs (chapter workers)

**Files:**
- Create: `app/agents/harness/workers/configs/broad_outline.json`
- Create: `app/agents/harness/workers/configs/plot_nodes.json`
- Create: `app/agents/harness/workers/configs/assignment.json`
- Create: `app/agents/harness/workers/configs/chapter_outline.json`
- Create: `app/agents/harness/workers/configs/chapter_text.json`

**Interfaces:**
- Consumes: existing worker prompts from `app/agents/harness/workers/chapter_workers.py` and `app/agents/harness/prompts/chapter_generation.py`
- Produces: JSON config files for chapter workers

- [ ] **Step 1: Create `broad_outline.json`**

```json
{
  "worker_name": "broad_outline",
  "description": "项目级总纲生成：根据项目摘要与现有大纲生成总纲",
  "system_prompt": "PASTE_EXISTING_BROAD_OUTLINE_PROMPT_HERE",
  "tools": ["read_outlines"],
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
      "changes": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "action": {"type": "string"},
            "entity_id": {"type": ["string", "null"]},
            "entity_type": {"type": "string"},
            "fields": {"type": "object"},
            "temp_id": {"type": "string"}
          },
          "required": ["action", "fields"]
        }
      },
      "artifacts": {"type": "object"}
    },
    "required": ["changes"]
  },
  "model_level": "high",
  "temperature": 0.7,
  "timeout": 60.0,
  "recursive_limit": 8
}
```

The existing prompt is in `app/agents/harness/prompts/chapter_generation.py` via `broad_outline_prompt(prompt_context)`. For Phase 1, keep it as a template function in code (covered in Task 6); the JSON `system_prompt` can be a short wrapper like `"你是项目总纲规划师。请根据提供的项目摘要、现有大纲、角色和剧情节点生成总纲。"` and the worker class will prepend the rendered prompt from the template function.

- [ ] **Step 2: Create `plot_nodes.json`, `assignment.json`, `chapter_outline.json`, `chapter_text.json`**

Use the same pattern. For `chapter_text`, set `model_level` to `high`, `timeout` to `120.0`, and `recursive_limit` to `16` because it performs multiple LLM calls internally.

- [ ] **Step 3: Update the config validation test**

Add these 5 configs to the glob check in `tests/agents/harness/test_worker_configs.py` (the existing glob already covers them).

Run:

```bash
cd backend && python -m unittest tests.agents.harness.test_worker_configs -v
```

Expected: 11 configs loaded.

- [ ] **Step 4: Commit**

```bash
git add app/agents/harness/workers/configs/broad_outline.json app/agents/harness/workers/configs/plot_nodes.json app/agents/harness/workers/configs/assignment.json app/agents/harness/workers/configs/chapter_outline.json app/agents/harness/workers/configs/chapter_text.json tests/agents/harness/test_worker_configs.py
git commit -m "feat(harness): add JSON configs for chapter workers"
```

---

## Task 4: Refactor WorkerBase for JSON-driven execution

**Files:**
- Modify: `app/agents/harness/worker_base.py`

**Interfaces:**
- Consumes: `WorkerMetadata` from `app/agents.harness.models`
- Produces: `WorkerBase` with new `run(task, context)` signature and `load_config()` helper

- [ ] **Step 1: Update `WorkerBase.__init__` to accept config**

```python
from app.agents.harness.models import WorkerMetadata

class WorkerBase:
    worker_name: str = "base"
    metadata: WorkerMetadata | None = None

    def __init__(
        self,
        db: AsyncSession,
        llm,
        recursive_limit: int,
        metadata: WorkerMetadata | None = None,
        timeout: float = 60.0,
    ):
        self.db = db
        self.llm = llm
        self.metadata = metadata
        self.timeout = timeout
        effective_limit = metadata.recursive_limit if metadata and metadata.recursive_limit else recursive_limit
        self.recursive_limit = min(max(effective_limit, 1), app_settings.recursive_limit_hard_cap)
```

- [ ] **Step 2: Add default `run` implementation that reads from config**

```python
async def run(self, task, context, history_context=None) -> dict:
    """Default JSON-driven run.

    Args:
        task: Task object with worker, goal, input_artifacts, meta.
        context: HarnessContext with entities, project_summary, etc.
        history_context: Optional chat history for LLM.
    Returns:
        dict with {"summary", "changes", "artifacts", "notes", "stage", "error"}.
    """
    if self.metadata is None:
        raise RuntimeError(f"Worker {self.worker_name} has no metadata")

    system_prompt = self.metadata.system_prompt
    # Inject output schema into prompt if available
    if self.metadata.output_schema:
        schema_text = json.dumps(self.metadata.output_schema, ensure_ascii=False, indent=2)
        system_prompt += f"\n\n你必须按以下 JSON schema 输出：\n{schema_text}\n只输出 JSON，不要 markdown 代码块，不要解释。"

    user_prompt = self._build_user_prompt(task, context)
    raw = await self._tool_loop(
        system_prompt,
        user_prompt,
        extra_tools=None,
        history_context=history_context,
    )
    return self._normalize_result(raw)


def _build_user_prompt(self, task, context) -> str:
    parts = [f"【用户目标】\n{task.goal}"]
    if task.input_artifacts:
        parts.append(f"【输入产物】\n{json.dumps(task.input_artifacts, ensure_ascii=False)}")
    parts.append(f"【项目摘要】\n{context.project_summary or '未提供'}")
    entities_text = self._render_entities(context)
    if entities_text:
        parts.append(f"【项目实体】\n{entities_text}")
    return "\n\n".join(parts)


def _render_entities(self, context) -> str:
    lines = []
    for entity_type, entities in context.entities.items():
        if not entities:
            continue
        lines.append(f"[{entity_type}]")
        for e in entities:
            lines.append(json.dumps(e, ensure_ascii=False))
    return "\n".join(lines)


def _normalize_result(self, raw: dict) -> dict:
    """Ensure result has the canonical keys."""
    if isinstance(raw, str):
        return {"summary": raw, "changes": [], "artifacts": {}, "notes": [], "stage": self.worker_name}
    return {
        "summary": raw.get("summary", ""),
        "changes": raw.get("changes") or [],
        "artifacts": raw.get("artifacts") or {},
        "notes": raw.get("notes") or [],
        "stage": raw.get("stage") or self.worker_name,
        "error": raw.get("error"),
    }
```

- [ ] **Step 3: Update `_tool_loop` to use metadata tools**

Inside `_tool_loop`, keep `schemas = tool_schemas() + (extra_tools or [])` but optionally filter to metadata.tools if set:

```python
schemas = tool_schemas() + (extra_tools or [])
if self.metadata and self.metadata.tools:
    allowed = set(self.metadata.tools)
    schemas = [s for s in schemas if s.get("name") in allowed] + (extra_tools or [])
```

- [ ] **Step 4: Update `run_worker` helper**

```python
async def run_worker(
    worker_cls: type["WorkerBase"],
    db: AsyncSession,
    llm,
    recursive_limit: int,
    task,
    context,
    metadata: WorkerMetadata | None = None,
    history_context: list[dict] | None = None,
) -> dict:
    worker = worker_cls(db, llm, recursive_limit, metadata=metadata)
    return await worker.run(task, context, history_context=history_context)
```

- [ ] **Step 5: Run compile check**

```bash
cd backend && python -m compileall app/agents/harness/worker_base.py
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add app/agents/harness/worker_base.py
git commit -m "feat(harness): make WorkerBase JSON-driven with Task/HarnessContext signature"
```

---

## Task 5: Split domain workers into separate files

**Files:**
- Create: `app/agents/harness/workers/character_worker.py`
- Create: `app/agents/harness/workers/world_worker.py`
- Create: `app/agents/harness/workers/outline_worker.py`
- Create: `app/agents/harness/workers/plot_worker.py`
- Create: `app/agents/harness/workers/foreshadow_worker.py`
- Create: `app/agents/harness/workers/outline_split_worker.py`
- Modify: `app/agents/harness/workers/__init__.py`

**Interfaces:**
- Consumes: updated `WorkerBase` from Task 4
- Produces: standalone worker classes still exposing the same behavior

- [ ] **Step 1: Create `character_worker.py`**

Move the `CharacterWorker` class verbatim from `app/agents/harness/workers/__init__.py` into the new file, preserving imports and `_normalize_character_changes`. Keep the class inheriting `WorkerBase`.

```python
from app.agents.harness.worker_base import WorkerBase
from app.agents.harness.context_builder import ContextBuilder, build_entities_from_context
from app.core.errors import AppError
from app.core.llm_client import LLMClient

import logging
import uuid

logger = logging.getLogger(__name__)


class CharacterWorker(WorkerBase):
    worker_name = "character"

    async def run(self, task, context, history_context=None) -> dict:
        goal = task.goal
        project_id = context.project_id

        related = ""
        if project_id:
            builder = ContextBuilder(self.db, self.llm, entities=build_entities_from_context(context.entities))
            related = await builder.build(
                goal,
                focus_entity_type="character",
                focus_entity_id=context.session_context.get("entity_id"),
            )

        chars = context.entity_list("characters")
        chars_desc = "\n".join(
            f"- {c.get('name')} (id={c.get('id')})"
            for c in chars
        ) or "暂无现有角色。"

        system = self.metadata.system_prompt if self.metadata else ""
        user_prompt = f"【现有角色】\n{chars_desc}\n\n【相关上下文】\n{related or '（无）'}\n\n【用户目标】\n{goal}"
        raw = await self._tool_loop(system, user_prompt, history_context=history_context)

        if isinstance(raw, dict) and "raw" in raw and isinstance(raw["raw"], str):
            conversion_msgs = [
                {
                    "role": "system",
                    "content": (
                        "你是 JSON 转换器。把下面的角色设计输出转换为严格合法的 JSON，"
                        '格式：{"changes":[{"action":"add|update","entity_id":null或id,"fields":'
                        '{"name":"","traits":"","ability":"","status":"","relations":[],"importance":0}}]}\n\n'
                        "只输出 JSON，不要 markdown 代码块，不要解释。只保留角色字段，忽略大纲更新。"
                    ),
                },
                {"role": "user", "content": raw["raw"]},
            ]
            try:
                converted = await self.llm.parse_llm_json(conversion_msgs)
                if isinstance(converted, dict):
                    raw = converted
                elif isinstance(converted, list):
                    raw = {"changes": converted}
            except AppError:
                raise
            except Exception:
                logger.exception("CharacterWorker raw-to-json conversion failed")

        normalized = self._normalize_character_changes(raw, chars)
        return self._normalize_result(normalized)

    @staticmethod
    def _normalize_character_changes(raw: dict, chars: list[dict]) -> dict:
        # PASTE the existing _normalize_character_changes method body here unchanged
        ...
```

Note: replace `context.get(...)` with `context.entities.get(...)` or `context.session_context.get(...)` as appropriate.

- [ ] **Step 2: Create the other 5 domain worker files**

Repeat for `WorldWorker`, `OutlineWorker`, `PlotWorker`, `ForeshadowWorker`, `OutlineSplitWorker`. Each file contains only that class and its helper methods.

For `OutlineWorker`, replace repo call `repo.list_outlines(self.db, project_id)` with `context.entity_list("outlines")` for the initial list (it already loads via context). Keep normalization methods unchanged.

For `OutlineSplitWorker`, keep the `repo.list_outlines(self.db, project_id)` call because it needs fresh data.

- [ ] **Step 3: Rewrite `app/agents/harness/workers/__init__.py`**

```python
from __future__ import annotations

from app.agents.harness.worker_base import WorkerBase, run_worker
from app.agents.harness.workers.assignment_worker import AssignmentWorker
from app.agents.harness.workers.broad_outline_worker import BroadOutlineWorker
from app.agents.harness.workers.character_worker import CharacterWorker
from app.agents.harness.workers.chapter_outline_worker import ChapterOutlineWorker
from app.agents.harness.workers.chapter_text_worker import ChapterTextWorker
from app.agents.harness.workers.foreshadow_worker import ForeshadowWorker
from app.agents.harness.workers.outline_split_worker import OutlineSplitWorker
from app.agents.harness.workers.outline_worker import OutlineWorker
from app.agents.harness.workers.plot_nodes_worker import PlotNodesWorker
from app.agents.harness.workers.plot_worker import PlotWorker
from app.agents.harness.workers.world_worker import WorldWorker

__all__ = [
    "WorkerBase",
    "run_worker",
    "CharacterWorker",
    "WorldWorker",
    "OutlineWorker",
    "PlotWorker",
    "ForeshadowWorker",
    "OutlineSplitWorker",
    "BroadOutlineWorker",
    "PlotNodesWorker",
    "AssignmentWorker",
    "ChapterOutlineWorker",
    "ChapterTextWorker",
]
```

- [ ] **Step 4: Run compile check**

```bash
cd backend && python -m compileall app/agents/harness/workers
```

Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add app/agents/harness/workers/__init__.py app/agents/harness/workers/character_worker.py app/agents/harness/workers/world_worker.py app/agents/harness/workers/outline_worker.py app/agents/harness/workers/plot_worker.py app/agents/harness/workers/foreshadow_worker.py app/agents/harness/workers/outline_split_worker.py
git commit -m "refactor(harness): split domain workers into separate files"
```

---

## Task 6: Split chapter workers into separate files

**Files:**
- Create: `app/agents/harness/workers/broad_outline_worker.py`
- Create: `app/agents/harness/workers/plot_nodes_worker.py`
- Create: `app/agents/harness/workers/assignment_worker.py`
- Create: `app/agents/harness/workers/chapter_outline_worker.py`
- Create: `app/agents/harness/workers/chapter_text_worker.py`
- Delete: `app/agents/harness/workers/chapter_workers.py`

**Interfaces:**
- Consumes: updated `WorkerBase`, JSON configs, chapter generation prompts
- Produces: standalone chapter worker classes

- [ ] **Step 1: Create `broad_outline_worker.py`**

Move `BroadOutlineWorker` from `chapter_workers.py`.

```python
from app import repositories as repo
from app.agents.harness.context_builder import ContextBuilder
from app.agents.harness.worker_base import WorkerBase
from app.agents.harness.prompts.chapter_generation import broad_outline_prompt

import json
import logging

logger = logging.getLogger(__name__)


def _project_summary(context) -> str:
    project = context.entities.get("project") or {}
    return (
        context.project_summary
        or f"{project.get('title', '未命名项目')}\n{project.get('description', '')}".strip()
        or "未提供项目摘要"
    )


def _user_prompt(goal: str, related: str = "") -> str:
    parts = []
    if related:
        parts.append(f"【相关上下文】\n{related}")
    parts.append(f"【用户目标】\n{goal}")
    return "\n\n".join(parts)


class BroadOutlineWorker(WorkerBase):
    worker_name = "broad_outline"

    async def run(self, task, context, history_context=None) -> dict:
        project_id = context.project_id
        if not project_id:
            return {"changes": [], "stage": "broad_outline", "error": "缺少 project_id"}

        existing_outlines = await repo.list_outlines(self.db, project_id)

        related = ""
        builder = ContextBuilder(self.db, self.llm, entities=context.entities)
        try:
            related = await builder.build(task.goal, focus_entity_type="outline")
        except Exception:
            logger.exception("ContextBuilder failed for broad_outline")

        prompt_context = {
            "project_summary": _project_summary(context),
            "existing_outlines": existing_outlines,
            "characters": context.entity_list("characters"),
            "world": context.entity_list("world"),
            "plot_nodes": context.entity_list("plot"),
        }
        system = broad_outline_prompt(prompt_context)
        user_prompt = _user_prompt(task.goal, related)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ]
        if history_context:
            messages = history_context + messages
        try:
            result = await self.llm.parse_llm_json(messages)
        except Exception:
            logger.exception("BroadOutlineWorker JSON generation failed")
            return {"changes": [], "stage": "broad_outline", "error": "无法解析 worker 输出"}
        return self._normalize_result({"changes": result.get("changes", []) if isinstance(result, dict) else result, "stage": "broad_outline"})
```

- [ ] **Step 2: Create the other 4 chapter worker files**

Move `PlotNodesWorker`, `AssignmentWorker`, `ChapterOutlineWorker`, `ChapterTextWorker` similarly. Keep their existing helper functions (`_parse_chapter_numbers`, `_find_target_chapter`, `_volume_outline_text`, `_character_memories_for_chapter`, etc.) either as module-level functions in the same file or as private methods on the class.

For `ChapterTextWorker`, keep the multi-segment generation, review, and rating logic intact. The `run()` signature changes to `(self, task, context, history_context=None)`; read `task.goal` and `context` fields.

- [ ] **Step 3: Delete `chapter_workers.py`**

Once all classes are moved and imports updated, delete the file.

- [ ] **Step 4: Run compile check**

```bash
cd backend && python -m compileall app/agents/harness/workers
```

Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add app/agents/harness/workers/broad_outline_worker.py app/agents/harness/workers/plot_nodes_worker.py app/agents/harness/workers/assignment_worker.py app/agents/harness/workers/chapter_outline_worker.py app/agents/harness/workers/chapter_text_worker.py app/agents/harness/workers/__init__.py
git rm app/agents/harness/workers/chapter_workers.py
git commit -m "refactor(harness): split chapter workers into separate files and remove chapter_workers.py"
```

---

## Task 7: Implement WorkerManager singleton

**Files:**
- Create: `app/agents/harness/worker_manager.py`
- Create: `tests/agents/harness/test_worker_manager.py`

**Interfaces:**
- Consumes: worker JSON configs and worker classes
- Produces: `WorkerManager` singleton with `list_workers()`, `get_worker_class()`, `create_worker()`

- [ ] **Step 1: Implement `app/agents/harness/worker_manager.py`**

```python
"""WorkerManager: discover workers from JSON configs and optional Python overrides."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.agents.harness.models import WorkerMetadata
from app.agents.harness.worker_base import WorkerBase

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent / "workers" / "configs"


class WorkerManager:
    _instance: "WorkerManager | None" = None

    def __new__(cls) -> "WorkerManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._workers: dict[str, tuple[type[WorkerBase], WorkerMetadata]] = {}
        self._initialized = True
        self._load_all()

    def _load_all(self) -> None:
        for path in sorted(CONFIG_DIR.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                metadata = WorkerMetadata(**data)
                worker_cls = self._resolve_class(metadata.worker_name)
                self._workers[metadata.worker_name] = (worker_cls, metadata)
            except Exception:
                logger.exception("Failed to load worker config %s", path.name)

    def _resolve_class(self, worker_name: str) -> type[WorkerBase]:
        """Try to import an overriding Python class; otherwise return WorkerBase."""
        module_name = f"app.agents.harness.workers.{worker_name}_worker"
        class_name = self._to_camel(worker_name) + "Worker"
        try:
            module = __import__(module_name, fromlist=[class_name])
            cls = getattr(module, class_name)
            if issubclass(cls, WorkerBase):
                return cls
        except Exception:
            pass
        return WorkerBase

    @staticmethod
    def _to_camel(snake: str) -> str:
        return "".join(part.capitalize() for part in snake.split("_"))

    def list_workers(self) -> list[WorkerMetadata]:
        return [metadata for _, metadata in self._workers.values()]

    def get_worker_class(self, worker_name: str) -> type[WorkerBase]:
        cls, _ = self._workers[worker_name]
        return cls

    def get_metadata(self, worker_name: str) -> WorkerMetadata:
        _, metadata = self._workers[worker_name]
        return metadata

    def create_worker(
        self,
        worker_name: str,
        db,
        llm,
        recursive_limit: int,
    ) -> WorkerBase:
        cls, metadata = self._workers[worker_name]
        return cls(db, llm, recursive_limit, metadata=metadata, timeout=metadata.timeout)

    def available_workers(self) -> list[str]:
        return list(self._workers.keys())


def get_worker_manager() -> WorkerManager:
    return WorkerManager()
```

- [ ] **Step 2: Write test**

```python
# tests/agents/harness/test_worker_manager.py
import unittest

from app.agents.harness.worker_manager import WorkerManager


class TestWorkerManager(unittest.TestCase):
    def test_manager_loads_all_workers(self):
        manager = WorkerManager()
        names = manager.available_workers()
        self.assertIn("character", names)
        self.assertIn("outline", names)
        self.assertIn("chapter_text", names)

    def test_metadata_has_required_fields(self):
        manager = WorkerManager()
        for metadata in manager.list_workers():
            self.assertTrue(metadata.worker_name)
            self.assertTrue(metadata.description)
            self.assertTrue(metadata.system_prompt)
            self.assertIsInstance(metadata.tools, list)

    def test_resolve_overriding_class(self):
        manager = WorkerManager()
        cls = manager.get_worker_class("character")
        self.assertEqual(cls.__name__, "CharacterWorker")


if __name__ == "__main__":
    unittest.main()
```

Run:

```bash
cd backend && python -m unittest tests.agents.harness.test_worker_manager -v
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add app/agents/harness/worker_manager.py tests/agents/harness/test_worker_manager.py
git commit -m "feat(harness): add WorkerManager singleton with auto-discovery and class override"
```

---

## Task 8: Implement DagExecutor

**Files:**
- Create: `app/agents/harness/dag_executor.py`
- Create: `tests/agents/harness/test_dag_executor.py`

**Interfaces:**
- Consumes: `ExecutionPlan`, `HarnessContext`, `WorkerManager`, `llm_factory`, `db`, `recursive_limit`, optional `history_context`
- Produces: `dict[str, WorkerResult]` keyed by task id

- [ ] **Step 1: Implement `app/agents/harness/dag_executor.py`**

```python
"""DAG Executor: run tasks in topological order with parallelism."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.agents.harness.models import ExecutionPlan, HarnessContext, Task, WorkerResult
from app.agents.harness.worker_base import WorkerBase
from app.agents.harness.worker_manager import WorkerManager

logger = logging.getLogger(__name__)


class DagExecutor:
    def __init__(
        self,
        manager: WorkerManager,
        db,
        llm_factory,
        recursive_limit: int,
        history_context: list[dict] | None = None,
    ):
        self.manager = manager
        self.db = db
        self.llm_factory = llm_factory
        self.recursive_limit = recursive_limit
        self.history_context = history_context or []

    async def execute(
        self,
        plan: ExecutionPlan,
        context: HarnessContext,
    ) -> dict[str, WorkerResult]:
        results: dict[str, WorkerResult] = {}
        tasks_by_id = {t.id: t for t in plan.tasks}
        remaining = set(tasks_by_id.keys())
        completed: set[str] = set()
        failed: set[str] = set()

        while remaining:
            ready = [
                tid for tid in remaining
                if all(dep in completed for dep in tasks_by_id[tid].deps)
                and not any(dep in failed for dep in tasks_by_id[tid].deps)
            ]
            if not ready:
                # Cyclic dependency or all remaining blocked by failure
                for tid in remaining:
                    task = tasks_by_id[tid]
                    if any(dep in failed for dep in task.deps):
                        results[tid] = WorkerResult(
                            worker=task.worker,
                            task_id=tid,
                            status="skipped",
                            error="上游任务失败",
                        )
                    else:
                        results[tid] = WorkerResult(
                            worker=task.worker,
                            task_id=tid,
                            status="error",
                            error="依赖循环或无法调度",
                        )
                break

            coros = [self._run_task(tasks_by_id[tid], context) for tid in ready]
            batch_results = await asyncio.gather(*coros, return_exceptions=True)

            for tid, res in zip(ready, batch_results):
                remaining.discard(tid)
                if isinstance(res, Exception):
                    logger.exception("Task %s failed", tid)
                    results[tid] = WorkerResult(
                        worker=tasks_by_id[tid].worker,
                        task_id=tid,
                        status="error",
                        error=str(res),
                    )
                    failed.add(tid)
                else:
                    results[tid] = res
                    if res.status == "error":
                        failed.add(tid)
                    else:
                        completed.add(tid)
                        context.artifacts[tid] = res.artifacts

        return results

    async def _run_task(self, task: Task, context: HarnessContext) -> WorkerResult:
        worker_cls = self.manager.get_worker_class(task.worker)
        metadata = self.manager.get_metadata(task.worker)
        llm = await self.llm_factory(metadata.model_level)
        worker = worker_cls(self.db, llm, self.recursive_limit, metadata=metadata, timeout=metadata.timeout)

        # Resolve input artifacts from upstream task results
        input_artifacts: dict[str, Any] = {}
        for key, upstream_id in task.input_artifacts.items():
            input_artifacts[key] = context.artifacts.get(upstream_id, {})

        raw = await worker.run(task, context, history_context=self.history_context)
        result = WorkerResult.from_raw(task.worker, task.id, raw)
        return result
```

- [ ] **Step 2: Write test**

```python
# tests/agents/harness/test_dag_executor.py
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from app.agents.harness.dag_executor import DagExecutor
from app.agents.harness.models import ExecutionPlan, HarnessContext, Task, WorkerResult
from app.agents.harness.worker_manager import WorkerManager


class TestDagExecutor(unittest.IsolatedAsyncioTestCase):
    async def test_sequential_dependency(self):
        manager = MagicMock(spec=WorkerManager)
        manager.get_worker_class.return_value = MagicMock()
        manager.get_metadata.return_value = MagicMock(model_level="default", timeout=60.0, recursive_limit=None)

        async def llm_factory(level):
            return MagicMock()

        worker_instance = MagicMock()
        worker_instance.run = AsyncMock(side_effect=[
            {"summary": "task1", "changes": [], "artifacts": {"x": 1}},
            {"summary": "task2", "changes": [], "artifacts": {}},
        ])
        manager.get_worker_class.return_value = lambda *args, **kwargs: worker_instance

        plan = ExecutionPlan(
            intent="test",
            tasks=[
                Task(id="t1", worker="character", goal="g1"),
                Task(id="t2", worker="outline", goal="g2", deps=["t1"]),
            ],
        )
        context = HarnessContext()
        executor = DagExecutor(manager, MagicMock(), llm_factory, 8)
        results = await executor.execute(plan, context)

        self.assertEqual(results["t1"].status, "completed")
        self.assertEqual(results["t2"].status, "completed")
        self.assertEqual(context.artifacts["t1"], {"x": 1})

    async def test_parallel_independent_tasks(self):
        manager = MagicMock(spec=WorkerManager)
        manager.get_worker_class.return_value = MagicMock()
        manager.get_metadata.return_value = MagicMock(model_level="default", timeout=60.0, recursive_limit=None)

        async def llm_factory(level):
            return MagicMock()

        worker_instance = MagicMock()
        worker_instance.run = AsyncMock(side_effect=[
            {"summary": "a", "changes": []},
            {"summary": "b", "changes": []},
        ])
        manager.get_worker_class.return_value = lambda *args, **kwargs: worker_instance

        plan = ExecutionPlan(
            intent="test",
            tasks=[
                Task(id="a", worker="character", goal="g"),
                Task(id="b", worker="world", goal="g"),
            ],
        )
        context = HarnessContext()
        executor = DagExecutor(manager, MagicMock(), llm_factory, 8)
        results = await executor.execute(plan, context)

        self.assertEqual(results["a"].status, "completed")
        self.assertEqual(results["b"].status, "completed")


if __name__ == "__main__":
    unittest.main()
```

Run:

```bash
cd backend && python -m unittest tests.agents.harness.test_dag_executor -v
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add app/agents/harness/dag_executor.py tests/agents/harness/test_dag_executor.py
git commit -m "feat(harness): add DagExecutor with parallel topological execution"
```

---

## Task 9: Implement HarnessContext helper and analyze node

**Files:**
- Create: `app/agents/harness/nodes/analyze.py`

**Interfaces:**
- Consumes: `HarnessState`, `db`, `settings`
- Produces: updated `HarnessState` with `context` populated and `stage=PLAN`

- [ ] **Step 1: Implement `app/agents/harness/nodes/analyze.py`**

```python
"""Analyze node: gather project context and historical summaries."""
from __future__ import annotations

from app import repositories as repo
from app.agents.harness.history import build_history_context, retrieve_similar_summaries
from app.agents.harness.models import HarnessContext, HarnessStage, HarnessState
from app.core.llm_factory import get_embedding_client
from app.models import Project


async def analyze(state: HarnessState, db, settings, recent_messages) -> HarnessState:
    project_id = state.project_id
    entities: dict[str, list[dict]] = {}
    project_summary = ""

    if project_id:
        project = await db.get(Project, project_id)
        if project:
            project_summary = f"{project.title}\n{project.description}".strip()
        entities = {
            "outlines": await repo.list_outlines(db, project_id),
            "characters": await repo.list_characters(db, project_id),
            "foreshadows": await repo.list_foreshadows(db, project_id),
            "world": await repo.list_world(db, project_id),
            "plot": await repo.list_plot(db, project_id),
            "chapters": await repo.list_chapters(db, project_id),
        }

    # Retrieve similar summaries (best-effort)
    retrieved_summaries = []
    try:
        embedding_client, dimension = await get_embedding_client(db)
        query_vectors = await embedding_client.embed(
            [state.user_input],
            model=embedding_client.model,
            dimensions=dimension if dimension > 0 else None,
        )
        top_k = max(0, settings.assistant_history_top_k or 5)
        if top_k > 0:
            retrieved_summaries = await retrieve_similar_summaries(
                db, state.session_id, query_vectors[0], top_k
            )
    except Exception:
        pass

    history_context = build_history_context(None, recent_messages, retrieved_summaries, settings)

    state.context = HarnessContext(
        project_id=project_id,
        user_input=state.user_input,
        project_summary=project_summary,
        entities=entities,
        session_context={},
    )
    state.stage = HarnessStage.PLAN
    return state
```

Note: `build_history_context` currently expects a session object; you may need to pass `None` and verify the function handles it, or adjust the helper to accept optional session. If it fails, create a small wrapper in `analyze.py` that builds the history from `recent_messages` and `retrieved_summaries` directly.

- [ ] **Step 2: Run compile check**

```bash
cd backend && python -m compileall app/agents/harness/nodes/analyze.py
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add app/agents/harness/nodes/analyze.py
git commit -m "feat(harness): add analyze node for context gathering"
```

---

## Task 10: Refactor supervisor node for ExecutionPlan

**Files:**
- Modify: `app/agents/harness/nodes/supervisor.py`

**Interfaces:**
- Consumes: `HarnessState`, `llm`, `WorkerManager`
- Produces: `HarnessState` with `plan: ExecutionPlan` and `stage=EXECUTE`

- [ ] **Step 1: Rewrite `app/agents/harness/nodes/supervisor.py`**

```python
"""Supervisor node: build ExecutionPlan from user input and worker metadata."""
from __future__ import annotations

import logging
import uuid

from app.agents.harness.models import ExecutionPlan, HarnessStage, HarnessState, Task
from app.agents.harness.worker_manager import WorkerManager
from app.core.llm_client import LLMClient

logger = logging.getLogger(__name__)


def _build_supervisor_prompt(worker_metadata: list, context_note: str = "") -> str:
    worker_descriptions = []
    for meta in worker_metadata:
        worker_descriptions.append(
            f"- {meta.worker_name}: {meta.description}\n"
            f"  可用工具: {', '.join(meta.tools)}\n"
            f"  输出格式: {meta.output_schema}"
        )

    return (
        "你是小说创作助手的调度器。根据用户指令与项目现有数据，判断需要派发哪些专精 Worker 来处理。\n\n"
        "可选 Worker 及其能力：\n" + "\n".join(worker_descriptions) + "\n\n"
        "请返回 JSON，格式如下：\n"
        '{"intent": "一句话意图", "tasks": [{"id": "t1", "worker": "character", "goal": "...", '
        '"deps": [], "input_artifacts": {}, "output_artifacts": []}, ...]}\n\n'
        "规则：\n"
        "1. 复合意图必须拆分为多个 task，每个 task 只派给一个对应 worker。\n"
        "2. 独立任务使用相同的 deps 列表（通常为 []），这样它们会被并行执行。\n"
        "3. 若下游 task 需要上游 task 的产物，用 output_artifacts/input_artifacts 显式引用。\n"
        "4. task 的 id 必须唯一。\n"
        "5. 若指令与长篇数据无关，返回 tasks 为空数组。\n"
        f"{context_note}\n\n"
        "只输出 JSON，不要 markdown 代码块，不要解释。"
    )


async def supervisor(state: HarnessState, llm: LLMClient, manager: WorkerManager) -> HarnessState:
    if not state.project_id:
        # Global chat: no plan
        state.plan = ExecutionPlan(intent="通用对话", tasks=[])
        state.stage = HarnessStage.EXECUTE
        return state

    metadata = manager.list_workers()
    context_note = ""
    if state.context.session_context:
        context_note = f"\n\n当前会话上下文：{state.context.session_context}"

    prompt = _build_supervisor_prompt(metadata, context_note)
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": state.user_input},
    ]

    plan = await _parse_plan(llm, messages)
    if not plan:
        # Fallback: single sequential task using outline worker
        plan = ExecutionPlan(
            intent=state.user_input[:50],
            tasks=[Task(id=_new_task_id(), worker="outline", goal=state.user_input)],
        )

    state.plan = plan
    state.stage = HarnessStage.EXECUTE
    return state


async def _parse_plan(llm: LLMClient, messages: list[dict]) -> ExecutionPlan | None:
    try:
        raw = await llm.parse_llm_json(messages)
        if not isinstance(raw, dict):
            return None
        tasks = raw.get("tasks") or []
        validated_tasks = []
        for t in tasks:
            if not isinstance(t, dict):
                continue
            validated_tasks.append(Task(
                id=t.get("id") or _new_task_id(),
                worker=t.get("worker", "outline"),
                goal=t.get("goal", ""),
                deps=t.get("deps") or [],
                input_artifacts=t.get("input_artifacts") or {},
                output_artifacts=t.get("output_artifacts") or [],
                meta=t.get("meta") or {},
            ))
        return ExecutionPlan(
            intent=raw.get("intent", ""),
            tasks=validated_tasks,
            global_context=raw.get("global_context", {}),
        )
    except Exception:
        logger.exception("Supervisor plan parsing failed")
    return None


def _new_task_id() -> str:
    return f"task_{uuid.uuid4().hex[:8]}"
```

- [ ] **Step 2: Run compile check**

```bash
cd backend && python -m compileall app/agents/harness/nodes/supervisor.py
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add app/agents/harness/nodes/supervisor.py
git commit -m "feat(harness): refactor supervisor node to output ExecutionPlan"
```

---

## Task 11: Implement executor node

**Files:**
- Create: `app/agents/harness/nodes/executor.py`

**Interfaces:**
- Consumes: `HarnessState`, `db`, `llm_factory`, `recursive_limit`, `history_context`
- Produces: `HarnessState` with `results` populated and `stage=AGGREGATE`

- [ ] **Step 1: Implement `app/agents/harness/nodes/executor.py`**

```python
"""Executor node: run the ExecutionPlan through DagExecutor."""
from __future__ import annotations

from app.agents.harness.dag_executor import DagExecutor
from app.agents.harness.models import HarnessStage, HarnessState
from app.agents.harness.worker_manager import WorkerManager


async def executor(
    state: HarnessState,
    db,
    llm_factory,
    recursive_limit: int,
    history_context: list[dict] | None = None,
) -> HarnessState:
    manager = WorkerManager()
    dag = DagExecutor(manager, db, llm_factory, recursive_limit, history_context=history_context)
    state.results = await dag.execute(state.plan, state.context)
    state.stage = HarnessStage.AGGREGATE
    return state
```

- [ ] **Step 2: Run compile check**

```bash
cd backend && python -m compileall app/agents/harness/nodes/executor.py
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add app/agents/harness/nodes/executor.py
git commit -m "feat(harness): add executor node wrapping DagExecutor"
```

---

## Task 12: Update aggregator node

**Files:**
- Modify: `app/agents/harness/nodes/aggregator.py`

**Interfaces:**
- Consumes: `HarnessState`
- Produces: `HarnessState` with `change_records` populated and `stage=RESPOND`

- [ ] **Step 1: Rewrite `aggregate` to accept `dict[str, WorkerResult]`**

```python
"""Aggregator node: WorkerResult -> ChangeRecord[]."""
from __future__ import annotations

import json
from typing import Any

from app.agents.harness.models import HarnessStage, HarnessState, WorkerResult
from app.agents.harness.state import ChangeRecord, make_change


_WORKER_ENTITY = {
    "character": "character",
    "world": "world",
    "outline": "outline",
    "broad_outline": "outline",
    "outline_split": "outline",
    "plot": "plot",
    "plot_nodes": "plot",
    "foreshadow": "foreshadow",
    "chapter_outline": "chapter",
    "chapter_text": "chapter",
    "assignment": "chapter",
}


def aggregate_state(state: HarnessState) -> HarnessState:
    state.change_records = _aggregate_results(state.project_id or "", state.results)
    state.stage = HarnessStage.RESPOND
    return state


def _aggregate_results(project_id: str, results: dict[str, WorkerResult]) -> list[ChangeRecord]:
    records: list[ChangeRecord] = []
    for task_id, res in results.items():
        worker = res.worker
        default_entity_type = _WORKER_ENTITY.get(worker, worker or "unknown")
        changes = res.changes
        stage = res.stage or worker
        if isinstance(changes, str):
            try:
                changes = json.loads(changes)
            except Exception:
                changes = []
        for ch in changes:
            if not isinstance(ch, dict):
                continue
            action = ch.get("action", "add")
            fields = ch.get("fields", {})
            entity_id = ch.get("entity_id")
            entity_type = ch.get("entity_type")
            if entity_type is None:
                if worker == "assignment":
                    entity_type = "plot" if "chapter_id" in fields else "chapter"
                else:
                    entity_type = default_entity_type
            records.append(make_change(
                project_id=project_id,
                action=action,
                entity_type=entity_type,
                after=fields,
                entity_id=entity_id,
                before=ch.get("before"),
                stage=stage,
                temp_id=ch.get("temp_id"),
            ))
    return records
```

- [ ] **Step 2: Keep backward-compatible `aggregate(project_id, worker_results)`**

Add a thin wrapper:

```python
def aggregate(project_id: str, worker_results: list[dict]) -> list[ChangeRecord]:
    """Legacy aggregator interface for callers still passing list[dict]."""
    mapped = {}
    for i, res in enumerate(worker_results):
        worker = res.get("worker", "unknown")
        mapped[str(i)] = WorkerResult(
            worker=worker,
            task_id=str(i),
            status="error" if res.get("error") else "completed",
            summary=res.get("summary", ""),
            changes=res.get("changes") or [],
            artifacts=res.get("artifacts", {}),
            notes=res.get("notes", []),
            error=res.get("error"),
            stage=res.get("stage", worker),
        )
    return _aggregate_results(project_id, mapped)
```

- [ ] **Step 3: Run compile check**

```bash
cd backend && python -m compileall app/agents/harness/nodes/aggregator.py
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add app/agents/harness/nodes/aggregator.py
git commit -m "feat(harness): update aggregator to consume WorkerResult dict"
```

---

## Task 13: Update responder node

**Files:**
- Modify: `app/agents/harness/nodes/responder.py`

**Interfaces:**
- Consumes: `HarnessState`, `llm`
- Produces: `HarnessState` with `summary` populated and `stage=COMMIT`

- [ ] **Step 1: Add state-based entry point**

Keep existing `respond()` function for compatibility, and add:

```python
async def respond_state(state: HarnessState, llm) -> HarnessState:
    worker_results = [
        {
            "worker": r.worker,
            "stage": r.stage,
            "changes": r.changes,
            "notes": r.notes,
        }
        for r in state.results.values()
    ]
    summary = await respond(
        llm,
        state.change_records,
        user_input=state.user_input,
        history_context=None,  # history handled separately
        system_prompt=GLOBAL_RESPONDER_PROMPT if not state.project_id else None,
        context=state.context.entities if state.project_id else None,
        worker_results=worker_results,
    )
    state.summary = summary
    state.stage = HarnessStage.COMMIT
    return state
```

- [ ] **Step 2: Run compile check**

```bash
cd backend && python -m compileall app/agents/harness/nodes/responder.py
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add app/agents/harness/nodes/responder.py
git commit -m "feat(harness): add state-based responder entry point"
```

---

## Task 14: Implement commit node

**Files:**
- Create: `app/agents/harness/nodes/commit.py`

**Interfaces:**
- Consumes: `HarnessState`, `db`, `is_global`
- Produces: `HarnessState` with `auto_applied` populated, `staged_changes` persisted, `stage=DONE`

- [ ] **Step 1: Implement `app/agents/harness/nodes/commit.py`**

```python
"""Commit node: auto-apply chapter changes and stage remaining records."""
from __future__ import annotations

import logging

from app import repositories as repo
from app.agents.harness.models import HarnessStage, HarnessState
from app.agents.harness.state import ChangeRecord
from app.models import LongChangeRecord
from app.services.change_apply import apply_change

logger = logging.getLogger(__name__)

_CHAPTER_AUTO_FIELDS = {"content", "detailed_outline", "status"}


def _is_chapter_auto_apply(record: ChangeRecord) -> bool:
    keys = set((record.after or {}).keys())
    return (
        record.entity_type == "chapter"
        and record.action == "update"
        and bool(record.entity_id)
        and keys <= _CHAPTER_AUTO_FIELDS
        and bool(keys & {"content", "detailed_outline"})
    )


async def commit_state(state: HarnessState, db, is_global: bool) -> HarnessState:
    notes_by_stage = {
        r.stage: r.notes
        for r in state.results.values()
        if r.notes
    }
    auto_applied: list[dict] = []
    staged_records: list[ChangeRecord] = []

    for r in state.change_records:
        if not is_global and _is_chapter_auto_apply(r):
            try:
                before_row = await repo.get_chapter(db, r.entity_id)
                before = _row_to_dict(before_row)
                await apply_change(db, state.project_id, r.model_dump())
            except Exception:
                logger.exception("自动应用章节变更失败，降级为待确认")
                await db.rollback()
                staged_records.append(r)
                continue
            try:
                db.add(LongChangeRecord(
                    project_id=state.project_id,
                    entity_type="chapter",
                    entity_id=r.entity_id,
                    before=before,
                    after=r.after,
                    status="applied",
                    source="auto",
                ))
                await db.commit()
            except Exception:
                logger.exception("自动应用审计记录写入失败（变更已应用）")
                await db.rollback()
            auto_applied.append({
                "change_id": r.id,
                "entity_id": r.entity_id,
                "entity_type": "chapter",
                "fields": list((r.after or {}).keys()),
                "notes": notes_by_stage.get(r.stage) or [],
            })
        else:
            staged_records.append(r)

    state.auto_applied = auto_applied
    # Note: actual staging to AssistantSession happens in assistant.py to keep node stateless
    state.staged_records = staged_records
    state.stage = HarnessStage.DONE
    return state


def _row_to_dict(row) -> dict | None:
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}
```

Note: we attach `staged_records` temporarily to state; `assistant.py` will write them to the session.

- [ ] **Step 2: Run compile check**

```bash
cd backend && python -m compileall app/agents/harness/nodes/commit.py
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add app/agents/harness/nodes/commit.py
git commit -m "feat(harness): add commit node for auto-apply and staging"
```

---

## Task 15: Implement HarnessRuntime

**Files:**
- Create: `app/agents/harness/runtime.py`

**Interfaces:**
- Consumes: `HarnessState`, `WorkerManager`, `db`, `llm_factory`, `settings`, `recent_messages`, `is_global`
- Produces: final `HarnessState` after running through all stages

- [ ] **Step 1: Implement `app/agents/harness/runtime.py`**

```python
"""HarnessRuntime: state-machine driver for the assistant harness."""
from __future__ import annotations

import logging

from app.agents.harness.models import HarnessError, HarnessStage, HarnessState
from app.agents.harness.nodes.aggregator import aggregate_state
from app.agents.harness.nodes.analyze import analyze
from app.agents.harness.nodes.commit import commit_state
from app.agents.harness.nodes.executor import executor
from app.agents.harness.nodes.responder import respond_state
from app.agents.harness.nodes.supervisor import supervisor
from app.agents.harness.worker_manager import WorkerManager

logger = logging.getLogger(__name__)


class HarnessRuntime:
    def __init__(
        self,
        state: HarnessState,
        manager: WorkerManager,
        db,
        llm_factory,
        settings,
        recent_messages: list,
        is_global: bool,
        recursive_limit: int,
    ):
        self.state = state
        self.manager = manager
        self.db = db
        self.llm_factory = llm_factory
        self.settings = settings
        self.recent_messages = recent_messages
        self.is_global = is_global
        self.recursive_limit = recursive_limit

    async def run(self) -> HarnessState:
        while self.state.stage not in (HarnessStage.DONE, HarnessStage.ERROR):
            await self.step()
        return self.state

    async def step(self) -> HarnessState:
        try:
            if self.state.stage == HarnessStage.INIT:
                self.state.stage = HarnessStage.ANALYZE
            elif self.state.stage == HarnessStage.ANALYZE:
                self.state = await analyze(self.state, self.db, self.settings, self.recent_messages)
            elif self.state.stage == HarnessStage.PLAN:
                llm = await self.llm_factory("medium")
                self.state = await supervisor(self.state, llm, self.manager)
            elif self.state.stage == HarnessStage.EXECUTE:
                self.state = await executor(
                    self.state,
                    self.db,
                    self.llm_factory,
                    self.recursive_limit,
                    history_context=None,  # history injected per node if needed
                )
            elif self.state.stage == HarnessStage.AGGREGATE:
                self.state = aggregate_state(self.state)
            elif self.state.stage == HarnessStage.RESPOND:
                llm = await self.llm_factory("low")
                self.state = await respond_state(self.state, llm)
            elif self.state.stage == HarnessStage.COMMIT:
                self.state = await commit_state(self.state, self.db, self.is_global)
            else:
                self.state.error = HarnessError(stage=self.state.stage, message=f"未知阶段: {self.state.stage}")
                self.state.stage = HarnessStage.ERROR
        except Exception as e:
            logger.exception("Harness runtime error at stage %s", self.state.stage)
            self.state.error = HarnessError(stage=self.state.stage, message=str(e), details={"type": type(e).__name__})
            self.state.stage = HarnessStage.ERROR
        return self.state
```

- [ ] **Step 2: Run compile check**

```bash
cd backend && python -m compileall app/agents/harness/runtime.py
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add app/agents/harness/runtime.py
git commit -m "feat(harness): add HarnessRuntime state-machine driver"
```

---

## Task 16: Rewrite assistant.py chat endpoint to use HarnessRuntime

**Files:**
- Modify: `app/api/assistant.py`

**Interfaces:**
- Consumes: `HarnessRuntime`, `HarnessState`, `WorkerManager`
- Produces: same `/assistant/chat` response shape as before

- [ ] **Step 1: Update imports**

Replace the worker and node imports with:

```python
from app.agents.harness.models import HarnessContext, HarnessState, HarnessStage
from app.agents.harness.runtime import HarnessRuntime
from app.agents.harness.worker_manager import WorkerManager
from app.agents.harness.nodes.responder import GLOBAL_RESPONDER_PROMPT, respond
```

Remove `_WORKERS`, `_WORKER_LEVEL`, `run_worker`, `aggregate`, `_apply_changes_to_context`, `_detect_chapter_generation_intent`, `_detect_foreshadow_intent`, `_detect_compound_intent`, and the large inline supervisor prompt. Keep session helpers, confirm/reject/undo endpoints, and `_is_chapter_auto_apply` (or use the one in commit node).

- [ ] **Step 2: Add rule-based pre-planning helper**

Keep lightweight rule detection for chapter generation and compound intent so we don't rely solely on the new supervisor immediately:

```python
def _rule_based_plan(user_input: str) -> dict | None:
    """Return a simple plan dict for chapter generation / foreshadow / compound intents.

    This is a compatibility bridge while the LLM supervisor learns to output DAGs.
    """
    text = user_input.lower()
    # Chapter generation
    range_match = __import__("re").search(r"第\s*(\d+)\s*章\s*(?:到|至)\s*第\s*(\d+)\s*章", user_input)
    prefix_match = __import__("re").search(r"前\s*(\d+)\s*章", user_input)
    single_match = __import__("re").search(r"第\s*(\d+)\s*章", user_input)
    chapter_nums = None
    label = ""
    if range_match:
        start, end = int(range_match.group(1)), int(range_match.group(2))
        chapter_nums = list(range(start, end + 1))
        label = f"第 {start} 章到第 {end} 章"
    elif prefix_match:
        n = int(prefix_match.group(1))
        chapter_nums = list(range(1, n + 1))
        label = f"前 {n} 章"
    elif single_match:
        chapter_nums = [int(single_match.group(1))]
        label = f"第 {chapter_nums[0]} 章"

    if chapter_nums:
        has_outline = "细纲" in user_input or "章节大纲" in user_input
        has_text = "正文" in user_input or "写" in user_input
        worker = "chapter_outline" if has_outline or not has_text else "chapter_text"
        return {"intent": f"生成{label}{'细纲' if worker == 'chapter_outline' else '正文'}", "tasks": [{"worker": worker, "goal": user_input}]}

    # Compound intent
    has_character = any(kw in text for kw in ("角色", "人物", "主角", "配角", "龙套", "npc"))
    has_world = any(kw in text for kw in ("世界观", "设定", "规则", "体系", "境界"))
    has_outline_modify = any(kw in text for kw in ("完善大纲", "调整大纲", "更新大纲", "修改大纲"))
    has_foreshadow = any(kw in text for kw in ("伏笔", "悬念", "回收", "呼应", "预埋"))
    tasks = []
    if has_foreshadow:
        tasks.append({"worker": "foreshadow", "goal": user_input})
    if has_world:
        tasks.append({"worker": "world", "goal": user_input})
    if has_character:
        tasks.append({"worker": "character", "goal": user_input})
    if has_outline_modify:
        tasks.append({"worker": "outline", "goal": user_input})
    if len(tasks) > 1:
        return {"intent": user_input, "tasks": tasks}
    return None
```

- [ ] **Step 3: Replace the chat flow body**

After loading `sess`, `recursive_limit`, `settings_obj`, `recent_messages`, and `history_context`, build the state and run the runtime:

```python
    # Build initial state
    state = HarnessState(
        project_id=effective_project_id,
        session_id=sess.id,
        user_input=user_input,
    )
    state.context = HarnessContext(
        project_id=effective_project_id,
        user_input=user_input,
        session_context=context_payload,
    )

    async def llm_factory(level: str | None = None):
        return await get_llm_client(db, level=level)

    runtime = HarnessRuntime(
        state=state,
        manager=WorkerManager(),
        db=db,
        llm_factory=llm_factory,
        settings=settings_obj,
        recent_messages=recent_messages,
        is_global=is_global,
        recursive_limit=recursive_limit,
    )

    # Rule-based pre-planning for chapter generation / compound intent
    rule_plan = _rule_based_plan(user_input) if not is_global else None
    if rule_plan:
        from app.agents.harness.models import ExecutionPlan, Task
        import uuid
        state.plan = ExecutionPlan(
            intent=rule_plan["intent"],
            tasks=[Task(id=f"task_{uuid.uuid4().hex[:8]}", worker=t["worker"], goal=t["goal"]) for t in rule_plan["tasks"]],
        )
        state.stage = HarnessStage.EXECUTE

    final_state = await runtime.run()

    if final_state.error:
        logger.error("Harness runtime ended in error: %s", final_state.error.message)
```

- [ ] **Step 4: Persist staged changes and build response**

After runtime completes, write staged records to session and build the response exactly as before:

```python
    staged_records = getattr(final_state, "staged_records", final_state.change_records)
    staged = list(sess.staged_changes or [])
    staged.extend([r.model_dump() for r in staged_records])
    sess.staged_changes = staged
    await db.commit()

    summary = final_state.summary
    auto_applied = final_state.auto_applied
    if auto_applied:
        chapter_titles = {c.get("id"): c.get("title") for c in (final_state.context.entities.get("chapters") or [])}
        field_labels = {"content": "正文", "detailed_outline": "细纲", "status": "状态"}
        lines = ["", "---", "**已直接写入：**"]
        for a in auto_applied:
            label = "、".join(field_labels.get(f, f) for f in a["fields"] if f != "status")
            title = chapter_titles.get(a["entity_id"]) or a["entity_id"]
            lines.append(f"- 章节《{title}》的{label}已保存（可撤销）")
            for n in a.get("notes", []):
                lines.append(f"  - {n}")
        summary += "\n".join(lines)

    records_data = [r.model_dump() for r in staged_records]
    # Persist assistant message, summarize, etc. (keep existing code)
```

- [ ] **Step 5: Run compile check**

```bash
cd backend && python -m compileall app/api/assistant.py
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add app/api/assistant.py
git commit -m "refactor(harness): wire assistant chat endpoint through HarnessRuntime"
```

---

## Task 17: Add unit tests for models and executor

**Files:**
- Create: `tests/agents/harness/test_models.py`

**Interfaces:**
- Consumes: models from `app/agents.harness.models`
- Produces: passing unit tests

- [ ] **Step 1: Create `tests/agents/harness/test_models.py`**

```python
import unittest

from app.agents.harness.models import ExecutionPlan, HarnessContext, Task, WorkerResult


class TestModels(unittest.TestCase):
    def test_task_defaults(self):
        t = Task(id="t1", worker="character", goal="add hero")
        self.assertEqual(t.deps, [])
        self.assertEqual(t.input_artifacts, {})

    def test_worker_result_from_raw_list(self):
        r = WorkerResult.from_raw("character", "t1", {"changes": [{"action": "add", "fields": {"name": "A"}}]})
        self.assertEqual(r.status, "completed")
        self.assertEqual(len(r.changes), 1)

    def test_harness_context_entities(self):
        ctx = HarnessContext(entities={"characters": [{"id": "1", "name": "A"}]})
        self.assertEqual(ctx.entity_list("characters")[0]["name"], "A")


if __name__ == "__main__":
    unittest.main()
```

Run:

```bash
cd backend && python -m unittest tests.agents.harness.test_models tests.agents.harness.test_dag_executor tests.agents.harness.test_worker_manager tests.agents.harness.test_worker_configs -v
```

Expected: all PASS.

- [ ] **Step 2: Commit**

```bash
git add tests/agents/harness/test_models.py
git commit -m "test(harness): add unit tests for harness models"
```

---

## Task 18: Run smoke tests and verification

**Files:**
- No new files.

**Interfaces:**
- Consumes: full backend
- Produces: verified behavior

- [ ] **Step 1: Backend syntax check**

```bash
cd backend && python -m compileall app
```

Expected: no errors.

- [ ] **Step 2: Run all unit tests**

```bash
cd backend && python -m unittest discover -s tests -v
```

Expected: all PASS.

- [ ] **Step 3: Start backend dev server and hit health**

```bash
cd backend && uvicorn app.main:app --reload --port 8765
```

In another terminal:

```bash
curl http://127.0.0.1:8765/health
```

Expected: `{"ok":true}` or similar healthy response.

- [ ] **Step 4: Test assistant chat manually**

Create a project via UI or API, then:

```bash
curl -X POST http://127.0.0.1:8765/api/assistant/chat \
  -H "Content-Type: application/json" \
  -d '{"project_id":"YOUR_PROJECT_ID","message":"新增一个主角，性格沉稳"}'
```

Expected: response contains `ok: true`, `change_records` with a character change, and `summary`.

- [ ] **Step 5: Test confirm/reject still work**

```bash
curl -X POST http://127.0.0.1:8765/api/assistant/confirm \
  -H "Content-Type: application/json" \
  -d '{"session_id":"YOUR_SESSION_ID"}'
```

Expected: `{"ok": true, "applied": [...]}`.

- [ ] **Step 6: Frontend typecheck**

```bash
cd frontend && npx tsc -b
```

Expected: no errors.

- [ ] **Step 7: Commit any final fixes**

```bash
git add -A
git commit -m "fix(harness): smoke test fixes and final verification"
```

---

## Self-Review Checklist

- [ ] **Spec coverage**: Every Phase 1 item in the design doc has at least one task.
  - Worker JSON configs: Tasks 2, 3
  - WorkerManager: Task 7
  - Separate worker files: Tasks 5, 6
  - Task DAG: Tasks 1 (models), 8 (executor), 10 (supervisor)
  - HarnessState/Runtime: Tasks 1, 15
  - Node refactor: Tasks 9-14
  - assistant.py rewrite: Task 16
  - API compatibility: Task 16 + Task 18
- [ ] **Placeholder scan**: No TBD/TODO/fill-in-details in steps.
- [ ] **Type consistency**: `Task`, `ExecutionPlan`, `WorkerResult`, `HarnessState`, `HarnessContext` names match across tasks.
- [ ] **Import paths**: All new files use absolute imports from `app.*`.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-29-agent-harness-phase1.md`.

Two execution options:

1. **Subagent-Driven (recommended)** - Dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** - Execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints.

Which approach do you prefer?

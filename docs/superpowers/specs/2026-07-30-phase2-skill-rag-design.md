# Phase 2: Skill / RAG 增强设计

日期：2026-07-30
状态：设计待实现
范围：Agent Harness Phase 2 —— 把网文创作方法论以 Skill / RAG 形式注入 worker prompt

## 1. 背景与目标

### 1.1 背景

Phase 1 已完成 Agent Harness 重构：

- `HarnessRuntime` 状态机驱动一次 `/assistant/chat`。
- `WorkerManager` 扫描 JSON 配置自动发现 11 个 worker。
- `DagExecutor` 按依赖拓扑并行调度 worker。
- Worker 提示词、可用工具、模型等级均已 JSON 化。

当前 worker 的 system prompt 只包含基础指令和 output schema，缺少系统化的网文创作方法论。Phase 2 目标是把网文创作知识以 **Skill** 和 **RAG** 形式注入 worker，提升 agent 在角色设计、大纲规划、伏笔布局、节奏控制等方面的创作能力。

### 1.2 目标

1. 建立 `SkillManager`，统一管理 inline skill 和 RAG skill。
2. 新增 7 个 inline skill（基于网文俱乐部创作指南整理）。
3. 新增 1 个 RAG skill（wangwenclub 案例库 chunks）。
4. 扩展 worker JSON 配置，支持 `skills` 和 `rag_skills` 字段。
5. 在 `WorkerBase` 构造 system prompt 时自动注入相关 skill 文本。
6. 复用现有 embedding / SQLite / numpy 检索基础设施，不引入外部向量库。
7. 保持 `/assistant/chat`、`/confirm`、`/reject` 接口形状不变。

## 2. 非目标

- 不实现 Phase 3（Workflow 剥离）和 Phase 4（Experience Reflection）。
- 不新增前端 UI（Phase 2 为后端能力增强）。
- 不引入 Chroma / Faiss / Pinecone 等外部向量库。
- 不自动抓取 wangwenclub，首次内容手动整理入库。

## 3. 目录结构

```
backend/app/agents/skills/
├── __init__.py              # 导出 SkillManager、get_skill_manager、SkillConfig
├── models.py                # SkillConfig, RagConfig, SkillQueryResult 数据模型
├── skill_manager.py         # SkillManager 单例：加载、查询、注入
├── registry/                # Inline skill：短规则 / 模板 / checklist
│   ├── writing_process.md
│   ├── plot_design.md
│   ├── character_arc.md
│   ├── world_building.md
│   ├── foreshadowing.md
│   ├── pacing.md
│   └── climax_hook.md
├── configs/                 # Skill 元数据 JSON
│   ├── writing_process.json
│   ├── plot_design.json
│   ├── character_arc.json
│   ├── world_building.json
│   ├── foreshadowing.json
│   ├── pacing.json
│   ├── climax_hook.json
│   └── wangwenclub_case.json
└── rag/
    ├── __init__.py
    ├── index.py             # chunk 切分、embedding、索引构建脚本
    └── chunks/
        └── wangwenclub/
            ├── plot_design_01.md
            ├── plot_design_02.md
            ├── character_arc_01.md
            └── ...
```

## 4. Skill Schema

### 4.1 Inline Skill

```json
{
  "skill_name": "plot_design",
  "description": "网络小说情节设计方法论",
  "type": "inline",
  "triggers": ["outline", "plot", "foreshadow", "chapter_outline", "chapter_text"],
  "content_file": "registry/plot_design.md",
  "priority": 1
}
```

字段说明：

- `skill_name`：唯一标识，用于 worker JSON 引用。
- `description`：供 supervisor 未来选用。
- `type`：`inline` 表示完整文本直接拼入 prompt。
- `triggers`：默认触发该 skill 的 worker 名称列表。
- `content_file`：相对于 `app/agents/skills/` 的 markdown 路径。
- `priority`：注入顺序，数字越小越靠前。

### 4.2 RAG Skill

```json
{
  "skill_name": "wangwenclub_case",
  "description": "网文俱乐部创作案例库",
  "type": "rag",
  "chunks_dir": "rag/chunks/wangwenclub/",
  "index_table": "skill_rag_embeddings",
  "top_k": 3,
  "priority": 2
}
```

字段说明：

- `type`：`rag` 表示按需检索 chunks。
- `chunks_dir`：chunk 文件目录。
- `index_table`：存储向量的表名（默认 `skill_rag_embeddings`）。
- `top_k`：默认检索条数。
- `priority`：注入顺序。

## 5. 数据模型

### 5.1 Pydantic 配置模型

```python
# app/agents/skills/models.py
from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class SkillType(str, Enum):
    INLINE = "inline"
    RAG = "rag"


class SkillConfig(BaseModel):
    skill_name: str
    description: str
    type: SkillType
    triggers: list[str] = Field(default_factory=list)
    priority: int = 1
    # inline
    content_file: str | None = None
    # rag
    chunks_dir: str | None = None
    index_table: str = "skill_rag_embeddings"
    top_k: int = 3

    def content_path(self, base_dir: Path) -> Path | None:
        if self.content_file:
            return base_dir / self.content_file
        return None

    def chunks_path(self, base_dir: Path) -> Path | None:
        if self.chunks_dir:
            return base_dir / self.chunks_dir
        return None


class SkillQueryResult(BaseModel):
    skill_name: str
    chunk_path: str
    chunk_text: str
    score: float
```

### 5.2 数据库表

新增 `SkillRagEmbedding` 表，复用现有 LargeBinary + numpy 检索模式：

```python
# app/models.py
class SkillRagEmbedding(Base):
    __tablename__ = "skill_rag_embeddings"

    id = Column(CHAR(36), primary_key=True, default=_uuid)
    skill_name = Column(String(64), nullable=False, index=True)
    chunk_path = Column(String(512), nullable=False)
    chunk_text = Column(Text, nullable=False)
    embedding = Column(LargeBinary, nullable=False)  # np.float32 bytes
    model = Column(String(128), nullable=False)
    dimension = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=_now, nullable=False)
```

## 6. SkillManager

### 6.1 职责

- 启动时扫描 `configs/*.json`，加载所有 `SkillConfig`。
- 缓存 inline skill 的 markdown 文本。
- 提供 `list_skills()` 给 supervisor 未来选用。
- 提供 `get_skills_for_worker(worker_name, task_goal)` 返回应注入的 inline skill 文本列表。
- 提供 `query_rag_skills(db, worker_name, query)` 对 RAG skill 做向量检索。

### 6.2 接口设计

```python
# app/agents/skills/skill_manager.py
from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.skills.models import SkillConfig, SkillQueryResult

SKILLS_DIR = Path(__file__).parent


class SkillManager:
    _instance: "SkillManager | None" = None

    def __new__(cls) -> "SkillManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._skills: dict[str, SkillConfig] = {}
        self._inline_cache: dict[str, str] = {}
        self._initialized = True
        self._load_all()

    def _load_all(self) -> None:
        ...

    def list_skills(self) -> list[SkillConfig]:
        return list(self._skills.values())

    def get_skill(self, skill_name: str) -> SkillConfig | None:
        return self._skills.get(skill_name)

    def get_skills_for_worker(
        self,
        worker_name: str,
        task_goal: str = "",
    ) -> list[tuple[SkillConfig, str]]:
        """返回 (SkillConfig, content) 列表，按 priority 排序。"""
        ...

    async def query_rag_skills(
        self,
        db: AsyncSession,
        worker_name: str,
        query: str,
        top_k: int | None = None,
    ) -> list[SkillQueryResult]:
        ...


def get_skill_manager() -> SkillManager:
    return SkillManager()
```

### 6.3 触发规则

1. 若 worker JSON 显式配置了 `skills` / `rag_skills`，优先使用配置。
2. 若未配置，使用 skill config 中的 `triggers` 字段匹配 worker_name。
3. 同一 worker 的多个 skill 按 `priority` 升序排列，同 priority 按 skill_name 排序。

## 7. Worker JSON 扩展

每个 worker JSON 增加 `skills` 和 `rag_skills`：

```json
{
  "worker_name": "outline",
  "description": "大纲规划师：负责大纲结构、拆分、调整",
  "system_prompt": "...",
  "tools": ["read_outlines", "propose_update_outline"],
  "skills": ["plot_design", "pacing"],
  "rag_skills": ["wangwenclub_case"],
  "model_level": "default",
  "temperature": 0.7,
  "timeout": 60.0,
  "recursive_limit": 8
}
```

`WorkerMetadata` 模型新增字段：

```python
class WorkerMetadata(BaseModel):
    ...
    skills: list[str] = Field(default_factory=list)
    rag_skills: list[str] = Field(default_factory=list)
```

## 8. WorkerBase Prompt 注入

在 `WorkerBase.run()` 默认实现中，构造 user prompt 之前注入 skill：

```python
async def run(self, task, context, history_context=None) -> dict:
    if self.metadata is None:
        raise RuntimeError(f"Worker {self.worker_name} has no metadata")

    # 1. system prompt 基础
    system_prompt = self.metadata.system_prompt

    # 2. 注入 output schema
    if self.metadata.output_schema:
        schema_text = json.dumps(...)
        system_prompt += f"\n\n你必须按以下 JSON schema 输出：\n{schema_text}\n只输出 JSON..."

    # 3. 注入 inline skills
    skill_manager = get_skill_manager()
    skill_texts = skill_manager.get_skills_for_worker(
        self.worker_name, task.goal
    )
    if skill_texts:
        system_prompt += "\n\n【创作方法论参考】\n"
        for cfg, content in skill_texts:
            system_prompt += f"\n--- {cfg.skill_name} ---\n{content}\n"

    # 4. 注入 RAG skill chunks
    rag_results = await skill_manager.query_rag_skills(
        self.db, self.worker_name, task.goal
    )
    if rag_results:
        system_prompt += "\n\n【相关案例参考】\n"
        for r in rag_results:
            system_prompt += f"\n--- {r.chunk_path} ---\n{r.chunk_text}\n"

    user_prompt = self._build_user_prompt(task, context)
    raw = await self._tool_loop(system_prompt, user_prompt, ...)
    return self._normalize_result(raw)
```

注意：

- skill 注入放在 output schema 要求之后，避免被 output schema 覆盖。
- RAG 检索是异步的，需要 `await`。
- 若 skill 内容为空或检索失败，保持原流程不变。

## 9. RAG 索引策略

### 9.1 Chunk 格式

每个 chunk 是一个 markdown 文件，头部带 YAML frontmatter：

```markdown
---
skill_name: wangwenclub_case
source: wangwenclub
source_url: https://www.wangwenclub.com/handbook/创作指南/情节设计
topic: 情节设计
tags: [plot, conflict, climax]
---

## 三幕剧结构

第一幕：建立（25%篇幅）
...
```

### 9.2 Chunk 切分原则

- 每 chunk 500-800 字，保证语义完整。
- 同一主题下按子主题切分（如“情节设计”拆分为三幕剧、英雄之旅、爽文模式、开篇设计等）。
- 保留 source_url 用于溯源。

### 9.3 索引构建脚本

```bash
cd backend && python -m app.agents.skills.rag.index
```

行为：

1. 扫描 `rag/chunks/**/*.md`。
2. 对每个 chunk 调用 `get_embedding_client(db)` 生成向量。
3. 删除该 `skill_name` 旧的索引记录。
4. 写入 `SkillRagEmbedding`。

实现位置：`app/agents/skills/rag/index.py`。

### 9.4 检索实现

复用 `app/agents/harness/retrieval.py` 的 L2 归一化 + 点积模式：

```python
async def retrieve_skill_chunks(
    db: AsyncSession,
    skill_names: list[str],
    query: str,
    top_k: int = 3,
) -> list[SkillQueryResult]:
    embedding_client, dimension = await get_embedding_client(db)
    query_vectors = await embedding_client.embed([query], ...)
    # 读取 skill_names 下所有 chunks，numpy cosine similarity，取 top_k
    ...
```

## 10. 与 Harness 的集成

| 组件 | 改动 |
|---|---|
| `analyze.py` | 无改动。skill 注入在 worker 层完成。 |
| `supervisor.py` | 未来可扩展：用 `SkillManager.list_skills()` 让 supervisor 主动选择 skill；Phase 2 先不启用。 |
| `executor.py` / `DagExecutor` | 无改动。 |
| `WorkerBase` | 注入 skill 文本。 |
| `assistant.py` | 无改动。 |

## 11. Skill 内容规划

基于网文俱乐部创作指南整理（来源见第 14 节）：

### 11.1 Inline Skills

| skill_name | 触发 worker | 来源 | 核心内容 |
|---|---|---|---|
| `writing_process` | `outline`, `broad_outline`, `plot_nodes`, `chapter_text` | 写作流程 | 选题立意、世界观设定、人物设定、大纲准备、日常写作流程 |
| `plot_design` | `outline`, `plot`, `plot_nodes`, `chapter_outline`, `chapter_text` | 情节设计 | 目标驱动、冲突推动、三幕剧、英雄之旅、爽文模式、开篇设计、高潮设计 |
| `character_arc` | `character`, `chapter_text` | 人物塑造 | 主角要素、主角类型、配角塑造、反派原则、人物档案模板 |
| `world_building` | `world`, `outline`, `chapter_text` | 世界观构建 | 力量体系、地理设定、社会结构、经济系统、历史背景、自洽原则 |
| `foreshadowing` | `foreshadow`, `outline`, `plot`, `chapter_outline` | 伏笔与照应 | 伏笔类型、照应类型、远近/明暗/多重/群体/连环伏笔技巧 |
| `pacing` | `outline`, `chapter_outline`, `chapter_text` | 节奏控制 | 不同阶段节奏、章节长度、爽点分布、张弛变化 |
| `climax_hook` | `outline`, `chapter_outline`, `chapter_text` | 高潮设计 | 高潮类型、战斗/智斗/情感/突破高潮结构、描写技巧 |

### 11.2 RAG Skill

- `wangwenclub_case`：把上述 7 篇文章按子主题切分为 20-30 个 chunks，每个 chunk 保留完整上下文。

## 12. 测试计划

### 12.1 单元测试

- `tests/agents/skills/test_skill_manager.py`
  - 加载所有 skill configs。
  - 验证 inline skill 内容非空。
  - 验证 `get_skills_for_worker` 按 trigger 返回正确 skill。
- `tests/agents/skills/test_rag_index.py`
  - chunk 文件存在且 frontmatter 解析正确。
  - 索引脚本可运行（mock embedding client）。
- `tests/agents/harness/test_worker_base.py`
  - 验证 `WorkerBase.run()` 注入 skill 文本到 system prompt。

### 12.2 冒烟测试

- `python -m compileall app` 通过。
- `python -m unittest discover -s tests` 通过。
- 启动后端，调用 `/assistant/chat`，确认 worker prompt 包含 skill 文本（可通过日志或测试接口验证）。

## 13. 迁移与部署

1. 新增 `SkillRagEmbedding` 表由 `scripts/migrate.py` 自动创建（项目启动时运行）。
2. 首次部署需运行索引脚本：
   ```bash
   cd backend && python -m app.agents.skills.rag.index
   ```
3. 不修改现有 `.env`，不需要新增环境变量。

## 14. 来源与参考

本设计参考以下网文俱乐部创作指南：

- [写作流程 | 网文俱乐部](https://www.wangwenclub.com/handbook/%E5%88%9B%E4%BD%9C%E6%8C%87%E5%8D%97/%E5%86%99%E4%BD%9C%E6%B5%81%E7%A8%8B)
- [情节设计 | 网文俱乐部](https://www.wangwenclub.com/handbook/%E5%88%9B%E4%BD%9C%E6%8C%87%E5%8D%97/%E6%83%85%E8%8A%82%E8%AE%BE%E8%AE%A1)
- [人物塑造 | 网文俱乐部](https://www.wangwenclub.com/handbook/%E5%88%9B%E4%BD%9C%E6%8C%87%E5%8D%97/%E4%BA%BA%E7%89%A9%E5%A1%91%E9%80%A0)
- [世界观构建 | 网文俱乐部](https://www.wangwenclub.com/handbook/%E5%88%9B%E4%BD%9C%E6%8C%87%E5%8D%97/%E4%B8%96%E7%95%8C%E8%A7%82%E6%9E%84%E5%BB%BA)
- [伏笔与照应 | 网文俱乐部](https://www.wangwenclub.com/handbook/%E5%88%9B%E4%BD%9C%E6%8C%87%E5%8D%97/%E4%BC%8F%E7%AC%94%E4%B8%8E%E7%85%A7%E5%BA%94)
- [节奏控制 | 网文俱乐部](https://www.wangwenclub.com/handbook/%E5%86%99%E4%BD%9C%E6%8A%80%E5%B7%A7/%E8%8A%82%E5%A5%8F%E6%8E%A7%E5%88%B6)
- [高潮设计 | 网文俱乐部](https://www.wangwenclub.com/handbook/%E5%86%99%E4%BD%9C%E6%8A%80%E5%B7%A7/%E9%AB%98%E6%BD%AE%E8%AE%BE%E8%AE%A1)

## 15. 后续阶段关系

- Phase 3 可把章节生成、伏笔检查等固定流程从 harness 中剥离为 workflow；workflow 内部仍可调用 skill。
- Phase 4 的 `ProjectExperience` 可复用 `SkillRagEmbedding` 的向量检索模式。

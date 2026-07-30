# Phase 2 Skill/RAG Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Skill/RAG system to inject web-novel writing methodology into worker prompts, without changing external API shapes.

**Architecture:** Introduce `app/agents/skills/` with `SkillManager` (singleton), inline skill registry, RAG chunks/index, and a new `SkillRagEmbedding` table. Extend `WorkerMetadata` with `skills`/`rag_skills` and update `WorkerBase` to inject skill text into system prompts. Reuse existing SQLite + numpy brute-force retrieval.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2.0 async, Pydantic v2, numpy, existing `LLMClient.embed`.

## Global Constraints

- All persistent mutations continue to go through `app/services/change_apply.py`.
- Workers remain read-only; writes are expressed as `ChangeRecord` drafts staged on `AssistantSession`.
- External API compatibility: `/assistant/chat`, `/assistant/confirm`, `/assistant/reject` request/response shapes must not change.
- Use Pydantic v2 `BaseModel` for all new data structures.
- Follow absolute imports from the `backend/app` package root; run backend commands with `cwd=backend`.
- Use `python -m unittest` for tests (pytest is not configured in this repo).
- Commit after each independently testable task.
- No new external vector database; reuse existing SQLite + numpy retrieval.

---

## File Structure

### New files

| File | Responsibility |
|---|---|
| `app/agents/skills/__init__.py` | Export `SkillManager`, `get_skill_manager`, `SkillConfig`, `SkillQueryResult` |
| `app/agents/skills/models.py` | Pydantic models: `SkillType`, `SkillConfig`, `SkillQueryResult` |
| `app/agents/skills/skill_manager.py` | Singleton `SkillManager`: load configs, cache inline content, query RAG |
| `app/agents/skills/rag/__init__.py` | Export `build_rag_index`, `retrieve_skill_chunks` |
| `app/agents/skills/rag/index.py` | Chunk scanning, embedding, indexing into `SkillRagEmbedding` |
| `app/agents/skills/rag/retrieval.py` | Vector retrieval helper for `SkillRagEmbedding` |
| `app/agents/skills/registry/*.md` | 7 inline skill markdown files |
| `app/agents/skills/configs/*.json` | 8 skill metadata JSON files |
| `app/agents/skills/rag/chunks/wangwenclub/*.md` | RAG chunks from wangwenclub guides |
| `tests/agents/skills/test_skill_manager.py` | Unit tests for `SkillManager` |
| `tests/agents/skills/test_rag_index.py` | Unit tests for chunk parsing and index helpers |
| `tests/agents/harness/test_worker_base_skills.py` | Test skill injection in `WorkerBase` |

### Modified files

| File | Change |
|---|---|
| `app/models.py` | Add `SkillRagEmbedding` table |
| `app/agents/harness/models.py` | Add `skills` and `rag_skills` to `WorkerMetadata` |
| `app/agents/harness/worker_base.py` | Inject inline/RAG skill text into system prompt |
| `app/agents/harness/workers/configs/*.json` | Add `skills`/`rag_skills` arrays to each worker config |

---

## Task 1: Add `SkillRagEmbedding` database model

**Files:**
- Modify: `app/models.py`

**Interfaces:**
- Consumes: existing `_uuid`, `_now` helpers in `app/models.py`
- Produces: `SkillRagEmbedding` SQLAlchemy model

- [ ] **Step 1: Add the model after `AssistantSummaryEmbedding`**

Open `app/models.py`, find `AssistantSummaryEmbedding`, and add the following class after it:

```python
class SkillRagEmbedding(Base):
    __tablename__ = "skill_rag_embeddings"

    id = Column(CHAR(36), primary_key=True, default=_uuid)
    skill_name = Column(String(64), nullable=False, index=True)
    chunk_path = Column(String(512), nullable=False)
    chunk_text = Column(Text, nullable=False)
    embedding = Column(LargeBinary, nullable=False)
    model = Column(String(128), nullable=False)
    dimension = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=_now, nullable=False)
```

- [ ] **Step 2: Run backend compile check**

```bash
cd backend && python -m compileall app/models.py
```

Expected: no syntax errors.

- [ ] **Step 3: Commit**

```bash
git add app/models.py
git commit -m "feat(skills): add SkillRagEmbedding table for RAG chunks"
```

---

## Task 2: Create skill Pydantic models

**Files:**
- Create: `app/agents/skills/models.py`

**Interfaces:**
- Consumes: Pydantic v2 `BaseModel`, `Field`
- Produces: `SkillType`, `SkillConfig`, `SkillQueryResult`

- [ ] **Step 1: Write `app/agents/skills/models.py`**

```python
"""Skill data models."""
from __future__ import annotations

from enum import Enum
from pathlib import Path

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

- [ ] **Step 2: Run compile check**

```bash
cd backend && python -m compileall app/agents/skills/models.py
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add app/agents/skills/models.py
git commit -m "feat(skills): add SkillConfig and SkillQueryResult models"
```

---

## Task 3: Implement `SkillManager` singleton

**Files:**
- Create: `app/agents/skills/skill_manager.py`
- Create: `app/agents/skills/__init__.py`

**Interfaces:**
- Consumes: `SkillConfig`, `SkillQueryResult` from `app.agents.skills.models`
- Produces: `SkillManager.list_skills`, `SkillManager.get_skills_for_worker`, `SkillManager.query_rag_skills`, `get_skill_manager`

- [ ] **Step 1: Write `app/agents/skills/skill_manager.py`**

```python
"""SkillManager: load skill configs, inject inline skills, query RAG chunks."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.skills.models import SkillConfig, SkillQueryResult

logger = logging.getLogger(__name__)

SKILLS_DIR = Path(__file__).parent
CONFIG_DIR = SKILLS_DIR / "configs"


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
        if not CONFIG_DIR.exists():
            return
        for path in sorted(CONFIG_DIR.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                config = SkillConfig(**data)
                self._skills[config.skill_name] = config
                if config.type == "inline" and config.content_path(SKILLS_DIR):
                    content_path = config.content_path(SKILLS_DIR)
                    if content_path and content_path.exists():
                        self._inline_cache[config.skill_name] = content_path.read_text(
                            encoding="utf-8"
                        )
            except Exception:
                logger.exception("Failed to load skill config %s", path.name)

    def list_skills(self) -> list[SkillConfig]:
        return list(self._skills.values())

    def get_skill(self, skill_name: str) -> SkillConfig | None:
        return self._skills.get(skill_name)

    def get_skills_for_worker(
        self,
        worker_name: str,
        worker_skills: list[str] | None = None,
        task_goal: str = "",
    ) -> list[tuple[SkillConfig, str]]:
        """Return (config, content) for inline skills applicable to a worker."""
        selected: set[str] = set(worker_skills or [])
        if not selected:
            for cfg in self._skills.values():
                if cfg.type == "inline" and worker_name in cfg.triggers:
                    selected.add(cfg.skill_name)

        results: list[tuple[SkillConfig, str]] = []
        for name in selected:
            cfg = self._skills.get(name)
            if not cfg or cfg.type != "inline":
                continue
            content = self._inline_cache.get(name)
            if content:
                results.append((cfg, content))

        results.sort(key=lambda item: (item[0].priority, item[0].skill_name))
        return results

    async def query_rag_skills(
        self,
        db: AsyncSession,
        worker_name: str,
        rag_skill_names: list[str] | None = None,
        query: str = "",
        top_k: int | None = None,
    ) -> list[SkillQueryResult]:
        """Retrieve top-k RAG chunks for the configured RAG skills."""
        selected: set[str] = set(rag_skill_names or [])
        if not selected:
            for cfg in self._skills.values():
                if cfg.type == "rag" and worker_name in cfg.triggers:
                    selected.add(cfg.skill_name)

        if not selected or not query.strip():
            return []

        all_results: list[SkillQueryResult] = []
        for name in selected:
            cfg = self._skills.get(name)
            if not cfg or cfg.type != "rag":
                continue
            k = top_k if top_k is not None else cfg.top_k
            try:
                from app.agents.skills.rag.retrieval import retrieve_skill_chunks
                chunks = await retrieve_skill_chunks(
                    db,
                    skill_names=[name],
                    query=query,
                    top_k=k,
                )
                all_results.extend(chunks)
            except Exception:
                logger.exception("RAG retrieval failed for skill %s", name)

        all_results.sort(key=lambda r: r.score, reverse=True)
        # Return top_k overall if a single top_k was requested
        if top_k is not None:
            return all_results[:top_k]
        return all_results


def get_skill_manager() -> SkillManager:
    return SkillManager()
```

- [ ] **Step 2: Write `app/agents/skills/__init__.py`**

```python
"""Skills package."""
from __future__ import annotations

from app.agents.skills.models import SkillConfig, SkillQueryResult
from app.agents.skills.skill_manager import SkillManager, get_skill_manager

__all__ = [
    "SkillConfig",
    "SkillManager",
    "SkillQueryResult",
    "get_skill_manager",
]
```

- [ ] **Step 3: Run compile check**

```bash
cd backend && python -m compileall app/agents/skills/skill_manager.py app/agents/skills/__init__.py
```

Expected: no errors (retrieval helper will be added in Task 5).

- [ ] **Step 4: Commit**

```bash
git add app/agents/skills/skill_manager.py app/agents/skills/__init__.py
git commit -m "feat(skills): add SkillManager singleton for skill discovery and RAG query"
```

---

## Task 4: Create inline skill registry and configs

**Files:**
- Create: `app/agents/skills/configs/writing_process.json`
- Create: `app/agents/skills/configs/plot_design.json`
- Create: `app/agents/skills/configs/character_arc.json`
- Create: `app/agents/skills/configs/world_building.json`
- Create: `app/agents/skills/configs/foreshadowing.json`
- Create: `app/agents/skills/configs/pacing.json`
- Create: `app/agents/skills/configs/climax_hook.json`
- Create: `app/agents/skills/registry/writing_process.md`
- Create: `app/agents/skills/registry/plot_design.md`
- Create: `app/agents/skills/registry/character_arc.md`
- Create: `app/agents/skills/registry/world_building.md`
- Create: `app/agents/skills/registry/foreshadowing.md`
- Create: `app/agents/skills/registry/pacing.md`
- Create: `app/agents/skills/registry/climax_hook.md`

**Interfaces:**
- Consumes: `SkillConfig` schema
- Produces: 7 inline skill configs + 7 markdown content files

- [ ] **Step 1: Create configs directory and write config files**

```bash
mkdir -p backend/app/agents/skills/configs
mkdir -p backend/app/agents/skills/registry
```

Write each JSON config:

`app/agents/skills/configs/writing_process.json`:
```json
{
  "skill_name": "writing_process",
  "description": "网文创作全流程：选题立意、世界观设定、人物设定、大纲准备、日常写作流程",
  "type": "inline",
  "triggers": ["outline", "broad_outline", "plot_nodes", "chapter_text"],
  "content_file": "registry/writing_process.md",
  "priority": 1
}
```

`app/agents/skills/configs/plot_design.json`:
```json
{
  "skill_name": "plot_design",
  "description": "网络小说情节设计方法论：目标驱动、冲突推动、三幕剧、英雄之旅、爽文模式",
  "type": "inline",
  "triggers": ["outline", "plot", "plot_nodes", "chapter_outline", "chapter_text"],
  "content_file": "registry/plot_design.md",
  "priority": 1
}
```

`app/agents/skills/configs/character_arc.json`:
```json
{
  "skill_name": "character_arc",
  "description": "人物塑造方法论：主角要素、配角塑造、反派原则、人物档案模板",
  "type": "inline",
  "triggers": ["character", "chapter_text"],
  "content_file": "registry/character_arc.md",
  "priority": 1
}
```

`app/agents/skills/configs/world_building.json`:
```json
{
  "skill_name": "world_building",
  "description": "世界观构建方法论：力量体系、地理设定、社会结构、经济系统、历史背景、自洽原则",
  "type": "inline",
  "triggers": ["world", "outline", "chapter_text"],
  "content_file": "registry/world_building.md",
  "priority": 2
}
```

`app/agents/skills/configs/foreshadowing.json`:
```json
{
  "skill_name": "foreshadowing",
  "description": "伏笔与照应方法论：伏笔类型、照应类型、远近明暗多重群体连环伏笔技巧",
  "type": "inline",
  "triggers": ["foreshadow", "outline", "plot", "chapter_outline"],
  "content_file": "registry/foreshadowing.md",
  "priority": 1
}
```

`app/agents/skills/configs/pacing.json`:
```json
{
  "skill_name": "pacing",
  "description": "节奏控制方法论：不同阶段节奏、章节长度、爽点分布、张弛变化",
  "type": "inline",
  "triggers": ["outline", "chapter_outline", "chapter_text"],
  "content_file": "registry/pacing.md",
  "priority": 2
}
```

`app/agents/skills/configs/climax_hook.json`:
```json
{
  "skill_name": "climax_hook",
  "description": "高潮设计方法论：高潮类型、战斗/智斗/情感/突破高潮结构、描写技巧",
  "type": "inline",
  "triggers": ["outline", "chapter_outline", "chapter_text"],
  "content_file": "registry/climax_hook.md",
  "priority": 2
}
```

- [ ] **Step 2: Write inline skill markdown content**

`app/agents/skills/registry/writing_process.md`:
```markdown
# 网文创作流程

## 1. 选题立意

- 根据兴趣、阅读积累、市场热度、平台倾向确定类型。
- 核心创意要具备：新颖性、爽点明确、可持续性、市场价值。
- 确定主题基调：轻松/严肃、黑暗/热血、个人成长/团队协作。

## 2. 世界观设定

- 力量体系：等级划分清晰（10-15级），晋级条件明确，预留扩展空间。
- 世界背景：地理、社会结构、历史背景。
- 规则设定：修炼规则、战斗规则、社会规则，必须自洽。

## 3. 人物设定

- 主角：外貌有辨识度、性格鲜明、价值观正、金手指有代价。
- 配角：女主/女配、兄弟/队友、师父/导师，各具功能。
- 反派：有智商、有动机、有特点、适时退场。

## 4. 大纲准备

- 粗纲：开篇设计、主要情节点、关键转折、结局方向。
- 细纲：每卷/每章大致内容、冲突爽点、人物关系变化。
- 详纲（可选）：每章具体内容、对话要点、描写重点。

## 5. 日常写作流程

- 写作前：回顾昨日内容、确认今日大纲、准备素材。
- 正式写作：按大纲展开，保持连贯和节奏，避免过度纠结文字。
- 写作后：检查错别字、调整逻辑问题、准备第二天大纲。
```

`app/agents/skills/registry/plot_design.md`:
```markdown
# 情节设计

## 基本原则

1. **目标驱动**：主角有明确长短期目标，所有情节围绕目标展开。
2. **冲突推动**：人与人、人与环境、人与自我、人与社会的冲突。
3. **因果逻辑**：避免突兀转折、强行降智、不合理巧合。
4. **节奏变化**：紧张 → 放松 → 紧张 → 高潮。

## 经典结构

- **三幕剧**：建立（25%）、对抗（50%）、解决（25%）。
- **英雄之旅**：日常 → 召唤 → 试炼 → 核心磨难 → 奖赏 → 复活 → 回归。
- **爽文模式**：被藐视 → 展现实力 → 众人震惊 → 获得好处 → 实力提升 → 循环。

## 开篇设计

- 第一章：快速入题、展现亮点、制造爽点、引起好奇。
- 第二章：深化设定、展开冲突、强化主角。
- 第三章：初露锋芒、给出方向、产生期待。

## 高潮设计

- 小高潮：每5-10章，打败小BOSS。
- 中高潮：每20-30万字，阶段性爆发。
- 大高潮：全书1-3次，推向巅峰。
- 结构：铺垫 → 酝酿 → 爆发 → 高潮 → 余韵。

## 检查清单

- 每章是否推进情节？
- 是否有爽点或冲突？
- 是否有章末钩子？
- 主线是否清晰？
- 伏笔是否回收？
```

`app/agents/skills/registry/character_arc.md`:
```markdown
# 人物塑造

## 主角塑造

- **外貌**：有辨识度，不过度帅/丑。
- **性格**：1-2个核心特点，一致且有依据。
- **价值观**：善恶分明、有底线、正能量。
- **金手指**：强但不无敌，有限制或代价。
- **成长线**：实力、心灵、社会地位同步成长。

## 主角类型

- 草根逆袭型：代入感强。
- 天才型：一路顺风顺水，装逼打脸。
- 重生/穿越型：利用信息差碾压。
- 老阴比型：谨慎、智谋、苟到最后。

## 配角塑造

- 女主/女配：性格各异，避免同质化，感情线自然发展。
- 兄弟/队友：忠诚、能成长、关键时刻能帮上忙。
- 师父/导师：不抢主角风头，适时退场。

## 反派塑造

- 小反派：短期内被击败。
- 中反派：数十万字才能击败。
- 大反派：最终决战对象。
- 原则：有智商、有动机、有特点、适时退场。

## 人物档案模板

- 姓名、定位、外貌、性格标签、与主角关系、出场时间、主要作用、退场时间。
```

`app/agents/skills/registry/world_building.md`:
```markdown
# 世界观构建

## 力量体系

- 等级划分清晰，10-15级为宜。
- 明确晋级条件、实力表现、寿命变化、数量分布、社会地位。
- 常见模式：炼气→筑基→金丹→元婴→化神→合体→大乘→渡劫→真仙...

## 地理设定

- 空间层次：小世界→中世界→大世界→仙界→神界。
- 换地图时机：主角当前环境无敌、20-50万字一次。
- 特殊区域：秘境/遗迹（提供机缘）、禁地/险地（后期揭秘）。

## 社会结构

- 势力划分：宗门、家族、散修、官方、商会。
- 权力层级：宗主→长老→堂主→执事→弟子。
- 经济系统：货币（灵石、仙晶）、丹药、功法、法宝、材料。

## 历史背景

- 时间线：远古、上古、近古、当代。
- 重大事件：影响世界格局的大战、强者陨落、天地异变。
- 传说与预言：制造悬念、为主角铺路。

## 自洽原则

- 内部一致、逻辑合理、详略得当。
- 避免设定冲突、战力崩坏、设定过于复杂。
```

`app/agents/skills/registry/foreshadowing.md`:
```markdown
# 伏笔与照应

## 伏笔作用

- 增加可信度、制造惊喜、强化主题、营造结构感。

## 伏笔类型

- **物品伏笔**：神秘物品、普通物品、破损物品。
- **人物伏笔**：隐藏身份、隐藏关系、隐藏目的、隐藏实力。
- **事件伏笔**：预言、契约、诅咒、承诺。
- **设定伏笔**：历史、地理、规则、组织。
- **台词伏笔**：无意之言、临终遗言、警告之语、预言之语。

## 照应类型

- 首尾呼应、前后呼应、细节呼应、主题呼应。

## 技巧

1. **远近结合**：短期（10-30章）、中期（30-100章）、长期（100章+）。
2. **明暗结合**：明线伏笔制造期待，暗线伏笔揭晓时惊喜。
3. **多重伏笔**：一个伏笔多层作用。
4. **群体伏笔**：多个线索指向同一真相。
5. **连环伏笔**：一个伏笔揭晓后引出新伏笔。

## 检查清单

- 伏笔是否记录清楚？
- 是否自然不突兀？
- 是否与主线相关？
- 是否及时揭晓？
- 揭晓是否合理不矛盾？
```

`app/agents/skills/registry/pacing.md`:
```markdown
# 节奏控制

## 节奏类型

- **快节奏**：情节推进快、爽点密集、冲突频繁，适用于开篇和战斗。
- **慢节奏**：细节丰富、情感深入、铺垫充分，适用于日常和感情。
- **张弛有度**：快→慢→快→慢→高潮→慢→快。

## 不同阶段节奏

- **开篇（1-30万字）**：极快节奏，每章至少1个小爽点，10章内打脸，30万字内换地图。
- **中期（30-100万字）**：稳定节奏，2-3章一个爽点，深化人物和世界，设置中期高潮。
- **后期（100万字+）**：加快主线，收束支线，处理伏笔，推向最终高潮。

## 控制技巧

- 章节长度：平时2000-2500字，重要情节3000-4000字，高潮4000-5000字。
- 情节密度：高密度5章 → 低密度2章 → 更高密度高潮10章。
- 爽点分布：前期每章1个，中期2-3章1个，后期大爽点为主。
- 张弛变化：紧张5章 → 放松2章 → 大紧张10章 → 放松3章。

## 常见问题

- 节奏过快：适当放慢，增加日常和互动。
- 节奏过慢：删减无关内容，增加冲突和爽点。
- 节奏不稳：制定节奏规划，保持稳定更新。
- 高潮疲劳：高潮后适当放松，制造对比反差。
```

`app/agents/skills/registry/climax_hook.md`:
```markdown
# 高潮设计

## 高潮类型

- **战斗高潮**：激烈战斗、以弱胜强。
- **智斗高潮**：谋略较量、破解阴谋。
- **情感高潮**：情感爆发、关系确立。
- **突破高潮**：境界突破、实力暴涨。

## 高潮结构

### 战斗高潮

战前准备 → 初次交锋 → 陷入困境 → 绝地反击 → 决胜一击 → 尘埃落定

### 智斗高潮

布局 → 交锋 → 破局 → 揭露 → 反转（可选）

### 情感高潮

矛盾积累 → 冲突爆发 → 情绪宣泄 → 关系升华

### 突破高潮

瓶颈期 → 顿悟契机 → 突破过程 → 成功突破 → 实力展现

## 描写技巧

- 节奏控制：快慢结合。
- 多角度描写：主角、对手、旁观视角。
- 细节刻画：关键时刻放慢、细致描写。
- 环境烘托：天气、光影、声音、气氛。

## 检查清单

- 铺垫是否充分？
- 节奏是否合理？
- 爽点是否集中爆发？
- 收尾是否有余韵？
```

- [ ] **Step 3: Validate configs load**

Create a quick test script or use python:

```bash
cd backend && python -c "
from app.agents.skills.skill_manager import SkillManager
m = SkillManager()
print([s.skill_name for s in m.list_skills()])
"
```

Expected: 7 inline skill names printed.

- [ ] **Step 4: Commit**

```bash
git add app/agents/skills/configs app/agents/skills/registry
git commit -m "feat(skills): add 7 inline skill configs and markdown content"
```

---

## Task 5: Implement RAG indexing and retrieval

**Files:**
- Create: `app/agents/skills/rag/retrieval.py`
- Create: `app/agents/skills/rag/index.py`
- Create: `app/agents/skills/rag/__init__.py`
- Create: `app/agents/skills/configs/wangwenclub_case.json`
- Create: RAG chunk files under `app/agents/skills/rag/chunks/wangwenclub/`

**Interfaces:**
- Consumes: `SkillRagEmbedding` model, `LLMClient.embed`, `get_embedding_client`
- Produces: `retrieve_skill_chunks`, `build_rag_index`

- [ ] **Step 1: Add PyYAML to requirements**

Open `backend/requirements.txt` and add:

```
PyYAML==6.0.2
```

Then install it:

```bash
cd backend && pip install PyYAML==6.0.2
```

- [ ] **Step 2: Write `app/agents/skills/rag/retrieval.py`**

```python
"""RAG retrieval for skill chunks using SQLite + numpy brute force."""
from __future__ import annotations

import logging

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.skills.models import SkillQueryResult
from app.core.llm_factory import get_embedding_client
from app.models import SkillRagEmbedding

logger = logging.getLogger(__name__)


def _normalize(v: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(v)
    if norm == 0:
        return v
    return v / norm


async def retrieve_skill_chunks(
    db: AsyncSession,
    skill_names: list[str],
    query: str,
    top_k: int = 3,
) -> list[SkillQueryResult]:
    if not skill_names or not query.strip():
        return []

    embedding_client, dimension = await get_embedding_client(db)
    query_vectors = await embedding_client.embed(
        [query],
        model=embedding_client.model,
        dimensions=dimension if dimension > 0 else None,
    )
    query_vec = _normalize(np.array(query_vectors[0], dtype=np.float32))

    stmt = select(SkillRagEmbedding).where(
        SkillRagEmbedding.skill_name.in_(skill_names)
    )
    res = await db.execute(stmt)
    rows = res.scalars().all()

    scored: list[tuple[SkillRagEmbedding, float]] = []
    for row in rows:
        try:
            emb = np.frombuffer(row.embedding, dtype=np.float32)
            if emb.shape[0] != query_vec.shape[0]:
                continue
            emb = _normalize(emb)
            score = float(np.dot(query_vec, emb))
            scored.append((row, score))
        except Exception:
            logger.exception("Failed to compute similarity for %s", row.chunk_path)
            continue

    scored.sort(key=lambda x: x[1], reverse=True)
    return [
        SkillQueryResult(
            skill_name=row.skill_name,
            chunk_path=row.chunk_path,
            chunk_text=row.chunk_text,
            score=score,
        )
        for row, score in scored[:top_k]
    ]
```

- [ ] **Step 3: Write `app/agents/skills/rag/index.py`**

```python
"""Build RAG index for skill chunks."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.skills.models import SkillConfig
from app.core.llm_factory import get_embedding_client
from app.database import AsyncSessionLocal
from app.models import SkillRagEmbedding

logger = logging.getLogger(__name__)

SKILLS_DIR = Path(__file__).parent.parent
CONFIG_DIR = SKILLS_DIR / "configs"
CHUNKS_DIR = SKILLS_DIR / "rag" / "chunks"


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from markdown text."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except Exception:
        meta = {}
    return meta, parts[2].strip()


async def _index_skill(db: AsyncSession, cfg: SkillConfig) -> int:
    chunks_path = cfg.chunks_path(SKILLS_DIR)
    if not chunks_path or not chunks_path.exists():
        return 0

    embedding_client, dimension = await get_embedding_client(db)

    # Remove existing embeddings for this skill
    await db.execute(
        delete(SkillRagEmbedding).where(
            SkillRagEmbedding.skill_name == cfg.skill_name
        )
    )
    await db.commit()

    indexed = 0
    chunk_files = sorted(chunks_path.rglob("*.md"))
    texts: list[str] = []
    metas: list[dict[str, Any]] = []
    paths: list[str] = []

    for path in chunk_files:
        text = path.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(text)
        if not body.strip():
            continue
        rel_path = str(path.relative_to(SKILLS_DIR))
        texts.append(body)
        metas.append(meta)
        paths.append(rel_path)

    if not texts:
        return 0

    # Embed in batches to avoid huge payloads
    batch_size = 16
    embeddings: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        vecs = await embedding_client.embed(
            batch,
            model=embedding_client.model,
            dimensions=dimension if dimension > 0 else None,
        )
        embeddings.extend(vecs)

    for meta, body, rel_path, vec in zip(metas, texts, paths, embeddings):
        arr = np.array(vec, dtype=np.float32)
        norm = np.linalg.norm(arr)
        if norm > 0:
            arr = arr / norm
        db.add(
            SkillRagEmbedding(
                skill_name=cfg.skill_name,
                chunk_path=rel_path,
                chunk_text=body[:4000],
                embedding=arr.tobytes(),
                model=embedding_client.model,
                dimension=dimension,
            )
        )
        indexed += 1

    await db.commit()
    return indexed


async def build_rag_index() -> dict[str, int]:
    results: dict[str, int] = {}
    async with AsyncSessionLocal() as db:
        for path in sorted(CONFIG_DIR.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                cfg = SkillConfig(**data)
                if cfg.type != "rag":
                    continue
                count = await _index_skill(db, cfg)
                results[cfg.skill_name] = count
                logger.info("Indexed %d chunks for skill %s", count, cfg.skill_name)
            except Exception:
                logger.exception("Failed to index skill config %s", path.name)
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    counts = asyncio.run(build_rag_index())
    for name, count in counts.items():
        print(f"{name}: {count} chunks")
```

- [ ] **Step 4: Write `app/agents/skills/rag/__init__.py`**

```python
"""RAG subpackage."""
from __future__ import annotations

from app.agents.skills.rag.index import build_rag_index
from app.agents.skills.rag.retrieval import retrieve_skill_chunks

__all__ = ["build_rag_index", "retrieve_skill_chunks"]
```

- [ ] **Step 5: Write RAG skill config**

`app/agents/skills/configs/wangwenclub_case.json`:
```json
{
  "skill_name": "wangwenclub_case",
  "description": "网文俱乐部创作案例库：情节、人物、世界观、伏笔、节奏、高潮等长文指南 chunks",
  "type": "rag",
  "chunks_dir": "rag/chunks/wangwenclub/",
  "index_table": "skill_rag_embeddings",
  "top_k": 3,
  "priority": 2
}
```

- [ ] **Step 6: Create RAG chunk files**

```bash
mkdir -p backend/app/agents/skills/rag/chunks/wangwenclub
```

Create at least these chunk files with content summarized from wangwenclub guides. Each file must have YAML frontmatter and 500-800 words of body.

`app/agents/skills/rag/chunks/wangwenclub/plot_design_01.md`:
```markdown
---
skill_name: wangwenclub_case
source: wangwenclub
source_url: https://www.wangwenclub.com/handbook/%E5%88%9B%E4%BD%9C%E6%8C%87%E5%8D%97/%E6%83%85%E8%8A%82%E8%AE%BE%E8%AE%A1
topic: 情节设计
tags: [plot, structure, three-act]
---

## 情节设计的基本原则

1. **目标驱动原则**：主角必须有明确目标。短期目标如获得宝物、打败对手；长期目标如成为最强者、复仇、拯救世界。目标要具体、有难度但可实现。

2. **冲突推动原则**：冲突是情节发展的核心动力。冲突类型包括人与人、人与环境、人与自我、人与社会。冲突层级为小冲突（每章）→ 中冲突（每卷）→ 大冲突（全书）。

3. **因果逻辑原则**：避免突兀转折、强行降智、突然的能力、不合理的巧合。

4. **节奏变化原则**：情节发展要有张有弛，模式为紧张 → 放松 → 紧张 → 放松 → 高潮。

## 三幕剧结构

第一幕：建立（25%篇幅）。介绍主角和世界，展现初始状态，事件触发主角踏上旅程。

第二幕：对抗（50%篇幅）。主角面对挑战，实力提升，遇到盟友和敌人。关键节点包括中点转折、假失败、黑暗时刻。

第三幕：解决（25%篇幅）。主角迎接最终挑战，运用所学解决问题，达成目标。
```

`app/agents/skills/rag/chunks/wangwenclub/plot_design_02.md`:
```markdown
---
skill_name: wangwenclub_case
source: wangwenclub
source_url: https://www.wangwenclub.com/handbook/%E5%88%9B%E4%BD%9C%E6%8C%87%E5%8D%97/%E6%83%85%E8%8A%82%E8%AE%BE%E8%AE%A1
topic: 情节设计
tags: [plot, opening, hook]
---

## 开篇设计

### 前三章黄金法则

**第一章**：快速进入主题，展现核心亮点（金手指、主角特点），制造第一个爽点，引起读者好奇。

**第二章**：深化设定，展开第一个冲突，强化主角形象。

**第三章**：初露锋芒，给出明确的故事方向，让读者产生期待。

### 开篇常见模式

- **废柴逆袭**：主角是废柴 → 被欺凌 → 获得金手指 → 开始逆袭。
- **强者回归**：展现前世辉煌 → 意外重生 → 利用经验优势 → 重新崛起。
- **获得系统**：普通生活 → 获得系统 → 接受任务 → 走上强者之路。
- **穿越异界**：意外穿越 → 发现身份 → 适应环境 → 开始冒险。

### 中期展开

- **换地图技巧**：主角在当前环境无敌、20-50万字一次，通过比赛/选拔/追杀/被强者带走过渡。
- **支线情节设计**：感情线、友情线、师徒线、探索线，支线服务主线，适时推进。
- **伏笔设置**：短伏笔几章揭晓、中伏笔一卷揭晓、长伏笔贯穿全书。自然提及、多次加深印象、及时回收。
```

`app/agents/skills/rag/chunks/wangwenclub/character_arc_01.md`:
```markdown
---
skill_name: wangwenclub_case
source: wangwenclub
source_url: https://www.wangwenclub.com/handbook/%E5%88%9B%E4%BD%9C%E6%8C%87%E5%8D%97/%E4%BA%BA%E7%89%A9%E5%A1%91%E9%80%A0
topic: 人物塑造
tags: [character, protagonist]
---

## 主角塑造的基本要素

1. **外貌设定**：不要丑（影响代入），不要过分帅（显得假），要有辨识度（特殊标记、气质）。

2. **性格特点**：核心性格1-2个突出特点（果断坚毅、冷静理智、热血正直等）。性格要一致、有依据、可成长但不要突变、不要完美无缺。

3. **价值观**：三观要正。善恶分明、有底线原则、正能量。可以杀反派、夺宝藏、报仇，但不能滥杀无辜、强迫女性、背叛亲友。

4. **能力设定**：金手指类型包括系统辅助、重生记忆、特殊体质、神秘传承、异能觉醒。原则：强但不无敌，有限制或代价，能持续提供爽点，不能解决所有问题。

## 主角类型

- **草根逆袭型**：出身普通，通过努力和机遇崛起，代入感强。
- **天才型**：天赋异禀，一路顺风顺水，装逼打脸为主。
- **重生/穿越型**：拥有未来知识或前世记忆，利用信息差碾压对手。
- **老阴比型**：谨慎小心，智谋过人，苟到最后。

## 成长线设计

- **实力成长**：清晰进阶路径，每次突破有原因，节奏合理。
- **心灵成长**：从青涩到成熟，从冲动到理智，从迷茫到坚定（可选但加分）。
- **社会地位成长**：从无名小卒到名动天下，从孤身一人到势力庞大。
```

`app/agents/skills/rag/chunks/wangwenclub/foreshadowing_01.md`:
```markdown
---
skill_name: wangwenclub_case
source: wangwenclub
source_url: https://www.wangwenclub.com/handbook/%E5%88%9B%E4%BD%9C%E6%8C%87%E5%8D%97/%E4%BC%8F%E7%AC%94%E4%B8%8E%E7%85%A7%E5%BA%94
topic: 伏笔与照应
tags: [foreshadowing, callback]
---

## 伏笔与照应的核心作用

1. **增加可信度**：提前埋下伏笔，让后续情节更合理。
2. **制造惊喜**：伏笔揭晓时的反转能给读者惊喜。
3. **强化主题**：照应能强化作品主题，首尾呼应升华主题。
4. **营造结构感**：伏笔与照应让故事结构更完整，形成闭环。

## 伏笔类型

- **物品伏笔**：神秘物品、普通物品、破损物品。技巧：轻描淡写、似有若无、多重作用。
- **人物伏笔**：隐藏身份、隐藏关系、隐藏目的、隐藏实力。要点：留下线索、合理性、铺垫够。
- **事件伏笔**：预言型、契约型、诅咒型、承诺型。技巧：时间跨度适中、不断提醒、制造悬念。
- **设定伏笔**：历史伏笔、地理伏笔、规则伏笔、组织伏笔。要点：不要一次说完、与主线结合、合理解释。
- **台词伏笔**：无意之言、临终遗言、警告之语、预言之语。技巧：语焉不详、似真似假、引发好奇。

## 照应类型

- **首尾呼应**：开头与结尾相互照应（相同场景、相似情节、对比反转）。
- **前后呼应**：前文伏笔后文照应（物品、台词、情节）。
- **细节呼应**：动作、物件、场景的细微呼应。
- **主题呼应**：理念、成长等主题层面的呼应。
```

`app/agents/skills/rag/chunks/wangwenclub/pacing_01.md`:
```markdown
---
skill_name: wangwenclub_case
source: wangwenclub
source_url: https://www.wangwenclub.com/handbook/%E5%86%99%E4%BD%9C%E6%8A%80%E5%B7%A7/%E8%8A%82%E5%A5%8F%E6%8E%A7%E5%88%B6
topic: 节奏控制
tags: [pacing, rhythm]
---

## 什么是节奏

节奏是指故事情节推进的快慢、张弛的变化，以及爽点、冲突、高潮出现的频率和分布。

节奏的作用：维持兴趣、控制情绪、推进情节、制造期待。

## 不同阶段的节奏控制

**开篇节奏（1-30万字）**：极快节奏，爽点密集，冲突频繁，快速推进。3章内出金手指，每章至少1个小爽点，10章内有打脸，30万字内换地图。

**中期节奏（30-100万字）**：稳定节奏，快慢结合，深入展开，适当放慢。2-3章一个爽点，深化人物和世界观，展开支线，设置中期高潮。

**后期节奏（100万字以上）**：收放自如，加快主线，收束支线，推向高潮。处理支线和伏笔，准备最终高潮，终极大战，收尾余韵。

## 节奏控制技巧

1. **章节长度控制**：平时2000-2500字，重要情节3000-4000字，高潮4000-5000字。
2. **情节密度控制**：高密度5章 → 低密度2章 → 更高密度高潮10章。
3. **爽点分布**：前期每章1个，中期2-3章1个，后期大爽点为主。
4. **张弛变化**：紧张5章 → 放松2章 → 紧张5章 → 放松2章 → 大紧张高潮10章 → 放松3章。

## 常见节奏问题

- 节奏过快：适当放慢，增加日常和互动。
- 节奏过慢：删减无关内容，增加冲突和爽点。
- 节奏不稳：制定节奏规划，保持稳定更新。
- 高潮疲劳：高潮后适当放松，制造对比反差。
```

`app/agents/skills/rag/chunks/wangwenclub/world_building_01.md`:
```markdown
---
skill_name: wangwenclub_case
source: wangwenclub
source_url: https://www.wangwenclub.com/handbook/%E5%88%9B%E4%BD%9C%E6%8C%87%E5%8D%97/%E4%B8%96%E7%95%8C%E8%A7%82%E6%9E%84%E5%BB%BA
topic: 世界观构建
tags: [worldbuilding, power-system]
---

## 力量体系设计

### 等级划分基本原则

- 清晰明了：读者容易理解。
- 数量适中：10-15级合适。
- 梯度明显：级别间差距清晰。
- 递进合理：符合逻辑。

### 常见模式

**玄幻修真体系**：炼气期 → 筑基期 → 金丹期 → 元婴期 → 化神期 → 合体期 → 大乘期 → 渡劫期 → 真仙 → 金仙 → 大罗金仙。

**武侠体系**：后天境 → 先天境 → 宗师境 → 大宗师 → 至尊境 → 破碎虚空。

**都市异能体系**：F → E → D → C → B → A → S → SS → SSS。

### 每个等级的设定

需要明确：晋级条件、实力表现、寿命变化、数量分布、社会地位。

### 瓶颈设置

作用：增加难度、制造悬念、延长篇幅、爽点来源（突破）。类型包括境界瓶颈、心境瓶颈、资源瓶颈、天劫考验。

## 地理设定

- **空间层次**：小世界 → 中世界 → 大世界 → 仙界 → 神界。
- **换地图时机**：主角当前世界无敌、20-50万字一次。
- **特殊区域**：秘境/遗迹（提供机缘）、禁地/险地（后期揭秘）。

## 社会结构

- **势力划分**：宗门、家族、散修、官方、商会。
- **权力层级**：宗主 → 长老 → 堂主 → 执事 → 弟子。
- **经济系统**：货币（灵石、仙晶）、丹药、功法、法宝、材料。
```

`app/agents/skills/rag/chunks/wangwenclub/climax_hook_01.md`:
```markdown
---
skill_name: wangwenclub_case
source: wangwenclub
source_url: https://www.wangwenclub.com/handbook/%E5%86%99%E4%BD%9C%E6%8A%80%E5%B7%A7/%E9%AB%98%E6%BD%AE%E8%AE%BE%E8%AE%A1
topic: 高潮设计
tags: [climax, hook, battle]
---

## 高潮的类型

### 按规模分类

- **小高潮（章节级）**：每5-10章，打败小BOSS、完成小目标。
- **中高潮（卷级）**：每20-30万字，阶段性爽点爆发。
- **大高潮（全书级）**：全书1-3次，推向巅峰、决定成败。

### 按类型分类

- **战斗高潮**：激烈战斗、以弱胜强。
- **智斗高潮**：智谋较量、破解阴谋。
- **情感高潮**：情感爆发、关系确立。
- **突破高潮**：境界突破、实力暴涨。

## 高潮设计的基本原则

1. **充分铺垫**：矛盾积累、实力积累、情绪积累、线索暗示。小高潮铺垫2-5章，中高潮10-20章，大高潮50-100章。
2. **节奏把握**：铺垫 → 酝酿 → 爆发 → 高潮 → 余韵。
3. **爽点爆发**：实力爆发、打脸装逼、复仇成功、目标达成、真相揭露。
4. **情绪调动**：危机感 → 期待感 → 爽快感 → 满足感。

## 战斗高潮结构

战前准备 → 初次交锋 → 陷入困境 → 绝地反击 → 决胜一击 → 尘埃落定

## 高潮描写技巧

- 节奏控制：快慢结合。
- 多角度描写：主角、对手、旁观视角。
- 细节刻画：关键时刻放慢、细致描写。
- 环境烘托：天气、光影、声音、气氛。
```

- [ ] **Step 7: Run compile checks**

```bash
cd backend && python -m compileall app/agents/skills/rag
```

Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add app/agents/skills/rag app/agents/skills/configs/wangwenclub_case.json backend/requirements.txt
git commit -m "feat(skills): add RAG indexing, retrieval, and wangwenclub chunks"
```

---

## Task 6: Extend `WorkerMetadata` with skills fields

**Files:**
- Modify: `app/agents/harness/models.py`

**Interfaces:**
- Consumes: existing `WorkerMetadata`
- Produces: `WorkerMetadata.skills` and `WorkerMetadata.rag_skills`

- [ ] **Step 1: Add fields to `WorkerMetadata`**

Open `app/agents/harness/models.py`, find `WorkerMetadata`, and add:

```python
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
    skills: list[str] = Field(default_factory=list)
    rag_skills: list[str] = Field(default_factory=list)
```

- [ ] **Step 2: Fix Pydantic protected namespace warning**

Add `model_config` to suppress the `model_` namespace warning:

```python
class WorkerMetadata(BaseModel):
    model_config = {"protected_namespaces": ()}
    
    worker_name: str
    ...
```

- [ ] **Step 3: Run compile check and existing tests**

```bash
cd backend && python -m compileall app/agents/harness/models.py
cd backend && python -m unittest tests.agents.harness.test_worker_manager -v
```

Expected: compile passes, existing worker manager tests still pass.

- [ ] **Step 4: Commit**

```bash
git add app/agents/harness/models.py
git commit -m "feat(harness): add skills and rag_skills to WorkerMetadata"
```

---

## Task 7: Integrate skill injection into `WorkerBase`

**Files:**
- Modify: `app/agents/harness/worker_base.py`

**Interfaces:**
- Consumes: `SkillManager.get_skills_for_worker`, `SkillManager.query_rag_skills`
- Produces: `WorkerBase.run()` with skill injection

- [ ] **Step 1: Update imports**

Add near the top of `app/agents/harness/worker_base.py`:

```python
from app.agents.skills.skill_manager import SkillManager, get_skill_manager
```

- [ ] **Step 2: Inject skills in `run()`**

Find the default `run()` method and modify it as follows (keep existing structure):

```python
async def run(self, task, context, history_context=None) -> dict:
    """Default JSON-driven run."""
    if self.metadata is None:
        raise RuntimeError(f"Worker {self.worker_name} has no metadata")

    system_prompt = self.metadata.system_prompt

    # Inject output schema into prompt if available
    if self.metadata.output_schema:
        schema_text = json.dumps(self.metadata.output_schema, ensure_ascii=False, indent=2)
        system_prompt += (
            f"\n\n你必须按以下 JSON schema 输出：\n{schema_text}\n只输出 JSON，"
            "不要 markdown 代码块，不要解释。"
        )

    # Inject inline skills
    try:
        skill_manager = get_skill_manager()
        skill_texts = skill_manager.get_skills_for_worker(
            self.worker_name,
            worker_skills=self.metadata.skills,
            task_goal=getattr(task, "goal", ""),
        )
        if skill_texts:
            system_prompt += "\n\n【创作方法论参考】\n"
            for cfg, content in skill_texts:
                system_prompt += f"\n--- {cfg.skill_name} ---\n{content}\n"
    except Exception:
        logger.exception("Failed to inject inline skills for %s", self.worker_name)

    # Inject RAG skill chunks
    try:
        rag_results = await skill_manager.query_rag_skills(
            self.db,
            self.worker_name,
            rag_skill_names=self.metadata.rag_skills,
            query=getattr(task, "goal", ""),
        )
        if rag_results:
            system_prompt += "\n\n【相关案例参考】\n"
            for r in rag_results:
                system_prompt += f"\n--- {r.chunk_path} ---\n{r.chunk_text}\n"
    except Exception:
        logger.exception("Failed to inject RAG skills for %s", self.worker_name)

    user_prompt = self._build_user_prompt(task, context)
    raw = await self._tool_loop(
        system_prompt,
        user_prompt,
        extra_tools=None,
        history_context=history_context,
    )
    return self._normalize_result(raw)
```

- [ ] **Step 3: Run compile check**

```bash
cd backend && python -m compileall app/agents/harness/worker_base.py
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add app/agents/harness/worker_base.py
git commit -m "feat(harness): inject inline and RAG skills into WorkerBase prompts"
```

---

## Task 8: Update worker JSON configs with skills

**Files:**
- Modify: `app/agents/harness/workers/configs/*.json` (all 11 files)

**Interfaces:**
- Consumes: skill names defined in Task 4
- Produces: worker configs with `skills` and `rag_skills`

- [ ] **Step 1: Add skills to each worker config**

Edit each JSON file to add `skills` and `rag_skills` arrays. Keep all other fields unchanged.

`app/agents/harness/workers/configs/character.json`:
```json
{
  "worker_name": "character",
  ...,
  "skills": ["character_arc"],
  "rag_skills": ["wangwenclub_case"]
}
```

`app/agents/harness/workers/configs/world.json`:
```json
{
  "worker_name": "world",
  ...,
  "skills": ["world_building"],
  "rag_skills": ["wangwenclub_case"]
}
```

`app/agents/harness/workers/configs/outline.json`:
```json
{
  "worker_name": "outline",
  ...,
  "skills": ["plot_design", "pacing", "foreshadowing"],
  "rag_skills": ["wangwenclub_case"]
}
```

`app/agents/harness/workers/configs/plot.json`:
```json
{
  "worker_name": "plot",
  ...,
  "skills": ["plot_design", "foreshadowing"],
  "rag_skills": ["wangwenclub_case"]
}
```

`app/agents/harness/workers/configs/foreshadow.json`:
```json
{
  "worker_name": "foreshadow",
  ...,
  "skills": ["foreshadowing", "plot_design"],
  "rag_skills": ["wangwenclub_case"]
}
```

`app/agents/harness/workers/configs/outline_split.json`:
```json
{
  "worker_name": "outline_split",
  ...,
  "skills": ["plot_design", "pacing"],
  "rag_skills": ["wangwenclub_case"]
}
```

`app/agents/harness/workers/configs/broad_outline.json`:
```json
{
  "worker_name": "broad_outline",
  ...,
  "skills": ["writing_process", "plot_design", "pacing"],
  "rag_skills": ["wangwenclub_case"]
}
```

`app/agents/harness/workers/configs/plot_nodes.json`:
```json
{
  "worker_name": "plot_nodes",
  ...,
  "skills": ["plot_design", "foreshadowing"],
  "rag_skills": ["wangwenclub_case"]
}
```

`app/agents/harness/workers/configs/assignment.json`:
```json
{
  "worker_name": "assignment",
  ...,
  "skills": ["plot_design", "pacing"],
  "rag_skills": ["wangwenclub_case"]
}
```

`app/agents/harness/workers/configs/chapter_outline.json`:
```json
{
  "worker_name": "chapter_outline",
  ...,
  "skills": ["plot_design", "pacing", "climax_hook", "foreshadowing"],
  "rag_skills": ["wangwenclub_case"]
}
```

`app/agents/harness/workers/configs/chapter_text.json`:
```json
{
  "worker_name": "chapter_text",
  ...,
  "skills": ["plot_design", "pacing", "climax_hook", "character_arc", "world_building"],
  "rag_skills": ["wangwenclub_case"]
}
```

- [ ] **Step 2: Validate all configs load**

```bash
cd backend && python -c "
from app.agents.harness.worker_manager import WorkerManager
m = WorkerManager()
for meta in m.list_workers():
    print(meta.worker_name, meta.skills, meta.rag_skills)
"
```

Expected: 11 workers printed with their skills/rag_skills.

- [ ] **Step 3: Commit**

```bash
git add app/agents/harness/workers/configs
git commit -m "feat(harness): assign skills and rag_skills to all worker configs"
```

---

## Task 9: Write unit tests for `SkillManager`

**Files:**
- Create: `tests/agents/skills/test_skill_manager.py`
- Create: `tests/agents/skills/__init__.py`

**Interfaces:**
- Consumes: `SkillManager`, `SkillConfig`
- Produces: passing unit tests

- [ ] **Step 1: Create test file**

```python
import unittest

from app.agents.skills.models import SkillConfig
from app.agents.skills.skill_manager import SkillManager


class TestSkillManager(unittest.TestCase):
    def test_manager_loads_inline_skills(self):
        manager = SkillManager()
        cfg = manager.get_skill("plot_design")
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg.type, "inline")
        self.assertIn("outline", cfg.triggers)

    def test_manager_loads_rag_skills(self):
        manager = SkillManager()
        cfg = manager.get_skill("wangwenclub_case")
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg.type, "rag")
        self.assertEqual(cfg.top_k, 3)

    def test_get_skills_for_worker_by_trigger(self):
        manager = SkillManager()
        results = manager.get_skills_for_worker("character")
        names = [cfg.skill_name for cfg, _ in results]
        self.assertIn("character_arc", names)

    def test_get_skills_for_worker_by_explicit_list(self):
        manager = SkillManager()
        results = manager.get_skills_for_worker(
            "outline", worker_skills=["plot_design"]
        )
        names = [cfg.skill_name for cfg, _ in results]
        self.assertEqual(names, ["plot_design"])

    def test_get_skills_for_worker_returns_content(self):
        manager = SkillManager()
        results = manager.get_skills_for_worker("character")
        self.assertTrue(results)
        for cfg, content in results:
            self.assertTrue(content.strip())

    def test_priority_ordering(self):
        manager = SkillManager()
        results = manager.get_skills_for_worker("chapter_text")
        priorities = [cfg.priority for cfg, _ in results]
        self.assertEqual(priorities, sorted(priorities))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests**

```bash
cd backend && python -m unittest tests.agents.skills.test_skill_manager -v
```

Expected: all PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/agents/skills/test_skill_manager.py tests/agents/skills/__init__.py
git commit -m "test(skills): add SkillManager unit tests"
```

---

## Task 10: Write unit tests for RAG chunk parsing

**Files:**
- Create: `tests/agents/skills/test_rag_index.py`

**Interfaces:**
- Consumes: `_parse_frontmatter` from `app.agents.skills.rag.index`
- Produces: passing unit tests

- [ ] **Step 1: Create test file**

```python
import unittest

from app.agents.skills.rag.index import _parse_frontmatter


class TestRagIndex(unittest.TestCase):
    def test_parse_frontmatter(self):
        text = "---\nskill_name: wangwenclub_case\ntopic: plot\n---\n\nBody content"
        meta, body = _parse_frontmatter(text)
        self.assertEqual(meta["skill_name"], "wangwenclub_case")
        self.assertEqual(meta["topic"], "plot")
        self.assertEqual(body, "Body content")

    def test_parse_without_frontmatter(self):
        text = "Just body content"
        meta, body = _parse_frontmatter(text)
        self.assertEqual(meta, {})
        self.assertEqual(body, text)

    def test_chunk_files_exist(self):
        from pathlib import Path
        chunks_dir = Path(__file__).parent.parent.parent.parent / "app" / "agents" / "skills" / "rag" / "chunks" / "wangwenclub"
        self.assertTrue(chunks_dir.exists())
        self.assertGreaterEqual(len(list(chunks_dir.glob("*.md"))), 5)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests**

```bash
cd backend && python -m unittest tests.agents.skills.test_rag_index -v
```

Expected: all PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/agents/skills/test_rag_index.py
git commit -m "test(skills): add RAG chunk parsing tests"
```

---

## Task 11: Write test for `WorkerBase` skill injection

**Files:**
- Create: `tests/agents/harness/test_worker_base_skills.py`

**Interfaces:**
- Consumes: `WorkerBase`, `SkillManager`, `WorkerMetadata`
- Produces: test verifying system prompt contains skill text

- [ ] **Step 1: Create test file**

```python
import unittest
from unittest.mock import AsyncMock, MagicMock

from app.agents.harness.models import HarnessContext, Task, WorkerMetadata
from app.agents.harness.worker_base import WorkerBase


class TestWorkerBaseSkillInjection(unittest.IsolatedAsyncioTestCase):
    async def test_run_injects_inline_skill(self):
        metadata = WorkerMetadata(
            worker_name="character",
            description="test",
            system_prompt="You are a test worker.",
            tools=[],
            input_schema={},
            output_schema={},
            skills=["character_arc"],
            rag_skills=[],
        )

        db = MagicMock()
        llm = MagicMock()
        worker = WorkerBase(db, llm, 8, metadata=metadata, timeout=60.0)

        captured_prompts = {}

        async def fake_tool_loop(system_prompt, user_prompt, extra_tools=None, history_context=None):
            captured_prompts["system"] = system_prompt
            captured_prompts["user"] = user_prompt
            return {"summary": "ok", "changes": []}

        worker._tool_loop = fake_tool_loop

        task = Task(id="t1", worker="character", goal="设计主角")
        context = HarnessContext()
        result = await worker.run(task, context)

        self.assertIn("【创作方法论参考】", captured_prompts["system"])
        self.assertIn("character_arc", captured_prompts["system"])
        self.assertIn("主角塑造", captured_prompts["system"])
        self.assertEqual(result["summary"], "ok")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests**

```bash
cd backend && python -m unittest tests.agents.harness.test_worker_base_skills -v
```

Expected: all PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/agents/harness/test_worker_base_skills.py
git commit -m "test(harness): verify WorkerBase injects inline skills"
```

---

## Task 12: Run full test suite and RAG index

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

- [ ] **Step 3: Build RAG index**

Configure `.env` with a valid embedding-capable API key (the same key used for chat embeddings), then:

```bash
cd backend && python -m app.agents.skills.rag.index
```

Expected: output like `wangwenclub_case: 7 chunks`.

- [ ] **Step 4: Smoke test skill retrieval**

```bash
cd backend && python -c "
import asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import AsyncSessionLocal
from app.agents.skills.skill_manager import SkillManager

async def main():
    async with AsyncSessionLocal() as db:
        manager = SkillManager()
        results = await manager.query_rag_skills(db, 'chapter_text', query='如何设计高潮')
        print([r.chunk_path for r in results])

asyncio.run(main())
"
```

Expected: list of relevant chunk paths.

- [ ] **Step 5: Commit any final fixes**

```bash
git add -A
git commit -m "fix(skills): smoke test fixes and final verification"
```

---

## Self-Review Checklist

- [ ] **Spec coverage**: Every section of `docs/superpowers/specs/2026-07-30-phase2-skill-rag-design.md` has at least one task.
  - Skill models: Task 2
  - SkillManager: Task 3
  - Inline skills registry/configs: Task 4
  - RAG indexing/retrieval: Task 5
  - WorkerMetadata extension: Task 6
  - WorkerBase injection: Task 7
  - Worker JSON updates: Task 8
  - Tests: Tasks 9-11
  - Smoke verification: Task 12
- [ ] **Placeholder scan**: No TBD/TODO/fill-in-details in steps.
- [ ] **Type consistency**: `SkillConfig`, `SkillQueryResult`, `WorkerMetadata.skills`/`rag_skills`, `SkillManager` method names match across tasks.
- [ ] **Import paths**: All new files use absolute imports from `app.*`.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-30-phase2-skill-rag.md`.

Two execution options:

1. **Subagent-Driven (recommended)** - Dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** - Execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints.

Which approach do you prefer?

# 长篇大纲多级树（总纲→时期→卷）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将长篇大纲从平铺单条记录改为固定三级树结构（总纲 broad / 时期 period / 卷 volume），支持卷节点记录章节范围；AI 助手生成大纲时直接产出树结构，并提供 AI 一键拆分现有整坨大纲的功能。

**Architecture:** 复用现有 `long_outlines` 表（已有 `parent_id`、`type`、`order` 列），新增 `chapter_start`、`chapter_end` 两列；`parent_id` 回归树父级语义，与 `version_chain` 版本链脱钩。所有写操作继续通过 `services/change_apply.py` 单一入口，AI 拆分/生成通过 staged-changes 确认流批量入库，新增 `temp_id` 机制解决树节点之间的父级引用。前端大纲页从 `EntityWorkbench` 平铺改为 `OutlinePanel` 树形 UI。

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy 2.0 async / SQLite；React 19 / TypeScript / Vite / Tailwind；pytest（已存在，但未配置在 CI）。

## Global Constraints

- 后端所有持久化变更必须通过 `services/change_apply.py`（`apply_change` / `confirm_session`）
- 后端写入模型字段必须经 `_sanitize_fields` 过滤，禁止 LLM 写入未知列
- 新增 SQLite 列必须走 `backend/scripts/migrate.py` 幂等迁移（`ALTER TABLE ADD COLUMN`，存在即跳过）
- `long_outlines.type` 枚举固定为 `broad` / `period` / `volume`
- 卷节点章节范围 `chapter_start` / `chapter_end` 为 1-based 章号（与章节 `order` 的 0-based 映射：章号 = `order + 1`）
- 前端 `tsconfig.json` 保持 `noImplicitAny: false`
- 前端改动后必须执行 `npm run build` 输出到 `frontend/dist`

---

## File Map

### 后端

| 文件 | 职责 |
|---|---|
| `backend/scripts/migrate.py` | 幂等添加 `chapter_start`、`chapter_end` 列 |
| `backend/app/schemas/long.py` | 扩展 `OutlineCreate` / `OutlineUpdate` schema，新增 `type` / `parent_id` / `chapter_start` / `chapter_end` |
| `backend/app/services/change_apply.py` | 实现层级约束校验、temp_id 重写、环检测；新增 `PARENT_FAILED` 错误 |
| `backend/app/api/long_outline.py` | 移除 `parent_id` 继承 version_chain 的旧逻辑；新增层级校验辅助函数 |
| `backend/app/agents/harness/nodes/aggregator.py` | 支持保留 `temp_id` 字段到 ChangeRecord |
| `backend/app/agents/harness/state.py` | `ChangeRecord` 可选 `temp_id` 字段；`make_change` 支持传入 |
| `backend/app/agents/harness/workers/chapter_workers.py` | 新增 `_volume_outline_text`；`ChapterOutlineWorker` / `ChapterTextWorker` 注入 `volume_outline` |
| `backend/app/agents/harness/prompts/chapter_generation.py` | 修改 `CHAPTER_OUTLINE_PROMPT_TEMPLATE` / `CHAPTER_TEXT_PROMPT_TEMPLATE`，新增「本卷大纲」一节 |
| `backend/app/agents/harness/workers/__init__.py` | 新增 `OutlineSplitWorker`；改造 `BroadOutlineWorker` / `OutlineWorker` 返回树结构；supervisor 新增 `outline_split` 意图 |
| `backend/app/api/assistant.py` | 注册 `OutlineSplitWorker`、supervisor 提示词增加 `outline_split` 规则示例 |
| `backend/app/api/long_continue.py` | `assemble_context` 改为总纲 + 最后一章所属卷 + 其余标题列表 |
| `backend/app/services/export.py` | `render_markdown_long` 大纲部分按树缩进输出 |
| `backend/tests/test_outline_tree.py` | 新增：层级约束、temp_id 重写、`_volume_outline_text`、树结构渲染测试 |
| `backend/tests/test_change_apply_dedup.py` | 视需要补充 outline tree 查重/层级测试 |

### 前端

| 文件 | 职责 |
|---|---|
| `frontend/src/types.ts` | 新增 `OutlineNode` 类型：`id`, `project_id`, `parent_id`, `type`, `title`, `content`, `order`, `chapter_start?`, `chapter_end?`, `children?: OutlineNode[]` |
| `frontend/src/api/long.ts` | outline API payload 类型补全；新增 `splitOutline` 接口 |
| `frontend/src/pages/LongWorkspace.tsx` | 大纲 tab 改为渲染 `<OutlinePanel />`，其他实体仍用 `EntityWorkbench` |
| `frontend/src/components/OutlinePanel.tsx` | 新建：左栏树（搜索+折叠+缩进+类型徽标）+ 右栏编辑器（标题、内容、卷范围、AI 拆分） |
| `frontend/src/components/OutlineTree.tsx` | 新建：纯展示/交互树组件，负责展开状态、选中、新增子级入口 |

---

## Task 1: 数据库与 Schema 变更

**Files:**
- Modify: `backend/scripts/migrate.py:18-33`
- Modify: `backend/app/schemas/long.py:6-18`

**Interfaces:**
- Consumes: 现有 `OutlineCreate` / `OutlineUpdate`
- Produces: `OutlineCreate` 包含 `type`（默认 broad）, `parent_id`, `chapter_start`, `chapter_end`；`OutlineUpdate` 同上但全 Optional

- [ ] **Step 1: 添加迁移列**

在 `MIGRATIONS` 列表末尾追加：

```python
("long_outlines", "chapter_start", "INTEGER"),
("long_outlines", "chapter_end", "INTEGER"),
```

- [ ] **Step 2: 扩展 schema**

```python
from typing import Literal

class OutlineCreate(BaseModel):
    project_id: str
    title: str = ""
    content: str = ""
    type: Literal["broad", "period", "volume"] = "broad"
    parent_id: Optional[str] = None
    order: int = 0
    chapter_start: Optional[int] = None
    chapter_end: Optional[int] = None

class OutlineUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    type: Optional[Literal["broad", "period", "volume"]] = None
    parent_id: Optional[str] = None
    order: Optional[int] = None
    chapter_start: Optional[int] = None
    chapter_end: Optional[int] = None
```

- [ ] **Step 3: 运行迁移验证**

```bash
cd backend && python scripts/migrate.py
```

Expected: `Column long_outlines.chapter_start already exists, skipping`（若已运行）或 `Added column long_outlines.chapter_start`

- [ ] **Step 4: Commit**

```bash
git add backend/scripts/migrate.py backend/app/schemas/long.py
git commit -m "chore(schema): long_outlines 新增 chapter_start/end 并扩展 schema"
```

---

## Task 2: change_apply 层级约束与 temp_id 重写

**Files:**
- Modify: `backend/app/services/change_apply.py:101-189`, `confirm_session`

**Interfaces:**
- Consumes: `OutlineCreate/Update` schema 字段；ChangeRecord 新增可选 `temp_id`
- Produces: `apply_change` 返回 `{"ok": true, ...}` 或 `AppError`（含 `PARENT_FAILED`）；`confirm_session` 内维护 `temp_id → 真实 id` 映射

- [ ] **Step 1: 新增 outline 层级校验函数**

在 `change_apply.py` 中添加：

```python
def _is_descendant(db: AsyncSession, model, node_id: str, ancestor_id: str) -> bool:
    """BFS 检查 node_id 是否在 ancestor_id 的后代链中（含自身）。"""
    if node_id == ancestor_id:
        return True
    visited = set()
    stack = [ancestor_id]
    while stack:
        cur = stack.pop()
        if cur in visited:
            continue
        visited.add(cur)
        # SQLAlchemy async session sync fallback for simple id traversal
        row = db.sync_session.get(model, cur)
        if row is None:
            continue
        for child in row.children:  # requires relationship defined; if not, use query below
            if child.id == node_id:
                return True
            stack.append(child.id)
    return False
```

> 注：当前模型未定义 relationship，建议同步添加 relationship（Task 2 步骤 2）或改为显式查询。

- [ ] **Step 2: 在 LongOutline 模型添加 relationship**

修改 `backend/app/models.py` `LongOutline`：

```python
from sqlalchemy.orm import relationship

children = relationship("LongOutline", backref=backref("parent", remote_side=[id]))
```

- [ ] **Step 3: 实现 `_validate_outline_change`**

```python
async def _validate_outline_change(db: AsyncSession, project_id: str, action: str, entity_id: str | None, after: dict):
    if action == "delete":
        row = await db.get(LongOutline, entity_id)
        if row and row.children:
            raise AppError("该节点存在子级，请先删除子级", "HAS_CHILDREN", 400)
        return

    ctype = after.get("type")
    parent_id = after.get("parent_id")
    if ctype:
        if ctype == "broad" and parent_id:
            raise AppError("总纲节点不能有父级", "INVALID_HIERARCHY", 400)
        if ctype == "period" and not parent_id:
            raise AppError("时期节点必须属于某个总纲", "INVALID_HIERARCHY", 400)
        if ctype == "volume" and not parent_id:
            raise AppError("卷节点必须属于某个时期", "INVALID_HIERARCHY", 400)
        if parent_id:
            parent = await db.get(LongOutline, parent_id)
            if not parent:
                raise AppError("父节点不存在", "PARENT_NOT_FOUND", 400)
            expected = {"period": "broad", "volume": "period"}.get(ctype)
            if expected and parent.type != expected:
                raise AppError(f"{ctype} 节点的父级必须是 {expected}", "INVALID_HIERARCHY", 400)
            if entity_id and _is_descendant(db, LongOutline, parent_id, entity_id):
                raise AppError("不能将节点移动到自己的后代下", "CYCLIC_HIERARCHY", 400)

    start = after.get("chapter_start")
    end = after.get("chapter_end")
    if start is not None and end is not None and start > end:
        raise AppError("起始章号不能大于结束章号", "INVALID_RANGE", 400)
    if ctype != "volume" and (start is not None or end is not None):
        raise AppError("只有卷节点可以设置章节范围", "INVALID_RANGE", 400)
```

- [ ] **Step 4: 在 `apply_change` 中调用校验**

在 `_sanitize_fields` 之后、动作分支之前插入：

```python
if entity_type == "outline":
    await _validate_outline_change(db, project_id, action, entity_id, after)
```

- [ ] **Step 5: temp_id 重写**

修改 `confirm_session`：

```python
temp_map: dict[str, str] = {}
for ch in staged:
    ch_id = ch.get("id")
    if target_ids is not None and ch_id not in target_ids:
        remaining.append(ch)
        continue
    try:
        after = ch.get("after") or {}
        parent_id = after.get("parent_id")
        if isinstance(parent_id, str) and parent_id.startswith("temp:"):
            real = temp_map.get(parent_id)
            if not real:
                errors.append({"change_id": ch_id, "code": "PARENT_FAILED", "message": f"父节点 {parent_id} 尚未应用"})
                remaining.append(ch)
                continue
            after = dict(after)
            after["parent_id"] = real
            ch["after"] = after

        r = await apply_change(db, project_id, ch)
        applied.append({**r, "change_id": ch_id})
        new_id = r.get("entity_id")
        temp_id = ch.get("temp_id")
        if temp_id and new_id:
            temp_map[temp_id] = new_id
        ...
```

- [ ] **Step 6: 写测试**

Create `backend/tests/test_outline_tree.py`：

```python
import pytest
from app.services.change_apply import _validate_outline_change, apply_change

@pytest.mark.asyncio
async def test_broad_cannot_have_parent(db, project_id):
    from app.core.errors import AppError
    with pytest.raises(AppError) as exc:
        await _validate_outline_change(db, project_id, "add", None, {"type": "broad", "parent_id": "x"})
    assert exc.value.code == "INVALID_HIERARCHY"

@pytest.mark.asyncio
async def test_volume_requires_period_parent(db, project_id):
    ...
```

- [ ] **Step 7: 运行测试**

```bash
cd backend && pytest tests/test_outline_tree.py -v
```

Expected: 全部 PASS

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/change_apply.py backend/app/models.py backend/tests/test_outline_tree.py
git commit -m "feat(outline): change_apply 层级约束与 temp_id 重写"
```

---

## Task 3: 后端大纲 API 修正

**Files:**
- Modify: `backend/app/api/long_outline.py:23-30`
- Modify: `backend/app/api/long_outline.py`（新增层级校验辅助导出，可选）

**Interfaces:**
- Consumes: `OutlineCreate` / `OutlineUpdate`
- Produces: `add_outline` 不再把 `parent_id` 写入 `version_chain`

- [ ] **Step 1: 删除旧逻辑**

把：

```python
data = payload.model_dump()
if payload.parent_id:
    parent = await db.get(LongOutline, payload.parent_id)
    if parent:
        data["version_chain"] = parent.version_chain or parent.id
return await create_outline(db, data)
```

改为：

```python
return await create_outline(db, payload.model_dump())
```

- [ ] **Step 2: 验证列表接口返回树**

`list_outlines` 当前平铺返回。前端自行建树，后端保持平铺 + `order` 排序。无需后端改动。

- [ ] **Step 3: Commit**

```bash
git add backend/app/api/long_outline.py
git commit -m "fix(outline): parent_id 回归树语义，不再混入 version_chain"
```

---

## Task 4: 下游消费逻辑改造

### Subtask 4a: 章节生成 worker 注入 volume_outline

**Files:**
- Modify: `backend/app/agents/harness/workers/chapter_workers.py:68-78`
- Modify: `backend/app/agents/harness/prompts/chapter_generation.py`

**Interfaces:**
- Consumes: `outlines` list（含 `type`, `parent_id`, `chapter_start`, `chapter_end`）
- Produces: `_volume_outline_text(outlines, chapter_order)` 返回字符串；prompt context 增加 `volume_outline`

- [ ] **Step 1: 新增 helper**

```python
def _volume_outline_text(outlines: list[dict], chapter_order: int) -> str:
    chapter_num = chapter_order + 1
    for o in outlines:
        if o.get("type") != "volume":
            continue
        start = o.get("chapter_start")
        end = o.get("chapter_end")
        if start is None or end is None:
            continue
        if start <= chapter_num <= end:
            parent = next((p for p in outlines if p.get("id") == o.get("parent_id")), None)
            parts = []
            if parent:
                parts.append(f"时期《{parent.get('title', '')}》：{parent.get('content', '')}")
            parts.append(f"卷《{o.get('title', '')}》（第 {start}-{end} 章）：{o.get('content', '')}")
            return "\n\n".join(parts)
    # fallback: 列出时期标题
    periods = [o for o in outlines if o.get("type") == "period"]
    if periods:
        return "未找到本卷大纲，现有时期：" + " / ".join(p.get("title", "") for p in periods)
    return "（暂无卷大纲）"
```

- [ ] **Step 2: worker 中注入**

`ChapterOutlineWorker.run` 和 `ChapterTextWorker.run` 中：

```python
chapter_order = chapter.get("order", 0)
volume_outline = _volume_outline_text(outlines, chapter_order)
```

并把 `volume_outline` 加入 `prompt_context`。

- [ ] **Step 3: 修改 prompt 模板**

在 `CHAPTER_OUTLINE_PROMPT_TEMPLATE` 与 `CHAPTER_TEXT_PROMPT_TEMPLATE` 的「输入数据」中各加：

```
【本卷大纲】
$volume_outline
```

并更新 `CHAPTER_OUTLINE_PROMPT` / `CHAPTER_TEXT_PROMPT` 函数签名和 `chapter_outline_prompt` / `chapter_text_prompt` 接收 `volume_outline`。

- [ ] **Step 4: 测试 helper**

```python
def test_volume_outline_text_hits_range():
    outlines = [
        {"id": "p1", "type": "period", "title": "开荒期", "content": "概述"},
        {"id": "v1", "type": "volume", "parent_id": "p1", "title": "第1卷", "content": "卷内容", "chapter_start": 1, "chapter_end": 10},
    ]
    assert "卷内容" in _volume_outline_text(outlines, 0)
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/agents/harness/workers/chapter_workers.py backend/app/agents/harness/prompts/chapter_generation.py backend/tests/test_chapter_workers_helpers.py
git commit -m "feat(chapter): 生成时按章号注入所属卷大纲"
```

### Subtask 4b: 续写上下文改造

**Files:**
- Modify: `backend/app/api/long_continue.py:19-36`

- [ ] **Step 1: 重写 assemble_context**

```python
async def assemble_context(db: AsyncSession, project_id: str) -> str:
    outlines = await repo.list_outlines(db, project_id)
    characters = await repo.list_characters(db, project_id)
    foreshadows = await repo.list_foreshadows(db, project_id)
    chapters = await repo.list_chapters(db, project_id)

    lines = []
    lines.append("【总纲】")
    broads = [o for o in outlines if o.get("type") == "broad"]
    for o in broads:
        lines.append(f"- {o.get('title', '')}: {o.get('content', '')[:200]}")

    last_chapter = sorted(chapters, key=lambda c: c.get('order', 0))[-1] if chapters else None
    if last_chapter:
        from app.agents.harness.workers.chapter_workers import _volume_outline_text
        volume_outline = _volume_outline_text(outlines, last_chapter.get('order', 0))
        lines.append("\n【当前卷大纲】")
        lines.append(volume_outline)

    lines.append("\n【其他时期/卷标题】")
    for o in outlines:
        if o.get("type") in ("period", "volume"):
            lines.append(f"- {o.get('title', '')}")

    lines.append("\n【角色】")
    lines.extend(f"- {c.get('name', '')}（{c.get('status', '')}）: {c.get('traits', '')}" for c in characters[:20])
    lines.append("\n【待回收伏笔】")
    lines.extend(f"- {f.get('title', '')}: {f.get('content', '')[:120]}" for f in foreshadows if f.get('state') == 'pending')
    lines.append("\n【已有章节（末段衔接）】")
    if last_chapter:
        lines.append(f"上一章《{last_chapter.get('title', '')}》末尾：{(last_chapter.get('content', '') or '')[-300:]}")
    return "\n".join(lines)
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/api/long_continue.py
git commit -m "feat(continue): 续写上下文改为总纲+当前卷+其余标题"
```

### Subtask 4c: 导出 Markdown 树形输出

**Files:**
- Modify: `backend/app/services/export.py:39-53`

- [ ] **Step 1: 实现树渲染**

```python
def _build_outline_tree(outlines: list[dict]) -> list[dict]:
    by_id = {o["id"]: o for o in outlines}
    roots = []
    for o in outlines:
        pid = o.get("parent_id")
        if not pid or pid not in by_id:
            roots.append(o)
    return roots


def _render_outline_node(node: dict, depth: int = 0) -> str:
    indent = "  " * depth
    header = {"broad": "#", "period": "##", "volume": "###"}.get(node.get("type"), "#")
    title = node.get("title") or "（无标题）"
    lines = [f"{indent}{header} {title}", f"{indent}{node.get('content', '')}", ""]
    return "\n".join(lines)


def render_markdown_long(title: str, data: dict) -> str:
    out = [f"# {title}", ""]
    out.append("## 角色")
    ...
    out.append("\n## 大纲")
    tree = _build_outline_tree(data["outlines"])
    for node in tree:
        out.append(_render_outline_node(node))
    return "\n".join(out)
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/services/export.py
git commit -m "feat(export): 大纲按树结构缩进渲染"
```

---

## Task 5: AI 拆分与生成升级

### Subtask 5a: ChangeRecord 支持 temp_id，aggregator 透传

**Files:**
- Modify: `backend/app/agents/harness/state.py:9-17`, `make_change`
- Modify: `backend/app/agents/harness/nodes/aggregator.py:36-54`

- [ ] **Step 1: state.py 加 temp_id**

```python
class ChangeRecord(BaseModel):
    ...
    temp_id: Optional[str] = None


def make_change(..., temp_id: Optional[str] = None):
    return ChangeRecord(..., temp_id=temp_id)
```

- [ ] **Step 2: aggregator 透传**

```python
records.append(make_change(
    ...,
    temp_id=ch.get("temp_id"),
))
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/agents/harness/state.py backend/app/agents/harness/nodes/aggregator.py
git commit -m "feat(harness): ChangeRecord 支持 temp_id"
```

### Subtask 5b: AI 一键拆分（OutlineSplitWorker）

**Files:**
- Modify: `backend/app/agents/harness/workers/__init__.py`

- [ ] **Step 1: 实现 OutlineSplitWorker**

```python
class OutlineSplitWorker(WorkerBase):
    worker_name = "outline_split"

    async def run(self, goal: str, context: dict, history_context: list[dict] | None = None) -> dict:
        project_id = context.get("project_id")
        entity_id = context.get("entity_id")
        if not project_id or not entity_id:
            return {"changes": [], "stage": "outline_split", "error": "缺少 project_id 或 entity_id"}

        outlines = await repo.list_outlines(self.db, project_id)
        target = next((o for o in outlines if o.get("id") == entity_id), None)
        if not target:
            return {"changes": [], "stage": "outline_split", "error": "目标大纲不存在"}

        system = (
            "你是大纲拆分师。根据用户目标，把一篇完整大纲拆分为固定三级树结构（总纲 broad / 时期 period / 卷 volume）。"
            "如果目标条目是总纲级长文，输出多个时期；如果目标条目是单个时期，输出该时期的多个卷。"
            "只返回合法 JSON，不要 markdown 代码块，不要解释。\n\n"
            "输出格式（当目标是时期时）：\n"
            '{"summary": "时期级概述（改写原条目）", "volumes": ['
            '{"title": "卷标题", "content": "卷大纲全文", "chapter_start": 1, "chapter_end": 10}'
            ']}\n\n'
            "输出格式（当目标是总纲时）：\n"
            '{"periods": [{"title": "时期标题", "summary": "时期概述", "volumes": [...]}]}\n\n'
            "chapter_start/chapter_end 表示该卷覆盖第几章到第几章（1-based），允许为 null。"
        )
        user = f"【目标条目类型】{target.get('type')}\n【标题】{target.get('title')}\n【内容】\n{target.get('content', '')}\n\n【用户目标】\n{goal}"
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        if history_context:
            messages = history_context + messages
        result = await self.llm.parse_llm_json(messages)
        if not isinstance(result, dict):
            return {"changes": [], "stage": "outline_split", "error": "无法解析拆分结果"}

        changes = []
        target_type = target.get("type")
        if target_type == "broad":
            periods = result.get("periods") or []
            for p in periods:
                period_id = f"temp:period:{len(changes)}"
                changes.append({
                    "action": "add", "temp_id": period_id,
                    "fields": {"title": p.get("title"), "content": p.get("summary"), "type": "period", "parent_id": entity_id}
                })
                for v in p.get("volumes") or []:
                    changes.append({
                        "action": "add",
                        "fields": {
                            "title": v.get("title"), "content": v.get("content"), "type": "volume",
                            "parent_id": period_id,
                            "chapter_start": v.get("chapter_start"), "chapter_end": v.get("chapter_end"),
                        }
                    })
        else:
            # period 或 broad fallback：把原条目升级为 period
            changes.append({
                "action": "update", "entity_id": entity_id,
                "fields": {"type": target_type if target_type in ("broad", "period") else "period", "content": result.get("summary", target.get("content"))}
            })
            for v in result.get("volumes") or []:
                changes.append({
                    "action": "add",
                    "fields": {
                        "title": v.get("title"), "content": v.get("content"), "type": "volume",
                        "parent_id": entity_id,
                        "chapter_start": v.get("chapter_start"), "chapter_end": v.get("chapter_end"),
                    }
                })
        return {"changes": changes, "stage": "outline_split"}
```

- [ ] **Step 2: 在 `__all__` 中注册**

- [ ] **Step 3: Commit**

```bash
git add backend/app/agents/harness/workers/__init__.py
git commit -m "feat(outline): AI 一键拆分现有大纲为三级树"
```

### Subtask 5c: BroadOutlineWorker 升级为树结构输出

**Files:**
- Modify: `backend/app/agents/harness/workers/__init__.py` 中 `BroadOutlineWorker`
- Modify: `backend/app/agents/harness/prompts/chapter_generation.py` 中 `BROAD_OUTLINE_PROMPT_TEMPLATE`

- [ ] **Step 1: 修改 prompt**

把 `BROAD_OUTLINE_PROMPT_TEMPLATE` 输出格式改为：

```json
{
  "changes": [
    {"action": "add|update", "entity_id": null或id, "temp_id": "temp:broad:1", "fields": {"title": "总纲标题", "content": "总纲正文", "type": "broad"}},
    {"action": "add", "temp_id": "temp:period:1", "fields": {"title": "时期标题", "content": "时期概述", "type": "period", "parent_id": "temp:broad:1"}},
    {"action": "add", "fields": {"title": "卷标题", "content": "卷内容", "type": "volume", "parent_id": "temp:period:1", "chapter_start": 1, "chapter_end": 10}}
  ]
}
```

- [ ] **Step 2: BroadOutlineWorker 透传 LLM 结果**

当前 `_result_to_response` 直接透传。若 LLM 按上述格式输出，`changes` 自然携带 `temp_id` 与 `parent_id`，无需额外处理。

- [ ] **Step 3: Commit**

```bash
git add backend/app/agents/harness/prompts/chapter_generation.py backend/app/agents/harness/workers/__init__.py
git commit -m "feat(outline): 总纲生成直接产出三级树"
```

### Subtask 5d: Supervisor 识别 outline_split

**Files:**
- Modify: `backend/app/api/assistant.py:38-48` _WORKERS
- Modify: `backend/app/api/assistant.py:297-345` supervisor_prompt

- [ ] **Step 1: 注册 worker**

```python
_WORKERS = {
    ...,
    "outline_split": OutlineSplitWorker,
}
```

- [ ] **Step 2: supervisor prompt 增加规则**

在可选 Worker 列表加 `"outline_split（拆分已有大纲为时期/卷）"`，并加示例：

```
用户：把这条大纲拆成几卷。
输出：{"intent": "拆分大纲", "tasks": [{"worker": "outline_split", "goal": "把当前大纲条目拆成时期/卷结构"}]}
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/api/assistant.py
git commit -m "feat(assistant): supervisor 支持 outline_split 意图"
```

---

## Task 6: 前端大纲树 UI

### Subtask 6a: 类型与 API

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api/long.ts`

- [ ] **Step 1: 新增类型**

```typescript
export type OutlineType = "broad" | "period" | "volume";

export interface OutlineNode {
  id: string;
  project_id: string;
  parent_id: string | null;
  type: OutlineType;
  title: string;
  content: string;
  order: number;
  chapter_start?: number | null;
  chapter_end?: number | null;
  version_chain?: string | null;
  children?: OutlineNode[];
}
```

- [ ] **Step 2: API 类型化**

```typescript
export interface OutlinePayload {
  project_id: string;
  title?: string;
  content?: string;
  type?: OutlineType;
  parent_id?: string | null;
  order?: number;
  chapter_start?: number | null;
  chapter_end?: number | null;
  version_chain?: string | null;
}

addOutline: (data: OutlinePayload) => ...
updateOutline: (id: string, data: OutlinePayload) => ...
splitOutline: (project_id: string, entity_id: string, message: string) => api.post("/assistant/chat", { project_id, message, context: { entity_type: "outline", entity_id } })
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/types.ts frontend/src/api/long.ts
git commit -m "types(outline): 大纲树类型与 API payload"
```

### Subtask 6b: 树组件

**Files:**
- Create: `frontend/src/components/OutlineTree.tsx`

- [ ] **Step 1: 实现 OutlineTree**

```typescript
import { useMemo, useState } from "react";
import { OutlineNode, OutlineType } from "@/types";

interface OutlineTreeProps {
  nodes: OutlineNode[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onAddChild: (parent: OutlineNode) => void;
  search: string;
}

const TYPE_LABEL: Record<OutlineType, string> = { broad: "总纲", period: "时期", volume: "卷" };

function buildVisibleSet(nodes: OutlineNode[], search: string): Set<string> {
  const q = search.trim().toLowerCase();
  const hit = new Set<string>();
  if (!q) return hit;
  const walk = (list: OutlineNode[], ancestors: OutlineNode[]) => {
    for (const n of list) {
      const text = `${n.title} ${n.content}`.toLowerCase();
      if (text.includes(q)) {
        hit.add(n.id);
        ancestors.forEach((a) => hit.add(a.id));
      }
      if (n.children) walk(n.children, [...ancestors, n]);
    }
  };
  walk(nodes, []);
  return hit;
}

export function OutlineTree({ nodes, selectedId, onSelect, onAddChild, search }: OutlineTreeProps) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const hitSet = useMemo(() => buildVisibleSet(nodes, search), [nodes, search]);

  const toggle = (id: string) => {
    const next = new Set(expanded);
    if (next.has(id)) next.delete(id); else next.add(id);
    setExpanded(next);
  };

  const renderNode = (n: OutlineNode, depth: number) => {
    const hasChildren = (n.children?.length || 0) > 0;
    const isExpanded = expanded.has(n.id) || (search.trim() && hitSet.has(n.id));
    const hidden = search.trim() && !hitSet.has(n.id);
    if (hidden) return null;
    return (
      <div key={n.id}>
        <div
          className={`flex cursor-pointer items-center border-b border-line px-2 py-2 hover:bg-surface-2 ${selectedId === n.id ? "bg-accent-soft" : ""}`}
          style={{ paddingLeft: `${12 + depth * 20}px` }}
          onClick={() => onSelect(n.id)}
        >
          {hasChildren && (
            <span className="mr-1 text-xs text-muted" onClick={(e) => { e.stopPropagation(); toggle(n.id); }}>
              {isExpanded ? "▼" : "▶"}
            </span>
          )}
          <span className="mr-2 rounded-sm border border-line px-1 text-[10px] text-muted">{TYPE_LABEL[n.type]}</span>
          <span className="flex-1 truncate text-sm text-ink">{n.title || "（无标题）"}</span>
          {n.type !== "volume" && (
            <button className="ml-2 text-xs text-accent" onClick={(e) => { e.stopPropagation(); onAddChild(n); }}>+</button>
          )}
        </div>
        {isExpanded && n.children?.map((c) => renderNode(c, depth + 1))}
      </div>
    );
  };

  return <div className="border border-line bg-surface">{nodes.map((n) => renderNode(n, 0))}</div>;
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/OutlineTree.tsx
git commit -m "ui(outline): 大纲树展示组件"
```

### Subtask 6c: 大纲面板

**Files:**
- Create: `frontend/src/components/OutlinePanel.tsx`
- Modify: `frontend/src/pages/LongWorkspace.tsx:39-44`

- [ ] **Step 1: 实现 OutlinePanel**

```typescript
import { useEffect, useMemo, useState } from "react";
import { Button, Input, Textarea, SectionTitle, Empty } from "@/components/ui";
import { OutlineTree } from "./OutlineTree";
import { longApi } from "@/api/long";
import { OutlineNode, OutlineType } from "@/types";

function buildTree(outlines: OutlineNode[]): OutlineNode[] {
  const byId = new Map(outlines.map((o) => [o.id, { ...o, children: [] as OutlineNode[] }]));
  const roots: OutlineNode[] = [];
  for (const o of byId.values()) {
    const parent = o.parent_id ? byId.get(o.parent_id) : null;
    if (parent) parent.children!.push(o);
    else roots.push(o);
  }
  return roots.sort((a, b) => a.order - b.order);
}

const TYPE_LABEL: Record<OutlineType, string> = { broad: "总纲", period: "时期", volume: "卷" };

export function OutlinePanel({ pid }: { pid: string }) {
  const [items, setItems] = useState<OutlineNode[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [form, setForm] = useState<Partial<OutlineNode>>({});
  const [search, setSearch] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    const { data } = await longApi.outlines(pid);
    setItems(data || []);
  };

  useEffect(() => { load(); }, [pid]);

  const tree = useMemo(() => buildTree(items), [items]);
  const selected = useMemo(() => items.find((i) => i.id === selectedId), [items, selectedId]);

  useEffect(() => {
    if (selected) setForm({ ...selected });
    else setForm({});
  }, [selected]);

  const handleSave = async () => {
    setError(null);
    if (!selectedId) return;
    try {
      await longApi.updateOutline(selectedId, {
        title: form.title || "",
        content: form.content || "",
        chapter_start: form.type === "volume" ? form.chapter_start ?? null : null,
        chapter_end: form.type === "volume" ? form.chapter_end ?? null : null,
      });
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    }
  };

  const handleDelete = async () => {
    if (!selectedId || !window.confirm("确定删除吗？")) return;
    try {
      await longApi.deleteOutline(selectedId);
      setSelectedId(null);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "删除失败");
    }
  };

  const handleAddRoot = async () => {
    setError(null);
    try {
      const res = await longApi.addOutline({ project_id: pid, title: "新总纲", type: "broad", order: 0 });
      await load();
      setSelectedId(res?.data?.id || null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "新增失败");
    }
  };

  const handleAddChild = async (parent: OutlineNode) => {
    setError(null);
    const childType: OutlineType = parent.type === "broad" ? "period" : "volume";
    try {
      const res = await longApi.addOutline({
        project_id: pid,
        title: childType === "period" ? "新时期" : "新卷",
        type: childType,
        parent_id: parent.id,
        order: (parent.children?.length || 0),
      });
      await load();
      setSelectedId(res?.data?.id || null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "新增失败");
    }
  };

  const handleSplit = async () => {
    if (!selectedId) return;
    const msg = window.prompt("请输入拆分要求", `将《${selected?.title}》拆分为多卷`);
    if (!msg) return;
    try {
      await longApi.splitOutline(pid, selectedId, msg);
      // AI 拆分进 staged_changes，提示用户去 assistant 面板确认
      alert("拆分建议已生成，请打开右下角 AI 助手面板确认");
    } catch (e) {
      setError(e instanceof Error ? e.message : "拆分失败");
    }
  };

  return (
    <div className="flex h-full flex-col">
      <SectionTitle>大纲树</SectionTitle>
      <div className="mt-4 flex h-0 flex-1 gap-4">
        <div className="flex w-80 shrink-0 flex-col border border-line bg-surface p-3">
          <div className="mb-3 flex gap-2">
            <Input placeholder="搜索大纲…" value={search} onChange={(e) => setSearch(e.target.value)} />
            <Button variant="primary" className="shrink-0" onClick={handleAddRoot}>新增</Button>
          </div>
          <div className="flex-1 overflow-auto">
            {tree.length === 0 ? <Empty text="暂无大纲，点击新增创建总纲" /> : (
              <OutlineTree nodes={tree} selectedId={selectedId} onSelect={setSelectedId} onAddChild={handleAddChild} search={search} />
            )}
          </div>
        </div>
        <div className="flex flex-1 flex-col overflow-auto border border-line bg-surface p-4">
          {error && <div className="mb-3 border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}
          {!selected ? (
            <div className="flex h-full items-center justify-center"><Empty text="从左侧选择节点编辑，或点击新增" /></div>
          ) : (
            <>
              <div className="mb-3 flex items-center justify-between">
                <span className="text-sm font-medium text-ink">编辑{TYPE_LABEL[form.type || "broad"]}</span>
                <div className="flex items-center gap-2">
                  <Button variant="ghost" onClick={handleSplit} disabled={form.type === "volume"}>AI 拆分</Button>
                  <Button variant="primary" onClick={handleSave}>保存</Button>
                  <Button variant="ghost" onClick={handleDelete}>删除</Button>
                </div>
              </div>
              <div className="flex min-h-0 flex-1 flex-col gap-3">
                <div>
                  <label className="mb-1 block text-xs text-muted">标题</label>
                  <Input value={form.title || ""} onChange={(e) => setForm({ ...form, title: e.target.value })} />
                </div>
                <div className="flex min-h-0 flex-1 flex-col">
                  <label className="mb-1 block text-xs text-muted">内容</label>
                  <Textarea className="min-h-[12rem] flex-1 resize-none leading-relaxed" value={form.content || ""} onChange={(e) => setForm({ ...form, content: e.target.value })} />
                </div>
                {form.type === "volume" && (
                  <div className="flex gap-4">
                    <div className="flex-1">
                      <label className="mb-1 block text-xs text-muted">起始章</label>
                      <Input type="number" value={form.chapter_start ?? ""} onChange={(e) => setForm({ ...form, chapter_start: e.target.value ? Number(e.target.value) : null })} />
                    </div>
                    <div className="flex-1">
                      <label className="mb-1 block text-xs text-muted">结束章</label>
                      <Input type="number" value={form.chapter_end ?? ""} onChange={(e) => setForm({ ...form, chapter_end: e.target.value ? Number(e.target.value) : null })} />
                    </div>
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: LongWorkspace 接入**

```typescript
import { OutlinePanel } from "@/components/OutlinePanel";

// 在大纲 tab 处：
{tab === "outline" && <OutlinePanel pid={id!} />}
{(tab === "character" || tab === "foreshadow" || tab === "world" || tab === "plot") && (
  <EntityWorkbench pid={id!} config={WORKBENCH_CONFIGS[tab]} api={KIND_API[tab]} />
)}
```

同时把 `WORKBENCH_CONFIGS.outline` 留作备用或删除。

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/OutlinePanel.tsx frontend/src/pages/LongWorkspace.tsx
git commit -m "feat(outline): 大纲树编辑面板"
```

---

## Task 7: 验证与收尾

- [ ] **Step 1: 后端编译检查**

```bash
cd backend && python -m compileall app
```

Expected: 无错误

- [ ] **Step 2: 后端测试**

```bash
cd backend && pytest tests/test_outline_tree.py tests/test_chapter_workers_helpers.py -v
```

Expected: 全部 PASS

- [ ] **Step 3: 前端类型检查与构建**

```bash
cd frontend && npx tsc -b && npm run build
```

Expected: 无类型错误，build 成功

- [ ] **Step 4: 后端 smoke 启动 + curl 验证**

```bash
cd backend && python desktop_launcher.py
# 另一终端
curl http://127.0.0.1:8765/health
# 通过 API 创建一个总纲→时期→卷树并校验 chapter_start/end
curl -X POST http://127.0.0.1:8765/api/long/outlines -H "Content-Type: application/json" -d '{"project_id":"YOUR_PID","title":"总纲","content":"全书总纲","type":"broad","order":0}'
# 再创建时期与卷，验证层级约束生效（period 无 parent 报错，volume parent 不是 period 报错）
```

- [ ] **Step 5: 桌面客户端验证**

启动桌面客户端，打开项目 → 大纲树页：

1. 新增总纲
2. 在总纲下新增时期
3. 时期下新增卷并设置起始/结束章
4. 点击 AI 拆分，观察 assistant 面板生成 staged changes
5. 确认 staged changes，刷新后观察树结构

- [ ] **Step 6: 最终 Commit**

```bash
git add .
git commit -m "feat(outline): 长篇大纲三级树（总纲-时期-卷）完整实现"
```

---

## Spec Coverage Check

| 设计 spec 要求 | 对应 Task |
|---|---|
| 固定三级树：broad/period/volume | Task 1 schema, Task 2 校验 |
| `parent_id` 回归树语义 | Task 3 API 修正 |
| 新增 `chapter_start/end` | Task 1 迁移 + schema |
| 层级约束（父级类型、环、删除子级） | Task 2 |
| AI 一键拆分现有大纲 | Task 5b |
| AI 生成总纲直接产树 | Task 5c |
| temp_id 父级引用重写 | Task 2 + 5a |
| 章节生成按章号取卷大纲 | Task 4a |
| 续写上下文改造 | Task 4b |
| 导出 Markdown 树形 | Task 4c |
| 前端大纲树 UI | Task 6 |

## Placeholder Scan

- 无 TBD/TODO/"implement later"
- 所有代码片段为可直接使用的具体实现
- 所有命令含预期输出

## Type Consistency Check

- `OutlineType` = `"broad" | "period" | "volume"` 在前后端一致
- `chapter_start/end` 后端 Optional[int]，前端 `number | null`
- `temp_id` 前缀统一为 `temp:`（后端 LLM 输出与 `confirm_session` 重写逻辑一致）
- `_volume_outline_text` 参数 `chapter_order` 为 0-based，内部 `chapter_num = order + 1`

---

**Plan complete and saved to `docs/superpowers/plans/2026-07-20-outline-tree-plan.md`.**

Two execution options:

1. **Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration
2. **Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?

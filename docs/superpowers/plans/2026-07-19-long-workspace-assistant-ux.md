# 长篇工作台重做 / 助手窗口缩放 / 确认去重 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 长篇项目各分类页改为「列表+编辑器」双栏工作台（搜索/分组/直接编辑）；AI 助手窗口支持八方向拖拽缩放；确认添加时同名实体自动合并更新。

**Architecture:** 前端新增配置驱动的通用组件 `EntityWorkbench`，在 `LongWorkspace` 中替换现有平铺卡片面板；`useResizable` 扩展方向参数；后端 `change_apply.apply_change` 的 add 分支增加查重，命中自动转 update（world 完全相同则跳过）。

**Tech Stack:** React 19 + TS + Tailwind（前端）；FastAPI + SQLAlchemy 2.0 async + SQLite + pytest（后端）。

**Spec:** `docs/superpowers/specs/2026-07-19-long-workspace-assistant-ux-design.md`

## Global Constraints

- 后端命令 cwd 一律为 `backend/`（imports 是包根绝对导入），测试运行：`cd backend && python -m pytest tests/ -v`。
- 后端语法检查：`cd backend && python -m compileall app`。
- 前端验证：`cd frontend && npx tsc -b`，改动完成后 `npm run build`。
- 前端无测试运行器；前端任务以 tsc + build + 手动冒烟为验收。
- 视觉风格保持现有体系（沿用 `src/components/ui.tsx` 的 Button/Input/Textarea/Card/SectionTitle/Empty 与现有配色、圆角），不引入新依赖、新设计系统。
- `tsconfig.json` 的 `noImplicitAny: false` 不得重新开启。
- 所有提交信息用中文/英文 conventional commits（与仓库历史一致，如 `feat(frontend): ...`）。

---

### Task 1: 后端确认去重（change_apply 自动合并/跳过）

**Files:**
- Modify: `backend/app/services/change_apply.py`
- Test: `backend/tests/test_change_apply_dedup.py`（新建）

**Interfaces:**
- Consumes: 现有 `apply_change(db, project_id, change)`、`_ENTITY_REPO`、`_ENTITY_MODELS`、`repo.*` CRUD（均返回 dict）。
- Produces:
  - `_find_duplicate(db, entity_type, project_id, after) -> ORM row | None`
  - `apply_change` 返回值新增可选键：`merged_into: str`、`skipped_duplicate: bool`、`before: dict`（合并时）。
  - `confirm_session` 写 `LongChangeRecord` 时 `entity_id` 用 `r.get("entity_id") or ch.get("entity_id")`，`before` 用 `r.get("before") or ch.get("before")`（合并场景记录真实前镜像）。

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_change_apply_dedup.py`：

```python
"""确认添加去重：同名实体自动合并更新，world 完全相同则跳过。"""
from __future__ import annotations

import pytest

from app.database import create_all, engine, AsyncSessionLocal
from app import repositories as repo
from app.models import Project
from app.services.change_apply import apply_change


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module", autouse=True)
async def setup_db():
    await create_all()
    yield


@pytest.fixture(autouse=True)
async def cleanup_tables():
    yield
    async with engine.begin() as conn:
        for table in (
            "long_change_records",
            "long_characters",
            "long_world_settings",
            "projects",
        ):
            await conn.exec_driver_sql(f"DELETE FROM {table};")


async def _make_project(db) -> str:
    p = Project(type="long", title="t", description="")
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p.id


@pytest.mark.anyio
async def test_add_duplicate_character_merges_into_update():
    async with AsyncSessionLocal() as db:
        pid = await _make_project(db)
        await repo.create_character(db, {"project_id": pid, "name": "张三", "traits": "沉稳"})

        r = await apply_change(db, pid, {
            "entity_type": "character", "action": "add",
            "after": {"name": "张三 ", "ability": "剑术"},
        })

        assert r["ok"] is True
        assert r.get("merged_into")
        chars = await repo.list_characters(db, pid)
        assert len(chars) == 1
        assert chars[0]["ability"] == "剑术"   # 非空字段覆盖
        assert chars[0]["traits"] == "沉稳"    # 未提供的字段保留


@pytest.mark.anyio
async def test_add_character_case_insensitive_merge():
    async with AsyncSessionLocal() as db:
        pid = await _make_project(db)
        await repo.create_character(db, {"project_id": pid, "name": "Alice"})
        r = await apply_change(db, pid, {
            "entity_type": "character", "action": "add",
            "after": {"name": "alice", "traits": "冷静"},
        })
        assert r.get("merged_into")
        assert len(await repo.list_characters(db, pid)) == 1


@pytest.mark.anyio
async def test_add_new_character_not_merged():
    async with AsyncSessionLocal() as db:
        pid = await _make_project(db)
        await repo.create_character(db, {"project_id": pid, "name": "张三"})
        r = await apply_change(db, pid, {
            "entity_type": "character", "action": "add",
            "after": {"name": "李四"},
        })
        assert r["ok"] is True
        assert "merged_into" not in r
        assert len(await repo.list_characters(db, pid)) == 2


@pytest.mark.anyio
async def test_add_world_exact_duplicate_skipped():
    async with AsyncSessionLocal() as db:
        pid = await _make_project(db)
        await repo.create_world(db, {"project_id": pid, "category": "地理", "content": "大陆分为五洲"})
        r = await apply_change(db, pid, {
            "entity_type": "world", "action": "add",
            "after": {"category": "地理", "content": "大陆分为五洲"},
        })
        assert r["ok"] is True
        assert r.get("skipped_duplicate") is True
        assert len(await repo.list_world(db, pid)) == 1


@pytest.mark.anyio
async def test_add_duplicate_with_empty_fields_skipped():
    """after 全为空字段时视为无操作跳过，不覆盖已有数据。"""
    async with AsyncSessionLocal() as db:
        pid = await _make_project(db)
        await repo.create_character(db, {"project_id": pid, "name": "张三", "traits": "沉稳"})
        r = await apply_change(db, pid, {
            "entity_type": "character", "action": "add",
            "after": {"name": "张三", "traits": "", "ability": None},
        })
        assert r.get("skipped_duplicate") is True
        chars = await repo.list_characters(db, pid)
        assert chars[0]["traits"] == "沉稳"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_change_apply_dedup.py -v`
Expected: FAIL（`merged_into` / `skipped_duplicate` 不存在，断言失败）。

注意：若 pytest 报 `anyio` marker 未知，检查 `backend/tests/test_assistant_history.py` 使用相同标记可运行——沿用其模式即可，不要改 pytest 配置。

- [ ] **Step 3: 实现查重与合并**

`backend/app/services/change_apply.py`：

在 `_ENTITY_MODELS` 之后新增：

```python
_DEDUP_KEY_FIELD = {
    "character": "name",
    "foreshadow": "title",
    "plot": "title",
    "outline": "title",
}


def _norm_text(v: Any) -> str:
    return str(v or "").strip().lower()


def _row_snapshot(row) -> dict:
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


async def _find_duplicate(db: AsyncSession, entity_type: str, project_id: str, after: dict):
    """按匹配键查同项目内已存在的实体。world 按 category+content 完全相同匹配；
    character/foreshadow/plot/outline 按名称/标题（trim + 大小写不敏感）匹配。"""
    model = _ENTITY_MODELS.get(entity_type)
    if model is None:
        return None
    res = await db.execute(select(model).where(model.project_id == project_id))
    rows = res.scalars().all()
    if entity_type == "world":
        cat = _norm_text(after.get("category"))
        content = _norm_text(after.get("content"))
        if not cat and not content:
            return None
        for row in rows:
            if _norm_text(row.category) == cat and _norm_text(row.content) == content:
                return row
        return None
    key = _DEDUP_KEY_FIELD.get(entity_type)
    if not key:
        return None
    val = _norm_text(after.get(key))
    if not val:
        return None
    for row in rows:
        if _norm_text(getattr(row, key)) == val:
            return row
    return None
```

修改 `apply_change` 的 add 分支（替换现有 `if action == "add":` 块）：

```python
        merged_info: dict | None = None
        if action == "add":
            try:
                dup = await _find_duplicate(db, entity_type, project_id, after)
            except Exception:
                logger.warning("查重异常，按新增处理", exc_info=True)
                dup = None
            if dup is not None:
                if entity_type == "world":
                    return {"ok": True, "entity_type": entity_type, "entity_id": dup.id, "skipped_duplicate": True}
                merged_fields = {
                    k: v for k, v in after.items()
                    if v is not None and v != "" and v != [] and v != {}
                }
                if not merged_fields:
                    return {"ok": True, "entity_type": entity_type, "entity_id": dup.id, "skipped_duplicate": True}
                merged_info = {"before": _row_snapshot(dup)}
                row = await update_fn(db, dup.id, merged_fields)
                if not row:
                    raise NotFoundError("待更新实体不存在")
                new_id = dup.id
                after = merged_fields  # Neo4j 镜像只同步实际应用的字段
            else:
                data = dict(after)
                data["project_id"] = project_id
                row = await create_fn(db, data)
                new_id = row.get("id")
        elif action == "update":
            ...
```

（`elif`/`else` 分支保持原样。）

修改函数末尾的返回（Neo4j 镜像块与最终 return）：

```python
        # —— Neo4j 镜像（id 主键，可选）——
        g = get_graph()
        if g:
            try:
                await g.sync_entity(entity_type, new_id, after)
            except Exception as e:  # 镜像失败不影响真相源，但结构化上报
                return {"ok": True, "entity_id": new_id, "warning": f"Neo4j 同步失败：{e}"}

        result = {"ok": True, "entity_type": entity_type, "entity_id": new_id}
        if merged_info is not None:
            result["merged_into"] = new_id
            result["before"] = merged_info["before"]
        return result
```

修改 `confirm_session` 中写 `LongChangeRecord` 的两行：

```python
            db.add(LongChangeRecord(
                project_id=project_id,
                entity_type=ch.get("entity_type"),
                entity_id=r.get("entity_id") or ch.get("entity_id"),
                before=r.get("before") or ch.get("before"),
                after=ch.get("after"),
                status="applied",
            ))
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_change_apply_dedup.py -v && python -m compileall app`
Expected: 5 passed；compileall 无错误。

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/change_apply.py backend/tests/test_change_apply_dedup.py
git commit -m "feat(backend): dedup on confirm — merge duplicate add into update"
```

---

### Task 2: 前端确认结果去重反馈

**Files:**
- Modify: `frontend/src/stores/useAssistantSession.ts`

**Interfaces:**
- Consumes: `assistantApi.confirm` 返回 `{ok, applied: [{merged_into?, skipped_duplicate?}], errors}`（Task 1 后端）。
- Produces: 合并/跳过时在 `messages` 末尾追加一条本地 assistant 提示消息。

- [ ] **Step 1: 修改 confirm 成功分支**

`frontend/src/stores/useAssistantSession.ts` 的 `confirm` 中，`if (!data.ok) { ... return; }` 之后、更新 `messages[lastAssistant]` 之前/之后均可，在成功路径（`data.ok` 为真）的 `set({...})` 前插入：

```ts
      const applied = data.applied || [];
      const merged = applied.filter((a: any) => a.merged_into).length;
      const skipped = applied.filter((a: any) => a.skipped_duplicate).length;
      if (merged > 0 || skipped > 0) {
        const parts: string[] = [];
        if (merged > 0) parts.push(`${merged} 条与现有条目同名，已合并更新`);
        if (skipped > 0) parts.push(`${skipped} 条重复内容已跳过`);
        messages.push({
          id: `local-dedup-${Date.now()}`,
          role: "assistant",
          content: `去重处理：${parts.join("；")}。`,
          created_at: new Date().toISOString(),
        });
      }
```

（该分支的 `messages` 变量是已复制的数组，直接 push 后由后续 `set({ messages, ... })` 提交。）

- [ ] **Step 2: 类型检查 + 构建**

Run: `cd frontend && npx tsc -b`
Expected: 无错误。

- [ ] **Step 3: Commit**

```bash
git add frontend/src/stores/useAssistantSession.ts
git commit -m "feat(frontend): show merge/skip feedback after confirm dedup"
```

---

### Task 3: EntityWorkbench 通用双栏组件

**Files:**
- Create: `frontend/src/components/EntityWorkbench.tsx`

**Interfaces:**
- Consumes: `@/components/ui` 的 `Button, Input, Textarea, SectionTitle, Empty`；`longApi` 风格的 CRUD API 对象。
- Produces（Task 4 依赖）:
  - `export interface FieldDef { key: string; label: string; multiline?: boolean; options?: string[] }`
  - `export interface EntityWorkbenchConfig { kind, label, fields, titleOf, subtitleOf?, groupBy?, groupOrder?, searchKeys, sortBy? }`
  - `export function EntityWorkbench({ pid, config, api, editorActions }: EntityWorkbenchProps)`

- [ ] **Step 1: 创建组件**

```tsx
import { useEffect, useMemo, useState } from "react";
import { Button, Input, Textarea, SectionTitle, Empty } from "@/components/ui";

export interface FieldDef {
  key: string;
  label: string;
  multiline?: boolean;
  options?: string[];
}

export interface EntityWorkbenchConfig {
  kind: string;
  label: string;
  fields: FieldDef[];
  titleOf: (item: any) => string;
  subtitleOf?: (item: any) => string;
  groupBy?: (item: any) => string;
  groupOrder?: string[];
  searchKeys: string[];
  sortBy?: (a: any, b: any) => number;
}

interface EntityApi {
  list: (pid: string) => Promise<{ data: any[] }>;
  add: (data: any) => Promise<any>;
  upd: (id: string, data: any) => Promise<any>;
  del: (id: string) => Promise<any>;
}

interface EntityWorkbenchProps {
  pid: string;
  config: EntityWorkbenchConfig;
  api: EntityApi;
  /** 编辑器头部的额外操作（如大纲的"复制为新版"），仅在选中已有条目时渲染 */
  editorActions?: (item: any, reload: () => void) => React.ReactNode;
}

const selectClass =
  "w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink outline-none transition focus:border-accent focus:ring-1 focus:ring-accent/30";

export function EntityWorkbench({ pid, config, api, editorActions }: EntityWorkbenchProps) {
  const [items, setItems] = useState<any[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState<Record<string, string>>({});
  const [search, setSearch] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    try {
      const { data } = await api.list(pid);
      setItems(data || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    }
  };

  useEffect(() => {
    setSelectedId(null);
    setCreating(false);
    setForm({});
    setSearch("");
    setError(null);
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid, config.kind]);

  const selected = useMemo(
    () => items.find((it) => it.id === selectedId) || null,
    [items, selectedId]
  );

  // 选中条目变化时载入表单（creating 时清空）
  useEffect(() => {
    if (creating) {
      setForm({});
      return;
    }
    if (selected) {
      const next: Record<string, string> = {};
      config.fields.forEach((f) => (next[f.key] = selected[f.key] ?? ""));
      setForm(next);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, creating]);

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    let list = items;
    if (q) {
      list = list.filter((it) =>
        config.searchKeys.some((k) => String(it[k] || "").toLowerCase().includes(q))
      );
    }
    if (config.sortBy) list = [...list].sort(config.sortBy);
    return list;
  }, [items, search, config]);

  const groups = useMemo(() => {
    if (!config.groupBy) return [{ name: "", items: visible }];
    const map = new Map<string, any[]>();
    visible.forEach((it) => {
      const g = config.groupBy!(it) || "未分组";
      if (!map.has(g)) map.set(g, []);
      map.get(g)!.push(it);
    });
    const names = Array.from(map.keys());
    if (config.groupOrder) {
      const order = config.groupOrder;
      names.sort((a, b) => {
        const ia = order.indexOf(a);
        const ib = order.indexOf(b);
        return (ia === -1 ? order.length : ia) - (ib === -1 ? order.length : ib);
      });
    } else {
      names.sort();
    }
    return names.map((name) => ({ name, items: map.get(name)! }));
  }, [visible, config]);

  const handleSelect = (id: string) => {
    setCreating(false);
    setSelectedId(id);
    setError(null);
  };

  const handleNew = () => {
    setSelectedId(null);
    setCreating(true);
    setForm({});
    setError(null);
  };

  const handleSave = async () => {
    setError(null);
    const payload: any = {};
    config.fields.forEach((f) => (payload[f.key] = form[f.key] ?? ""));
    try {
      if (creating) {
        const { data } = await api.add({ ...payload, project_id: pid });
        await load();
        setCreating(false);
        if (data?.id) setSelectedId(data.id);
      } else if (selectedId) {
        await api.upd(selectedId, payload);
        await load();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    }
  };

  const handleDelete = async () => {
    if (!selectedId) return;
    if (!window.confirm(`确定删除该${config.label}吗？`)) return;
    try {
      await api.del(selectedId);
      setSelectedId(null);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "删除失败");
    }
  };

  const editing = creating || selected !== null;

  return (
    <div className="flex h-full flex-col">
      <SectionTitle>{config.label}</SectionTitle>
      <div className="mt-4 flex h-0 flex-1 gap-4">
        {/* 中栏：搜索 + 分组列表 */}
        <div className="flex w-72 shrink-0 flex-col border border-line bg-surface p-3">
          <div className="mb-3 flex gap-2">
            <Input
              placeholder={`搜索${config.label}…`}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <Button variant="primary" className="shrink-0" onClick={handleNew}>
              新增
            </Button>
          </div>
          <div className="flex-1 overflow-auto">
            {visible.length === 0 && <Empty text={search ? "无匹配结果" : `暂无${config.label}，点击「新增」创建`} />}
            {groups.map((g) => (
              <div key={g.name || "_all"} className="mb-3">
                {g.name && (
                  <div className="mb-1 px-1 text-xs font-medium text-muted">
                    {g.name}（{g.items.length}）
                  </div>
                )}
                {g.items.map((it) => (
                  <div
                    key={it.id}
                    onClick={() => handleSelect(it.id)}
                    className={
                      "mb-1 cursor-pointer border border-line px-3 py-2 transition-colors " +
                      (selectedId === it.id
                        ? "border-accent bg-accent-soft"
                        : "bg-surface hover:bg-surface-2")
                    }
                  >
                    <div className="truncate text-sm font-medium text-ink">{config.titleOf(it)}</div>
                    {config.subtitleOf && (
                      <div className="truncate text-xs text-muted">{config.subtitleOf(it)}</div>
                    )}
                  </div>
                ))}
              </div>
            ))}
          </div>
        </div>

        {/* 右栏：编辑器 */}
        <div className="flex flex-1 flex-col overflow-auto border border-line bg-surface p-4">
          {error && (
            <div className="mb-3 border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
              {error}
            </div>
          )}
          {!editing ? (
            <div className="flex h-full items-center justify-center">
              <Empty text="从左侧选择条目进行编辑，或点击「新增」创建" />
            </div>
          ) : (
            <>
              <div className="mb-3 flex items-center justify-between">
                <span className="text-sm font-medium text-ink">
                  {creating ? `新增${config.label}` : `编辑${config.label}`}
                </span>
                <div className="flex items-center gap-2">
                  {!creating && selected && editorActions?.(selected, load)}
                  <Button variant="primary" onClick={handleSave}>
                    保存
                  </Button>
                  {!creating && (
                    <Button variant="ghost" onClick={handleDelete}>
                      删除
                    </Button>
                  )}
                </div>
              </div>
              <div className="space-y-3">
                {config.fields.map((f) => (
                  <div key={f.key}>
                    <label className="mb-1 block text-xs text-muted">{f.label}</label>
                    {f.options ? (
                      <select
                        className={selectClass}
                        value={form[f.key] || ""}
                        onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
                      >
                        <option value="">（未设置）</option>
                        {f.options.map((o) => (
                          <option key={o} value={o}>{o}</option>
                        ))}
                      </select>
                    ) : f.multiline ? (
                      <Textarea
                        rows={4}
                        value={form[f.key] || ""}
                        onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
                      />
                    ) : (
                      <Input
                        value={form[f.key] || ""}
                        onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
                      />
                    )}
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: 类型检查**

Run: `cd frontend && npx tsc -b`
Expected: 无错误。

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/EntityWorkbench.tsx
git commit -m "feat(frontend): add generic EntityWorkbench (list + editor) component"
```

---

### Task 4: LongWorkspace 接入 EntityWorkbench

**Files:**
- Modify: `frontend/src/pages/LongWorkspace.tsx`

**Interfaces:**
- Consumes: Task 3 的 `EntityWorkbench` / `EntityWorkbenchConfig`；现有 `longApi`（含全部 `update*` 方法）。
- Produces: 分类 tab = `character | foreshadow | world | plot | outline` 均渲染 `EntityWorkbench`；`ChapterPanel`、`GraphPanel`、左侧导航不变。

- [ ] **Step 1: 重写 LongWorkspace 的实体面板**

在 `frontend/src/pages/LongWorkspace.tsx` 中：

1. 修改 import：

```tsx
import { useEffect, useRef, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { longApi } from "@/api/long";
import { graphApi } from "@/api/graph";
import { ChapterList } from "@/components/chapter/ChapterList";
import { ChapterEditor } from "@/components/chapter/ChapterEditor";
import { EntityWorkbench, type EntityWorkbenchConfig } from "@/components/EntityWorkbench";
import { Button, Card, SectionTitle } from "@/components/ui";
import type { Chapter } from "@/types";
```

（移除不再使用的 `Input, Textarea`；`Button` 仍被导航与 GraphPanel 使用则保留——若 tsc 报未使用再删。）

2. 替换 tab 渲染区（原 `{tab === "outline" && <OutlinePanel .../>}` 等五个分支）：

```tsx
        {(tab === "outline" || tab === "character" || tab === "foreshadow" || tab === "world" || tab === "plot") && (
          <EntityWorkbench
            pid={id!}
            config={WORKBENCH_CONFIGS[tab]}
            api={KIND_API[tab]}
            editorActions={tab === "outline" ? makeOutlineActions(id!) : undefined}
          />
        )}
        {tab === "chapter" && <ChapterPanel pid={id!} />}
        {tab === "graph" && <GraphPanel pid={id!} />}
```

3. 用以下内容整体替换 `OutlinePanel` 函数与 `KIND_API`、`CrudPanel`（删除这两个函数，替换为）：

```tsx
const KIND_API: Record<string, any> = {
  outline: { list: longApi.outlines, add: longApi.addOutline, upd: longApi.updateOutline, del: longApi.deleteOutline },
  character: { list: longApi.characters, add: longApi.addCharacter, upd: longApi.updateCharacter, del: longApi.deleteCharacter },
  foreshadow: { list: longApi.foreshadows, add: longApi.addForeshadow, upd: longApi.updateForeshadow, del: longApi.deleteForeshadow },
  world: { list: longApi.world, add: longApi.addWorld, upd: longApi.updateWorld, del: longApi.deleteWorld },
  plot: { list: longApi.plot, add: longApi.addPlot, upd: longApi.updatePlot, del: longApi.deletePlot },
};

const WORKBENCH_CONFIGS: Record<string, EntityWorkbenchConfig> = {
  outline: {
    kind: "outline",
    label: "大纲",
    fields: [
      { key: "title", label: "标题" },
      { key: "content", label: "内容", multiline: true },
    ],
    titleOf: (it) => it.title || "（无标题）",
    subtitleOf: (it) => (it.content || "").slice(0, 40),
    searchKeys: ["title", "content"],
    sortBy: (a, b) => (a.order || 0) - (b.order || 0),
  },
  character: {
    kind: "character",
    label: "角色",
    fields: [
      { key: "name", label: "名称" },
      { key: "traits", label: "性格", multiline: true },
      { key: "ability", label: "能力", multiline: true },
      { key: "status", label: "状态" },
    ],
    titleOf: (it) => it.name || "（未命名）",
    subtitleOf: (it) => (it.traits || "").slice(0, 30),
    groupBy: (it) => it.status || "未知",
    searchKeys: ["name", "traits"],
  },
  foreshadow: {
    kind: "foreshadow",
    label: "伏笔",
    fields: [
      { key: "title", label: "标题" },
      { key: "content", label: "内容", multiline: true },
      { key: "state", label: "状态", options: ["pending", "revealed", "abandoned"] },
    ],
    titleOf: (it) => it.title || "（无标题）",
    subtitleOf: (it) => (it.content || "").slice(0, 30),
    groupBy: (it) => it.state || "pending",
    groupOrder: ["pending", "revealed", "abandoned"],
    searchKeys: ["title", "content"],
  },
  world: {
    kind: "world",
    label: "世界观",
    fields: [
      { key: "category", label: "分类" },
      { key: "content", label: "内容", multiline: true },
    ],
    titleOf: (it) => it.category || "未分类",
    subtitleOf: (it) => (it.content || "").slice(0, 40),
    groupBy: (it) => it.category || "未分类",
    searchKeys: ["category", "content"],
  },
  plot: {
    kind: "plot",
    label: "剧情节点",
    fields: [
      { key: "title", label: "标题" },
      { key: "summary", label: "概要", multiline: true },
      { key: "timeline_pos", label: "时间位置" },
    ],
    titleOf: (it) => it.title || "（无标题）",
    subtitleOf: (it) => (it.summary || "").slice(0, 30),
    searchKeys: ["title", "summary"],
    sortBy: (a, b) =>
      String(a.timeline_pos || "").localeCompare(String(b.timeline_pos || ""), "zh") ||
      (a.order || 0) - (b.order || 0),
  },
};

function makeOutlineActions(pid: string) {
  return (item: any, reload: () => void) => (
    <Button
      variant="ghost"
      onClick={async () => {
        await longApi.addOutline({
          project_id: pid,
          title: item.title,
          content: item.content,
          version_chain: item.id,
        });
        reload();
      }}
    >
      复制为新版
    </Button>
  );
}
```

4. 删除 `OutlinePanel` 与 `CrudPanel` 两个函数定义。`ChapterPanel`、`GraphPanel` 保持不变。

- [ ] **Step 2: 类型检查 + 构建**

Run: `cd frontend && npx tsc -b && npm run build`
Expected: 无错误；dist 构建成功。

- [ ] **Step 3: 手动冒烟**

启动后端（`cd backend && uvicorn app.main:app --port 8765`），打开一个长篇项目：角色页搜索/分组/选中编辑保存/新增/删除；世界观按分类分组显示；大纲「复制为新版」可用。

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/LongWorkspace.tsx frontend/dist
git commit -m "feat(frontend): rework long-workspace entity tabs as EntityWorkbench"
```

（注：若仓库不跟踪 `frontend/dist`，只提交 tsx。用 `git status` 确认。）

---

### Task 5: 助手窗口八方向缩放

**Files:**
- Modify: `frontend/src/hooks/useResizable.ts`
- Modify: `frontend/src/components/FloatingAssistant.tsx`

**Interfaces:**
- Consumes: 现有 `useResizable({initial, min, max, storageKey})` 状态与持久化逻辑。
- Produces:
  - `export type ResizeDirection = "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw"`
  - `startResize(e: React.MouseEvent, direction?: ResizeDirection)`（默认 `"se"`，向后兼容）。

- [ ] **Step 1: 升级 useResizable**

`frontend/src/hooks/useResizable.ts`：在 `Size` 接口后新增方向类型，并替换 `startResize`：

```ts
export type ResizeDirection = "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw";
```

```ts
  const startResize = useCallback((e: React.MouseEvent, direction: ResizeDirection = "se") => {
    e.preventDefault();
    e.stopPropagation();
    setIsResizing(true);
    startRef.current = {
      x: e.clientX,
      y: e.clientY,
      width: size.width,
      height: size.height,
    };

    const viewportMax = {
      width: Math.min(max.width, window.innerWidth - 32),
      height: Math.min(max.height, window.innerHeight - 32),
    };

    const handleMove = (moveEvent: MouseEvent) => {
      const start = startRef.current;
      if (!start) return;
      const dx = moveEvent.clientX - start.x;
      const dy = moveEvent.clientY - start.y;
      // 面板锚定右下：w/n 方向增长 = 向左/向上扩展
      const dw = direction.includes("e") ? dx : direction.includes("w") ? -dx : 0;
      const dh = direction.includes("s") ? dy : direction.includes("n") ? -dy : 0;
      setSize({
        width: Math.max(min.width, Math.min(viewportMax.width, start.width + dw)),
        height: Math.max(min.height, Math.min(viewportMax.height, start.height + dh)),
      });
    };

    const handleUp = () => {
      setIsResizing(false);
      startRef.current = null;
      window.removeEventListener("mousemove", handleMove);
      window.removeEventListener("mouseup", handleUp);
    };

    window.addEventListener("mousemove", handleMove);
    window.addEventListener("mouseup", handleUp);
  }, [size, min, max]);
```

- [ ] **Step 2: FloatingAssistant 渲染八个热区 + 左上角可见手柄**

`frontend/src/components/FloatingAssistant.tsx`：

1. import 改为：

```tsx
import { useResizable, type ResizeDirection } from "@/hooks/useResizable";
```

2. 在组件内（`panelStyle` 定义之后）新增：

```tsx
  const resizeZones: { dir: ResizeDirection; className: string; cursor: string }[] = [
    { dir: "n", className: "absolute left-2 right-2 top-0 h-1.5", cursor: "ns-resize" },
    { dir: "s", className: "absolute bottom-0 left-2 right-2 h-1.5", cursor: "ns-resize" },
    { dir: "w", className: "absolute bottom-2 left-0 top-2 w-1.5", cursor: "ew-resize" },
    { dir: "e", className: "absolute bottom-2 right-0 top-2 w-1.5", cursor: "ew-resize" },
    { dir: "nw", className: "absolute left-0 top-0 h-3 w-3", cursor: "nwse-resize" },
    { dir: "ne", className: "absolute right-0 top-0 h-3 w-3", cursor: "nesw-resize" },
    { dir: "sw", className: "absolute bottom-0 left-0 h-3 w-3", cursor: "nesw-resize" },
    { dir: "se", className: "absolute bottom-0 right-0 h-3 w-3", cursor: "nwse-resize" },
  ];
```

3. 用以下块替换文件末尾的 `{!isMaximized && (<div onMouseDown={startResize} ...>...右下角手柄...</div>)}` 整块：

```tsx
      {!isMaximized && (
        <>
          {resizeZones.map((z) => (
            <div
              key={z.dir}
              onMouseDown={(e) => startResize(e, z.dir)}
              className={`z-10 ${z.className}`}
              style={{ cursor: z.cursor }}
            />
          ))}
          {/* 可见手柄：左上角（面板锚定右下，向左上拖拽即放大） */}
          <div
            onMouseDown={(e) => startResize(e, "nw")}
            className="absolute left-0 top-0 z-20 flex h-5 w-5 cursor-nwse-resize items-center justify-center"
            title="拖拽缩放"
          >
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none" className="text-muted">
              <path d="M4 0V4H0" stroke="currentColor" strokeWidth="1" />
              <path d="M8 0V8H0" stroke="currentColor" strokeWidth="1" />
            </svg>
          </div>
        </>
      )}
```

注意：左上角手柄会覆盖标题栏左侧区域，标题栏的会话选择器靠左排列——手柄仅 20px 宽，若遮挡「创作助手」标题，给标题栏容器加 `pl-4`（在 `flex items-center gap-2 overflow-hidden` 上加 `pl-4`）。

- [ ] **Step 3: 类型检查 + 构建**

Run: `cd frontend && npx tsc -b && npm run build`
Expected: 无错误。

- [ ] **Step 4: 手动冒烟**

打开助手面板：四边/四角均可拖拽缩放；左上角可见手柄向左上拖放大；最大化时热区禁用；还原后尺寸保持；刷新页面尺寸保持（localStorage）。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/hooks/useResizable.ts frontend/src/components/FloatingAssistant.tsx frontend/dist
git commit -m "feat(frontend): 8-direction resize for assistant panel with visible top-left handle"
```

---

## Self-Review 记录

- Spec 覆盖：三栏工作台（Task 3/4）、八方向缩放（Task 5）、确认去重+自动合并（Task 1）、合并反馈（Task 2）——全覆盖；章节/图谱 tab 不动、relations/importance 不做，与 spec「范围外」一致。
- 类型一致性：`EntityWorkbenchConfig`/`FieldDef`/`editorActions` 在 Task 3 定义、Task 4 消费，签名一致；`startResize(e, direction)` 默认参数向后兼容旧调用。
- 占位符：无。

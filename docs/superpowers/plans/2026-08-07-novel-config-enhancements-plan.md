# Novel Studio 配置增强实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Novel Studio 中实现文风配置、大纲变更类型增强、模型配置 UI 重设计、章节生成配置下沉到小说四个子系统。

**Architecture:** 采用最小改动扩展现有表：在 `Project` 上新增 `writing_style` 与 `generation_config` JSON 字段；在 `change_apply.py` 中新增 `partial_update` / `append` / `patch` 分支；`ModelConfig` 新增 `temperature` 字段并区分 LLM/Embedding 测试；前端模型配置页重设计为分类卡片。

**Tech Stack:** Python 3.11 + FastAPI + SQLAlchemy 2.0 async + SQLite；React 19 + TypeScript + Vite + Tailwind；OpenAI 兼容 API。

## Global Constraints

- 所有后端持久化变更必须通过 `app/services/change_apply.py`。
- 错误统一使用 `app.core.errors.AppError` 子类返回 `{"ok": false, "code": ..., "message": ...}`。
- `tsconfig.json` 当前 `noImplicitAny: false`，保持现状。
- 前端风格保持现有非 Lovart 设计（米色、衬线标题、圆角卡片）。
- 修改后需执行 `cd backend && python -m compileall app` 和 `cd frontend && npx tsc -b`。

---

## 子系统 1：文风配置

### Task 1.1: 扩展数据库模型

**Files:**
- Modify: `backend/app/models.py:34-52`
- Test: 启动后端，确认表结构包含新字段

**Interfaces:**
- Consumes: 无
- Produces: `Project.writing_style` (dict), `Project.generation_config` (dict)

- [ ] **Step 1: 在 `Project` 模型新增两个 JSON 字段**

在 `Project` 类的 `description` 字段后添加：

```python
    writing_style = Column(JSON, default=dict)
    generation_config = Column(JSON, default=dict)
```

并在 `to_dict()` 返回中增加：

```python
            "writing_style": self.writing_style or {},
            "generation_config": self.generation_config or {},
```

- [ ] **Step 2: 处理已有数据库迁移**

由于项目无 Alembic，在 `backend/app/database.py` 的 `create_all()` 中新增字段对已有 SQLite 数据库不生效。创建一次性迁移脚本 `backend/scripts/migrate_project_json_fields.py`：

```python
import asyncio
import sqlalchemy
from sqlalchemy import inspect, text
from app.database import engine

async def migrate():
    async with engine.begin() as conn:
        def _add_columns(sync_conn):
            cols = inspect(sync_conn).get_columns("projects")
            names = {c["name"] for c in cols}
            if "writing_style" not in names:
                sync_conn.execute(text('ALTER TABLE projects ADD COLUMN writing_style TEXT DEFAULT "{}"'))
            if "generation_config" not in names:
                sync_conn.execute(text('ALTER TABLE projects ADD COLUMN generation_config TEXT DEFAULT "{}"'))
            cols2 = inspect(sync_conn).get_columns("model_configs")
            if "temperature" not in {c["name"] for c in cols2}:
                sync_conn.execute(text('ALTER TABLE model_configs ADD COLUMN temperature FLOAT DEFAULT NULL'))
        await conn.run_sync(_add_columns)

if __name__ == "__main__":
    asyncio.run(migrate())
```

运行：`cd backend && python scripts/migrate_project_json_fields.py`

- [ ] **Step 3: 提交**

```bash
git add backend/app/models.py backend/scripts/migrate_project_json_fields.py
git commit -m "feat(writing-style): add writing_style and generation_config to Project"
```

---

### Task 1.2: 后端 Schema 与 API

**Files:**
- Modify: `backend/app/schemas/project.py`
- Modify: `backend/app/api/projects.py`
- Test: `curl` 更新项目文风字段

**Interfaces:**
- Consumes: `Project.writing_style`, `Project.generation_config`
- Produces: `ProjectUpdate.writing_style`, `ProjectOut.writing_style`

- [ ] **Step 1: 更新 Pydantic schema**

```python
# backend/app/schemas/project.py
from typing import Optional
from pydantic import BaseModel, Field


class WritingStyle(BaseModel):
    perspective: Optional[str] = None
    language_style: Optional[str] = None
    pace: Optional[str] = None
    tone: Optional[str] = None
    custom_note: Optional[str] = None


class GenerationConfig(BaseModel):
    chapter_target_words: Optional[int] = None
    content_rating: Optional[str] = None


class ProjectCreate(BaseModel):
    type: str = Field(..., pattern="^long$")
    title: str = "未命名"
    description: str = ""


class ProjectUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    writing_style: Optional[WritingStyle] = None
    generation_config: Optional[GenerationConfig] = None


class ProjectOut(BaseModel):
    id: str
    type: str
    title: str
    description: str
    writing_style: dict
    generation_config: dict
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
```

- [ ] **Step 2: 更新 projects API**

在 `update_project` 中增加：

```python
    if payload.writing_style is not None:
        p.writing_style = payload.writing_style.model_dump(exclude_none=True)
    if payload.generation_config is not None:
        p.generation_config = payload.generation_config.model_dump(exclude_none=True)
```

- [ ] **Step 3: 测试**

```bash
cd backend
python -m compileall app
curl -X PUT http://127.0.0.1:8765/api/projects/{project_id} \
  -H "Content-Type: application/json" \
  -d '{"writing_style":{"perspective":"第三人称限知","custom_note":"对话短促"}}'
```

- [ ] **Step 4: 提交**

```bash
git add backend/app/schemas/project.py backend/app/api/projects.py
git commit -m "feat(writing-style): expose writing_style and generation_config in project API"
```

---

### Task 1.3: 将文风注入章节生成 Prompt

**Files:**
- Modify: `backend/app/agents/workflows/chapter_generation.py:31-73`
- Modify: `backend/app/agents/harness/prompts/chapter_generation.py`（添加风格占位处）
- Test: 运行章节生成，检查日志中 prompt 是否包含文风

**Interfaces:**
- Consumes: `context["writing_style"]`
- Produces: `chapter_text_prompt(context)` 生成的 system prompt 包含文风段落

- [ ] **Step 1: 在 `load_context` 中读取项目文风**

修改 `load_context`：

```python
from app.models import Project

    project = await db.get(Project, project_id)
    writing_style = (project.writing_style or {}) if project else {}
```

将 `writing_style` 注入 context：

```python
        "writing_style": writing_style,
```

- [ ] **Step 2: 在 `chapter_text_prompt` 中消费文风**

在 `backend/app/agents/harness/prompts/chapter_generation.py` 的 `chapter_text_prompt` 函数末尾（返回前）追加：

```python
    style = context.get("writing_style") or {}
    if any(style.values()):
        parts = []
        if style.get("perspective"):
            parts.append(f"叙事视角：{style['perspective']}。")
        if style.get("language_style"):
            parts.append(f"语言风格：{style['language_style']}。")
        if style.get("pace"):
            parts.append(f"节奏：{style['pace']}。")
        if style.get("tone"):
            parts.append(f"情感基调：{style['tone']}。")
        if style.get("custom_note"):
            parts.append(f"补充：{style['custom_note']}")
        prompt += "\n\n【文风要求】\n" + "".join(parts)
```

- [ ] **Step 3: 测试**

```bash
cd backend && python -m compileall app
```

手动运行一次章节生成，查看后端日志中 prompt 是否包含「文风要求」。

- [ ] **Step 4: 提交**

```bash
git add backend/app/agents/workflows/chapter_generation.py backend/app/agents/harness/prompts/chapter_generation.py
git commit -m "feat(writing-style): inject writing_style into chapter generation prompt"
```

---

### Task 1.4: 前端项目设置页（文风表单）

**Files:**
- Create: `frontend/src/components/project/ProjectSettingsDialog.tsx`
- Modify: `frontend/src/pages/LongWorkspace.tsx:23-40`
- Modify: `frontend/src/types/index.ts:1-8`
- Test: 在 LongWorkspace 中打开项目设置，保存文风，刷新后仍保留

**Interfaces:**
- Consumes: `Project.writing_style`, `projectsApi.update`
- Produces: `ProjectSettingsDialog` 组件

- [ ] **Step 1: 更新 TypeScript 类型**

```typescript
// frontend/src/types/index.ts
export interface WritingStyle {
  perspective?: string;
  language_style?: string;
  pace?: string;
  tone?: string;
  custom_note?: string;
}

export interface GenerationConfig {
  chapter_target_words?: number;
  content_rating?: string;
}

export interface Project {
  id: string;
  type: "long";
  title: string;
  description: string;
  writing_style?: WritingStyle;
  generation_config?: GenerationConfig;
  created_at: string | null;
  updated_at: string | null;
}
```

- [ ] **Step 2: 确认 `projectsApi.update` 支持新字段**

`frontend/src/api/projects.ts` 已存在：

```typescript
export const projectsApi = {
  list: (type?: string) => api.get<Project[]>("/projects", { params: { type } }),
  get: (id: string) => api.get<Project>(`/projects/${id}`),
  create: (type: string, title: string, description = "") =>
    api.post<Project>("/projects", { type, title, description }),
  update: (id: string, data: Partial<Project>) => api.put(`/projects/${id}`, data),
  remove: (id: string) => api.delete(`/projects/${id}`),
};
```

`Project` 类型更新后，`update` 方法会自动接受 `writing_style` 和 `generation_config`，无需改动。

- [ ] **Step 3: 创建项目设置弹窗组件**

```tsx
// frontend/src/components/project/ProjectSettingsDialog.tsx
import { useEffect, useState } from "react";
import { projectsApi } from "@/api/projects";
import { Button, Input } from "@/components/ui";
import type { Project, WritingStyle, GenerationConfig } from "@/types";

const PERSPECTIVE_OPTIONS = ["第一人称", "第三人称限知", "第三人称全知", "多视角"];
const LANGUAGE_OPTIONS = ["现代白话", "古风文言", "翻译腔", "口语化", "华丽繁复", "简洁克制"];
const PACE_OPTIONS = ["舒缓", "适中", "紧凑", "快节奏"];
const TONE_OPTIONS = ["轻松", "沉重", "热血", "悬疑", "温情", "冷峻"];
const RATING_OPTIONS = [
  { value: "loose", label: "宽松" },
  { value: "standard", label: "标准" },
  { value: "strict", label: "严格" },
];

interface Props {
  project: Project;
  open: boolean;
  initialTab?: "style" | "generation";
  onClose: () => void;
  onSaved?: (p: Project) => void;
}

export default function ProjectSettingsDialog({ project, open, initialTab = "style", onClose, onSaved }: Props) {
  const [tab, setTab] = useState(initialTab);
  const [style, setStyle] = useState<WritingStyle>(project.writing_style || {});
  const [gen, setGen] = useState<GenerationConfig>(project.generation_config || {});
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setStyle(project.writing_style || {});
    setGen(project.generation_config || {});
    setTab(initialTab);
  }, [project, initialTab, open]);

  const save = async () => {
    setSaving(true);
    try {
      const { data } = await projectsApi.update(project.id, {
        writing_style: style,
        generation_config: gen,
      });
      onSaved?.(data);
      onClose();
    } finally {
      setSaving(false);
    }
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="w-full max-w-lg rounded-xl bg-surface p-6 shadow-lg">
        <h2 className="mb-4 font-serif text-lg font-semibold text-ink">项目设置</h2>
        <div className="mb-4 flex gap-4 border-b border-line pb-2">
          <button className={tab === "style" ? "text-ink" : "text-muted"} onClick={() => setTab("style")}>文风</button>
          <button className={tab === "generation" ? "text-ink" : "text-muted"} onClick={() => setTab("generation")}>章节生成</button>
        </div>

        {tab === "style" ? (
          <div className="space-y-3">
            <SelectRow label="叙事视角" value={style.perspective || ""} options={PERSPECTIVE_OPTIONS} onChange={(v) => setStyle({ ...style, perspective: v })} />
            <SelectRow label="语言风格" value={style.language_style || ""} options={LANGUAGE_OPTIONS} onChange={(v) => setStyle({ ...style, language_style: v })} />
            <SelectRow label="节奏" value={style.pace || ""} options={PACE_OPTIONS} onChange={(v) => setStyle({ ...style, pace: v })} />
            <SelectRow label="情感基调" value={style.tone || ""} options={TONE_OPTIONS} onChange={(v) => setStyle({ ...style, tone: v })} />
            <div>
              <label className="mb-1 block text-sm text-ink">自定义补充</label>
              <textarea
                className="h-24 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-accent"
                value={style.custom_note || ""}
                onChange={(e) => setStyle({ ...style, custom_note: e.target.value })}
                placeholder="补充任何关于文风的自由描述..."
              />
            </div>
          </div>
        ) : (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <span className="text-sm text-ink">每章目标字数</span>
              <Input type="number" min={1000} max={8000} value={gen.chapter_target_words || 2500} onChange={(e) => setGen({ ...gen, chapter_target_words: Number(e.target.value) })} className="w-24" />
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-ink">内容尺度等级</span>
              <select
                className="w-40 rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-accent"
                value={gen.content_rating || "standard"}
                onChange={(e) => setGen({ ...gen, content_rating: e.target.value })}
              >
                {RATING_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </div>
          </div>
        )}

        <div className="mt-6 flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>取消</Button>
          <Button variant="primary" onClick={save} disabled={saving}>{saving ? "保存中..." : "保存"}</Button>
        </div>
      </div>
    </div>
  );
}

function SelectRow({ label, value, options, onChange }: { label: string; value: string; options: string[]; onChange: (v: string) => void }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-sm text-ink">{label}</span>
      <select
        className="w-48 rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-accent"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">未指定</option>
        {options.map((o) => (
          <option key={o} value={o}>{o}</option>
        ))}
      </select>
    </div>
  );
}
```

- [ ] **Step 4: 在 LongWorkspace 中添加入口**

在 `LongWorkspace` 顶部添加「项目设置」按钮：

```tsx
import ProjectSettingsDialog from "@/components/project/ProjectSettingsDialog";

const [settingsOpen, setSettingsOpen] = useState(false);
const [project, setProject] = useState<Project | null>(null);

useEffect(() => {
  if (!id) return;
  projectsApi.get(id).then((r) => setProject(r.data));
}, [id]);
```

在侧边栏「返回」按钮下方添加：

```tsx
<Button variant="subtle" className="mb-4 w-full justify-start" onClick={() => setSettingsOpen(true)}>⚙ 项目设置</Button>
```

渲染弹窗：

```tsx
{project && (
  <ProjectSettingsDialog
    project={project}
    open={settingsOpen}
    onClose={() => setSettingsOpen(false)}
    onSaved={setProject}
  />
)}
```

- [ ] **Step 5: 测试**

```bash
cd frontend && npx tsc -b
```

启动前后端，打开 LongWorkspace，点击「项目设置」，设置文风后保存，刷新页面验证保留。

- [ ] **Step 6: 提交**

```bash
git add frontend/src/types/index.ts frontend/src/api/projects.ts frontend/src/components/project/ProjectSettingsDialog.tsx frontend/src/pages/LongWorkspace.tsx
git commit -m "feat(writing-style): add project settings dialog for writing style"
```

---

## 子系统 2：新增变更类型

### Task 2.1: 扩展 change_apply 支持 partial_update / append / patch

**Files:**
- Modify: `backend/app/services/change_apply.py:124-275`
- Test: 通过 `/confirm` 接口测试三种新变更类型

**Interfaces:**
- Consumes: `change["action"]` ∈ {"add", "update", "delete", "partial_update", "append", "patch"}
- Produces: `apply_change()` 对新增类型的处理逻辑

- [ ] **Step 1: 更新 `_validate_outline_change` 支持新类型**

将 `_validate_outline_change` 开头：

```python
    if action == "delete":
```

改为：

```python
    if action == "delete":
```

保持不变。将 `action == "update"` 的检查扩展为：

```python
    if action in ("update", "partial_update", "append", "patch") and entity_id:
```

- [ ] **Step 2: 在 `apply_change` 中新增分支**

在 `elif action == "update":` 之前插入：

```python
        elif action == "partial_update":
            if not entity_id:
                raise AppError("partial_update 缺少 entity_id", "BAD_CHANGE", 400)
            existing = await get_fn(db, entity_id)
            if not existing:
                raise NotFoundError("待更新实体不存在")
            # 过滤掉 None 值，只更新显式字段
            merged = {k: v for k, v in after.items() if v is not None}
            row = await update_fn(db, entity_id, merged)
            if not row:
                raise NotFoundError("待更新实体不存在")
            new_id = entity_id
            after = merged
        elif action == "append":
            if not entity_id:
                raise AppError("append 缺少 entity_id", "BAD_CHANGE", 400)
            existing = await get_fn(db, entity_id)
            if not existing:
                raise NotFoundError("待追加实体不存在")
            merged = {}
            for k, v in after.items():
                old = existing.get(k)
                if isinstance(old, str) and isinstance(v, str):
                    merged[k] = old + v
                else:
                    merged[k] = v
            row = await update_fn(db, entity_id, merged)
            if not row:
                raise NotFoundError("待追加实体不存在")
            new_id = entity_id
            after = merged
        elif action == "patch":
            if not entity_id:
                raise AppError("patch 缺少 entity_id", "BAD_CHANGE", 400)
            existing = await get_fn(db, entity_id)
            if not existing:
                raise NotFoundError("待补丁实体不存在")
            search = after.get("search")
            replace = after.get("replace")
            field = after.get("field", "content")
            if not isinstance(search, str) or not isinstance(replace, str):
                raise AppError("patch 必须提供 search/replace 字符串", "BAD_CHANGE", 400)
            old_value = existing.get(field, "")
            if not isinstance(old_value, str):
                raise AppError(f"patch 字段 {field} 不是文本", "BAD_CHANGE", 400)
            count = old_value.count(search)
            if count == 0:
                raise AppError(f"patch 未找到匹配文本：{search[:50]}", "PATCH_NOT_FOUND", 400)
            if count > 1:
                raise AppError(f"patch 匹配文本出现 {count} 次，请扩大上下文", "PATCH_AMBIGUOUS", 400)
            merged = {field: old_value.replace(search, replace, 1)}
            row = await update_fn(db, entity_id, merged)
            if not row:
                raise NotFoundError("待补丁实体不存在")
            new_id = entity_id
            after = merged
```

- [ ] **Step 3: 测试**

```bash
cd backend && python -m compileall app
```

通过 assistant chat 让 AI 修改总纲，检查 staged_changes 中是否出现新 action 类型，确认 confirm 后行为正确。

- [ ] **Step 4: 提交**

```bash
git add backend/app/services/change_apply.py
git commit -m "feat(changes): add partial_update, append, patch change actions"
```

---

### Task 2.2: 更新 Worker Prompt 引导使用新类型

**Files:**
- Modify: `backend/app/agents/harness/workers/configs/broad_outline.json`
- Modify: `backend/app/agents/harness/workers/configs/outline.json`
- Modify: `backend/app/agents/harness/prompts/chapter_generation.py`（总纲相关 prompt）
- Test: 让 AI 修改总纲，观察返回的 changes 是否使用新类型

**Interfaces:**
- Consumes: 无
- Produces: Worker prompt 中新增类型说明

- [ ] **Step 1: 更新 worker JSON 配置**

在 `backend/app/agents/harness/workers/configs/broad_outline.json` 的 `output_schema.properties.changes.items.properties.action.enum` 中增加：

```json
"action": {"type": "string", "enum": ["add", "update", "partial_update", "append", "patch"]}
```

同样更新 `outline.json`。

- [ ] **Step 2: 在 prompt 中新增类型说明**

在 `backend/app/agents/harness/prompts/chapter_generation.py` 中总纲生成相关的 prompt 末尾追加：

```python
"""
变更类型说明：
- add: 新增节点
- update: 重写整个节点（必须提供完整字段，会覆盖原内容）
- partial_update: 只更新指定的字段，未提及字段保持原样
- append: 在 content 等长文本字段末尾追加内容
- patch: 通过 search + replace 精确定位替换一段文本

重要：如果用户只是修改总纲中的某一部分，优先使用 partial_update、append 或 patch，避免使用 update 导致未提及内容丢失。
"""
```

- [ ] **Step 3: 测试**

```bash
cd backend && python -m compileall app
```

在助手对话中输入「把总纲里的 XXX 改成 YYY」，确认返回的 change action 为 `patch` 或 `partial_update`。

- [ ] **Step 4: 提交**

```bash
git add backend/app/agents/harness/workers/configs/broad_outline.json backend/app/agents/harness/workers/configs/outline.json backend/app/agents/harness/prompts/chapter_generation.py
git commit -m "feat(changes): teach workers to use partial_update/append/patch"
```

---

## 子系统 3：模型配置 UI 重设计

### Task 3.1: 扩展 ModelConfig 与 Schema

**Files:**
- Modify: `backend/app/models.py:55-79`
- Modify: `backend/app/schemas/setting.py`
- Test: 通过 API 创建带 temperature 的模型

**Interfaces:**
- Consumes: 无
- Produces: `ModelConfig.temperature` (Optional[float])

- [ ] **Step 1: 在 ModelConfig 新增 temperature 字段**

```python
    temperature = Column(Float, nullable=True)
```

在 `to_dict()` 中增加：

```python
            "temperature": self.temperature,
```

- [ ] **Step 2: 更新 schema**

```python
class ModelConfigCreate(BaseModel):
    name: str
    base_url: str
    api_key: str = ""
    model: str
    level: Optional[str] = None
    embedding_model: Optional[str] = None
    embedding_dimension: Optional[int] = None
    temperature: Optional[float] = None
    is_default: bool = False


class ModelConfigUpdate(BaseModel):
    name: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    level: Optional[str] = None
    embedding_model: Optional[str] = None
    embedding_dimension: Optional[int] = None
    temperature: Optional[float] = None
    is_default: Optional[bool] = None


class ModelConfigTest(BaseModel):
    base_url: str
    api_key: str = ""
    model: str
    kind: Optional[str] = "chat"  # "chat" | "embedding"
    embedding_dimension: Optional[int] = None
```

- [ ] **Step 3: 测试**

```bash
cd backend && python -m compileall app
curl -X POST http://127.0.0.1:8765/api/settings/models \
  -H "Content-Type: application/json" \
  -d '{"name":"DeepSeek","base_url":"https://api.deepseek.com/v1","model":"deepseek-chat","temperature":0.7,"is_default":true}'
```

- [ ] **Step 4: 提交**

```bash
git add backend/app/models.py backend/app/schemas/setting.py
git commit -m "feat(models): add temperature field to ModelConfig"
```

---

### Task 3.2: 模型拉取与差异化测试 API

**Files:**
- Modify: `backend/app/api/settings.py`
- Test: `/settings/models/fetch` 和 `/settings/models/test` 对 LLM 与 Embedding 分别有效

**Interfaces:**
- Consumes: `ModelConfigTest`, `httpx`
- Produces: `POST /settings/models/fetch`, 增强的 `POST /settings/models/test`

- [ ] **Step 1: 新增拉取模型列表接口**

在 `backend/app/api/settings.py` 中新增：

```python
@router.get("/models/fetch")
async def fetch_provider_models(base_url: str, api_key: str = ""):
    url = base_url.rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            models = [item.get("id") or item.get("model") for item in data.get("data", [])]
            return {"ok": True, "models": [m for m in models if m]}
    except Exception as e:
        return {"ok": False, "error": str(e)}
```

- [ ] **Step 2: 修改测试连接接口**

替换 `/models/test` 实现：

```python
@router.post("/models/test")
async def test_model(payload: ModelConfigTest):
    if payload.kind == "embedding":
        client = LLMClient(base_url=payload.base_url, api_key=payload.api_key, model=payload.model)
        try:
            vectors = await client.embed(["ping"])
            dim = len(vectors[0]) if vectors and vectors[0] else 0
            expected = payload.embedding_dimension or dim
            if expected and dim != expected:
                return {"ok": False, "error": f"维度不匹配：返回 {dim}，预期 {expected}"}
            return {"ok": True, "dimension": dim}
        except httpx.HTTPError as e:
            return {"ok": False, "error": str(e)}

    client = LLMClient(base_url=payload.base_url, api_key=payload.api_key, model=payload.model)
    try:
        resp = await client.chat(
            [{"role": "user", "content": "ping"}],
            timeout=15,
        )
        return {"ok": True, "reply": (resp or "").strip()[:200]}
    except httpx.HTTPError as e:
        return {"ok": False, "error": str(e)}
```

- [ ] **Step 3: 测试**

```bash
cd backend && python -m compileall app
```

分别测试 LLM 和 Embedding：

```bash
curl -X POST http://127.0.0.1:8765/api/settings/models/test \
  -H "Content-Type: application/json" \
  -d '{"base_url":"https://api.deepseek.com/v1","api_key":"...","model":"deepseek-chat","kind":"chat"}'

curl -X POST http://127.0.0.1:8765/api/settings/models/test \
  -H "Content-Type: application/json" \
  -d '{"base_url":"...","api_key":"...","model":"...","kind":"embedding","embedding_dimension":1536}'
```

- [ ] **Step 4: 提交**

```bash
git add backend/app/api/settings.py
git commit -m "feat(models): add model fetch and differentiated model test API"
```

---

### Task 3.3: LLM 客户端使用模型级温度

**Files:**
- Modify: `backend/app/core/llm_factory.py`
- Test: 确认 `get_llm_client` 构造的 `LLMClient` 携带配置温度

**Interfaces:**
- Consumes: `ModelConfig.temperature`
- Produces: `LLMClient(..., temperature=cfg.temperature)`

- [ ] **Step 1: 在 `get_llm_client` 中传递温度**

```python
    if cfg:
        return LLMClient(
            base_url=cfg.base_url or None,
            api_key=cfg.api_key or None,
            model=cfg.model or None,
            temperature=cfg.temperature if cfg.temperature is not None else None,
        )
```

- [ ] **Step 2: 测试**

```bash
cd backend && python -m compileall app
```

- [ ] **Step 3: 提交**

```bash
git add backend/app/core/llm_factory.py
git commit -m "feat(models): use per-model temperature in LLMClient"
```

---

### Task 3.4: 调整默认模型种子

**Files:**
- Modify: `backend/app/services/settings_seed.py`
- Test: 删除 model_configs 后重启后端，只保留 DeepSeek

**Interfaces:**
- Consumes: 无
- Produces: 精简后的 `DEFAULT_MODEL_PRESETS`

- [ ] **Step 1: 只保留 DeepSeek 预设**

```python
DEFAULT_MODEL_PRESETS: list[dict[str, str | float]] = [
    {"name": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat", "temperature": 0.7},
]
```

修改 `seed_default_models`：

```python
        db.add(ModelConfig(
            name=preset["name"],
            base_url=preset["base_url"],
            api_key="",
            model=preset["model"],
            temperature=preset.get("temperature"),
            is_default=True,
        ))
```

- [ ] **Step 2: 测试**

```bash
cd backend && python -m compileall app
```

- [ ] **Step 3: 提交**

```bash
git add backend/app/services/settings_seed.py
git commit -m "feat(models): simplify default model presets to DeepSeek only"
```

---

### Task 3.5: 前端模型配置页重设计

**Files:**
- Modify: `frontend/src/pages/SettingsPage.tsx`
- Modify: `frontend/src/api/settings.ts`
- Modify: `frontend/src/types/index.ts:10-19`
- Test: 页面分类展示、新增模型、测试连接、拉取模型列表

**Interfaces:**
- Consumes: `ModelConfig.kind`, `settingsApi.fetchModels`, `settingsApi.testModel`
- Produces: 新的 SettingsPage UI

- [ ] **Step 1: 更新类型与 API**

```typescript
// frontend/src/types/index.ts
export interface ModelConfig {
  id: string;
  name: string;
  base_url: string;
  model: string;
  is_default: boolean;
  level?: string;
  embedding_model?: string;
  embedding_dimension?: number;
  temperature?: number;
}
```

```typescript
// frontend/src/api/settings.ts
export interface ModelConfigPayload {
  name: string;
  base_url: string;
  model: string;
  api_key?: string;
  is_default?: boolean;
  level?: string;
  embedding_model?: string;
  embedding_dimension?: number;
  temperature?: number;
  kind?: "chat" | "embedding";
}

export const settingsApi = {
  get: () => api.get<UserSettings>("/settings"),
  update: (data: Partial<UserSettings>) => api.put<UserSettings>("/settings", data),
  listModels: () => api.get<ModelConfig[]>("/settings/models"),
  createModel: (data: ModelConfigPayload) => api.post("/settings/models", data),
  updateModel: (id: string, data: Partial<ModelConfigPayload>) => api.put(`/settings/models/${id}`, data),
  deleteModel: (id: string) => api.delete(`/settings/models/${id}`),
  testModel: (data: Partial<ModelConfigPayload>) => api.post("/settings/models/test", data),
  fetchModels: (base_url: string, api_key?: string) =>
    api.get<{ ok: boolean; models?: string[]; error?: string }>("/settings/models/fetch", {
      params: { base_url, api_key: api_key || "" },
    }),
};
```

- [ ] **Step 2: 重写 SettingsPage 模型配置部分**

将模型配置部分替换为分类卡片 + 可折叠新增表单。核心结构：

```tsx
const [showAdd, setShowAdd] = useState(false);
const [addForm, setAddForm] = useState({
  name: "",
  base_url: "",
  api_key: "",
  model: "",
  kind: "chat" as "chat" | "embedding",
  temperature: 0.7,
  embedding_dimension: 1536,
});
const [availableModels, setAvailableModels] = useState<string[]>([]);

const chatModels = models.filter((m) => !m.embedding_model && !m.level?.includes("embedding"));
const embeddingModels = models.filter((m) => m.embedding_model || m.level === "embedding");

const fetchAvailable = async () => {
  const r = await settingsApi.fetchModels(addForm.base_url, addForm.api_key);
  if (r.data.ok && r.data.models) {
    setAvailableModels(r.data.models);
  } else {
    setTestMsg("拉取失败：" + (r.data.error || "未知错误"));
  }
};

const testModel = async () => {
  const r = await settingsApi.testModel({
    base_url: addForm.base_url,
    api_key: addForm.api_key,
    model: addForm.model,
    kind: addForm.kind,
    embedding_dimension: addForm.embedding_dimension,
  });
  setTestMsg(r.data.ok ? (addForm.kind === "embedding" ? `维度校验通过：${r.data.dimension}` : "连接成功") : "失败：" + r.data.error);
};

const saveNewModel = async () => {
  const payload: ModelConfigPayload = {
    name: addForm.name,
    base_url: addForm.base_url,
    model: addForm.model,
    api_key: addForm.api_key,
    is_default: models.length === 0,
    level: addForm.kind === "embedding" ? "embedding" : undefined,
    embedding_model: addForm.kind === "embedding" ? addForm.model : undefined,
    embedding_dimension: addForm.kind === "embedding" ? addForm.embedding_dimension : undefined,
    temperature: addForm.kind === "chat" ? addForm.temperature : undefined,
  };
  await settingsApi.createModel(payload);
  setAddForm({ ...addForm, name: "", base_url: "", api_key: "", model: "" });
  setShowAdd(false);
  await load();
};
```

UI 渲染：

```tsx
<Card className="mt-6">
  <div className="border-b border-line px-4 py-3 font-serif text-sm font-medium text-ink">模型配置</div>
  <div className="space-y-3 p-4">
    <div className="text-sm font-medium text-ink">文本模型</div>
    {chatModels.map(renderModelCard)}
    <div className="mt-4 text-sm font-medium text-ink">向量模型</div>
    {embeddingModels.map(renderModelCard)}

    {!showAdd ? (
      <Button variant="primary" className="w-full" onClick={() => setShowAdd(true)}>+ 新增模型</Button>
    ) : (
      <div className="space-y-2 rounded-lg border border-line p-3">
        {/* 表单字段 */}
        <select value={addForm.kind} onChange={(e) => setAddForm({ ...addForm, kind: e.target.value as any })} className={selectClass}>
          <option value="chat">文本模型</option>
          <option value="embedding">向量模型</option>
        </select>
        <Input placeholder="名称" value={addForm.name} onChange={(e) => setAddForm({ ...addForm, name: e.target.value })} />
        <Input placeholder="base_url" value={addForm.base_url} onChange={(e) => setAddForm({ ...addForm, base_url: e.target.value })} />
        <Input placeholder="api_key" type="password" value={addForm.api_key} onChange={(e) => setAddForm({ ...addForm, api_key: e.target.value })} />
        <Button variant="ghost" onClick={fetchAvailable}>刷新模型列表</Button>
        <select value={addForm.model} onChange={(e) => setAddForm({ ...addForm, model: e.target.value })} className={selectClass}>
          <option value="">选择模型...</option>
          {availableModels.map((m) => (
            <option key={m} value={m}>{m}</option>
          ))}
        </select>
        {addForm.kind === "chat" && (
          <Input type="number" step={0.1} min={0} max={2} placeholder="温度" value={addForm.temperature} onChange={(e) => setAddForm({ ...addForm, temperature: Number(e.target.value) })} />
        )}
        {addForm.kind === "embedding" && (
          <Input type="number" placeholder="向量维度" value={addForm.embedding_dimension} onChange={(e) => setAddForm({ ...addForm, embedding_dimension: Number(e.target.value) })} />
        )}
        <div className="flex gap-2">
          <Button variant="ghost" onClick={testModel}>测试连接</Button>
          <Button variant="primary" onClick={saveNewModel}>保存</Button>
          <Button variant="ghost" onClick={() => setShowAdd(false)}>取消</Button>
        </div>
        {testMsg && <div className="text-xs text-muted">{testMsg}</div>}
      </div>
    )}
  </div>
</Card>
```

```tsx
function ModelCard({ m, onChange }: { m: ModelConfig; onChange: () => void }) {
  const [editing, setEditing] = useState(false);
  const kind = m.level === "embedding" || m.embedding_model ? "embedding" : "chat";
  const [form, setForm] = useState({
    name: m.name,
    base_url: m.base_url,
    api_key: "",
    model: m.model,
    temperature: m.temperature ?? 0.7,
    embedding_dimension: m.embedding_dimension ?? 1536,
  });

  const save = async () => {
    const payload: Partial<ModelConfigPayload> = {
      name: form.name,
      base_url: form.base_url,
      model: form.model,
      temperature: kind === "chat" ? form.temperature : undefined,
      embedding_dimension: kind === "embedding" ? form.embedding_dimension : undefined,
    };
    if (form.api_key) payload.api_key = form.api_key;
    await settingsApi.updateModel(m.id, payload);
    setEditing(false);
    onChange();
  };

  if (editing) {
    return (
      <div className="rounded-lg border border-line p-3 space-y-2">
        <Input placeholder="名称" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        <Input placeholder="base_url" value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })} />
        <Input placeholder="api_key（留空则保持原值）" type="password" value={form.api_key} onChange={(e) => setForm({ ...form, api_key: e.target.value })} />
        <Input placeholder="模型" value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} />
        {kind === "chat" && (
          <Input type="number" step={0.1} min={0} max={2} value={form.temperature} onChange={(e) => setForm({ ...form, temperature: Number(e.target.value) })} />
        )}
        {kind === "embedding" && (
          <Input type="number" value={form.embedding_dimension} onChange={(e) => setForm({ ...form, embedding_dimension: Number(e.target.value) })} />
        )}
        <div className="flex gap-2">
          <Button variant="primary" onClick={save}>保存</Button>
          <Button variant="ghost" onClick={() => setEditing(false)}>取消</Button>
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-line p-3">
      <div className="flex items-center justify-between">
        <div>
          <span className="font-medium text-ink">{m.name}</span>
          {m.is_default && <span className="ml-2 rounded bg-accent px-2 py-0.5 text-xs text-white">默认</span>}
          <div className="text-xs text-muted">{m.model}{kind === "chat" && m.temperature !== undefined ? ` · 温度 ${m.temperature}` : ""}{kind === "embedding" ? ` · dim ${m.embedding_dimension}` : ""}</div>
          <div className="text-xs text-muted">{m.base_url}</div>
        </div>
        <div className="flex gap-2">
          <Button variant="ghost" onClick={() => setEditing(true)}>编辑</Button>
          <Button variant="ghost" onClick={async () => { await settingsApi.updateModel(m.id, { is_default: true }); onChange(); }}>设为默认</Button>
          <Button variant="ghost" onClick={async () => { await settingsApi.deleteModel(m.id); onChange(); }}>删</Button>
        </div>
      </div>
    </div>
  );
}
```

卡片列表渲染改为：

```tsx
{chatModels.map((m) => (
  <ModelCard key={m.id} m={m} onChange={load} />
))}
```

- [ ] **Step 3: 测试**

```bash
cd frontend && npx tsc -b
```

- [ ] **Step 4: 提交**

```bash
git add frontend/src/types/index.ts frontend/src/api/settings.ts frontend/src/pages/SettingsPage.tsx
git commit -m "feat(models): redesign settings page model config UI"
```

---

## 子系统 4：章节生成配置下沉到小说

### Task 4.1: 后端读取项目级生成配置

**Files:**
- Modify: `backend/app/agents/harness/workers/_chapter_utils.py:161-167`
- Modify: `backend/app/agents/workflows/chapter_generation.py:48`
- Test: 修改项目 generation_config 后，章节生成使用新值

**Interfaces:**
- Consumes: `project_id`, `Project.generation_config`, `UserSetting`
- Produces: `generation_settings(db, project_id) -> tuple[int, str]`

- [ ] **Step 1: 更新 `generation_settings` 函数**

```python
async def generation_settings(db, project_id: str | None = None) -> tuple[int, str]:
    """读取生成相关设置：(每章目标字数, 尺度等级)。优先级：项目 > 全局 > 默认。"""
    if project_id:
        from app.models import Project
        project = await db.get(Project, project_id)
        if project and project.generation_config:
            cfg = project.generation_config
            target = cfg.get("chapter_target_words")
            rating = cfg.get("content_rating")
            if target or rating:
                return (
                    int(target) if target else 2500,
                    rating if rating else "standard",
                )

    res = await db.execute(select(UserSetting))
    s = res.scalars().first()
    target = s.chapter_target_words if s and s.chapter_target_words else 2500
    rating = s.content_rating if s and s.content_rating else "standard"
    return target, rating
```

- [ ] **Step 2: 更新调用点**

在 `chapter_generation.py` 的 `load_context` 中：

```python
    target_words, rating = await generation_settings(ctx.db, project_id)
```

- [ ] **Step 3: 新建项目时复制全局默认值**

在 `backend/app/api/projects.py` 的 `create_project` 中：

```python
async def create_project(payload: ProjectCreate, db: AsyncSession = Depends(get_db)):
    from app.models import UserSetting
    from sqlalchemy import select

    p = Project(type=payload.type, title=payload.title, description=payload.description)
    res = await db.execute(select(UserSetting))
    s = res.scalars().first()
    if s:
        p.generation_config = {
            "chapter_target_words": s.chapter_target_words,
            "content_rating": s.content_rating,
        }
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p.to_dict()
```

- [ ] **Step 4: 测试**

```bash
cd backend && python -m compileall app
```

- [ ] **Step 5: 提交**

```bash
git add backend/app/agents/harness/workers/_chapter_utils.py backend/app/agents/workflows/chapter_generation.py backend/app/api/projects.py
git commit -m "feat(generation): read chapter generation config per project with global fallback"
```

---

### Task 4.2: 全局设置文案与快捷入口

**Files:**
- Modify: `frontend/src/pages/SettingsPage.tsx:162-192`
- Modify: `frontend/src/pages/LongWorkspace.tsx`（章节面板附近）
- Test: 全局设置显示「默认值」；LongWorkspace 有快捷入口

**Interfaces:**
- Consumes: `Project.generation_config`
- Produces: 快捷按钮打开项目设置弹窗到「章节生成」标签

- [ ] **Step 1: 修改全局设置文案**

将「章节生成」卡片标题改为「章节生成默认值（新建项目）」，并在说明文字中增加：

```tsx
<div className="text-xs text-muted">
  此处仅作为新建项目的初始默认值。每个项目可在项目设置中单独覆盖。
</div>
```

- [ ] **Step 2: 在 LongWorkspace 添加快捷入口**

在 `ChapterPanel` 的章节列表上方或生成按钮旁添加：

```tsx
<Button variant="ghost" onClick={() => setGenSettingsOpen(true)}>⚙ 生成设置</Button>
```

使用 `ProjectSettingsDialog` 的 `initialTab="generation"` 打开。

- [ ] **Step 3: 测试**

```bash
cd frontend && npx tsc -b
```

- [ ] **Step 4: 提交**

```bash
git add frontend/src/pages/SettingsPage.tsx frontend/src/pages/LongWorkspace.tsx
git commit -m "feat(generation): add project-level generation settings shortcut and relabel global defaults"
```

---

## 最终验证

- [ ] **后端编译**

```bash
cd backend && python -m compileall app
```

- [ ] **前端类型检查**

```bash
cd frontend && npx tsc -b
```

- [ ] **端到端手动验证**

1. 创建新项目，进入项目设置，设置文风。
2. 触发章节生成，查看后端日志包含「文风要求」。
3. 让 AI 修改总纲某句话，确认使用 `patch` 类型且其他内容不丢失。
4. 在设置页添加 DeepSeek 模型，测试连接成功；添加向量模型，测试维度校验。
5. 修改项目级「每章目标字数」，重新生成章节，确认字数按新项目值。

- [ ] **提交最终汇总**

```bash
git log --oneline -10
```

---

## Self-Review

**Spec coverage:**
- 文风配置：Task 1.1-1.4 覆盖数据模型、API、prompt 注入、前端表单。
- 变更类型增强：Task 2.1-2.2 覆盖 `change_apply` 新分支和 worker prompt。
- 模型配置 UI：Task 3.1-3.5 覆盖字段、API、温度、默认模型、前端重设计。
- 章节生成配置下沉：Task 4.1-4.2 覆盖读取优先级、新建复制、快捷入口。

**Placeholder scan：** 无 TBD/TODO/模糊描述。

**Type consistency：**
- `Project.writing_style` / `generation_config` 在 models、schemas、types 中均定义为 dict/JSON。
- `ModelConfig.temperature` 在 model、schema、API、factory、前端类型中一致为 `Optional[float]` / `number`。
- `generation_settings(db, project_id)` 签名在 Task 4.1 中定义并同步更新调用点。

# 温暖纸感文学风 · UI 重设计 实现计划（暖褐书香）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有"黑白灰零圆角工具风"前端重设计为"暖褐书香"温暖纸感文学风（奶油底 + 深棕字 + 金棕强调 + 宋体衬线标题），全局统一改造所有页面视觉层。

**Architecture:** 仅替换视觉层——修改 Tailwind 主题令牌与 CSS 变量建立设计系统，重写通用组件（`ui.tsx`）与顶栏（`AppShell`），再逐页替换类名与少量结构（标题宋体化、卡片化、按钮分级）。所有数据逻辑与 API 调用保持不变。

**Tech Stack:** React 19 + TypeScript + Vite + Tailwind CSS 3.4（纯 CSS 变量 + 主题扩展，无新依赖）。

## Global Constraints

- 不改动任何业务逻辑与接口调用（仅视觉/层级类名与少量结构）。
- 不引入新依赖（纯 Tailwind + CSS 变量）。
- 保留 `noImplicitAny: false`，不触发大范围类型改动。
- 字体走系统宋体栈（`"Songti SC","STSong","SimSun","Noto Serif SC",serif`），不依赖网络字体。
- 现有 `bg-ink text-paper` 反白用法必须替换为新选中态（浅金棕底 + 金棕左竖条 + 深棕字）；`bg-paper/text-ink/border-line/text-muted/bg-subtle` 类名保留（值已改暖）。
- 验证门禁：`cd frontend && npx tsc -b`（类型）+ `cd frontend && npm run build`（最终构建，含 `tsc -b && vite build`）。

---

## 文件结构与责任

- Modify `frontend/src/index.css` — 设计令牌 CSS 变量 + body/控件圆角/滚动条配色
- Modify `frontend/tailwind.config.js` — colors / borderRadius / fontFamily / boxShadow 扩展
- Modify `frontend/src/components/ui.tsx` — Button 变体 + Card + Input/Textarea 升级 + 新增 SectionTitle/Tag/Empty
- Modify `frontend/src/components/AppShell.tsx` — 顶栏暖色化、品牌宋体
- Modify `frontend/src/pages/HomePage.tsx` — 标题宋体、Tab 药丸、项目卡 Card 化
- Modify `frontend/src/pages/LongWorkspace.tsx` — 侧栏选中态、Panel 卡片化、Graph 配色、Assistant 按钮
- Modify `frontend/src/pages/ShortStudio.tsx` — 侧栏步骤态、Section Card 化、Hotspot 卡片化、按钮分级
- Modify `frontend/src/pages/SettingsPage.tsx` — section Card 化、滑块着色、按钮分级
- Build `frontend/` — 最终 `npm run build` 验证

---

### Task 1: 设计令牌 · index.css

**Files:**
- Modify: `frontend/src/index.css`

**Interfaces:** 无（被 Task 3/4/5/6/7/8 通过 Tailwind 类名消费）

- [ ] **Step 1: 用以下完整内容替换 `index.css`**

```css
/* 温暖纸感文学风 — 暖褐书香
   字体策略：标题走系统宋体栈（离线可靠）。如需联网统一字体，可在 index.html 加入：
   <link href="https://fonts.googleapis.com/css2?family=Noto+Serif+SC:wght@500;700&display=swap" rel="stylesheet"> */
@tailwind base;
@tailwind components;
@tailwind utilities;

:root {
  --paper: #faf6ee;
  --surface: #fffdf8;
  --surface-2: #f3ecdf;
  --ink: #3a2c22;
  --ink-soft: #6b5d4f;
  --muted: #9a8c7b;
  --line: #e6dccb;
  --accent: #b07a3c;
  --accent-strong: #8f5f2c;
  --accent-soft: #f0e4d2;
}

* {
  box-sizing: border-box;
}

html,
body,
#root {
  height: 100%;
  margin: 0;
}

body {
  background: var(--paper);
  color: var(--ink);
  font-family: "Inter", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
  -webkit-font-smoothing: antialiased;
  letter-spacing: 0.01em;
}

button {
  border-radius: 8px;
  cursor: pointer;
}

input,
textarea,
select {
  border-radius: 8px;
}

/* 暖灰细线分割 */
.hairline {
  border-color: var(--line);
}

/* 滚动条极简（暖灰） */
::-webkit-scrollbar {
  width: 8px;
  height: 8px;
}
::-webkit-scrollbar-thumb {
  background: #d8cdb8;
  border-radius: 4px;
}
::-webkit-scrollbar-track {
  background: transparent;
}
```

- [ ] **Step 2: 类型/构建检查（样式文件无类型，仅确认无语法错误）**

Run: `cd frontend && npx tsc -b`
Expected: 无报错（CSS 不参与 tsc，此步主要为后续任务预热；若无改动可跳过报错）

- [ ] **Step 3: 提交**

```bash
git add frontend/src/index.css
git commit -m "style: 暖褐书香设计令牌 (index.css 变量与控件圆角/滚动条)"
```

---

### Task 2: 设计令牌 · tailwind.config.js

**Files:**
- Modify: `frontend/tailwind.config.js`

**Interfaces:** 暴露 `paper/surface/surface-2/ink/ink-soft/muted/line/accent/accent-strong/accent-soft` 颜色、`serif` 字体、`shadow-soft`/`shadow-card-hover` 阴影、`sm/DEFAULT/lg` 圆角，供后续所有任务消费。

- [ ] **Step 1: 用以下完整内容替换 `tailwind.config.js`**

```js
/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      borderRadius: {
        none: "0",
        sm: "6px",
        DEFAULT: "8px",
        lg: "12px",
      },
      colors: {
        ink: "#3a2c22",
        "ink-soft": "#6b5d4f",
        paper: "#faf6ee",
        surface: "#fffdf8",
        "surface-2": "#f3ecdf",
        muted: "#9a8c7b",
        line: "#e6dccb",
        accent: "#b07a3c",
        "accent-strong": "#8f5f2c",
        "accent-soft": "#f0e4d2",
      },
      fontFamily: {
        sans: ['"Inter"', '"PingFang SC"', '"Microsoft YaHei"', "system-ui", "sans-serif"],
        serif: ['"Songti SC"', '"STSong"', '"SimSun"', '"Noto Serif SC"', "serif"],
      },
      boxShadow: {
        soft: "0 1px 3px rgba(58,44,34,.08), 0 6px 18px rgba(58,44,34,.06)",
        "card-hover": "0 2px 6px rgba(58,44,34,.10), 0 12px 28px rgba(58,44,34,.08)",
      },
    },
  },
  plugins: [],
};
```

- [ ] **Step 2: 类型/构建检查**

Run: `cd frontend && npx tsc -b`
Expected: 无报错

- [ ] **Step 3: 提交**

```bash
git add frontend/tailwind.config.js
git commit -m "style: 扩展 Tailwind 主题 (暖色/圆角/衬线字体/柔阴影)"
```

---

### Task 3: 通用组件系统 · ui.tsx

**Files:**
- Modify: `frontend/src/components/ui.tsx`

**Interfaces:**
- 产出 `Button`（`variant: "primary" | "ghost" | "subtle"`，默认 `"ghost"`）、`Input`、`Textarea`、`Card`、`SectionTitle`、`Tag`、`Empty`。
- 后续所有页面任务消费这些组件；`Button` 保留 `className` 追加兼容现有调用。

- [ ] **Step 1: 用以下完整内容替换 `ui.tsx`**

```tsx
import { ReactNode, ButtonHTMLAttributes } from "react";

type ButtonVariant = "primary" | "ghost" | "subtle";

export function Button({
  children,
  variant = "ghost",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant }) {
  const base =
    "inline-flex items-center justify-center rounded-lg px-3 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40 ";
  const variants: Record<ButtonVariant, string> = {
    primary: "bg-accent text-paper shadow-soft hover:bg-accent-strong ",
    ghost: "border border-ink/25 text-ink hover:bg-surface-2 ",
    subtle: "text-ink-soft hover:bg-surface-2 ",
  };
  return (
    <button {...props} className={base + variants[variant] + (props.className || "")}>
      {children}
    </button>
  );
}

export function Input(props: any) {
  return (
    <input
      {...props}
      className={
        "w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink outline-none transition focus:border-accent focus:ring-1 focus:ring-accent/30 " +
        (props.className || "")
      }
    />
  );
}

export function Textarea(props: any) {
  return (
    <textarea
      {...props}
      className={
        "w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink outline-none transition focus:border-accent focus:ring-1 focus:ring-accent/30 " +
        (props.className || "")
      }
    />
  );
}

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div className={"rounded-lg border border-line bg-surface shadow-soft " + className}>{children}</div>
  );
}

export function SectionTitle({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div className={"border-l-2 border-accent pl-2 font-serif text-base font-medium text-ink " + className}>
      {children}
    </div>
  );
}

export function Tag({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <span className={"rounded-full bg-accent-soft px-2 py-0.5 text-xs font-medium text-accent-strong " + className}>
      {children}
    </span>
  );
}

export function Empty({ text }: { text: string }) {
  return <div className="px-4 py-10 text-center text-sm text-muted">{text}</div>;
}
```

- [ ] **Step 2: 类型/构建检查**

Run: `cd frontend && npx tsc -b`
Expected: 无报错

- [ ] **Step 3: 提交**

```bash
git add frontend/src/components/ui.tsx
git commit -m "style: 重写通用组件 (Button 变体/Card/SectionTitle/Tag)"
```

---

### Task 4: 顶栏 · AppShell.tsx

**Files:**
- Modify: `frontend/src/components/AppShell.tsx`

**Interfaces:** 消费 `paper/surface/accent/ink/muted` 令牌与 `font-serif`；为所有页面提供外壳。

- [ ] **Step 1: 用以下完整内容替换 `AppShell.tsx`**

```tsx
import { ReactNode } from "react";

export default function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-full flex-col bg-paper text-ink">
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-accent/30 bg-paper px-6">
        <div className="flex items-baseline gap-2">
          <span className="font-serif text-lg font-semibold tracking-wide text-ink">NOVEL STUDIO</span>
          <span className="text-xs text-muted">小说创作助手</span>
        </div>
        <nav className="flex items-center gap-6 text-sm">
          <a href="/" className="text-ink-soft transition-colors hover:text-accent">项目</a>
          <a href="/settings" className="text-muted transition-colors hover:text-accent">设置</a>
        </nav>
      </header>
      <main className="flex-1 overflow-auto bg-paper">{children}</main>
    </div>
  );
}
```

- [ ] **Step 2: 类型/构建检查**

Run: `cd frontend && npx tsc -b`
Expected: 无报错

- [ ] **Step 3: 提交**

```bash
git add frontend/src/components/AppShell.tsx
git commit -m "style: 顶栏暖色化与品牌宋体"
```

---

### Task 5: 首页 · HomePage.tsx

**Files:**
- Modify: `frontend/src/pages/HomePage.tsx`

**Interfaces:** 消费 `Button(variant=primary/ghost)`、`Input`、`Card`、`font-serif`；逻辑（projectsApi / 路由）不变。

- [ ] **Step 1: 用以下完整内容替换 `HomePage.tsx`**

```tsx
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { projectsApi } from "@/api/projects";
import type { Project } from "@/types";
import { Button, Input, Card } from "@/components/ui";

type Tab = "long" | "short";

export default function HomePage() {
  const [tab, setTab] = useState<Tab>("long");
  const [projects, setProjects] = useState<Project[]>([]);
  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState("");
  const nav = useNavigate();

  const load = async () => {
    const { data } = await projectsApi.list(tab);
    setProjects(data);
  };

  useEffect(() => { load(); }, [tab]);

  const create = async () => {
    if (!title.trim()) return;
    const { data } = await projectsApi.create(tab, title.trim());
    setCreating(false);
    setTitle("");
    nav(`/project/${tab}/${data.id}`);
  };

  return (
    <div className="mx-auto max-w-5xl px-6 py-12">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="font-serif text-3xl font-semibold tracking-wide text-ink">项目</h1>
          <p className="mt-2 text-sm text-muted">短篇 / 长篇小说项目管理</p>
        </div>
        <Button variant="primary" onClick={() => setCreating(true)} disabled={creating}>新建项目</Button>
      </div>

      <div className="mt-8 inline-flex rounded-full border border-line bg-surface p-1">
        {(["long", "short"] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={
              "rounded-full px-5 py-1.5 text-sm font-medium transition-colors " +
              (tab === t ? "bg-accent-soft text-accent-strong" : "text-muted hover:text-ink")
            }
          >
            {t === "long" ? "长篇" : "短篇"}
          </button>
        ))}
      </div>

      {creating && (
        <Card className="mt-5 p-4">
          <div className="flex items-center gap-2">
            <Input
              placeholder={tab === "long" ? "长篇标题" : "短篇标题"}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && create()}
              autoFocus
            />
            <Button variant="primary" onClick={create}>创建</Button>
            <Button variant="ghost" onClick={() => setCreating(false)}>取消</Button>
          </div>
        </Card>
      )}

      <div className="mt-6 grid grid-cols-2 gap-4">
        {projects.length === 0 && (
          <div className="col-span-2 rounded-lg border border-dashed border-line bg-surface p-10 text-center text-sm text-muted">暂无项目</div>
        )}
        {projects.map((p) => (
          <button
            key={p.id}
            onClick={() => nav(`/project/${p.type}/${p.id}`)}
            className="rounded-lg border border-line bg-surface p-6 text-left shadow-soft transition-all hover:-translate-y-0.5 hover:shadow-card-hover"
          >
            <div className="font-serif text-lg font-medium text-ink">{p.title}</div>
            <div className="mt-2 line-clamp-2 text-sm text-muted">{p.description || "（无简介）"}</div>
            <div className="mt-4 text-xs text-muted">
              {p.updated_at ? new Date(p.updated_at).toLocaleString() : ""}
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: 类型/构建检查**

Run: `cd frontend && npx tsc -b`
Expected: 无报错

- [ ] **Step 3: 提交**

```bash
git add frontend/src/pages/HomePage.tsx
git commit -m "style: 首页标题宋体/Tab药丸/项目卡卡片化"
```

---

### Task 6: 长篇工作区 · LongWorkspace.tsx

**Files:**
- Modify: `frontend/src/pages/LongWorkspace.tsx`

**Interfaces:** 消费 `Button(variant)`、`Card`、`SectionTitle`、`Input`、`Textarea`；逻辑（longApi/assistantApi/graphApi 与各 Panel 状态）完全不变。

- [ ] **Step 1: 用以下完整内容替换 `LongWorkspace.tsx`**

```tsx
import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { longApi } from "@/api/long";
import { assistantApi } from "@/api/short";
import { graphApi } from "@/api/graph";
import type { ChangeRecord } from "@/types";
import { Button, Input, Textarea, Card, SectionTitle } from "@/components/ui";

export default function LongWorkspace() {
  const { id } = useParams();
  const nav = useNavigate();
  const [tab, setTab] = useState("outline");

  return (
    <div className="flex h-full">
      <aside className="w-52 shrink-0 border-r border-line bg-surface p-4">
        <Button variant="subtle" className="mb-4 w-full justify-start" onClick={() => nav("/")}>← 返回</Button>
        {[
          ["outline", "大纲树"], ["character", "角色"], ["foreshadow", "伏笔"],
          ["world", "世界观"], ["plot", "剧情节点"], ["chapter", "章节"], ["graph", "图谱"], ["assistant", "创作助手"],
        ].map(([k, label]) => (
          <button
            key={k}
            onClick={() => setTab(k)}
            className={
              "mb-1 block w-full rounded-lg border-l-2 px-3 py-2 text-left text-sm transition-colors " +
              (tab === k ? "border-accent bg-accent-soft text-ink" : "border-transparent text-muted hover:bg-surface-2 hover:text-ink")
            }
          >
            {label}
          </button>
        ))}
      </aside>
      <div className="flex-1 overflow-auto p-8">
        {tab === "outline" && <OutlinePanel pid={id!} />}
        {tab === "character" && <CrudPanel pid={id!} kind="character" label="角色" fields={[
          { key: "name", label: "名称" }, { key: "traits", label: "性格" }, { key: "ability", label: "能力" }, { key: "status", label: "状态" },
        ]} />}
        {tab === "foreshadow" && <CrudPanel pid={id!} kind="foreshadow" label="伏笔" fields={[
          { key: "title", label: "标题" }, { key: "content", label: "内容" }, { key: "state", label: "状态(pending/revealed/abandoned)" },
        ]} />}
        {tab === "world" && <CrudPanel pid={id!} kind="world" label="世界观" fields={[
          { key: "category", label: "分类" }, { key: "content", label: "内容" },
        ]} />}
        {tab === "plot" && <CrudPanel pid={id!} kind="plot" label="剧情节点" fields={[
          { key: "title", label: "标题" }, { key: "summary", label: "概要" }, { key: "timeline_pos", label: "时间位置" },
        ]} />}
        {tab === "chapter" && <ChapterPanel pid={id!} />}
        {tab === "graph" && <GraphPanel pid={id!} />}
        {tab === "assistant" && <AssistantPanel pid={id!} />}
      </div>
    </div>
  );
}

function OutlinePanel({ pid }: { pid: string }) {
  const [items, setItems] = useState<any[]>([]);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");

  const load = async () => {
    const { data } = await longApi.outlines(pid);
    setItems(data);
  };
  useEffect(() => { load(); }, [pid]);

  const add = async () => {
    if (!title.trim()) return;
    await longApi.addOutline({ project_id: pid, title, content });
    setTitle(""); setContent(""); load();
  };

  return (
    <div>
      <SectionTitle>大纲树</SectionTitle>
      <div className="mt-4 space-y-3">
        {items.map((it) => (
          <Card key={it.id} className="p-4">
            <div className="flex items-start gap-3">
              <div className="flex-1">
                <div className="text-sm font-medium text-ink">{it.title || "（无标题）"}</div>
                <div className="mt-1 whitespace-pre-wrap text-sm text-muted">{it.content}</div>
              </div>
              <Button variant="ghost" onClick={() => { setTitle(it.title); setContent(it.content); }}>复制为新版</Button>
              <Button variant="ghost" onClick={async () => { await longApi.deleteOutline(it.id); load(); }}>删</Button>
            </div>
          </Card>
        ))}
      </div>
      <Card className="mt-4 space-y-3 p-4">
        <Input placeholder="标题" value={title} onChange={(e) => setTitle(e.target.value)} />
        <Textarea placeholder="内容" rows={4} value={content} onChange={(e) => setContent(e.target.value)} />
        <div><Button variant="primary" onClick={add}>+ 新增大纲</Button></div>
      </Card>
    </div>
  );
}

const KIND_API: any = {
  character: { list: longApi.characters, add: longApi.addCharacter, upd: longApi.updateCharacter, del: longApi.deleteCharacter },
  foreshadow: { list: longApi.foreshadows, add: longApi.addForeshadow, upd: longApi.updateForeshadow, del: longApi.deleteForeshadow },
  world: { list: longApi.world, add: longApi.addWorld, upd: longApi.updateWorld, del: longApi.deleteWorld },
  plot: { list: longApi.plot, add: longApi.addPlot, upd: longApi.updatePlot, del: longApi.deletePlot },
};

function CrudPanel({ pid, kind, label, fields }: { pid: string; kind: string; label: string; fields: { key: string; label: string }[] }) {
  const [items, setItems] = useState<any[]>([]);
  const [form, setForm] = useState<Record<string, string>>({});
  const api = KIND_API[kind];

  const load = async () => {
    const { data } = await api.list(pid);
    setItems(data);
  };
  useEffect(() => { load(); }, [pid, kind]);

  const add = async () => {
    const payload: any = { project_id: pid };
    fields.forEach((f) => (payload[f.key] = form[f.key] || ""));
    await api.add(payload);
    setForm({}); load();
  };

  return (
    <div>
      <SectionTitle>{label}</SectionTitle>
      <div className="mt-4 space-y-3">
        {items.map((it) => (
          <Card key={it.id} className="p-4">
            <div className="flex items-start gap-3">
              <div className="flex-1 text-sm">
                {fields.map((f) => (
                  <div key={f.key} className="mt-1">
                    <span className="text-muted">{f.label}：</span>
                    <span className="whitespace-pre-wrap text-ink">{it[f.key] || "—"}</span>
                  </div>
                ))}
              </div>
              <Button variant="ghost" onClick={async () => { await api.del(it.id); load(); }}>删</Button>
            </div>
          </Card>
        ))}
      </div>
      <Card className="mt-4 space-y-3 p-4">
        {fields.map((f) => (
          <div key={f.key}>
            <label className="mb-1 block text-xs text-muted">{f.label}</label>
            <Input value={form[f.key] || ""} onChange={(e) => setForm({ ...form, [f.key]: e.target.value })} />
          </div>
        ))}
        <div><Button variant="primary" onClick={add}>+ 新增{label}</Button></div>
      </Card>
    </div>
  );
}

function ChapterPanel({ pid }: { pid: string }) {
  const [items, setItems] = useState<any[]>([]);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");

  const load = async () => {
    const { data } = await longApi.chapters(pid);
    setItems(data);
  };
  useEffect(() => { load(); }, [pid]);

  const add = async () => {
    if (!title.trim()) return;
    await longApi.addChapter({ project_id: pid, title, content, order: items.length });
    setTitle(""); setContent(""); load();
  };
  const saveContent = async (it: any, val: string) => {
    await longApi.updateChapter(it.id, { content: val });
  };

  return (
    <div>
      <SectionTitle>章节</SectionTitle>
      <div className="mt-4 space-y-3">
        {items.map((it) => (
          <Card key={it.id} className="p-4">
            <div className="text-sm font-medium text-ink">{it.title}</div>
            <Textarea className="mt-2" rows={6} defaultValue={it.content} onBlur={(e) => saveContent(it, e.target.value)} />
          </Card>
        ))}
      </div>
      <Card className="mt-4 space-y-3 p-4">
        <Input placeholder="章节标题" value={title} onChange={(e) => setTitle(e.target.value)} />
        <Textarea placeholder="正文" rows={4} value={content} onChange={(e) => setContent(e.target.value)} />
        <div><Button variant="primary" onClick={add}>+ 新增章节</Button></div>
      </Card>
    </div>
  );
}

function AssistantPanel({ pid }: { pid: string }) {
  const [msg, setMsg] = useState("");
  const [records, setRecords] = useState<ChangeRecord[]>([]);
  const [summary, setSummary] = useState("");
  const [sessionId, setSessionId] = useState("");
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState("");

  const send = async () => {
    if (!msg.trim()) return;
    setBusy(true); setLog("分析中…");
    try {
      const { data } = await assistantApi.chat(pid, msg);
      setRecords(data.change_records);
      setSummary(data.summary);
      setSessionId(data.session_id);
      setLog("完成，请确认变更。");
    } catch (e: any) {
      setLog("错误：" + e.message);
    } finally {
      setBusy(false);
    }
  };

  const confirm = async () => {
    if (!sessionId) return;
    setBusy(true);
    try {
      const { data } = await assistantApi.confirm(sessionId);
      setLog(data.ok ? `已应用 ${data.applied.length} 条变更` : "部分失败：" + JSON.stringify(data.errors));
      setRecords([]);
    } finally { setBusy(false); }
  };

  const reject = async () => {
    if (!sessionId) return;
    await assistantApi.reject(sessionId);
    setRecords([]); setLog("已拒绝。");
  };

  return (
    <div>
      <SectionTitle>创作助手</SectionTitle>
      <p className="mt-2 text-xs text-muted">Agent 仅读取真实数据 → 生成变更建议 → 你确认后才落库。</p>
      <div className="mt-4 flex gap-2">
        <Input placeholder="描述你的创作意图，例如：为主角增加一个宿敌角色" value={msg} onChange={(e) => setMsg(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send()} />
        <Button variant="primary" onClick={send} disabled={busy}>发送</Button>
      </div>

      {summary && (
        <Card className="mt-4 bg-surface-2 p-4 text-sm">
          <div className="mb-2 text-xs font-medium text-muted">摘要</div>
          <div className="whitespace-pre-wrap text-ink">{summary}</div>
        </Card>
      )}

      {records.length > 0 && (
        <div className="mt-4 space-y-3">
          <div className="text-sm font-medium text-ink">待确认变更（{records.length}）</div>
          {records.map((r) => (
            <Card key={r.id} className="p-3 text-xs">
              <div className="font-medium text-ink">{r.action} / {r.entity_type} {r.entity_id || "(新增)"}</div>
              <pre className="mt-1 overflow-auto whitespace-pre-wrap text-muted">{JSON.stringify(r.after, null, 2)}</pre>
            </Card>
          ))}
          <div className="flex gap-2">
            <Button variant="primary" onClick={confirm}>确认并应用</Button>
            <Button variant="ghost" onClick={reject}>拒绝</Button>
          </div>
        </div>
      )}

      {log && <div className="mt-4 text-xs text-muted">{log}</div>}
    </div>
  );
}

function GraphPanel({ pid }: { pid: string }) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await graphApi.view(pid);
      setData(data);
    } finally { setLoading(false); }
  };
  useEffect(() => { load(); }, [pid]);

  if (loading) return <div className="text-sm text-muted">加载中…</div>;
  if (!data) return <div className="text-sm text-muted">无数据</div>;

  const colorByType: any = { character: "#3a2c22", foreshadow: "#b07a3c" };

  return (
    <div>
      <div className="flex items-center justify-between">
        <SectionTitle>知识图谱</SectionTitle>
        <span className="text-xs text-muted">数据源：{data.source}</span>
      </div>
      <div className="mt-2 text-xs text-muted">节点 {data.nodes.length} · 关系 {data.edges.length}（点击角色可查看关系）</div>
      <div className="mt-4 grid grid-cols-2 gap-4">
        <Card className="p-4">
          <div className="mb-2 text-xs font-medium text-muted">节点</div>
          <div className="space-y-1">
            {data.nodes.map((n: any) => (
              <div key={n.id} className="flex items-center gap-2 text-sm">
                <span className="inline-block h-2 w-2 rounded-full" style={{ background: colorByType[n.type] || "#9a8c7b" }} />
                <span className="text-ink">{n.label}</span>
                {n.state && <span className="text-[11px] text-muted">（{n.state}）</span>}
              </div>
            ))}
          </div>
        </Card>
        <Card className="p-4">
          <div className="mb-2 text-xs font-medium text-muted">关系</div>
          <div className="space-y-1 text-sm">
            {data.edges.length === 0 && <div className="text-muted">暂无关系</div>}
            {data.edges.map((e: any, i: number) => (
              <div key={i} className="text-xs">
                <span className="font-medium text-ink">{e.from}</span> <span className="text-muted">—{e.label}→</span> <span className="font-medium text-ink">{e.to}</span>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: 类型/构建检查**

Run: `cd frontend && npx tsc -b`
Expected: 无报错

- [ ] **Step 3: 提交**

```bash
git add frontend/src/pages/LongWorkspace.tsx
git commit -m "style: 长篇工作区侧栏选中态/Panel卡片化/Graph配色/Assistant按钮"
```

---

### Task 7: 短篇六步法 · ShortStudio.tsx

**Files:**
- Modify: `frontend/src/pages/ShortStudio.tsx`

**Interfaces:** 消费 `Button(variant)`、`Card`、`SectionTitle`、`Input`、`Textarea`；逻辑（shortApi/hotspotApi 与步骤状态）完全不变。

- [ ] **Step 1: 用以下完整内容替换 `ShortStudio.tsx`**

```tsx
import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { shortApi, hotspotApi } from "@/api/short";
import { Button, Input, Textarea, Card } from "@/components/ui";

const STEPS = ["爽点", "方案", "详细规划", "章节规划", "写作", "整合"];

export default function ShortStudio() {
  const { id } = useParams();
  const nav = useNavigate();
  const [data, setData] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState("");

  const load = async () => {
    const { data } = await shortApi.progress(id!);
    setData(data);
  };
  useEffect(() => { load(); }, [id]);

  if (!data) return <div className="p-10 text-sm text-muted">加载中…</div>;

  const step = data.step ?? 0;
  const run = async (fn: () => Promise<any>, okMsg: string) => {
    setBusy(true); setLog("生成中…");
    try { const { data: r } = await fn(); setData(r); setLog(okMsg); }
    catch (e: any) { setLog("错误：" + e.message); }
    finally { setBusy(false); }
  };

  return (
    <div className="flex h-full">
      <aside className="w-52 shrink-0 border-r border-line bg-surface p-4">
        <Button variant="subtle" className="mb-4 w-full justify-start" onClick={() => nav("/")}>← 返回</Button>
        {STEPS.map((s, i) => (
          <div key={i} className={"px-3 py-2 text-sm " + (i < step ? "text-accent" : i === step ? "font-medium text-ink" : "text-muted")}>
            {i + 1}. {s}{i < step ? " ✓" : ""}
          </div>
        ))}
      </aside>

      <div className="flex-1 overflow-auto p-8">
        <div className="mb-6 flex items-center justify-between">
          <h2 className="font-serif text-xl font-medium text-ink">短篇六步法</h2>
          <Button variant="ghost" onClick={load}>刷新</Button>
        </div>
        {log && <div className="mb-4 text-xs text-muted">{log}</div>}

        {/* Step 1: 爽点 */}
        <Section title="1. 爽点 / 核心设定">
          <Textarea rows={3} value={data.core_hook || ""} onChange={(e) => setData({ ...data, core_hook: e.target.value })} placeholder="描述这个故事的爽点…" />
          <Button variant="primary" className="mt-2" disabled={busy || !data.core_hook} onClick={() => run(() => shortApi.setHook(id!, data.core_hook), "已保存爽点")}>保存爽点</Button>
        </Section>

        {/* Step 2: 方案 */}
        <Section title="2. 剧情方案">
          <Button variant="primary" disabled={busy} onClick={() => run(() => shortApi.genPlans(id!), "已生成方案")}>生成方案</Button>
          <div className="mt-3 space-y-3">
            {(data.plans || []).map((p: any, i: number) => (
              <Card key={i} className="p-4 text-sm">
                <div className="font-medium text-ink">{p.name}</div>
                <div className="mt-1 text-xs text-muted">{p.direction} / {p.conflict}</div>
                <Button variant="ghost" className="mt-2" disabled={busy} onClick={() => run(() => shortApi.selectPlan(id!, i), "已选定方案")}>选定此方案</Button>
              </Card>
            ))}
          </div>
        </Section>

        {/* Step 3: 详细规划 */}
        <Section title="3. 详细规划">
          <Button variant="primary" disabled={busy || !data.selected_plan} onClick={() => run(() => shortApi.genDetail(id!), "已生成详细规划")}>生成详细规划</Button>
          <Textarea className="mt-2" rows={6} value={data.detail_plan || ""} onChange={(e) => setData({ ...data, detail_plan: e.target.value })} />
        </Section>

        {/* Step 4: 章节规划 */}
        <Section title="4. 章节规划">
          <Button variant="primary" disabled={busy || !data.detail_plan} onClick={() => run(() => shortApi.genChapters(id!), "已生成章节规划")}>生成章节规划</Button>
          <div className="mt-2 space-y-1">
            {(data.chapters_plan || []).map((c: any, i: number) => (
              <div key={i} className="rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink">{i + 1}. {c.title} — {c.summary}</div>
            ))}
          </div>
        </Section>

        {/* Step 5: 写作 */}
        <Section title="5. 写作">
          <div className="space-y-3">
            {(data.chapters_plan || []).map((c: any, i: number) => {
              const written = (data.writing || [])[i];
              return (
                <Card key={i} className="p-4">
                  <div className="text-sm font-medium text-ink">{i + 1}. {c.title}</div>
                  {written ? (
                    <div className="mt-1 whitespace-pre-wrap text-xs text-ink">{written.content}</div>
                  ) : (
                    <Button variant="ghost" className="mt-2" disabled={busy} onClick={() => run(() => shortApi.writeChapter(id!, i), `已写作第${i + 1}章`)}>写本章</Button>
                  )}
                </Card>
              );
            })}
          </div>
        </Section>

        {/* Step 6: 整合 */}
        <Section title="6. 整合">
          <Button variant="primary" disabled={busy || (data.writing || []).length === 0} onClick={() => run(() => shortApi.integrate(id!), "已整合")}>整合全文</Button>
          <Textarea className="mt-2" rows={10} value={data.integration || ""} onChange={(e) => setData({ ...data, integration: e.target.value })} placeholder="整合结果…" />
        </Section>

        <HotspotPanel pid={id!} />
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Card className="mb-6 p-5">
      <div className="mb-3 text-sm font-medium text-ink">{title}</div>
      {children}
    </Card>
  );
}

function HotspotPanel({ pid }: { pid: string }) {
  const [items, setItems] = useState<any[]>([]);
  const [url, setUrl] = useState("");
  const [loading, setLoading] = useState(false);

  const load = async () => {
    const { data } = await hotspotApi.stored(pid);
    setItems(data);
  };
  useEffect(() => { load(); }, [pid]);

  const fetch = async () => {
    setLoading(true);
    try { await hotspotApi.fetch(pid, url || undefined); await load(); }
    finally { setLoading(false); }
  };
  const analyze = async () => {
    setLoading(true);
    try { await hotspotApi.analyze(pid); await load(); }
    finally { setLoading(false); }
  };

  return (
    <Card className="mb-6 p-5">
      <div className="mb-3 text-sm font-medium text-ink">热搜辅助</div>
      <div className="flex gap-2">
        <Input placeholder="热搜源 URL（留空用设置中的源）" value={url} onChange={(e) => setUrl(e.target.value)} />
        <Button variant="ghost" disabled={loading} onClick={fetch}>抓取</Button>
        <Button variant="ghost" disabled={loading} onClick={analyze}>LLM 分析</Button>
      </div>
      <div className="mt-3 space-y-2">
        {items.map((h) => (
          <Card key={h.id} className="p-3 text-sm">
            <div className="text-ink">{h.title}</div>
            {h.analysis?.advice && <div className="mt-1 text-xs text-muted">建议：{h.analysis.advice}</div>}
          </Card>
        ))}
      </div>
    </Card>
  );
}
```

- [ ] **Step 2: 类型/构建检查**

Run: `cd frontend && npx tsc -b`
Expected: 无报错

- [ ] **Step 3: 提交**

```bash
git add frontend/src/pages/ShortStudio.tsx
git commit -m "style: 短篇六步法侧栏步骤态/Section卡片化/Hotspot卡片化/按钮分级"
```

---

### Task 8: 设置页 · SettingsPage.tsx

**Files:**
- Modify: `frontend/src/pages/SettingsPage.tsx`

**Interfaces:** 消费 `Button(variant)`、`Card`、`Tag`、`Input`、`Textarea`；逻辑（settingsApi 与各 save 回调）完全不变。

- [ ] **Step 1: 用以下完整内容替换 `SettingsPage.tsx`**

```tsx
import { useEffect, useState } from "react";
import { settingsApi } from "@/api/settings";
import type { ModelConfig, UserSettings } from "@/types";
import { Button, Input, Textarea, Card, Tag } from "@/components/ui";

export default function SettingsPage() {
  const [settings, setSettings] = useState<UserSettings | null>(null);
  const [models, setModels] = useState<ModelConfig[]>([]);
  const [newModel, setNewModel] = useState({ name: "", base_url: "", api_key: "", model: "", is_default: false });
  const [testMsg, setTestMsg] = useState("");

  const load = async () => {
    const s = await settingsApi.get();
    setSettings(s.data);
    const m = await settingsApi.listModels();
    setModels(m.data);
  };
  useEffect(() => { load(); }, []);

  if (!settings) return <div className="p-10 text-sm text-muted">加载中…</div>;

  const saveSettings = async (patch: Partial<UserSettings>) => {
    const { data } = await settingsApi.update(patch);
    setSettings(data);
  };

  const addHotspot = () => {
    const src = [...(settings.hotspot_sources || []), { url: "", name: "" }];
    saveSettings({ hotspot_sources: src });
  };
  const updHotspot = (i: number, key: string, val: string) => {
    const src = settings.hotspot_sources.map((s, idx) => (idx === i ? { ...s, [key]: val } : s));
    saveSettings({ hotspot_sources: src });
  };
  const delHotspot = (i: number) => {
    saveSettings({ hotspot_sources: settings.hotspot_sources.filter((_, idx) => idx !== i) });
  };

  return (
    <div className="mx-auto max-w-3xl px-6 py-12">
      <h1 className="font-serif text-2xl font-semibold tracking-wide text-ink">设置</h1>

      <Card className="mt-8">
        <div className="border-b border-line px-4 py-3 font-serif text-sm font-medium text-ink">递归调用上限（Agent 取数）</div>
        <div className="flex items-center gap-4 px-4 py-4">
          <input
            type="range" min={1} max={30}
            value={settings.recursive_limit}
            onChange={(e) => saveSettings({ recursive_limit: Number(e.target.value) })}
            className="accent-accent"
          />
          <span className="text-sm tabular-nums text-ink">{settings.recursive_limit}</span>
        </div>
      </Card>

      <Card className="mt-6">
        <div className="border-b border-line px-4 py-3 font-serif text-sm font-medium text-ink">热搜源（请求 URL + 适配器）</div>
        <div className="space-y-2 p-4">
          {settings.hotspot_sources.map((s, i) => (
            <div key={i} className="flex items-center gap-2">
              <Input placeholder="名称" value={s.name || ""} onChange={(e) => updHotspot(i, "name", e.target.value)} />
              <Input placeholder="https://..." value={s.url || ""} onChange={(e) => updHotspot(i, "url", e.target.value)} />
              <Button variant="ghost" onClick={() => delHotspot(i)}>删</Button>
            </div>
          ))}
          <Button variant="ghost" onClick={addHotspot}>+ 添加热搜源</Button>
        </div>
      </Card>

      <Card className="mt-6">
        <div className="border-b border-line px-4 py-3 font-serif text-sm font-medium text-ink">模型配置</div>
        <div className="space-y-2 p-4">
          {models.map((m) => (
            <Card key={m.id} className="flex items-center justify-between px-4 py-3 text-sm">
              <div>
                <span className="font-medium text-ink">{m.name}</span>
                <span className="ml-2 text-muted">{m.model}</span>
                {m.is_default && <Tag className="ml-2">默认</Tag>}
              </div>
              <div className="flex gap-2">
                <Button variant="ghost" onClick={async () => { await settingsApi.updateModel(m.id, { is_default: true }); load(); }}>设为默认</Button>
                <Button variant="ghost" onClick={async () => { await settingsApi.deleteModel(m.id); load(); }}>删</Button>
              </div>
            </Card>
          ))}
          <div className="grid grid-cols-2 gap-2 pt-2">
            <Input placeholder="名称" value={newModel.name} onChange={(e) => setNewModel({ ...newModel, name: e.target.value })} />
            <Input placeholder="model" value={newModel.model} onChange={(e) => setNewModel({ ...newModel, model: e.target.value })} />
            <Input placeholder="base_url" value={newModel.base_url} onChange={(e) => setNewModel({ ...newModel, base_url: e.target.value })} />
            <Input placeholder="api_key" type="password" value={newModel.api_key} onChange={(e) => setNewModel({ ...newModel, api_key: e.target.value })} />
          </div>
          <div className="flex gap-2">
            <Button variant="primary" onClick={async () => {
              if (!newModel.name || !newModel.base_url || !newModel.model) return;
              await settingsApi.createModel(newModel);
              setNewModel({ name: "", base_url: "", api_key: "", model: "", is_default: false });
              load();
            }}>+ 新增模型</Button>
            <Button variant="ghost" onClick={async () => {
              const r = await settingsApi.testModel(newModel);
              setTestMsg(r.data.ok ? "连接成功：" + (r.data.reply || "").slice(0, 50) : "失败：" + r.data.error);
            }}>测试连接</Button>
            <span className="text-xs text-muted self-center">{testMsg}</span>
          </div>
        </div>
      </Card>
    </div>
  );
}
```

- [ ] **Step 2: 类型/构建检查**

Run: `cd frontend && npx tsc -b`
Expected: 无报错

- [ ] **Step 3: 提交**

```bash
git add frontend/src/pages/SettingsPage.tsx
git commit -m "style: 设置页 section卡片化/滑块着色/按钮分级"
```

---

### Task 9: 最终构建验证

**Files:**
- Build: `frontend/` (全部已改文件)

**Interfaces:** 汇总 Task 1–8 的产出，产出可部署的 `frontend/dist`。

- [ ] **Step 1: 运行完整构建**

Run: `cd frontend && npm run build`
Expected: 输出 `dist/` 且命令以 0 退出（执行 `tsc -b && vite build`，无类型/构建错误）

- [ ] **Step 2: 抽查关键类名一致性**

Run: `cd frontend && grep -rn "bg-ink\b" src/ || echo "无残留反白用法"`
Expected: 输出 `无残留反白用法`（确认 `bg-ink text-paper` 反白已完全替换）

- [ ] **Step 3: 提交（若构建产生 dist 或 lock 变化）**

```bash
git add frontend/
git commit -m "style: 暖褐书香全局 UI 重设计完成 (构建通过)" || echo "无需提交"
```

---

## 自审对照（执行前已核对 spec）

- **Spec §3 令牌** → Task 1 (index.css) + Task 2 (tailwind.config.js) 实现颜色/字体/圆角/阴影。
- **Spec §4 组件** → Task 3 实现 Button 变体 / Card / Input·Textarea / SectionTitle / Tag / Empty。
- **Spec §5.1 AppShell** → Task 4。
- **Spec §5.2 HomePage** → Task 5（宋体标题、Tab 药丸、项目卡 Card 化、主按钮 primary）。
- **Spec §5.3 LongWorkspace** → Task 6（侧栏选中态、Panel 卡片化、Graph 暖色、Assistant 按钮分级）。
- **Spec §5.4 ShortStudio** → Task 7（步骤态、Section Card、Hotspot Card、按钮分级）。
- **Spec §5.5 SettingsPage** → Task 8（section Card、滑块 accent、按钮分级、Tag 默认标识）。
- **Spec §6/§8 实现与验证** → Task 9 全量 `npm run build` + 反白残留检查。
- 无占位符/TODO；类型签名在各 Task 间一致（组件 props 由 Task 3 定义，后续仅消费）；范围聚焦单一计划。

# 温暖纸感文学风 · UI 重设计方案（暖褐书香）

- **日期**：2026-07-12
- **状态**：已确认，待生成实现计划
- **范围**：全局统一改造（设计系统 + 全部页面视觉层），不改动任何业务逻辑与接口调用
- **基调**：B 暖褐书香（奶油底 + 深棕字 + 金棕强调 + 宋体衬线标题）

---

## 1. 背景与目标

现有 UI 为 "Lovart 极简工具风"：黑白灰、零圆角、1px 灰线、低密度、层级弱、缺乏文学氛围。用户评价"好丑"，要求重新设计为**温暖纸感文学风**，并做**全局统一改造**。

目标：
- 视觉从冷硬工具风转为书卷气、有温度、适合小说创作的阅读氛围。
- 保留全部页面结构、组件 props 接口与数据逻辑（仅替换视觉层）。
- 建立可复用的设计令牌与组件系统，后续页面自动继承新风格。

## 2. 设计原则

- **纸感优先**：奶油暖底 + 轻投影 + 柔圆角，营造纸质书写感。
- **衬线标题**：所有页面/区块标题用宋体衬线，正文保持清晰无衬线。
- **金棕点睛**：金棕 `#b07a3c` 仅用于强调（主按钮、选中态、关键标识），不滥用。
- **层级靠阴影与底色，不靠硬线**：用 surface / surface-2 / 轻阴影区分层次，分隔线改为暖灰。
- **离线可靠**：字体走系统栈，不依赖网络字体。

## 3. 设计令牌

### 3.1 颜色（替换并重命名现有 Tailwind 颜色）

现有 `ink / paper / muted / line / subtle` 改值并新增暖色变量：

| Token | 值 | 用途 |
|-------|-----|------|
| `paper` | `#faf6ee` | 奶油主背景（body / 顶栏底） |
| `surface` | `#fffdf8` | 纸面卡片（比背景更亮） |
| `surface-2` | `#f3ecdf` | 次级面 / hover 底 |
| `ink` | `#3a2c22` | 深棕主文字 |
| `ink-soft` | `#6b5d4f` | 次要文字 |
| `muted` | `#9a8c7b` | 弱文字 / 辅助说明 |
| `line` | `#e6dccb` | 暖灰分隔线 |
| `accent` | `#b07a3c` | 金棕强调（主按钮 / 选中 / 标识） |
| `accent-strong` | `#8f5f2c` | 强调 hover 加深 |
| `accent-soft` | `#f0e4d2` | 浅金棕底（选中态底 / 标签底） |

> 现有代码大量使用 `bg-paper / text-ink / border-line / text-muted / bg-subtle / bg-ink / text-paper`。`bg-ink text-paper` 原用于反白（侧栏选中、Button hover），重设计后**不再使用黑底反白**：侧栏选中改用 `accent-soft` 浅底 + 金棕左竖条 + 深棕字；Button 主变体用金棕填充白字。

### 3.2 字体

- `serif`（标题）：`"Songti SC","STSong","SimSun","Noto Serif SC",serif`
- `sans`（正文，沿用现有）：`"Inter","PingFang SC","Microsoft YaHei",system-ui,sans-serif`

### 3.3 圆角（替换原全 0）

- `none: 0` · `sm: 6px` · `DEFAULT: 8px` · `lg: 12px`

### 3.4 阴影

- `shadow-soft`：`0 1px 3px rgba(58,44,34,.08), 0 6px 18px rgba(58,44,34,.06)`
- `shadow-card-hover`：`0 2px 6px rgba(58,44,34,.10), 0 12px 28px rgba(58,44,34,.08)`

### 3.5 实施位置

- `frontend/src/index.css`：`:root` 变量改名 / 改值（保留 `--radius` 概念可删除，改用 Tailwind 圆角）。`body` 背景改 `paper`、文字改 `ink`、字体栈保留。`button/input/textarea` 圆角从 0 改为 `8px`。滚动条配色改暖灰。
- `frontend/tailwind.config.js`：`theme.extend` 中 `colors` 改暖色并新增 `surface/surface-2/ink-soft/accent/accent-strong/accent-soft`；`borderRadius` 改为 `none:0, sm:6, DEFAULT:8, lg:12`；`fontFamily` 新增 `serif`；`boxShadow` 新增 `soft` 与 `card-hover`。

## 4. 组件系统（`frontend/src/components/ui.tsx`）

- **Button**：新增 `variant?: "primary" | "ghost" | "subtle"`（默认 `ghost`）。
  - `primary`：金棕填充 `bg-accent text-paper`、圆角、`shadow-soft`，hover `bg-accent-strong`。
  - `ghost`：暖棕描边 `border border-ink/30 text-ink`，hover `bg-surface-2`（`border-line` 去掉硬边）。
  - `subtle`：浅底文字 `text-ink-soft hover:bg-surface-2`。
  - 保留 `className` 追加逻辑，兼容现有调用（现有 `border-line text-muted hover:bg-subtle` 类可继续用）。
- **Card**：`bg-surface border border-line rounded-lg shadow-soft`，新增 `padding` 变体（默认 `p-4`，支持 `p-0`）。
- **Input / Textarea**：`bg-surface border border-line rounded-lg text-ink`，聚焦 `focus:border-accent focus:ring-1 focus:ring-accent/30`。
- **新增 SectionTitle**：宋体小标题，左侧金棕短竖条（`border-l-2 border-accent pl-2 font-serif`）。
- **新增 Tag**：浅金棕胶囊 `bg-accent-soft text-accent-strong rounded-full px-2 py-0.5 text-xs`。
- **新增 EmptyState**：居中弱字空态 `text-center text-muted py-10`。

## 5. 页面改造规范（逻辑不变，仅视觉/层级）

### 5.1 AppShell
- 顶栏：`bg-paper` + 底部 `border-b border-accent/30`（细金棕线）；品牌字 "NOVEL STUDIO" 改 `font-serif text-lg`；中文副标 `text-muted`；导航项 hover 转 `text-accent`；`main` 背景继承 `paper`。

### 5.2 HomePage（项目列表）
- 标题 `font-serif`；Tab 选中改金棕下划线 / 药丸（`bg-accent-soft text-accent-strong rounded-full`）；"新建项目"按钮改 `primary`。
- 项目网格：每项用 `Card`，标题 `font-serif`，简介 `text-muted`，时间 `text-muted text-xs`；hover 加 `shadow-card-hover` 与轻微上移 `hover:-translate-y-0.5 transition`。
- 新建输入区：用 `Card` 包裹。

### 5.3 LongWorkspace（长篇工作区）
- 侧栏：底色 `bg-surface`，选中项改 `bg-accent-soft text-ink border-l-2 border-accent`（替原 `bg-ink text-paper`）；"返回"按钮 `subtle`。
- 各 Panel（Outline/Character/Foreshadow/World/Plot/Chapter/Graph/Assistant）：标题用 `SectionTitle`；列表项与表单区改 `Card`。
- `GraphPanel`：节点色 `colorByType` 改暖棕 `#3a2c22` / 金棕 `#b07a3c` / 灰 `#9a8c7b`；关系用暖灰。
- `AssistantPanel`：变更卡片用 `Card`；"确认并应用" → `primary`，"拒绝" → `ghost`；摘要卡片 `bg-surface-2`。

### 5.4 ShortStudio（短篇六步法）
- 侧栏步骤：完成态 `text-accent` + 金棕勾 `✓`；当前步 `text-ink font-medium`，未达 `text-muted`。
- `Section` 组件改 `Card`（替原 `border border-line p-4`）；"刷新"/`ghost`；生成类按钮 `primary`（主操作）、`ghost`（次操作）。
- `HotspotPanel`：改 `Card` 包裹。

### 5.5 SettingsPage
- 各 `section` 改 `Card`（替原 `border border-line`）；标题 `font-serif` / `SectionTitle`。
- 滑块 `range`：用 `accent-accent` 着色（原生 `accent-color`）。
- 模型列表项改 `Card` 化小卡；"设为默认/删" → `ghost`；"新增模型/测试连接" → `primary` / `ghost`。
- 热搜源行：`Input` + `ghost` 删除按钮。

## 6. 字体与离线策略

- 不引入网络字体（桌面端为 pywebview / Edge WebView，离线场景网络字体不稳）。
- 标题用系统宋体栈兜底；若运行环境装有 Noto Serif SC 则自动采用。
- 在 `index.css` 顶部注释说明：如需联网统一字体，可加入 Google Fonts 的 Noto Serif SC `<link>`。

## 7. 实现步骤

1. `index.css`：修改变量、body 字体/背景、控件圆角、滚动条配色。
2. `tailwind.config.js`：扩展 colors / borderRadius / fontFamily / boxShadow。
3. `components/ui.tsx`：Button 变体 + Card + Input/Textarea 升级 + 新增组件。
4. `components/AppShell.tsx`：顶栏暖色化、品牌宋体。
5. `pages/HomePage.tsx`：标题宋体、Tab 药丸、项目卡 Card 化、主按钮 primary。
6. `pages/LongWorkspace.tsx`：侧栏选中态、Panel 标题/卡片化、Graph 配色、Assistant 按钮。
7. `pages/ShortStudio.tsx`：侧栏步骤态、Section Card 化、Hotspot 卡片化、按钮分级。
8. `pages/SettingsPage.tsx`：section Card 化、滑块着色、按钮分级。
9. 构建验证：`cd frontend && npm run build`。

## 8. 验证与测试

- 主验证：`npm run build`（执行 `tsc -b && vite build`），确保无 TypeScript / 构建错误。项目未配置 ESLint，`noImplicitAny:false`，构建通过即视为视觉层改造成功。
- 桌面端反映：构建后 `frontend/dist` 由后端 `desktop_launcher.py` 作为 SPA 服务，重跑桌面端即可见新界面。
- 不改动任何 API 调用与业务逻辑，回归风险仅限样式。

## 9. 风险与注意

- 现有组件调用大量使用 `bg-ink / text-paper / border-line / text-muted`，重构时需保证这些类名仍存在于 Tailwind 配置（值改暖），避免样式丢失；其中 `bg-ink text-paper` 的反白用法需显式替换为新选中态。
- 不引入新依赖（纯 Tailwind + CSS 变量），不改变构建配置主版本。
- 保持 `noImplicitAny:false`，不触发大范围类型改动。

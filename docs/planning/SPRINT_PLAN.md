# 小说创作助手 — 重建版 Sprint 详细计划（Greenfield）

> **范围声明**：本计划为**从零开始**的全新实现，代码落地于 `F:\python_project\novel`，**不与现有 `novel_create` 代码产生任何直接依赖/引用**。现有 `novel_create` 仅作为"反面教材"（已知缺陷）参考；`F:\python_project\fanqie-short-novel` 作为**设计借鉴**（本地客户端形态、短篇六步法、热搜 URL 方案），但所有代码重新编写。
>
> 需求来源：`project.md`（长篇推倒重建 / 本地客户端 / 长短篇分离 / 重新设计 Agent Harness）。

---

## 0. 架构决策（全新基线）

| 维度 | 决策 | 理由 |
|------|------|------|
| 部署形态 | **本地客户端**（无云端） | 需求明确"不云端部署" |
| 桌面壳 | **pywebview + 系统 WebView**（参考 fanqie 的 `desktop_launcher.py` 思路，重新实现） | 比 Electron/Tauri 轻，Python 生态无缝，无需 Rust |
| 后端 | **FastAPI + SQLAlchemy 2.0 (async)** | 轻量、成熟 |
| 关系数据库 | **SQLite（本地文件）** | 本地客户端无需独立 DB 服务；`create_all` 即可建表 |
| 缓存 / Checkpoint | **移除 Redis**；LangGraph 用内置 `SqliteSaver`；热点缓存走 SQLite 表 | 去外部依赖 |
| 知识图谱 | **SQLite 表为唯一真相源**；Neo4j 作为**可选派生镜像**（id 主键，仅可视化/关系查询） | 根治旧"角色不显示"：agent 直查 SQLite 仓库；Neo4j 仅按 id 同步 |
| 数据模型 | **长/短完全分表**，每篇小说 = 一个 `projects`（`type=long|short`），`project_id` 为唯一 key | 需求"具体数据不在同一张表，项目 ID 做唯一 key" |
| Agent 框架 | 重写 **Harness**：Supervisor 拆分 → 专精 Worker **仅通过 tool/MCP 读数据**（有限递归）→ 变更记录 → 确认落库（Saga 双写 id 化）。**Worker 禁止直接读写数据库** | 需求第 5/6/7 条 |
| Agent 数据访问 | **强约束**：Worker 只能调用 `agents/tools/`（或 MCP server）暴露的**只读工具**获取数据；**任何写操作一律禁止直连 DB**，只能产出 `ChangeRecord`，经用户确认后由 `change_apply` 统一落库 | 隔离副作用，保证"变更→确认→应用"闭环不可绕过 |
| 热搜获取 | **请求 URL + 可配置适配器**（数据源 URL + 字段映射存设置），无本地爬虫 | 需求"热搜获取不再依赖本地爬虫，请求 url 来获取" |
| 前端 | React 19 + TS + Vite + Tailwind + shadcn/ui + Zustand + axios；视觉统一 Lovart 黑白灰、无圆角、高对比、大留白 | 需求第 3 条 |

---

## 1. 目标目录结构（`F:\python_project\novel`）

```
novel/
├── backend/
│   ├── app/
│   │   ├── main.py                 # FastAPI 入口（SPA 静态托管 + 路由注册 + 生命周期）
│   │   ├── config.py               # Pydantic Settings（读本地 .env / 配置文件）
│   │   ├── database.py             # async SQLAlchemy engine + session
│   │   ├── models.py               # 全部 ORM（projects / long_* / short_* / 公共）
│   │   ├── schemas/                # Pydantic 请求/响应（按资源分文件）
│   │   ├── api/                    # 路由（projects / long_* / short_* / assistant / settings / hotspots）
│   │   ├── services/               # 业务逻辑（short_story / hotspot / change_apply / ...）
│   │   ├── agents/                 # 新 Harness（harness/ + tools/）
│   │   ├── repositories/           # 数据访问层（供 Worker 与前端共用）
│   │   ├── graph/                  # Neo4j 客户端 + id 主键 Cypher（可选镜像）
│   │   └── core/                   # llm_client / errors / security / preset_data
│   ├── requirements.txt
│   └── desktop_launcher.py         # pywebview 启动器
├── frontend/
│   ├── src/
│   │   ├── main.tsx / App.tsx       # 路由
│   │   ├── api/                     # axios 封装（分模块）
│   │   ├── pages/                   # 页面（项目/长篇工作区/短篇六步/设置）
│   │   ├── components/              # ui/ (shadcn) + 业务组件
│   │   ├── stores/                  # Zustand（按项目维度）
│   │   ├── types/                   # TS 类型
│   │   └── index.css                # Lovart 黑白灰主题
│   ├── package.json / vite.config.ts / tailwind.config.js / components.json
├── data/                            # 本地 SQLite 文件 + 配置（运行时生成）
└── docs/planning/                   # 本计划
```

---

## 2. 数据模型设计（全部重新设计）

```
projects
  id(UUID,PK) | type('long'|'short') | title | description | created_at | updated_at
  -- 唯一 key；每篇小说一个 project

# 长篇小说数据（project_id 外键；全部手动可改 + 持久化 + 供 agent 取数）
long_outlines        id | project_id | parent_id(树) | title | content | version_chain(上一版id) | order
long_characters      id | project_id | name | traits | ability | status | relations(JSON) | importance
long_foreshadows     id | project_id | title | content | state('pending'|'revealed'|'abandoned') | subplot_id
long_world_settings  id | project_id | category | content
long_plot_nodes      id | project_id | title | summary | timeline_pos
long_chapters        id | project_id | title | content | order | constraints(JSON)
long_change_records  id | project_id | entity_type | entity_id | before(JSON) | after(JSON) | status('staged'|'applied'|'rejected')

# 短篇小说数据（与长篇完全分表）
short_settings       id(PK=project_id) | core_hook | plans(JSON) | detail_plan | chapters_plan | writing | integration | ...
short_chapters       id | project_id | title | content | order
short_hotspots       id | project_id | source | title | url | analysis(JSON) | used(bool)

# 公共
model_configs        id | name | base_url | api_key | model | is_default
user_settings        id | recursive_limit(int) | hotspot_sources(JSON:[{url,adapter}]) | theme
assistant_sessions   id | project_id | staged_changes(JSON:变更引用) | updated_at
```

**设计要点**
- 长/短数据零耦合，互不可见对方表。
- `long_*` 全部 `project_id` 索引；`long_change_records` 支撑"变更→确认→应用"闭环。
- `long_outlines.version_chain` 实现"大纲调整依赖上一版"。
- `user_settings.recursive_limit` 控制 agent 递归取数上限（需求第 7 条）。

---

## 3. 新 Agent Harness 流程（核心修复点）

```
用户输入
 → [前置数据获取]   repositories 拉取当前 project 全量上下文（大纲/角色/伏笔/世界观/剧情节点）
 → [任务分析]       Supervisor LLM → ExecutionPlan（意图 + 子任务 + 派发参数）
 → [任务分配]       按子任务派发专精 Worker（character/world/outline/plot/foreshadow）
 → [各 agent 取数]  Worker **仅通过 tool/MCP 只读工具**获取数据（工具内部封装 repositories/Neo4j），
                    **禁止直连数据库**；允许**有限次递归调用**工具组装数据；上限 = user_settings.recursive_limit
 → [LLM 返回]       各 Worker 产出结构化结果（仅描述"期望的变更"，不落库）
 → [生成变更记录]   aggregator → ChangeRecord[](稳定 id + 实体类型 + before/after + requires_confirmation)
 → [LLM 总结]       responder 汇总变更摘要 + 前端预览，发到前端
 → [用户确认]       前端 diff 展示，确认 / 拒绝 / 局部改
 → [应用变更]       **唯一写入口** change_apply Saga 双写：SQLite 表（真相源）+ Neo4j（id 主键镜像）
```

**数据访问强约束（本次新增）**
- Worker **只能读、不能写**：读数据一律经 `agents/tools/`（或 MCP server）暴露的**只读工具**；工具内部才调用 `repositories/*`（Worker 不直接 import repositories，更不直连 `AsyncSession`）。
- **唯一写入口**：`services/change_apply.py`。除它以外，Harness 全链路（Supervisor/Worker/aggregator/responder/tools）**禁止任何 DB 写操作**。
- 写意图只能表达为 `ChangeRecord`，必须经用户确认才由 `change_apply` 执行；未确认的变更永不落库。

**相对旧 Harness 的关键修复**
1. Worker 经**只读 tool/MCP** 查真实数据（非会话 state 快照），且物理上无写能力 → 新建数据可见、副作用可控。
2. ChangeRecord 携带**稳定 id**；确认时按 id 双写，Neo4j 用 `create_character_node(id=…)` → 根治"角色不显示"。
3. `change_apply` 为唯一写入口且用 Saga，失败**结构化返回前端可见**，不静默 rollback。
4. `assistant_sessions` 只存变更引用，去除"会话 JSON 与真实表分裂"。
5. 递归取数上限可配置，硬上限 + 超时保护防失控。

---

## Sprint 总览

| Sprint | 主题 | 核心交付 |
|--------|------|----------|
| **S0** | 工程骨架（本地客户端） | pywebview + FastAPI + SQLite + 现代前端壳可运行 |
| **S1** | 项目与数据模型 + 设置 | `projects` + 长/短分表 + CRUD + 模型/递归上限/热搜源设置 |
| **S2** | 短篇小说六步法 + URL 热搜 | 六步流程 + 进度续接 + 可配置热搜适配器 |
| **S3** | 长篇小说基础数据管理 | 大纲/角色/伏笔/世界观/剧情节点 手动可改 + 持久化 + 前端面板 + repositories |
| **S4** | 新 Agent Harness 内核 | Supervisor/Worker/aggregator/responder + 递归取数 + Saga 双写（id 化） |
| **S5** | 变更确认流与 Agent 工具 | 变更记录→前端 diff→确认→应用；agent 工具（如大纲依赖上一版） |
| **S6** | 知识图谱与上下文续写 | Neo4j id 镜像 + 上下文感知续写 + 图谱可视化 |
| **S7** | 现代前端打磨与本地打包 | 视觉/主题 + 打包 + 备份恢复 + 导出 |

---

## S0：工程骨架（本地客户端）— 从零搭建

### 目标
在 `F:\python_project\novel` 从零搭建可运行空壳：pywebview 壳 + FastAPI + SQLite + React/Vite 现代壳。无任何旧代码依赖。

### 任务清单
| 任务 | 文件/内容 | 预估 |
|------|-----------|------|
| 0.1 | 初始化目录结构（backend/ frontend/ data/ docs/） | 0.5h |
| 0.2 | `backend/requirements.txt`：fastapi, uvicorn, sqlalchemy, aiosqlite, pydantic-settings, httpx, langgraph, neo4j, python-dotenv | 0.5h |
| 0.3 | `backend/app/config.py`：Pydantic Settings（DB 路径、LLM 默认、递归上限默认值） | 1.5h |
| 0.4 | `backend/app/database.py`：async engine（sqlite+aiosqlite）+ `AsyncSession` + `get_db` 依赖 | 2h |
| 0.5 | `backend/app/main.py`：FastAPI 入口、`/health`、CORS（本地）、SPA 静态托管占位、启动 `create_all` | 2h |
| 0.6 | `backend/app/core/errors.py`：统一错误码 + 异常处理器 | 1.5h |
| 0.7 | `backend/app/core/llm_client.py`：OpenAI 兼容客户端（非流式 `chat` + 流式 `chat_stream` + `parse_llm_json` + 错误处理） | 3h |
| 0.8 | `backend/desktop_launcher.py`：pywebview（`gui="edgechromium"`）启动 FastAPI 并开窗口；前端构建产物目录配置 | 2h |
| 0.9 | 前端：`npm create vite`（react-ts）、Tailwind、shadcn/ui、Zustand、axios；`api/client.ts` 基地址指向本地后端 | 2.5h |
| 0.10 | `frontend/src/index.css`：统一 Lovart 黑白灰 + `--radius:0` + 1px 细线，清洗暖橙残留（参考 fanqie `StepNavigator.tsx` 做法） | 1.5h |
| 0.11 | `App.tsx` + `AppShell.tsx`（Header+Sidebar+Content）+ 空路由；首页占位 | 2h |

### 验收标准
- [ ] `python backend/desktop_launcher.py` 打开本地窗口，访问 `/health` 返回 200
- [ ] 前端显示现代简洁空壳（黑白灰、无圆角）
- [ ] SQLite 文件在 `data/` 生成；`create_all` 建表成功（即便表尚空）
- [ ] 代码提交，目录结构清晰，无对 `novel_create` 的引用

### 风险
- pywebview 缺 Edge → 兜底 `gui="cef"` 或打开系统浏览器
- Node 版本 → 用项目内嵌或固定 LTS

---

## S1：项目与数据模型 + 设置

### 目标
实现 `projects` 与长/短完全分离的 `long_*` / `short_*` 表（以 `project_id` 唯一 key）；项目/设置 CRUD。

### 任务清单
| 任务 | 文件/内容 | 预估 |
|------|-----------|------|
| 1.1 | `models.py`：`Project`（type 枚举）、`ModelConfig`、`UserSetting`、`AssistantSession` | 2h |
| 1.2 | 长篇表：`LongOutline`(树+版本链)、`LongCharacter`、`LongForeshadow`、`LongWorldSetting`、`LongPlotNode`、`LongChapter`、`LongChangeRecord` | 4h |
| 1.3 | 短篇表：`ShortSetting`(累积 JSON)、`ShortChapter`、`ShortHotspot` | 2h |
| 1.4 | `schemas/`：`project.py`、`long.py`、`short.py`、`setting.py`（请求/响应分文件） | 3h |
| 1.5 | `api/projects.py`：CRUD；`GET /projects` 按 type 过滤；返回对应数据入口 | 2h |
| 1.6 | `api/settings.py`：`/settings/models`（增删改查/测试/默认）、`/settings`（读写为 recursive_limit、hotspot_sources、theme） | 2.5h |
| 1.7 | 前端：首页项目列表（卡片）、新建项目（选长/短）、项目详情骨架 | 3h |
| 1.8 | 前端：设置页（模型配置表单 + 递归调用上限滑块 + 热搜源 URL 列表编辑 + 主题切换） | 3h |

### 验收标准
- [ ] 创建长/短项目，互不可见对方数据表
- [ ] `long_*` 与 `short_*` 确为不同表（检查 DDL）
- [ ] 可配置多 LLM、设置递归上限、增删热搜源 URL，持久化重启不丢

### 风险
- 长表字段多 → 先最小字段集，迭代补充

---

## S2：短篇小说六步法 + URL 热搜

### 目标
实现短篇六步法（爽点→方案→详细规划→章节规划→写作→整合），与参考项目思路一致但重写；热搜走**请求 URL + 可配置适配器**，无爬虫。

### 任务清单
| 任务 | 文件/内容 | 预估 |
|------|-----------|------|
| 2.1 | `services/short_story.py`：`ShortStoryService` — 六步方法（set_core_hook / generate_plans / select_plan / generate_detail_plan / generate_chapters / generate_chapter_content / integrate） | 4h |
| 2.2 | Prompt 模板（独立 `services/prompts/short_story.py`）：PLAN / DETAIL / CHAPTER / INTEGRATION / OPENING_HOOK / TITLE | 3h |
| 2.3 | `api/short_story.py`：各步骤端点 + `GET /{id}/progress`（进度续接） | 3h |
| 2.4 | `services/hotspot.py`：`SourceAdapter` 抽象 `fetch(url, headers) -> List[Hotspot]`；数据源来自 `user_settings.hotspot_sources`（URL + 字段映射）；`fetch_hotspots` 带 DB 缓存 TTL | 3h |
| 2.5 | `api/hotspots.py`：`/hotspots`（抓取）、`/hotspots/analyze`（LLM 二次筛选适配创作）、`/hotspots/stored` | 2h |
| 2.6 | 前端：短篇六步页面（分类→爽点→方案→规划→章节→写作→整合）+ 进度续接跳转（参考 fanqie `ShortStoryRedirectPage` 思路） | 5h |
| 2.7 | 前端：热搜面板（配置源抓取结果 + LLM 适配建议，可插入创作） | 2h |

### 验收标准
- [ ] 短篇六步走通并累积存储到 `short_settings`
- [ ] 刷新不丢进度（进度续接）
- [ ] 热搜走 URL 请求（非爬虫）；新增数据源仅需在设置填 URL+映射，不改代码
- [ ] 热搜结果经 LLM 分析可用于创作

### 风险
- 平台 URL 结构变动 → 适配器配置化降低耦合
- Prompt 过长 → 按需精简，保留高质量模板

---

## S3：长篇小说基础数据管理

### 目标
长篇大纲/角色/伏笔/世界观/剧情节点**手动可改 + 持久化**，构成 agent 取数底座；建立 repositories 层。

### 任务清单
| 任务 | 文件/内容 | 预估 |
|------|-----------|------|
| 3.1 | `repositories/long_outline.py`、`long_character.py`、`long_foreshadow.py`、`long_world.py`、`long_plot.py`、`long_chapter.py`：统一查询/更新接口。**注意分层**：repositories 仅供 API 层与 `change_apply`/只读工具层调用，**Worker 不直接 import repositories**（Worker 只用 S4 的只读工具/MCP） | 3h |
| 3.2 | `api/long_outline.py`：树形 CRUD + 版本链（每次修改存上一版引用，保留最近 N 版可配） | 2h |
| 3.3 | `api/long_character.py`、`long_foreshadow.py`（状态流转）、`long_world.py`、`long_plot.py`、`long_chapter.py`：手动编辑 API（PUT 局部更新） | 4h |
| 3.4 | 前端：长篇工作区（大纲树面板、角色面板、伏笔面板、世界观面板、剧情节点时间线） | 6h |
| 3.5 | 编辑器：长篇章节编辑 + 自动保存 + 选中文本标记伏笔 | 3h |

### 验收标准
- [ ] 大纲/角色/伏笔/世界观/剧情节点均可手动增删改并持久化
- [ ] 大纲修改保留版本链（可查看依赖的上一版）
- [ ] 前端各面板可编辑、刷新不丢
- [ ] repositories 接口可被 S4 Worker 直接调用

### 风险
- 树形大纲拖拽 → 先用上下移/缩进，不做自由拖拽
- 版本链膨胀 → 仅保留最近 N 版，可配

---

## S4：新 Agent Harness 内核

### 目标
重写 Harness：Supervisor 拆分 + Worker **仅通过只读 tool/MCP 取数**（有限递归，禁止直连 DB）+ 变更记录 + Saga 双写（id 化，唯一写入口）。修复旧落库缺陷。

### 任务清单
| 任务 | 文件/内容 | 预估 |
|------|-----------|------|
| 4.1 | `agents/harness/state.py`：`HarnessState`（含 ChangeRecord reducer） | 2h |
| 4.2 | `agents/tools/`：**只读工具层**（`read_outline`、`read_outline_prev_version`、`read_characters`、`read_foreshadows`、`read_world`、`read_plot_nodes` 等）。工具内部封装 repositories/Neo4j，**只读、无写能力**；统一工具注册表 + 参数 schema | 4h |
| 4.3 | （可选）MCP server：将上述只读工具以 MCP 协议暴露（`design-converter` 之外的本地 `novel-data` server），便于 Worker/外部 agent 复用 | 2h |
| 4.4 | `agents/harness/nodes/supervisor.py`：任务分析/拆分（LLM→ExecutionPlan，schema 校验+兜底） | 3h |
| 4.5 | `agents/harness/worker_base.py`：**仅通过工具注册表调用只读工具**取数（不 import repositories、不持有 session），递归上限读 `user_settings.recursive_limit`，硬上限+超时；提供工具调用循环（tool-calling loop） | 3h |
| 4.6 | 专精 Workers：`character_worker.py`、`world_worker.py`、`outline_worker.py`、`plot_worker.py`、`foreshadow_worker.py`（经工具取数 + LLM 抽取 → **仅产出"期望变更"结构化结果，不落库**） | 5h |
| 4.7 | `agents/harness/nodes/aggregator.py`：Worker 结果 → `ChangeRecord[]`（稳定 id + 实体类型 + before/after + requires_confirmation） | 3h |
| 4.8 | `agents/harness/nodes/responder.py`：LLM 汇总变更摘要 + 前端预览 | 2h |
| 4.9 | `services/change_apply.py`：**唯一写入口** + Saga 双写（SQLite 真相源 + Neo4j id 主键镜像），结构化错误返回，去静默 rollback | 4h |
| 4.10 | LangGraph `SqliteSaver` checkpoint 接入 `agents/harness/graph.py` | 1h |

### 验收标准
- [ ] Supervisor 正确拆分并派发任务
- [ ] Worker **仅通过 tool/MCP 读取**真实 DB 状态（非快照）；代码审查确认 Worker 无 repositories/session 直连、无任何写操作
- [ ] 递归取数次数受 `user_settings` 限制
- [ ] ChangeRecord 带稳定 id；确认后 Neo4j 角色**正常显示**（id 主键）
- [ ] 写操作只发生在 `change_apply`；落库失败前端可见错误，无静默丢失

### 风险
- LLM 拆分质量 → supervisor 输出 schema 校验 + 兜底
- 递归失控 → 硬上限 + 超时保护
- 工具边界被绕过 → 通过分层约定 + 代码审查/静态检查（Worker 模块禁止 import repositories/database）保证

---

## S5：变更确认流与 Agent 工具

### 目标
打通"变更记录→前端 diff→用户确认→应用"全链路；支持 agent 直接调用接口/工具调整数据（如大纲依赖上一版）。

### 任务清单
| 任务 | 文件/内容 | 预估 |
|------|-----------|------|
| 5.1 | `api/assistant.py`：`/chat`（输入→前置取数→分析→派发→变更）、`/confirm`（应用）、`/reject`、`/undo` | 3h |
| 5.2 | 前端：变更 diff 视图（before/after 高亮）、确认/拒绝/局部改、undo | 4h |
| 5.3 | 扩展 `agents/tools/`：新增**"写意图"工具**（propose_update_character、propose_add_foreshadow、propose_update_outline…）。注意：这类工具**不落库**，仅生成/追加 `ChangeRecord` 到当前会话的 staged_changes，最终仍由 `change_apply` 在确认后统一写入 | 4h |
| 5.4 | 大纲调整场景：agent 用只读工具读上一版大纲→LLM 出新版→写意图工具生成变更→用户确认→`change_apply` 落库（验证依赖历史，全程 Worker 不直连 DB） | 3h |
| 5.5 | 流式展示：节点执行进度（分析中/取数中/生成变更中） | 2h |

### 验收标准
- [ ] 用户看到清晰变更 diff 并确认/拒绝
- [ ] 确认后数据正确落 SQLite 且 Neo4j 同步
- [ ] agent 可在递归中调用工具读取历史大纲并生成新版
- [ ] 前端实时显示各阶段进度

### 风险
- 复杂结构 diff → 先文本/字段级 diff，富结构后续
- 工具误改 → 写操作一律"变更记录+确认"，不直接落库

---

## S6：知识图谱与上下文续写

### 目标
Neo4j 作为 id 主键派生镜像；长篇上下文感知续写注入大纲/角色/伏笔；图谱可视化。

### 任务清单
| 任务 | 文件/内容 | 预估 |
|------|-----------|------|
| 6.1 | `graph/client.py`：Neo4j 驱动连接（可选；无则降级 SQLite 关系查询） | 2h |
| 6.2 | `graph/queries.py`：id 主键 Cypher（create_character_node(id=…)、关系边、列表查询） | 3h |
| 6.3 | 同步：Saga 双写已覆盖；补充关系边（角色关系、伏笔-章节）按 id 写入 | 2h |
| 6.4 | 上下文组装：续写注入大纲/角色设定/待回收伏笔/前文衔接约束 | 3h |
| 6.5 | 长篇续写端点（SSE 流式，复用 `core/llm_client.py`） | 2h |
| 6.6 | 前端：续写面板（流式）+ 本次使用上下文标注 | 2h |
| 6.7 | 图谱可视化（力导向图：角色/关系/时间线/伏笔回收） | 4h |

### 验收标准
- [ ] Neo4j 角色/关系正常显示（id 主键，无"不显示"）
- [ ] 续写能引用大纲/角色/伏笔上下文
- [ ] 前端可视化图谱，点击节点看详情
- [ ] 无 Neo4j 时功能降级可用

### 风险
- Neo4j 本地内存 → 可选关闭，SQLite 关系查询兜底
- 上下文过长 → 截断/优先级策略

---

## S7：现代前端打磨与本地打包

### 目标
视觉打磨、主题、桌面打包、备份恢复、导出。

### 任务清单
| 任务 | 文件/内容 | 预估 |
|------|-----------|------|
| 7.1 | 视觉打磨：统一间距/层级/对比；骨架屏；Toast 错误；快捷键（Ctrl+S/Ctrl+Enter） | 4h |
| 7.2 | 暗/亮主题切换（清洗 CSS 变量，统一黑白灰） | 2h |
| 7.3 | 桌面打包：`desktop_launcher.py` 加固（后端进程守护/崩溃重启/单实例/图标/构建产物路径） | 3h |
| 7.4 | 备份/恢复：导出项目完整数据（长/短表 + 可选 Neo4j 导出）、导入恢复 | 3h |
| 7.5 | 导出：Markdown / TXT / EPUB（长/短篇） | 3h |
| 7.6 | 文档：`development.md`、`agent_workflow.md`、README | 2h |

### 验收标准
- [ ] 界面现代简洁、主题一致、无暖橙残留
- [ ] 双击启动本地客户端，自动拉起后端
- [ ] 可备份/恢复完整项目数据
- [ ] 可导出 Markdown/TXT/EPUB

### 风险
- pywebview 分发 → 提前测试干净环境
- EPUB 复杂 → 后置，先 Markdown/TXT

---

## 跨 Sprint 依赖

```
S0 工程骨架
  └─→ S1 项目与数据模型
        ├─→ S2 短篇六步法（可与 S3 并行）
        ├─→ S3 长篇数据管理
        │     └─→ S4 新 Harness 内核
        │           └─→ S5 变更确认流 + Agent 工具
        │                 └─→ S6 知识图谱 + 上下文续写
        │                       └─→ S7 打磨 + 打包
        └─→ S7（前端基线贯穿全程）
```

## 关键里程碑

| 里程碑 | Sprint | 标志 |
|--------|--------|------|
| 本地客户端可运行 | S0 | pywebview 打开空壳 |
| 数据模型重建完成 | S1 | 长/短分表、项目隔离 |
| 短篇可用 | S2 | 六步法 + URL 热搜 |
| 长篇数据可管 | S3 | 大纲/角色等手动可改持久化 |
| Harness 修复验证 | S4 | Neo4j 角色正常显示、变更记录 id 化 |
| 确认流闭环 | S5 | 变更 diff→确认→落库 |
| 图谱+续写 | S6 | 上下文续写 + 可视化 |
| 发布候选 | S7 | 本地打包 + 备份导出 |

---

*本计划为 `project.md` 重建需求的 Sprint 详细切分，落地于 `F:\python_project\novel`（greenfield）。下一步从 **S0** 开始实现。*

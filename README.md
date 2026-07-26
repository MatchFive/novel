# Novel Studio

> 本地优先的长篇小说创作工作台。AI 辅助、数据本地存储、所有变更需经用户确认后才会写入。

Novel Studio 是一款面向长篇小说的本地桌面创作工具。它将 FastAPI 后端与 React 前端打包为一个独立的桌面应用，所有项目数据保存在本地 SQLite；可选 Neo4j 做关系镜像。AI 助手通过只读工具分析项目，所有写操作以变更草稿形式呈现，经用户确认后由唯一写入口统一落库。

## 功能特性

- **本地桌面应用**：`backend/desktop_launcher.py` 一键启动 FastAPI + pywebview 窗口（Windows 默认 Edge WebView，缺失时回退 CEF 或系统浏览器）。
- **长篇项目管理**：大纲（树形 + 版本链）、角色、伏笔、世界观、剧情节点、章节。
- **AI 助手工作流**：Supervisor 拆分任务 → Worker 只读工具分析 → 生成 ChangeRecord → 前端 Diff → 用户确认/拒绝 → Saga 双写入库。
- **上下文感知续写**：基于大纲、角色、伏笔与最近章节，通过 SSE 流式生成续写。
- **知识图谱**：支持 Neo4j 关系镜像；未启用时自动降级为 SQLite 关系查询。
- **导出**：Markdown / JSON 导出。

## 技术栈

| 层级 | 技术 |
|------|------|
| 桌面壳 | Python + pywebview |
| 后端 | Python 3.11+、FastAPI、SQLAlchemy 2.0 (async)、aiosqlite、Pydantic Settings |
| 前端 | React 19、TypeScript、Vite、Tailwind CSS、Zustand、Axios |
| AI Harness | 自研文本协议工具循环、OpenAI 兼容 LLM 客户端 |
| 可选图数据库 | Neo4j |

## 快速开始

### 环境要求

- Python ≥ 3.11
- Node.js（用于前端构建与开发）

### 1. 安装依赖

```bash
# 后端
cd backend
pip install -r requirements.txt

# 前端
cd ../frontend
npm install
```

### 2. 配置环境变量

复制示例配置并填写 LLM 信息：

```bash
cp .env.example .env
```

最少需要配置：

```env
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=your-api-key
LLM_MODEL=gpt-4o-mini
```

完整可选配置见 [.env.example](.env.example)。

### 3. 构建并启动桌面客户端

```bash
cd frontend
npm run build      # 产物输出到 frontend/dist，由后端静态托管

cd ../backend
python desktop_launcher.py
```

启动后自动拉起 FastAPI（默认 `http://127.0.0.1:8765`）并打开本地窗口；访问 `/health` 应返回 200。

> 开发前端时可使用 `cd frontend && npm run dev`，Vite 代理 `/api` 到 `127.0.0.1:8765`。

## 开发

### 后端开发

```bash
cd backend
uvicorn app.main:app --reload --port 8765
```

语法检查：

```bash
cd backend && python -m compileall app
```

冒烟测试：

```bash
curl http://127.0.0.1:8765/health
```

### 前端开发

```bash
cd frontend
npm run dev
```

类型检查：

```bash
cd frontend && npx tsc -b
```

> 修改前端后请重新执行 `npm run build`，否则通过桌面客户端运行时不会看到最新改动。

## 项目结构

```
backend/
  app/
    main.py              # FastAPI 入口：/health、路由注册、SPA 托管
    config.py            # Pydantic Settings，读取 .env
    database.py          # 异步引擎 + AsyncSessionLocal
    models.py            # SQLAlchemy ORM 模型
    core/                # LLM 客户端、错误定义
    repositories/        # 唯一 CRUD 模块（API、change_apply、只读工具共用）
    api/                 # /api、/api/settings、/api/long、/api/assistant、/api/export、/api/graph
    services/            # change_apply（唯一写入口）、export
    agents/              # 只读工具 + Harness（supervisor/workers/aggregator/responder）
    graph/               # Neo4j 客户端（可选）
  desktop_launcher.py    # 桌面入口
frontend/
  src/
    api/                 # axios 封装
    pages/               # 页面
    components/          # 组件
    stores/              # Zustand 状态
    types/               # TypeScript 类型
  dist/                  # 生产构建产物（由后端托管）
```

## 核心设计

- **Single-write-entry**：所有实体持久化变更必须通过 `app/services/change_apply.py`，执行 SQLite + 可选 Neo4j 的 Saga 双写。
- **只读 Worker**：Agent Worker 只能通过 `app/agents/tools/` 中的工具读取数据，禁止直接写库。
- **变更确认流**：AI 生成的修改先进入 `AssistantSession.staged_changes`，用户在前端确认后才真正落库。
- **工具调用协议**：Worker 使用自定义文本协议 `TOOL_CALL:{"name":"...","arguments":{...}}`，循环调用直至完成或达到递归上限。

## 已实现阶段

- **S0** 工程骨架（pywebview + FastAPI + SQLite + 现代前端壳）
- **S1** 项目模型与基础设置
- **S2** 长篇大纲（树 + 版本链）/ 角色 / 伏笔 / 世界观 / 剧情节点持久化 + repositories
- **S3** 新 Harness（Supervisor / Worker 只读工具 / 递归上限 / Saga 双写）
- **S4** 变更确认流（chat → diff → 确认 / 拒绝 → 应用）
- **S5** 知识图谱（Neo4j 可选 / SQLite 降级）+ 上下文感知续写
- **S6** 现代前端 + Markdown / JSON 导出

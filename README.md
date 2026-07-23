# Novel Studio — 长篇小说创作助手（重建版）

本地客户端形态的长篇小说创作工具：**不依赖任何现有 `novel_create` 代码**，Agent 读写严格隔离。

## 技术栈

- **桌面壳**：pywebview + 系统 WebView（Edge/CEF 兜底）
- **后端**：FastAPI + SQLAlchemy 2.0 (async) + SQLite（本地文件，`data/novel.db`）
- **前端**：React 19 + TypeScript + Vite + Tailwind
- **Agent**：自研 Harness（Supervisor → 只读工具 → Worker → ChangeRecord → 唯一写入口 Saga 双写）

## 关键设计

1. **Agent 强约束**：Worker **只能经只读工具读取真实 DB**，禁止直连 `AsyncSession`、禁止任何写操作；写意图只能表达为 `ChangeRecord`，经用户在前端确认后才由 `services/change_apply.py` 统一落库（唯一写入口）+ Neo4j id 镜像双写（可选，无则降级）。
2. **递归取数上限**：`user_settings.recursive_limit`，硬上限 30 + 超时保护。

## 运行

### 后端依赖
```bash
cd backend
pip install -r requirements.txt
```

### 前端依赖与构建
```bash
cd frontend
npm install
npm run build      # 产物输出到 frontend/dist，由后端静态托管
```

### 启动桌面客户端
```bash
cd backend
python desktop_launcher.py
```
启动后自动拉起 FastAPI（默认 `http://127.0.0.1:8765`）并打开本地窗口；访问 `/health` 应返回 200。

> 缺 Edge WebView 时自动回退 CEF 或系统浏览器。

### 仅开发前端
```bash
cd frontend && npm run dev   # 代理 /api -> 127.0.0.1:8765
```

## 目录结构
```
backend/app/
  main.py            FastAPI 入口（SPA 托管 + 路由 + 生命周期）
  config.py  database.py  models.py  core/  repositories/
  api/               projects / settings / long_* / assistant / export / graph / long_continue
  services/          change_apply（唯一写入口）/ export
  agents/            tools/（只读工具 + 写意图工具）/ harness/（state/supervisor/workers/aggregator/responder）
  graph/             Neo4j 客户端（可选镜像，id 主键）
frontend/src/
  api/ pages/ components/ stores/ types/
```

## 已实现 Sprint

- **S0** 工程骨架（pywebview + FastAPI + SQLite + 现代前端壳）
- **S1** 项目模型与基础设置
- **S2** 长篇大纲(树+版本链)/角色/伏笔/世界观/剧情节点 手动可改持久化 + repositories
- **S3** 新 Harness（Supervisor/Worker 只读工具/递归上限/Saga 双写 id 化）
- **S4** 变更确认流（chat → diff → 确认/拒绝 → 应用）
- **S5** 知识图谱（Neo4j 可选 / SQLite 降级）+ 上下文感知续写（SSE 流式）
- **S6** 现代前端 + Markdown/JSON 导出

> EPUB 导出为后置项，当前先提供 Markdown / JSON 导出。

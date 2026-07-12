# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Novel Studio is a local-only desktop novel-writing application. The repository is a monorepo with a Python FastAPI backend and a React/TypeScript frontend.

- **Backend**: FastAPI + SQLAlchemy 2.0 async + SQLite (`data/novel.db`). Serves the built frontend as an SPA and exposes `/api/*`.
- **Desktop shell**: `backend/desktop_launcher.py` starts uvicorn in a daemon thread, waits for `/health`, then opens a pywebview window using the system WebView (Edge on Windows, with CEF/browser fallback).
- **Frontend**: React 19 + TypeScript + Vite + Tailwind. Dev server proxies `/api` to `http://127.0.0.1:8765`. Production output is `frontend/dist`, which the backend serves.

Fiction projects are either **long** (`project.type='long'`) or **short** (`'short'`). They live in completely separate tables; `project_id` is the universal key.

## Common commands

All commands assume the repo root as the working directory. The backend expects Python >=3.11 and a virtual environment is recommended.

### Backend

```bash
# Install dependencies (run once)
cd backend
pip install -r requirements.txt

# Run the desktop client (intended end-user entry)
cd backend && python desktop_launcher.py

# Run backend dev server only
# Imports are absolute from the backend package root, so run with cwd=backend
cd backend && uvicorn app.main:app --reload --port 8765
# Or with PYTHONPATH:
PYTHONPATH=backend uvicorn app.main:app --reload --port 8765
```

### Frontend

```bash
# Install dependencies
cd frontend && npm install

# Dev server (http://127.0.0.1:5173, proxies /api -> 127.0.0.1:8765)
cd frontend && npm run dev

# Production build (output to frontend/dist, served by backend)
cd frontend && npm run build

# Type-check only
cd frontend && npx tsc -b
```

### Verification / smoke tests

There is no pytest or frontend test runner configured. Verify changes with:

```bash
# Backend syntax check
cd backend && python -m compileall app

# Backend smoke test (with server running)
curl http://127.0.0.1:8765/health

# Frontend typecheck
cd frontend && npx tsc -b
```

After frontend changes, rebuild `frontend/dist` with `npm run build` before testing via the desktop client or the backend-served SPA.

## Architecture

### Backend layering

- `app/main.py` defines `create_app()`. Routers are registered with prefixes `/api`, `/api/settings`, `/api/short`, `/api/long`, `/api/assistant`, `/api/export`, `/api/graph`. The `/health` endpoint is registered **before** the SPA catch-all in `/_mount_spa`; keep that order or `/health` will return HTML.
- `app/config.py` is a Pydantic `Settings` object reading `.env`. Defines `DATA_DIR=data/`, `db_path=data/novel.db`, `frontend_dist=frontend/dist`, LLM settings, and optional Neo4j settings.
- `app/database.py` holds the async engine, `AsyncSessionLocal`, and the `get_db` dependency.
- `app/models.py` holds all SQLAlchemy ORM models. Notable columns:
  - `LongOutline.version_chain` points to the previous version row (outline history).
  - `AssistantSession.staged_changes` buffers unconfirmed change drafts as JSON.
  - `LongChangeRecord` is the audit/confirmation log.
- `app/repositories/__init__.py` is the only module with real CRUD. It is used by API routes, services, and the **read-only** agent tools.

### Single-write-entry constraint

All persistent mutations of long-fiction entities must go through `app/services/change_apply.py` (`apply_change`, `confirm_session`, `reject_session`). This is the only DB write path and performs a Saga-style double write: SQLite (source of truth) plus an optional Neo4j mirror keyed by entity `id`. If you add a new mutable entity, register it in `_ENTITY_REPO` and route writes here. Failures are returned structurally as `{"ok": false, "errors": [...]}`; they are not silently rolled back.

### Agent harness

The assistant flow lives in `app/api/assistant.py` (`/chat`):

1. Gather full project context via repositories.
2. Supervisor (`app/agents/harness/nodes/supervisor.py`) classifies intent into an `ExecutionPlan`.
3. Workers (`app/agents/harness/workers/__init__.py`) run `WorkerBase._tool_loop`, a bounded tool-calling loop.
4. Aggregator (`app/agents/harness/nodes/aggregator.py`) converts worker results into `ChangeRecord[]`.
5. Staging: records are stored only in `assistant_sessions.staged_changes`.
6. Responder (`app/agents/harness/nodes/responder.py`) summarizes for the UI.

The user then calls `/confirm` (which delegates to `change_apply`) or `/reject`. Workers are read-only by design: they call only tools in `app/agents/tools/__init__.py`. Write intent is expressed through `app/agents/tools/propose.py` functions, which also do not write to the DB; they only append `ChangeRecord` drafts to the session.

### Tool-calling protocol

Workers use a custom text protocol, not native OpenAI function calling. The LLM emits `TOOL_CALL:{"name":"...","arguments":{...}}` inside its reply. `WorkerBase._tool_loop` parses the marker, invokes `call_tool(db, name, args)`, and feeds the result back as a `tool` message. The loop continues until no `TOOL_CALL` remains or `recursive_limit` is hit. `recursive_limit` comes from `user_settings` (default 8), clamped to a hard cap of 30, with a 60s timeout.

### LLM client

`app/core/llm_client.py` is OpenAI-compatible only (`/chat/completions`). It provides `chat` (non-stream), `chat_stream` (SSE `data:` chunks), and `parse_llm_json` (forces JSON output with fenced-code and substring fallbacks). Base URL, key, model, and temperature come from `Settings`.

### Key services

- `services/short_story.py`: the short-fiction six-step method (hook → plans → select → detail → chapters → write → integrate). Progress is resumed via `GET /api/short/{id}/progress`.
- `services/hotspot.py`: fetches trending topics from configured URL + adapter pairs in `user_settings.hotspot_sources`; no local crawler.
- `services/export.py`: renders Markdown/JSON for long and short projects. Dynamic export filenames must stay ASCII/Latin-1 safe.
- `services/change_apply.py`: the single write entry described above.
- `app/graph/client.py` + `api/graph.py`: knowledge graph. `get_graph()` returns `None` when Neo4j is disabled; the API falls back to SQLite relation queries.
- `api/long_continue.py`: `POST /api/long/{project_id}/continue` provides SSE streaming continuation by assembling outline/character/foreshadow/recent-chapter context and calling `chat_stream`.

### Error contract

`app/core/errors.py` defines `AppError(code, status_code)` subclasses (`NotFoundError`, `ValidationError`, `ConflictError`) and registers handlers returning `{"ok": false, "code": ..., "message": ...}`. Raise `AppError` subclasses from API/services so the client receives a consistent shape.

### Frontend

- `src/api/client.ts` is the axios base targeting `/api`; per-domain wrappers live in `src/api/*.ts`.
- State is managed with Zustand (`src/stores/`).
- Routes in `src/App.tsx`: `/` Home, `/settings`, `/project/long/:id` (LongWorkspace), `/project/short/:id` (ShortStudio).
- The visual style is Lovart black/white/gray, zero border radius, 1px borders, defined in `src/index.css`.
- `tsconfig.json` has `noImplicitAny: false`. Do not re-enable it without fixing all implicit-`any` sites.

## Environment

Copy `.env.example` to `.env` and adjust:

- `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_TEMPERATURE`
- `RECURSIVE_LIMIT_DEFAULT` (default 8), `RECURSIVE_LIMIT_HARD_CAP` (default 30)
- Optional Neo4j: `NEO4J_ENABLED`, `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`

# CODEBUDDY.md This file provides guidance to CodeBuddy when working with code in this repository.

## Commands

### Backend (Python ≥3.11, FastAPI + SQLAlchemy async)

- **Install deps**: `cd backend && pip install -r requirements.txt`
  Installs FastAPI, SQLAlchemy 2.0, aiosqlite, langgraph, neo4j, pywebview. Run once before first launch.

- **Run dev server**: `cd backend && uvicorn app.main:app --reload --port 8765`
  `main.py` exposes `app = create_app()` at module level. Run with cwd=`backend` (imports are `app.*`) or set `PYTHONPATH=backend`. Default port 8765.

- **Run desktop client**: `cd backend && python desktop_launcher.py`
  Launches uvicorn in a daemon thread, waits for `/health`, then opens a pywebview Edge window (falls back to CEF/browser). This is the intended end-user entry.

- **Syntax check**: `python -m py_compile $(git ls-files 'backend/app/*.py')` (or `python -m compileall backend/app`)
  No test framework is configured. Use this to catch parse errors before running.

- **Smoke test** (single endpoint): `curl http://127.0.0.1:8765/health`
  Returns `{"ok":true,...}`. Equivalent to a single unit check since the app has no pytest harness — verify new routes the same way.

### Frontend (React 19 + Vite + Tailwind)

- **Install**: `cd frontend && npm install`
- **Dev server**: `cd frontend && npm run dev`
  Vite on :5173 with proxy `/api` → `http://127.0.0.1:8765`. Use this for UI work while the backend runs separately.
- **Build**: `cd frontend && npm run build`
  Runs `tsc -b && vite build`; output to `frontend/dist`, which the backend serves as the SPA. Rebuild after frontend changes so the desktop client reflects them.
- **Typecheck**: `cd frontend && npx tsc -b`
  No ESLint is configured. `tsconfig.json` sets `noImplicitAny: false` — do not re-enable it without fixing all implicit-any sites.

## Architecture

### Shape
Monorepo: `backend/` (Python FastAPI service + desktop shell) and `frontend/` (React SPA). The product is a **local-only desktop novel-writing tool for long fiction** — no cloud. `backend/desktop_launcher.py` starts the FastAPI server and hosts the UI in a pywebview Edge window; when pywebview/Edge is missing it falls back to the system browser. The same FastAPI app also serves the built `frontend/dist` as an SPA, so a single process is the whole app.

### Backend layering (read this before editing)
The backend enforces a strict write-isolation model. Understanding it avoids corrupting the "change → confirm → apply" contract.

- **`app/main.py` — `create_app()`** registers `@app.get("/health")` *before* calling `_mount_spa()`. The SPA catch-all (`/{full_path:path}`) returns `index.html` for every non-`api` path and 404s `api/*`. **Preserve this ordering** — moving `/health` after the SPA mount makes it return HTML instead of JSON. Routers are included in `_register_routers` with prefixes `/api`, `/api/settings`, `/api/long`, `/api/assistant`, `/api/export`, `/api/graph`. `lifespan` runs `create_all()` to build SQLite tables.

- **`app/config.py`** Pydantic `Settings` reading `.env` (see `.env.example`). Sets `DATA_DIR=data/`, `db_path=data/novel.db`, `frontend_dist=frontend/dist`, LLM defaults, `recursive_limit` defaults, and optional Neo4j creds. `database_url` is `sqlite+aiosqlite:///...`.

- **`app/database.py`** async engine + `AsyncSessionLocal` + the `get_db` FastAPI dependency. **`app/models.py`** holds all ORM. `LongOutline` has a `version_chain` column pointing to its previous-version row (outline history). `LongChangeRecord` is the audit/confirmation log. `AssistantSession.staged_changes` (JSON) buffers unconfirmed change drafts.

- **`app/repositories/__init__.py`** is the **only** data-access module with real CRUD (`_list/_get/_create/_update/_delete` + per-entity wrappers). It is called by the API layer, by `change_apply`, and by the **read-only tools**. **Workers must never import `repositories` or `database` directly** — they read solely through the tool layer.

### The single-write-entry constraint (critical)
All persistent mutations of long-fiction entities go through **`app/services/change_apply.py`** (`apply_change` / `confirm_session` / `reject_session`). This is the **only** DB write path for entity changes and performs a Saga-style double write: SQLite (source of truth) **plus** an optional Neo4j mirror keyed by entity `id`. Failures are returned structurally (`{"ok":False,"errors":[...]}`) — never silently rolled back. If you add a new mutable entity, register it in `_ENTITY_REPO` and route writes here.

### Agent harness (`app/agents/`)
The AI assistant follows a fixed pipeline implemented in `app/api/assistant.py` (`/chat`):

1. **Gather context** via `repositories` (all outlines/characters/foreshadows/world/plot for the project).
2. **Supervisor** (`harness/nodes/supervisor.py`) — LLM classifies intent into an `ExecutionPlan` of worker tasks; falls back to a single `outline` task on parse failure.
3. **Dispatch Workers** (`harness/workers/__init__.py`: Character/World/Outline/Plot/Foreshadow). Each runs `WorkerBase._tool_loop` — a bounded tool-calling loop.
4. **Aggregator** (`harness/nodes/aggregator.py`) turns worker results into `ChangeRecord[]`.
5. **Stage, don't write**: records are stored only in `assistant_sessions.staged_changes`. Nothing touches real tables yet.
6. **Responder** (`harness/nodes/responder.py`) summarizes for the UI.

The user then calls `/confirm` (→ `change_apply`) or `/reject`. **Workers are read-only by construction**: they fetch data exclusively through `app/agents/tools/__init__.py`'s `TOOL_REGISTRY` (`read_outlines`, `read_characters`, `read_foreshadows`, `read_world`, `read_plot_nodes`, `read_chapters`, plus `read_outline`/`read_outline_prev_version`). Any *write intent* is expressed via `app/agents/tools/propose.py` functions (`propose_add_character`, etc.) — these also **don't write**; they only append a `ChangeRecord` draft to the session. The hard rule: **no `ChangeRecord` is ever applied outside `change_apply`.**

### Tool-calling protocol (non-standard)
Workers use a **custom text protocol**, not OpenAI native function calling. The LLM emits `TOOL_CALL:{"name":"...","arguments":{...}}` inside its reply; `WorkerBase._tool_loop` parses that marker, calls `call_tool(db, name, args)`, and feeds the result back as a `tool` message, looping until no `TOOL_CALL` remains or `recursive_limit` is hit. `recursive_limit` comes from `user_settings` (default 8), clamped to `recursive_limit_hard_cap` (30) with a 60s timeout. Keep this convention when adding tools.

### LLM client (`app/core/llm_client.py`)
OpenAI-compatible only (`/chat/completions`). `chat` (non-stream), `chat_stream` (SSE `data:` chunks), and `parse_llm_json` (forces `response_format=json_object`, with fenced-code and substring fallbacks). It reads base URL/key/model from `Settings`. No streaming except via `chat_stream`.

### Supporting services
- **`services/export.py`** — `render_markdown_long` and a JSON project dump; `api/export.py` serves `GET /{project_id}?fmt=markdown|json`. **Export filenames must stay ASCII** (Latin-1 safe) — keep any dynamic naming ASCII-only.
- **`app/graph/client.py` + `api/graph.py`** — knowledge graph. `get_graph()` returns `None` when Neo4j is disabled, and the API **degrades to SQLite relation queries** (character `relations` + foreshadow nodes). Always code for the `source: "sqlite"` path being the default.
- **`api/long_continue.py`** — `POST /continue/{project_id}/continue` SSE stream; assembles outline/character/foreshadow/recent-chapter context and streams continuation via `chat_stream`.

### Error contract
`app/core/errors.py` defines `AppError(code, status_code)` subclasses (`NotFoundError`, `ValidationError`, `ConflictError`) and registers handlers returning `{"ok":False,"code":...,"message":...}`. Raise `AppError` subclasses (not raw `Exception`) from API/services so the client gets a consistent shape.

### Frontend
React 19 + TypeScript + Vite + Tailwind. `src/api/client.ts` is the axios base (targets `/api`); per-domain wrappers live in `src/api/*.ts`. State is Zustand. Routes (`src/App.tsx`): `/` Home, `/settings`, `/project/long/:id` (LongWorkspace, multi-tab: outline/character/foreshadow/world/plot/chapter/graph/assistant + GraphPanel). The dev server proxies `/api`; the production build is served by the backend, so **always rebuild `frontend/dist` after frontend changes** for desktop runs.

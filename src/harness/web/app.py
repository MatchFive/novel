"""FastAPI 应用骨架：书架 / state / events 只读 API + 起 run / 命令 API（Task 5）。

约束：

- **禁 asyncio.run()**——与 harness 核心共存同进程；路由全同步（读盘即文件 IO，ms 级）。
  **例外**：Task 5 起 run / commands 路由为 ``async def``——``AsyncRunner.launch``
  要用 ``asyncio.create_task`` 把 run 绑到**本就在跑的事件循环**上（禁另起 loop）。
- 只绑 127.0.0.1、不鉴权（本机单用户，cli ``harness web`` 层负责）。
- ``poll_interval`` / ``heartbeat`` 挂到 ``app.state``，由 SSE 流路由消费（Task 9）。
- ``app.state.runner`` = :class:`AsyncRunner`（Task 5 落地）；state API 的 ``running``
  按 ``runner.running`` 探测。起 run / 命令路由经 ``app.state.sessions`` 缓存的
  :class:`RunSession` 驱动（现建现缓存，单进程内复用 components 与 events 句柄）。
- counts 一律走容错口径：单项目坏文件不能让 state API 500。
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

from harness.characters import carded_names, collect_candidates
from harness.config import Settings
from harness.memory.store import MemoryStore
from harness.projects import ProjectInfo, create_project, list_projects
from harness.run.events import EventLog
from harness.run.issues import Issue, IssuesStore
from harness.run.state import RunState
from harness.session import Components, RunBusyError, RunSession, abort_run, pause_run, resume_run, start_run
from harness.store.frontmatter import FileFormatError
from harness.store.project import ProjectStore
from harness.text import count_words
from harness.web import approvals, readers
from harness.web.runner import AsyncRunner
from harness.web.stream import event_stream

log = logging.getLogger("harness.web")


def _web_dist() -> Path | None:
    """前端构建产物目录（web/dist）；不存在 → None（开发模式不挂静态文件）。

    优先取包相对路径（仓库内 src/harness/web/app.py → 仓库根/web/dist）；
    安装部署场景回退到进程 cwd 下的 web/dist（``harness web`` 从仓库根启动）。
    """
    candidates = [
        Path(__file__).resolve().parents[3] / "web" / "dist",
        Path.cwd() / "web" / "dist",
    ]
    for cand in candidates:
        if cand.is_dir():
            return cand
    return None

#: ``EventLog.tail(n)`` 取末尾 n 条；取全量流给一个大数（n 超过总数时即全量，
#: 语义见 tail 实现），避免 touch EventLog 私有成员。
_TAIL_ALL = 10 ** 9


class CreateProjectBody(BaseModel):
    title: str

    @field_validator("title", mode="before")
    @classmethod
    def _title_not_blank(cls, value: object) -> object:
        # strip 后非空才放行：空串 / 纯空白会让 slugify 落出 ""→"-2" 这类畸形 slug。
        if isinstance(value, str) and not value.strip():
            raise ValueError("title 不能为空或纯空白")
        return value


class TaskBody(BaseModel):
    """POST /task 的 body：brief 原文 + 交付物（web 显式给出 → user_specified）。"""

    raw: str
    deliverables: list[str] = ["outline"]

    @field_validator("raw", mode="before")
    @classmethod
    def _raw_not_blank(cls, value: object) -> object:
        # 与 CreateProjectBody._title_not_blank 同口径：空需求不起 run。
        if isinstance(value, str) and not value.strip():
            raise ValueError("raw 不能为空或纯空白")
        return value


class ChapterSaveBody(BaseModel):
    """PUT /chapters/{seq} 的 body：只改正文段（front-matter 不碰，§4.8）。"""

    body: str


class ResolveBody(BaseModel):
    """POST issues/{id}/resolve 的 body：人工终裁状态（resolved / ignored 二选一）。"""

    status: Literal["resolved", "ignored"]


class CharacterDraftBody(BaseModel):
    """POST /characters/draft 的 body：起草一张角色卡草稿（§4.6.2，Task 17）。"""

    name: str
    role_type: Literal["protagonist", "major", "supporting", "daily"]
    requirement: str = ""

    @field_validator("name", mode="before")
    @classmethod
    def _name_not_blank(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            raise ValueError("name 不能为空或纯空白")
        return value


# ------------------------------------------------------------------ 依赖

def _get_settings(request: Request) -> Settings:
    return request.app.state.settings


#: 模块级 Depends 单例（B008：参数默认值里不允许函数调用）。
_SETTINGS_DEP = Depends(_get_settings)


def _project_info(project_id: str, settings: Settings) -> ProjectInfo:
    """按 list_projects 匹配文件夹名；找不到 → 404。"""
    for info in list_projects(settings.library_root):
        if info.path.name == project_id:
            return info
    raise HTTPException(status_code=404, detail=f"未知项目: {project_id}")


def _store(project_id: str, settings: Settings = _SETTINGS_DEP) -> ProjectStore:
    return ProjectStore(_project_info(project_id, settings).path)


_STORE_DEP = Depends(_store)


# ------------------------------------------------------------------ app

def create_app(settings: Settings, *, poll_interval: float = 0.5,
               heartbeat: float = 25.0) -> FastAPI:
    """建 FastAPI 应用；settings / runner / 会话缓存挂到 app.state。"""
    app = FastAPI(title="novel-harness", docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.poll_interval = poll_interval
    app.state.heartbeat = heartbeat
    app.state.runner = AsyncRunner()
    app.state.sessions = {}  # {project_id: RunSession}——现建现缓存，复用 events/state 句柄
    # 终审 Finding ②：fix 在飞的项目集合——fix_issue 与 run/二次 fix 的审批卡共用
    # comp.ui 的 pending.md 单槽，fix 在飞时必须把起 run / 续跑 / 二次 fix 一律挡成 409，
    # 否则两个 waiter 共享一份 pending.answer → 静默错答。finally 里丢弃。
    app.state.fix_projects: set[str] = set()

    def _session(project_id: str) -> RunSession:
        """项目 → RunSession（缓存）。项目存在性由路由的 store 依赖先行 404 保证。"""
        sess = app.state.sessions.get(project_id)
        if sess is None:
            info = _project_info(project_id, settings)
            # force_file_ui=True：web 会话的审批**强制文件协议**——服务端进程 stdin 是
            # TTY 时（Git Bash 终端直连 harness web）不走终端路径 EOF 秒取消，
            # pending.md 照常落地等 /approvals/answer（2026-11-18 验收 Critical ②）。
            sess = RunSession(info, ProjectStore(info.path), settings, force_file_ui=True)
            app.state.sessions[project_id] = sess
        return sess

    def _current_status(sess: RunSession) -> str | None:
        st = sess.load_state()
        return st.status if st is not None else None

    api = APIRouter(prefix="/api")

    # ------------------------------------------------------------ 书架

    @api.get("/projects")
    def get_projects(request: Request) -> dict:
        root = request.app.state.settings.library_root
        return {"projects": [{"id": p.path.name, "title": p.title}
                             for p in list_projects(root)]}

    @api.post("/projects")
    def post_projects(body: CreateProjectBody, request: Request) -> dict:
        root = request.app.state.settings.library_root
        info = create_project(Path(root), body.title.strip())
        return {"id": info.path.name, "title": info.title}

    # ------------------------------------------------------------ 项目 state

    @api.get("/project/{project_id}/state")
    def get_state(request: Request, store: ProjectStore = _STORE_DEP) -> dict:
        state = RunState.load(store.root)
        runner = getattr(request.app.state, "runner", None)
        return {
            "status": state.status if state else None,
            "brief": state.brief if state else None,
            "counts": _counts(store.root),
            "running": bool(runner is not None and getattr(runner, "running", False)),
        }

    # ------------------------------------------------------------ 事件流

    @api.get("/project/{project_id}/events")
    def get_events(store: ProjectStore = _STORE_DEP, after: int = 0) -> dict:
        events_log = EventLog(store.root)
        # tail(n) 内部已跳过坏行 / 非 dict 行；n 取全量 → events API 的完整流。
        all_events = events_log.tail(_TAIL_ALL)
        events = [e for e in all_events if _is_seq(e.get("seq")) and e["seq"] > after]
        return {"events": events, "last_seq": _last_seq(all_events)}

    # ------------------------------------------------------------ SSE 事件流（Task 9）

    @api.get("/project/{project_id}/stream")
    def get_stream(request: Request, store: ProjectStore = _STORE_DEP,
                   after: int = 0) -> StreamingResponse:
        """SSE：hello 首帧 + 增量事件 + 心跳；after 重连补发 seq>after 后进入稳态增量。"""
        return StreamingResponse(
            event_stream(store, after=after,
                         poll_interval=request.app.state.poll_interval,
                         heartbeat=request.app.state.heartbeat),
            media_type="text/event-stream",
        )

    # ------------------------------------------------------------ 七视图只读 API（Task 6）

    @api.get("/project/{project_id}/outline")
    def get_outline(store: ProjectStore = _STORE_DEP) -> dict:
        return readers.outline_view(store)

    @api.get("/project/{project_id}/chapters")
    def get_chapters(store: ProjectStore = _STORE_DEP) -> dict:
        return readers.chapters_view(store)

    @api.get("/project/{project_id}/chapters/{seq}")
    def get_chapter_detail(seq: int, store: ProjectStore = _STORE_DEP) -> dict:
        try:
            return readers.chapter_detail(store, seq)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"未知章节: {seq}") from None

    @api.put("/project/{project_id}/chapters/{seq}")
    def put_chapter(seq: int, body: ChapterSaveBody, store: ProjectStore = _STORE_DEP) -> dict:
        """正文编辑（Task 13）：只换正文段，写盘走 read 回来的 doc.path（§4.8 纪律）。"""
        try:
            doc = readers.save_chapter_body(store, seq, body.body)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"未知章节: {seq}") from None
        except FileFormatError as exc:
            raise HTTPException(status_code=409, detail=f"front-matter 损坏: {exc}") from exc
        return {"seq": doc.seq, "word_count": count_words(doc.body),
                "status": doc.meta.get("status")}

    @api.get("/project/{project_id}/characters")
    def get_characters(store: ProjectStore = _STORE_DEP) -> dict:
        return readers.characters_view(store)

    @api.post("/project/{project_id}/characters/draft")
    async def post_character_draft(body: CharacterDraftBody, request: Request,
                                   project_id: str, store: ProjectStore = _STORE_DEP):
        """角色卡起草（Task 17）：一次 LLM 调用——**直接 await** 返回 {"card": ...}。

        不起 asyncio task、不 202（前端确认卡拿 card 回填可改；落卡是 Task 18 的 PUT）。
        components 缺（未配置 provider）→ 409；LLM 侧失败 → 500 draft_failed（如实，不硬跑）。
        """
        from harness.characters import CharacterDraftError, draft_character_card
        from harness.prompts.loader import PromptLoader

        sess = _session(project_id)
        comp = sess.components()
        if comp is None:
            return JSONResponse(status_code=409, content={"error": "no_provider"})
        try:
            card = await draft_character_card(
                store=store, llm=comp.llm, prompts=PromptLoader(project_root=sess.store.root),
                settings=request.app.state.settings, name=body.name.strip(),
                role_type=body.role_type, requirement=body.requirement)
        except CharacterDraftError as exc:
            return JSONResponse(status_code=500,
                                content={"error": "draft_failed", "reason": exc.reason})
        return {"card": card}

    @api.put("/project/{project_id}/characters/{name}")
    async def put_character(name: str, body: dict, request: Request, project_id: str,
                            store: ProjectStore = _STORE_DEP):
        """落卡（Task 18）：body = 卡 dict（§4.6.2）→ save_character_card → 202。

        契约逐字：202 ``{"saved": true, "path": str, "reextract_started": [seq...]}``。
        卡一落名册即对已有章记忆逐章重提取（reextract_cast，幂等）——LLM 慢，
        ``asyncio.create_task`` 后台跑（同 loop，禁 asyncio.run），不阻塞应答；
        ``components()`` 缺（未配置 provider）→ 卡照落（落卡不依赖 LLM），
        ``reextract_started`` 如实回 []（绝不硬跑、不起幻影调用）。事件
        ``characters_built`` 由 save_character_card 内发出（§8.4：web 路由调此函数即自带事件）。
        """
        from harness.characters import save_character_card

        if not isinstance(body, dict):
            return JSONResponse(status_code=400, content={"error": "bad_card"})
        card_name = str(body.get("name") or "").strip()
        if not card_name:
            return JSONResponse(status_code=400, content={"error": "bad_card"})
        if card_name != name:
            # Task 21 follow-up ④：路径名与卡内 name 不一致此前静默落 body name——
            # 两个名字都写进错误里，一致才落。
            return JSONResponse(status_code=400, content={
                "error": f"name 不一致：路径名为 {name!r}，卡内 name 为 {card_name!r}——请对齐后再落卡"})
        sess = _session(project_id)
        path = save_character_card(store, sess.events, body)
        started: list[int] = []
        comp = sess.components()
        if comp is not None:
            # 容错口径：单个坏章不拖垮落卡应答（它仍可重提取——reextract_cast 逐章同口径跳过）
            started = [doc.seq for doc in store.list_chapters(on_error=_warn_bad_chapter)]
            asyncio.create_task(_reextract_cast_task(sess, comp))
        return JSONResponse(status_code=202, content={"saved": True, "path": str(path),
                                                      "reextract_started": started})

    @api.get("/project/{project_id}/memory")
    def get_memory(store: ProjectStore = _STORE_DEP) -> dict:
        return readers.memory_view(store)

    @api.get("/project/{project_id}/memory/{seq}")
    def get_chapter_memory(seq: int, store: ProjectStore = _STORE_DEP) -> dict:
        try:
            return readers.chapter_memory_view(store, seq)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"未知章记忆: {seq}") from None

    @api.get("/project/{project_id}/issues")
    def get_issues(source: str | None = None, status: str | None = None,
                   store: ProjectStore = _STORE_DEP) -> list[dict]:
        return readers.issues_view(store, source=source, status=status)

    # ------------------------------------------------------------ 问题台账处置（Task 14）

    @api.post("/project/{project_id}/issues/{issue_id}/resolve")
    def post_issue_resolve(issue_id: str, body: ResolveBody,
                           store: ProjectStore = _STORE_DEP) -> dict:
        """人工终裁（已解决 / 忽略）：只翻台账状态，无需 provider；未知 id → 404。"""
        try:
            issue = IssuesStore(store.root).set_status(issue_id, body.status)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"未知 issue: {issue_id}") from None
        return {"id": issue.id, "status": issue.status}

    @api.post("/project/{project_id}/issues/{issue_id}/fix")
    async def post_issue_fix(request: Request, project_id: str, issue_id: str,
                             store: ProjectStore = _STORE_DEP):
        """「交给 AI 修」：components() 缺（未配置 provider）→ 409（不硬跑，保持 pending）；
        有 run 在跑（runner.alive()）→ 409 busy（不硬跑——fix_issue 与 run 的审批卡
        共用 comp.ui 的 pending.md 协议，并发会互相覆写 pending.answer → 静默错答）；
        本项目的 fix 已在飞（app.state.fix_projects，终审 Finding ②）→ 409 busy——
        pending.md 是单槽，fix 在飞时再起 run/二次 fix 会让两个 waiter 共享一份
        pending.answer：第二个 ask 覆写问题、用户答自己看到的、**两个** waiter 消费同一
        答案（fix 可能在用户从未见过确认卡的情况下应用 diff）；
        否则把项目加进 fix_projects 并 ``asyncio.create_task`` 后台跑 fix_issue → 202
        （进度经事件流可见，fix_issue 内部的 diff 确认卡由审批弹窗应答——同进程
        pending.md 文件协议；槽位在 ``_fix_issue_task`` 的 finally 里丢弃）。
        """
        sess = _session(project_id)
        if request.app.state.runner.alive():
            return JSONResponse(status_code=409, content={"error": "busy"})
        if project_id in request.app.state.fix_projects:
            return JSONResponse(status_code=409, content={"error": "busy"})
        comp = sess.components()
        if comp is None:
            return JSONResponse(status_code=409, content={"error": "no_provider"})
        issue = IssuesStore(store.root).one(issue_id)
        if issue is None:
            raise HTTPException(status_code=404, detail=f"未知 issue: {issue_id}")
        request.app.state.fix_projects.add(project_id)
        asyncio.create_task(_fix_issue_task(sess, comp, issue,
                                            fix_projects=request.app.state.fix_projects,
                                            project_id=project_id))
        return JSONResponse(status_code=202, content={"accepted": True})

    # ------------------------------------------------------------ 起 run / 命令

    @api.post("/project/{project_id}/task")
    async def post_task(body: TaskBody, request: Request, project_id: str,
                        store: ProjectStore = _STORE_DEP):
        """起一个新 run：落盘 state.json + run_started 事件，AsyncRunner 同 loop 驱动。

        async def：launch 的 ``asyncio.create_task`` 需要运行中的事件循环。
        契约：200 {"status": "planning"}；RunBusyError → 409 {"error": "busy"}；
        无 provider → 409（绝不先落幻影 run——守卫/检查都在 start_run 内）。
        """
        sess = _session(project_id)
        # 终审 Finding ②：本项目的 fix 在飞（pending.md 单槽被占）→ 409 busy，
        # 不起第二个 run 去抢槽（两个 waiter 共享一份 pending.answer → 静默错答）。
        if project_id in request.app.state.fix_projects:
            return JSONResponse(status_code=409, content={"error": "busy"})
        try:
            state = start_run(sess, request.app.state.runner, body.raw,
                              list(body.deliverables), user_specified=True)
        except RunBusyError:
            return JSONResponse(status_code=409, content={"error": "busy"})
        if state is None:
            return JSONResponse(status_code=409, content={"error": "no_provider"})
        return {"status": "planning"}

    @api.post("/project/{project_id}/commands/{name}")
    async def post_command(request: Request, project_id: str, name: str,
                           store: ProjectStore = _STORE_DEP):
        """pause / resume / abort：语义全在 session 层（pause_run/resume_run/abort_run）。

        无可作用的 run 时不报错——如实回当前状态（200）；未知命令名 → 404。
        """
        if name not in ("pause", "resume", "abort"):
            raise HTTPException(status_code=404, detail=f"未知命令: {name}")
        sess = _session(project_id)
        runner = request.app.state.runner
        if name == "pause":
            try:
                return {"status": pause_run(sess, "web pause").status}
            except ValueError:
                return {"status": _current_status(sess)}
        if name == "abort":
            try:
                return {"status": abort_run(sess).status}
            except ValueError:
                return {"status": _current_status(sess)}
        # 终审 Finding ②：fix 在飞占住 pending.md 单槽 → 续跑同样 409 busy
        # （resume 起来的 run 与 fix 的确认卡会共用一份 pending.answer）。
        if project_id in request.app.state.fix_projects:
            return JSONResponse(status_code=409, content={"error": "busy"})
        try:
            state = resume_run(sess, runner)
        except RunBusyError:
            return JSONResponse(status_code=409, content={"error": "busy"})
        return {"status": state.status if state is not None else _current_status(sess)}

    # ------------------------------------------------------------ 审批（Task 11）

    approvals.register(api, _STORE_DEP)

    app.include_router(api)

    # 前端托管（G5）：web/dist 存在才挂 StaticFiles（html=True → GET / 直接出单页应用）；
    # dist 不存在（开发模式 / 未构建）不得挂——避免把 404 语义变成挂载点的 500。
    # 挂在 include_router 之后：/api/* 路由优先匹配，静态文件只兜其余路径。
    dist = _web_dist()
    if dist is not None:
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
    return app


# ------------------------------------------------------------------ 落卡重提取 / 台账修复后台任务

def _warn_bad_chapter(path: Path, exc: FileFormatError) -> None:
    """落卡路由列章的容错回调：坏章记日志跳过（同 _counts 的 _warn 口径）。"""
    log.warning("落卡 reextract_started 跳过坏章: %s（%s）", path, exc)


async def _reextract_cast_task(sess: RunSession, comp: Components) -> None:
    """落卡后的记忆重提取协程体（被 ``asyncio.create_task`` 调起，同 loop，禁 asyncio.run）。

    名册由 ``reextract_cast`` 内部 ``cast_roster(store)`` 现取——刚落的卡即刻进
    各章 ``cast_cards`` 渲染（§8.4：卡一落名册即生效）；逐章幂等，失败的章跳过
    不阻断（P4 铁律）。结果记日志（进度经事件流可见；reextract_chapter 本轮不发事件）。
    """
    from harness.characters import reextract_cast
    from harness.prompts.loader import PromptLoader

    mem = MemoryStore(sess.store.root)
    prompts = PromptLoader(project_root=sess.store.root)
    try:
        seqs = await reextract_cast(store=sess.store, mem=mem, llm=comp.llm,
                                    prompts=prompts, settings=sess.settings)
    except Exception:
        # 对照 _fix_issue_task：一层兜底防「Task exception was never retrieved」
        log.exception("reextract_cast 后台任务失败（项目 %s）", sess.store.root.name)
        return
    log.info("reextract_cast 完成（项目 %s）：重提 %d 章 %s",
             sess.store.root.name, len(seqs), seqs)


async def _fix_issue_task(sess: RunSession, comp: Components, issue: Issue, *,
                          fix_projects: set[str], project_id: str) -> None:
    """「交给 AI 修」的协程体（被 ``asyncio.create_task`` 调起）：repl ``_fix_issue_by_ai``
    的 async 版——同一份 ``harness.audit.fix.fix_issue``，只是驱动方式从
    ``asyncio.run``（repl 的同步世界）换成同 loop 的 create_task（禁另起 loop）。

    确认卡零额外接线：``comp.ui`` 是 ``ApprovalUI(file_base=项目根/.harness)`` 的文件模式，
    fix_issue 的 ``ui.ask`` 写 ``pending.md`` 并轮询 ``pending.answer``——与审批 API
    （Task 11）和 SSE 的 approval_pending 播报（Task 12）读写的是**同一个目录**，
    前端审批弹窗天然能答。观测结果记日志（``emit`` 是 fix_issue 的预留口，本轮不消费）。

    终审 Finding ②：finally 里从 ``fix_projects`` 丢弃本项目槽位——fix 结束（含失败）
    后须立即放行起 run / 续跑 / 二次 fix，否则守卫把项目永久锁成 409 busy。
    """
    from harness.audit.fix import fix_issue
    from harness.prompts.loader import PromptLoader

    mem = MemoryStore(sess.store.root)
    prompts = PromptLoader(project_root=sess.store.root)
    try:
        try:
            text = await fix_issue(store=sess.store, mem=mem, llm=comp.llm, prompts=prompts,
                                   settings=sess.settings, issue=issue,
                                   emit=sess.events.append, ui=comp.ui)
        except Exception:
            # 对照 runner.py 的 _drive：一层兜底防「Task exception was never retrieved」。
            log.exception("fix_issue(%s) 后台任务失败", issue.id)
            return
        log.info("fix_issue(%s) 完成：%s", issue.id, text)
    finally:
        fix_projects.discard(project_id)


# ------------------------------------------------------------------ counts

def _is_seq(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _last_seq(events: list[dict]) -> int:
    """已解析事件流的最大 seq；空流 / 无合法 seq → 0（seq 从 1 起）。"""
    seqs = [e["seq"] for e in events if _is_seq(e.get("seq"))]
    return max(seqs) if seqs else 0


def _counts(root: Path) -> dict:
    """sidebar 计数。读盘一律容错口径：单项目坏文件按 0/跳过计，不让 state API 500。"""
    store = ProjectStore(root)

    def _warn(path: Path, exc: FileFormatError) -> None:
        log.warning("state counts 跳过坏文件: %s（%s）", path, exc)

    chapters = store.list_chapters(on_error=_warn)
    try:
        analysis = collect_candidates(store)
        characters_pending = len(analysis.uncarded_major(carded_names(store)))
    except FileFormatError as exc:
        # collect_candidates 内部 list_chapters 不容错：坏章降级为 0，不拖垮 state API
        log.warning("state counts 角色候选聚合失败（按 0 计）: %s", exc)
        characters_pending = 0
    return {
        "volumes": len(store.read_arcs(on_error=_warn)),
        "plots": len(store.read_plots()),
        "chapters": len(chapters),
        "prose": len([d for d in chapters if d.meta.get("status") == "done"]),
        "characters_pending": characters_pending,
        "memory": len(MemoryStore(root).list_chapter_memories()),
        "issues_pending": len(IssuesStore(root).list("pending")),
        "events_last_seq": _last_seq(EventLog(root).tail(_TAIL_ALL)),
    }

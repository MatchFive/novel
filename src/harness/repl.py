"""dsh 式项目选择器 + 员工 ReAct REPL（Task 12）。

无前缀自然语言 → 构建 brief（raw=输入，deliverables 默认 ["outline"]；/task 可显式
带 deliverables）→ 新 run（run_started 事件）→ AgentLoop.run()。新斜杠命令：
/pause（落盘 paused）/resume（WorkMemory.rebuild 回放续跑）/abort /issues（逐条
已解决/忽略/交给 AI 修——后者 P1 仅 stub 说明）/answer（非 TTY 审批：写
pending.answer，协议 v2 文件审批：答案首行带本轮轮次）。Ctrl+C = /pause 语义（run 进行中）。
"""
from __future__ import annotations

import asyncio
import logging
import os
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger("repl")
from harness.config import Settings, load_settings
from harness.projects import ProjectInfo, create_project, list_projects, open_project
from harness.run.approval import InvalidAnswerError, PendingMissingError, cancel_answer, write_answer
from harness.run.constants import WORK_MEMORY_REPLAY
from harness.run.issues import Issue, IssuesStore
from harness.run.state import TERMINAL_STATUSES, RunState
from harness.run.whitelist import DELIVERABLE_TOOLS
from harness.session import (
    Components,
    RunBusyError,
    RunSession,
    abort_run,
    drive_run,
    pause_run,
    resume_run,
    start_run,
)
from harness.store.frontmatter import FileFormatError
from harness.store.project import ProjectStore

_STUB_NOTICE = ("员工主循环（P1）未启动：需要已配置的 default_provider"
                "（~/.harness/config.toml 或项目 .harness/config.toml）。")

#: issue 来源 → 分组小标题（audit 在前、check 在后；其余来源按来源名排其后）
#: /answer 的取消标记（与 approval 模块的取消词表同款）
_ANSWER_CANCEL_MARKERS = {"c", "cancel", "取消"}

_SOURCE_GROUP_TITLES = {"audit": "【一致性审计】", "check": "【验收】", "checklist": "【验收清单】"}

#: 全局类意见（``quote`` 为空）的处置说明：没有可定位的原文片段 → 改写无从下手。
#: **不承诺台账里不存在的功能**（「给已有意见补 quote」的入口没有——M-7）。
_GLOBAL_ISSUE_NOTE = ("全局类意见（无原文片段）——只能标记已解决或忽略；"
                      "改写需在正文里自行定位后手工修改")


def _warn_broken_chapter(path: Path, exc: FileFormatError) -> None:
    """坏章只降级当前列表项：点名文件 + 原因，REPL 会话不受影响。"""
    print(f"警告：章节文件损坏已跳过 {path.name}（{exc.reason}）")


_HELP = """可用命令：
  /task [deliverables] <需求>   发起新 run（deliverables 如 outline chapters，默认 outline）
  /state       项目卡 + 卷/情节/章节计数与标题树
  /world       世界观条目速览
  /characters  角色卡速览
  /outline     卷骨架与情节速览
  /chapters    章节速览
  /memory      现状快照速览
  /pause       暂停进行中的 run（落盘 paused，可 /resume 续跑）
  /resume      续跑未完的 run（回放事件重建工作记忆）
  /abort       终止未完的 run（aborted）
  /issues      待处理 issue 按来源分组逐条：已解决 / 忽略 / 交给 AI 修
               （全局类意见无原文片段，只能已解决或忽略）
  /fix         按章聚合修订：同一章的多条意见一章一张确认卡 + 一次改写
               （只处理带原文片段的章级意见；改完该章的意见置 resolved）
  /audit [all|<seq>|<from>-<to>]  一致性审计（默认确定性全量 + LLM 复核最近 12 章）
  /check [plot|global] [editor]  验收（默认全书级；plot=情节级；editor=签约责编视角；只报不改）
  /skills      两级 skill 清单（项目级 + 用户级，项目级覆盖同名）
  /checklist [edit|add|delete <stage> <标题>]  验收清单：列出（带来源标注）/
               编辑器改条目（edit/add 保全原内容建项目级覆盖；delete 只删项目级）
  /skill import <路径> [--project]  导入外部 skill（单文件或整目录；默认用户级）
  /answer      非 TTY 审批应答：/answer 1 2 备注:...（写 .harness/pending.answer，
               首行自动带本轮轮次——迟到的回答不进下一轮）
  /help        显示本帮助
  /quit        退出
其他无前缀输入视为自然语言 brief，直接进入员工主循环。"""


# ---------------------------------------------------------------- 项目选择器

def select_project(settings: Settings) -> ProjectInfo:
    """编号列出库内项目 + ``n. 新建项目`` + ``o. 打开其他路径``，返回选中项。"""
    while True:
        projects = list_projects(settings.library_root)
        print(f"项目库：{settings.library_root}")
        for i, p in enumerate(projects, 1):
            print(f"  {i}. {p.title}（{p.path}）")
        print("  n. 新建项目")
        print("  o. 打开其他路径")
        choice = input("请选择：").strip()
        if choice == "n":
            title = input("书名：").strip()
            if title:
                return create_project(settings.library_root, title)
            print("书名不能为空。")
        elif choice == "o":
            raw = input("项目路径：").strip()
            try:
                return open_project(Path(raw))
            except FileNotFoundError as exc:
                print(exc)
        elif choice.isdigit() and 1 <= int(choice) <= len(projects):
            return projects[int(choice) - 1]
        else:
            print("无效选择：请输入编号、n 或 o。")


# ------------------------------------------------------------------ 只读速览

def _cmd_state(project: ProjectInfo, store: ProjectStore) -> None:
    world = store.read_world()
    arcs = store.read_arcs()
    plots = store.read_plots()
    chapters = store.list_chapters(on_error=_warn_broken_chapter)
    print(f"《{project.title}》  {project.path}")
    print(f"世界观条目 {len(world)} ｜ 卷 {len(arcs)} ｜ 情节 {len(plots)} ｜ 章节 {len(chapters)}")
    if world:
        print("世界观：" + "、".join(str(e.get("title") or "?") for e in world))
    if arcs:
        print("卷骨架：")
        for arc in arcs:
            vol = arc.get("vol", "?")
            print(f"  第{vol}卷 {arc.get('title', '')}".rstrip())
    if plots:
        print("情节：")
        for p in plots:
            print(f"  {p.get('seq', '?')}. {p.get('title', '')}（{p.get('status', 'planned')}）")
    if chapters:
        print("章节：")
        for c in chapters:
            title = c.meta.get("title") or c.seq
            print(f"  第{c.seq:04d}章 {title}（{c.meta.get('status', '')}）".rstrip())


def _cmd_world(store: ProjectStore) -> None:
    entries = store.read_world()
    if not entries:
        print("（世界观暂无条目）")
        return
    for e in entries:
        line = f"- [{e.get('category', '未分类')}] {e.get('title', '?')}（重要度 {e.get('importance', '中')}）"
        summary = str(e.get("summary") or "").strip()
        if summary:
            line += f"：{summary}"
        print(line)


def _cmd_characters(store: ProjectStore) -> None:
    chars = sorted((store.root / "characters").glob("*.md"))
    if not chars:
        print("（暂无角色）")
        return
    for p in chars:
        print(f"- {p.stem}")


def _cmd_outline(store: ProjectStore) -> None:
    arcs = store.read_arcs()
    plots = store.read_plots()
    if not arcs and not plots:
        print("（卷骨架与情节均为空）")
        return
    if arcs:
        print("卷骨架：")
        for arc in arcs:
            print(f"  第{arc.get('vol', '?')}卷 {arc.get('title', '')}".rstrip())
    if plots:
        print("情节：")
        for p in plots:
            print(f"  {p.get('seq', '?')}. {p.get('title', '')}（{p.get('status', 'planned')}）")


def _cmd_chapters(store: ProjectStore) -> None:
    chapters = store.list_chapters(on_error=_warn_broken_chapter)
    if not chapters:
        print("（暂无章节）")
        return
    for c in chapters:
        title = c.meta.get("title") or c.seq
        print(f"第{c.seq:04d}章 {title}（{c.meta.get('status', '')}）".rstrip())


def _cmd_memory(store: ProjectStore) -> None:
    """现状快照速览：state.md 两节行数 + 卷级 digest 列表 + 章记忆章号统计（只读）。"""
    state = store.root / "memory" / "state.md"
    text: str | None = None
    unreadable = False
    if state.exists():
        try:
            text = state.read_text(encoding="utf-8")
        except UnicodeDecodeError:      # M-d：守卫同 MemoryStore.read_state 口径，不炸
            unreadable = True
    if text is not None:
        counts: dict[str, int] = {}
        cur: str | None = None
        for ln in text.splitlines():
            s = ln.strip()
            if s.startswith("## "):
                cur = s[3:].strip()
                counts[cur] = 0
            elif cur is not None and s:
                counts[cur] += 1
        sect = " ｜ ".join(f"{name} {n} 行" for name, n in counts.items()) or "（无小节）"
        print(f"【现状快照】{sect}"
              f"（共 {len(text.splitlines())} 行，全文见 memory/state.md）")
    elif unreadable:
        print("（现状快照不可读：非 UTF-8，详见 memory/state.md）")
    else:
        print("（暂无现状快照）")
    digests = sorted((store.root / "memory" / "digests").glob("*.md")) \
        if (store.root / "memory" / "digests").is_dir() else []
    if digests:
        names = "、".join(p.name for p in digests)
        print(f"【卷级前情提要】{len(digests)} 份：{names}")
    else:
        print("【卷级前情提要】（无）")
    from harness.memory.store import MemoryStore
    seqs = MemoryStore(store.root).list_chapter_memories()
    if seqs:
        rng = f"第{seqs[0]}~{seqs[-1]}章" if seqs == list(range(seqs[0], seqs[-1] + 1)) \
            else "、".join(f"第{s}章" for s in seqs)
        print(f"【章记忆】共 {len(seqs)} 章：{rng}")
    else:
        print("【章记忆】（无）")


def _open_in_editor(path: Path) -> None:
    """用 ``$EDITOR`` 打开清单文件；未设 → 打印路径让用户手动开（不炸）。"""
    editor = os.environ.get("EDITOR", "").strip()
    if editor:
        subprocess.call([*shlex.split(editor), str(path)])
    else:
        print(f"EDITOR 未设置，请手动编辑：{path}")


def _cmd_checklist(store: ProjectStore, arg: str) -> None:
    """验收清单的查看/编辑入口（P34，C1）：清单是文件（文件名=标题，正文=提示），
    门槛在「得知道文件在哪、命名规则」——本命令替用户解析生效文件并直接调起编辑器。

    - 无参：按 stage 列出生效条目（标注 生效中/未接入 与来源 内置/用户/项目）。
    - ``edit|add <stage> <标题>``：ensure 项目级文件（override 保全内容）→ 开编辑器。
    - ``delete <stage> <标题>``：只删项目级条目；内置/用户级如实说明不删。
    """
    from harness.checklists import (
        ACTIVE_STAGES,
        checklist_overview,
        delete_checklist_item,
        ensure_editable_item,
    )

    arg = (arg or "").strip()
    if not arg:
        overview = checklist_overview(store.root)
        if not overview:
            print("（无任何清单条目）")
            return
        badges = {"builtin": "［内置］", "user": "［用户］", "project": "［项目］"}
        for stage in sorted(overview):
            label = "生效中" if stage in ACTIVE_STAGES else "未接入"
            print(f"[{stage}]（{label}）")
            for i, item in enumerate(overview[stage], 1):
                hint = " ".join(str(item["hint"] or "").split())[:100]
                line = f"  {i}. {item['title']} {badges[item['source']]}"
                if hint:
                    line += f"\n     {hint}"
                print(line)
        return

    sub, _, rest = arg.partition(" ")
    stage, _, title = rest.strip().partition(" ")
    title = title.strip()
    if sub not in ("edit", "add", "delete") or not stage or not title:
        print("用法：/checklist ｜ /checklist edit|add <stage> <标题> ｜ "
              "/checklist delete <stage> <标题>")
        return
    if sub == "delete":
        if delete_checklist_item(store.root, stage, title):
            print(f"已删除项目级清单条目「{title}」（stage={stage}）")
        else:
            print(f"「{title}」非项目级条目（内置或用户级）：请改对应层"
                  f"（用户级在 ~/.harness/checklists/{stage}/），"
                  "或在项目级建同名覆盖")
        return
    try:
        path = ensure_editable_item(store.root, stage, title)
    except ValueError as exc:
        print(f"清单标题非法：{exc}")
        return
    print(f"编辑：{path}")
    _open_in_editor(path)


# ------------------------------------------------------------------ run 会话（委托 harness.session）

def _drive_blocking(session: RunSession, state: RunState, comp: Components, *,
                    intake: tuple[str, list[str], bool] | None = None) -> None:
    """同步驱动一个 run 到结束（REPL 是同步世界；loop 的 async 边界在此）。

    观测/note 走 ``print``、收尾提示逐字与搬移前一致；循环本体在
    :func:`harness.session.drive_run`。
    """
    try:
        asyncio.run(drive_run(session, state, comp, intake=intake, on_note=print))
    except KeyboardInterrupt:
        # loop 已落 paused；asyncio.run 会把 Ctrl+C 再抛一次（见 loop.run 契约）
        print("已暂停（Ctrl+C）。可 /resume 续跑。")
    finally:
        session.state = state
    print(f"run 结束：status={state.status}")


def _submit_brief(sess: RunSession, raw: str, deliverables: list[str], *,
                  user_specified: bool = False) -> None:
    """REPL 的「起新 run」入口（/task 与自然语言输入共用）——委托 :func:`start_run`。

    单 run 守卫（P16-1）与 provider 检查都在 session 层；这里的 print 是 REPL 侧
    的**唯一**提示输出（文案逐字不变）。
    """
    try:
        state = start_run(sess, _RUNNER, raw, deliverables, user_specified=user_specified)
    except RunBusyError:
        print("已有后台任务在跑（本地同时只跑一个员工任务）——"
              "等它结束，或用 /abort 终止后再发新任务。")
        return
    if state is None:
        # 未配置 provider 时直接打 P1 提示返回——绝不先落 state.json / run_started
        # （否则留下永远没跑过、却可被 /resume 的幻影 run）。
        print(_STUB_NOTICE)


# ------------------------------------------------------------------ 后台 run（P16）

class _ThreadRunner:
    """REPL 的 :class:`harness.session.Runner`：TTY **阻塞**；非 TTY **后台** daemon 线程。

    **为什么分两种**（P16，合并复审发现的「同进程 /answer 不可达」）：

    - **TTY**：审批走**终端选项卡**，由 run 自己 `input()` 读 stdin → REPL 与 run
      **共享同一个 stdin** → 两个读者会互相抢输入 → 必须**阻塞**（run 期间 REPL 不读）。
    - **非 TTY**：审批走**文件协议**（pending.md / pending.answer），run **完全不碰 stdin**
      → REPL 可以**继续读命令** → 同一会话里的 ``/answer 1`` 就能应答 run 提出的审批 →
      `printf '/task …\\n/answer 1\\n' | harness <项目>` 这种脚本用法才成立。

    后台 run 用 **daemon 线程**：主线程 join 时若 run 卡在等审批，Ctrl+C 能中断主线程、
    进程随之退出（不会因为等一个没人答的审批而永远挂着）。
    """

    def __init__(self) -> None:
        #: 当前后台 run 的线程（run_repl 退出时 join 它们）——即原 ``_BACKGROUND["threads"]``。
        self.threads: list[threading.Thread] = []

    def alive(self) -> bool:
        """当前是否有**活着**的后台 run 线程。"""
        self._prune()
        return any(t.is_alive() for t in self.threads)

    def _prune(self) -> None:
        """清掉**已结束**的线程对象（否则每次 run 留一个死线程，会话越长越脏）。"""
        self.threads = [t for t in self.threads if t.is_alive()]

    def launch(self, session: RunSession, state: RunState, comp: Components, *,
               intake: tuple[str, list[str], bool] | None = None) -> None:
        if sys.stdin.isatty():
            _drive_blocking(session, state, comp, intake=intake)
            return

        def _worker() -> None:
            try:
                _drive_blocking(session, state, comp, intake=intake)
            except KeyboardInterrupt:            # pragma: no cover - 后台线程收到 Ctrl+C
                pass
            except Exception as exc:            # 后台线程兜底
                log.exception("后台 run 失败")
                print(f"后台 run 失败：{type(exc).__name__}——详情见日志")

        thread = threading.Thread(target=_worker, name="harness-run", daemon=True)
        thread.start()
        self.threads.append(thread)
        print("run 已在后台启动——可继续用 /answer 应答审批；"
              "输入结束（EOF）时会等待 run 收尾。")


#: REPL 进程内的单 run 驱动器（/task、自然语言、/resume 共用；守卫判据 = 线程表）。
_RUNNER = _ThreadRunner()

#: EOF / /quit 时等后台 run 收尾的**上限**（秒）。
#: **不能无限等**（2026-11-17 独立复审 P16-3）：run 卡在等一个**没人答**的审批时，
#: 无限等 = 进程**永不退出**（daemon 线程救不了**主线程的 join**）。
_JOIN_TIMEOUT = 30.0


def _join_background(timeout: float | None = None,
                     pending_dir: Path | None = None) -> None:
    """等**所有**后台 run 收尾（EOF / /quit 时调用）。Ctrl+C 可中断。

    **有上限**（P21）：默认 ``_JOIN_TIMEOUT`` 秒。超时 → **如实报**「未收尾即退出」
    并告诉用户去哪儿答（``pending.answer``），而不是无声挂死。
    """
    threads = [t for t in _RUNNER.threads if t.is_alive()]
    if not threads:
        _RUNNER._prune()
        return
    limit = _JOIN_TIMEOUT if timeout is None else timeout
    print("等待后台 run 收尾…（Ctrl+C 可中断）")
    deadline = time.monotonic() + limit
    try:
        for thread in threads:
            remaining = max(0.0, deadline - time.monotonic())
            thread.join(remaining)
    except KeyboardInterrupt:
        print("已中断等待（后台 run 随进程退出）。")
        raise
    still = [t for t in threads if t.is_alive()]
    if still:
        hint = ""
        if pending_dir is not None:
            hint = (f"答案写进 {pending_dir / 'pending.answer'} 后可用 /resume 续跑")
        print(f"后台任务 {limit:.0f} 秒内未收尾（可能正在等审批）→ 进程退出。{hint}")
    _RUNNER._prune()



def _parse_task_arg(rest: str) -> tuple[str, list[str], bool]:
    """``/task [deliverables...] <需求>``：前导已知 deliverable 名被消费，其余为 brief 原文。

    第三个返回值 = **用户是否显式列了交付物**（P10 裁定 6）：显式列出的**赢过**需求解析
    的模型推断；没列（走默认 "outline"）时模型可以参与。
    """
    tokens = rest.split()
    deliverables: list[str] = []
    while tokens and tokens[0] in DELIVERABLE_TOOLS:
        deliverables.append(tokens.pop(0))
    return " ".join(tokens), (deliverables or ["outline"]), bool(deliverables)


# ------------------------------------------------------------------ run 命令
# 状态机语义（status+save+事件+memory 清空、续跑 replay/intake 补跑）全在
# harness.session（pause_run/abort_run/resume_run）——这里只委托 + 保留提示文案。

def _cmd_pause(sess: RunSession) -> None:
    try:
        pause_run(sess, "user /pause")
    except ValueError:
        print("没有进行中的 run（无需暂停）。")
        return
    print("已暂停（/resume 续跑，/abort 终止）。")


def _cmd_resume(sess: RunSession) -> None:
    # 「没有可续跑的 run」的提示要分于「未配置 provider」——先自行 load_state 分辨
    # （resume_run 的 None 对两种原因不做区分；load_state 有缓存，成本可忽略）。
    if sess.load_state() is None or sess.state.status in TERMINAL_STATUSES:
        print("没有可续跑的 run（state 缺失或已终态）。")
        return
    try:
        state = resume_run(sess, _RUNNER)
    except RunBusyError:
        print("已有后台任务在跑（本地同时只跑一个员工任务）——"
              "等它结束，或用 /abort 终止后再续跑。")
        return
    if state is None:
        # 能走到这：state 存在且非终态 → None 的唯一剩因是未配置 provider。
        print(_STUB_NOTICE)
        return
    print(f"续跑 run（status={state.status}，replay {WORK_MEMORY_REPLAY * 2} 条事件重建记忆）。")


def _cmd_abort(sess: RunSession) -> None:
    try:
        abort_run(sess)
    except ValueError:
        print("没有进行中的 run（无需终止）。")
        return
    print("已终止（aborted）。")


def _print_command_error(exc: Exception) -> None:
    """命令失败 → **一行**说明（不打印 traceback），会话继续。

    异常类型名对用户没意义（也不该上屏英文类名）→ 只给「什么坏了 + 怎么继续」；
    详细信息进日志。
    """
    log.exception("命令执行失败（会话继续）: %s", exc)
    print(f"命令执行失败：{type(exc).__name__}——已记录详情，会话继续"
          "（可用 /help 看可用命令；若是 /fix 或 /check，可先 /issues 看看台账是否损坏）。")


def _group_issues_by_source(issues: list[Issue]) -> list[tuple[str, list[Issue]]]:
    """按来源分组：``audit`` 在前、``check`` 在后（其余来源按来源名排其后）；组内按 id 升序。

    两类候选（审计意见 / 验收意见）不混作一列——人工要分别看。
    """
    buckets: dict[str, list[Issue]] = {}
    for issue in issues:
        buckets.setdefault(issue.source, []).append(issue)
    known = [src for src in ("audit", "check") if src in buckets]
    rest = sorted(src for src in buckets if src not in ("audit", "check"))
    return [(src, sorted(buckets[src], key=lambda i: i.id)) for src in [*known, *rest]]


def _cmd_issues(sess: RunSession) -> None:
    """待处理 issue 逐条处置：**按来源分组**（审计 / 验收各一组，两类候选不混作一列）。

    处置选项按意见类型分流：

    - **章级意见**（``quote`` 有值）→ 已解决 / 忽略 / **交给 AI 修**（P5 的 ``fix_issue``：
      quote 定位 → diff 确认卡 → 落盘 → 记忆重提取 → resolved）；
    - **全局类意见**（``quote`` 为空——跨章的结构性问题）→ **只给 已解决 / 忽略**：
      它没有可定位的原文片段，改写无从下手（给「交给 AI 修」是死路），如实说明一行。

    「已解决 / 忽略」的语义与 issues 台账结构不动。
    """
    issues = IssuesStore(sess.store.root)
    pending = issues.list("pending")
    if not pending:
        print("（无待处理 issue）")
        return
    ui = sess.approval_ui()
    for pos, (source, group) in enumerate(_group_issues_by_source(pending)):
        if pos:
            print()
        print(_SOURCE_GROUP_TITLES.get(source, f"【{source}】"))
        for issue in group:
            print(f"[{issue.id}] {issue.title}（{issue.source}）")
            if issue.detail:
                print(f"  {issue.detail}")
            options = ["已解决", "忽略", "交给 AI 修"]
            if not (issue.quote or "").strip():
                options = ["已解决", "忽略"]
                print(f"  {_GLOBAL_ISSUE_NOTE}")
            try:
                choice = ui.ask("如何处理这条 issue？", options, multi=False)
            except Exception:  # noqa: BLE001 - 审批取消：剩余保持 pending
                print("已取消，剩余 issue 保持 pending。")
                return
            picked = choice.indices[0]
            if picked == 0:
                issues.set_status(issue.id, "resolved")
                print("→ 已标记 resolved")
            elif picked == 1:
                issues.set_status(issue.id, "ignored")
                print("→ 已标记 ignored")
            else:
                _fix_issue_by_ai(sess, issue, ui=ui)


def _fix_issue_by_ai(sess: RunSession, issue, ui=None) -> None:
    """「交给 AI 修」（P5 T5）：quote 定位 → consistency_fix → diff 确认卡 → 落盘 →
    记忆重提取 → issue resolved（audit.fix.fix_issue）。

    ``ui`` 是这条通道的确认门（把 REPL 那个实例复用过来：处置选项与 diff 确认卡共用
    同一个门——不新建第二个审批入口）。未配置 provider 时如实说明并保持
    pending（不猜、不硬跑）。
    """
    comp = sess.components()
    if comp is None:
        print(_STUB_NOTICE)
        print("→ 交给 AI 修未执行：未配置 provider，"
              "本 issue 保持 pending。")
        return
    llm = comp.llm
    from harness.audit.fix import fix_issue
    from harness.memory.store import MemoryStore
    from harness.prompts.loader import PromptLoader

    mem = MemoryStore(sess.store.root)
    prompts = PromptLoader(project_root=sess.store.root)
    text = asyncio.run(fix_issue(store=sess.store, mem=mem, llm=llm, prompts=prompts,
                                 settings=sess.settings, issue=issue,
                                 emit=sess.events.append, ui=ui))
    print("→ " + text)


def _parse_audit_arg(arg: str, done_seqs: list[int]) -> list[int] | None:
    """``/audit [all|<seq>|<from>-<to>]`` → LLM 复核章号列表；None = 默认最近 12 章。

    ``all`` = 全部已定稿章；``<seq>``/``<from>-<to>`` = 指定章（闭区间）。非法输入
    raise ValueError（调用方打印用法不炸）。
    """
    arg = arg.strip()
    if not arg:
        return None
    if arg == "all":
        return sorted(done_seqs)
    if "-" in arg:
        start, _, end = arg.partition("-")
        if start.isdigit() and end.isdigit():
            lo, hi = int(start), int(end)
            if 1 <= lo <= hi:
                return list(range(lo, hi + 1))
    elif arg.isdigit() and int(arg) >= 1:
        return [int(arg)]
    raise ValueError(f"/audit 参数非法：{arg!r}（用法：/audit [all|<seq>|<from>-<to>]）")


def _cmd_audit(sess: RunSession, arg: str) -> None:
    """一致性审计（P5 T3）：确定性全量 + LLM 复核（默认最近 12 章；all=全量；
    数字/区间=指定章）。未配置 provider 如实提示不执行。"""
    try:
        done = [d.seq for d in sess.store.list_chapters(on_error=_warn_broken_chapter)
                if d.meta.get("status") == "done"]
        chapters = _parse_audit_arg(arg, done)
    except ValueError as exc:
        print(exc)
        return
    comp = sess.components()
    if comp is None:
        print(_STUB_NOTICE)
        print("一致性审计未执行：未配置 provider"
              "（~/.harness/config.toml 或项目 .harness/config.toml 配置 default_provider）。")
        return
    llm = comp.llm
    from harness.audit.runner import run_audit
    from harness.memory.store import MemoryStore
    from harness.prompts.loader import PromptLoader

    mem = MemoryStore(sess.store.root)
    prompts = PromptLoader(project_root=sess.store.root)
    text = asyncio.run(run_audit(store=sess.store, mem=mem, llm=llm, prompts=prompts,
                                 settings=sess.settings, chapters=chapters,
                                 emit=sess.events.append))
    print(text)


def _parse_check_arg(arg: str) -> tuple[str, bool]:
    """``/check [plot|global] [editor]`` → ``(kind, editor_mode)``。

    - 无参 → 全书级总编辑视角（``kind="global"``、``editor_mode=False``）；
    - ``plot`` → 情节级检查官视角；``global`` → 全书级总编辑视角；
    - ``editor`` → 签约责编视角（**只对全书级有效**：editor_mode="on"）；
    - 非法参数 raise ValueError（调用方打印用法不炸）；
    - ``plot editor`` → **直接拒**（M-3）：责编视角对情节级无效——静默丢掉 editor
      请求会让用户以为跑过责编视角（实际跑的是检查官视角）。
    """
    kind = "global"
    editor = False
    for token in (arg or "").split():
        word = token.strip()
        if not word:
            continue
        if word == "plot":
            kind = "plot"
        elif word == "global":
            kind = "global"
        elif word == "editor":
            editor = True
        else:
            raise ValueError(f"/check 参数非法：{word!r}（用法：/check [plot|global] [editor]）")
    if editor and kind == "plot":
        raise ValueError("/check 参数非法：责编视角只对全书级有效"
                         "（用法：/check [plot|global] [editor]）")
    return kind, editor


def _cmd_check(sess: RunSession, arg: str) -> None:
    """验收（P6 §1.5）：``/check`` = 全书级；``/check plot`` = 情节级；
    ``/check global editor`` = 签约责编视角。

    未配置 provider 时如实提示不执行（照 ``/audit`` 的既有口径）——
    绝不假装跑过（验收会落 issues，假跑比不跑更坏）。
    """
    try:
        kind, editor = _parse_check_arg(arg)
    except ValueError as exc:
        print(exc)
        return
    comp = sess.components()
    if comp is None:
        print(_STUB_NOTICE)
        print("验收未执行：未配置 provider"
              "（~/.harness/config.toml 或项目 .harness/config.toml 配置 default_provider）。")
        return
    llm = comp.llm
    from harness.check.runner import run_check
    from harness.memory.store import MemoryStore
    from harness.prompts.loader import PromptLoader

    mem = MemoryStore(sess.store.root)
    prompts = PromptLoader(project_root=sess.store.root)

    async def _run_and_close() -> str:
        try:
            return await run_check(store=sess.store, mem=mem, llm=llm, prompts=prompts,
                                   settings=sess.settings, kind=kind, editor_mode=editor,
                                   emit=sess.events.append)
        finally:
            # M-8：/check 每次新建的 llm 客户端用完就关（aclose 幂等）——
            # 只关这一条新路径；/audit 与「交给 AI 修」的同类泄漏是既有模式 → backlog。
            close = getattr(llm, "aclose", None)
            if callable(close):
                await close()

    text = asyncio.run(_run_and_close())
    print(text)


def _cmd_fix(sess: RunSession) -> None:
    """按章聚合修订（P8 §1.5）：**一章一张确认卡 + 一次改写**。

    与 ``/issues`` 的逐条处置**并存**（不合并）：``/issues`` 管「已解决/忽略」与逐条改写，
    ``/fix`` 管同一章的多条意见**一次**改完（seed 的「fix 按章聚合、每章只修一轮」）。

    - 参与的是**带原文片段的章级意见**（全局类意见与无章意见不参与——没有可定位的片段）；
    - 无可聚合的意见 → 如实说明，不假装跑过；
    - 每章的确认卡与 diff 在 ``harness.check.fix.fix_chapter`` 里（**一章一张**）。
    """
    from harness.check.fix import fix_chapter, group_by_chapter

    issues_store = IssuesStore(sess.store.root)
    groups = group_by_chapter(issues_store.list("pending"))
    if not groups:
        print("无可聚合修订的意见（需要带原文片段的章级意见；"
              "全局类意见请用 /issues 标记已解决或忽略）。")
        return
    comp = sess.components()
    if comp is None:
        print(_STUB_NOTICE)
        print("聚合修订未执行：未配置 provider"
              "（~/.harness/config.toml 或项目 .harness/config.toml 配置 default_provider）。")
        return
    llm, ui = comp.llm, comp.ui
    from harness.memory.store import MemoryStore
    from harness.prompts.loader import PromptLoader

    mem = MemoryStore(sess.store.root)
    prompts = PromptLoader(project_root=sess.store.root)

    async def _drive() -> None:
        """**所有章在同一个事件循环里跑**（2026-11-15 独立审核 D1）。

        逐章 ``asyncio.run`` 会让第 2 章起复用**上一个已关闭的 loop** 的 httpx 连接池
        → ``RuntimeError: Event loop is closed``（**不是** LLMError → 不被
        ``complete_structured`` 捕获）→ 一路穿出 REPL 命令分发（无兜底 except）
        → **整个会话带 traceback 退出**。
        （与 P11 裁定 71「intake 与 ReAct 循环绑同一个 loop」同款纪律。）
        """
        for chapter, group in groups.items():
            print(f"—— 第{chapter}章：{len(group)} 条待修意见 ——")
            for issue in group:
                print(f"[{issue.id}] {issue.title}（{issue.source}）")
                print(f"  待替换原文：{issue.quote}")
            text = await fix_chapter(store=sess.store, mem=mem, llm=llm, prompts=prompts,
                                     settings=sess.settings, chapter=chapter,
                                     issues=group, ui=ui)
            print("→ " + text)

    asyncio.run(_drive())


def _cmd_skills(sess: RunSession) -> None:
    """两级 skill 清单（项目级 + 用户级，项目级覆盖同名）——复用 skills.scan_skills。"""
    from harness.skills import scan_skills

    found = scan_skills(sess.store.root)
    if not found:
        print("无可用 skill")
        return
    for name in sorted(found):
        skill = found[name]
        print(f"{skill.name}（{skill.scope}）：{skill.description or '（无描述）'}")


def _cmd_skill(sess: RunSession, arg: str) -> None:
    """``/skill import <路径> [--project]``——导入外部 skill（单文件或整目录）。

    复制语义（设计 §2.6 拍板⑫）：单 SKILL.md 文件或整个目录均可；name 取 frontmatter
    name（缺省 = 目录名 / 文件 stem）；默认落用户级 ``~/.harness/skills/<name>/``，
    ``--project`` 落项目级 ``<项目>/.harness/skills/<name>/``；同名覆盖前过确认卡；
    导入后可任意手改，**改动即生效（下次载入）**。
    """
    from harness.skills import copy_skill, import_target, resolve_import, user_skills_dir

    sub, _, rest = arg.strip().partition(" ")
    if sub != "import":
        print("用法：/skill import <路径> [--project]")
        return
    raw = rest.strip()
    project_flag = False
    if raw.endswith("--project"):
        project_flag = True
        raw = raw[: -len("--project")].strip()
    if not raw:
        print("用法：/skill import <路径> [--project]")
        return
    try:
        name, src_path, _is_dir = resolve_import(Path(raw))
        base = (sess.store.root / ".harness" / "skills") if project_flag else user_skills_dir()
        target = import_target(base, name)
    except ValueError as exc:
        print(str(exc))
        return

    if target.exists():
        ui = sess.approval_ui()
        try:
            choice = ui.ask(f"目标已存在：{target}\n覆盖导入？", ["覆盖", "取消"], multi=False)
        except Exception:  # noqa: BLE001 - 审批取消：不动任何文件
            print("已取消，未改动。")
            return
        if not choice.indices or choice.indices[0] != 0:
            print("已取消，未改动。")
            return
    try:
        copy_skill(src_path, target)
    except OSError as exc:
        # 复制失败（Windows 保留设备名 / 权限 / 磁盘）→ 如实报错，**不带栈**
        print(f"导入失败：{exc}")
        return
    scope = "项目级" if project_flag else "用户级"
    print(f"已导入 skill「{name}」（{scope}）→ {target}")
    print("改动即生效（下次载入）。")


def _cmd_answer(sess: RunSession, arg: str) -> None:
    """非 TTY 审批应答：写 .harness/pending.answer（协议 v2/B3：首行 round:<本轮轮次>
    自动从 pending.md 抄——run 只收本轮的，迟到的回答不进下一轮）。

    备注按首个「 备注:」切分（而非空格 token 化）——备注里的多空格输入不被静默截断。

    **P15：写之前先校验**——越界/非法答案若写进文件，run 侧只会**继续等**（协议如此），
    答的人以为答了、run 却没动 → 这里**当场报错**，不写文件。

    校验 + 写盘本体在 :func:`harness.run.approval.write_answer`（web 应答 API 同一份，
    Task 11）；本函数只做参数解析与**逐字不变**的用户可见文案。
    """
    base = sess.store.root / ".harness"
    base.mkdir(parents=True, exist_ok=True)
    if " 备注:" in arg:
        nums_part, _, note = arg.partition(" 备注:")
    else:
        nums_part, note = arg, None
    # **取消标记**（独立复审 R2）：协议本身支持 `c`/`cancel`/`取消`（`wait_for_answer` 会抛
    # ApprovalCancelled → 取消不动数据），但 `/answer` 此前只收数字 → **取消不可达**。
    first_tok = nums_part.strip().split()[0] if nums_part.strip() else ""
    if first_tok.casefold() in _ANSWER_CANCEL_MARKERS:
        print(cancel_answer(base))
        return
    nums = [t for t in nums_part.split() if t.isdigit()]
    if not nums:
        print("/answer 需要至少一个选项号：/answer 1 2 备注:同意（或 /answer c 取消）")
        return
    try:
        echo = write_answer(base, indices=[int(n) for n in nums], note=note)
    except PendingMissingError:
        # 没有 pending.md（可能还没问）→ 不校验，照写（维持 Task 9 现状语义）
        lines = [" ".join(nums)]
        if note:
            lines.append(f"备注:{note}")
        (base / "pending.answer").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"已写入 pending.answer：{' '.join(nums)}" + (f"（备注：{note}）" if note else ""))
        return
    except InvalidAnswerError as exc:
        print(str(exc))
        return
    print(echo)


# ------------------------------------------------------------------ REPL

def run_repl(project: ProjectInfo, store: ProjectStore, *, resume: bool = False,
             settings: Settings | None = None) -> None:
    """``[{title}] >`` 循环；无前缀输入进员工主循环；Ctrl+C = /pause 语义。"""
    settings = settings or load_settings(project_path=store.root / ".harness" / "config.toml")
    sess = RunSession(project, store, settings)
    print(f"已进入项目《{project.title}》（{project.path}）；/help 查看命令，/quit 退出。")
    if resume:
        _cmd_resume(sess)
    commands = {
        "/state": lambda arg: _cmd_state(project, store),
        "/world": lambda arg: _cmd_world(store),
        "/characters": lambda arg: _cmd_characters(store),
        "/outline": lambda arg: _cmd_outline(store),
        "/chapters": lambda arg: _cmd_chapters(store),
        "/memory": lambda arg: _cmd_memory(store),
        "/pause": lambda arg: _cmd_pause(sess),
        "/resume": lambda arg: _cmd_resume(sess),
        "/abort": lambda arg: _cmd_abort(sess),
        "/issues": lambda arg: _cmd_issues(sess),
        "/fix": lambda arg: _cmd_fix(sess),
        "/audit": lambda arg: _cmd_audit(sess, arg),
        "/check": lambda arg: _cmd_check(sess, arg),
        "/skills": lambda arg: _cmd_skills(sess),
        "/checklist": lambda arg: _cmd_checklist(store, arg),
        "/skill": lambda arg: _cmd_skill(sess, arg),
        "/answer": lambda arg: _cmd_answer(sess, arg),
        "/help": lambda arg: print(_HELP),
    }
    # **非 TTY**：run 走后台线程（P16）→ REPL 必须**继续读命令**（否则 /answer 不可达）；
    # 且**不打印提示符**（会与后台 run 的输出交错在一起，污染管道输出）。
    non_tty = not sys.stdin.isatty()
    while True:
        try:
            line = input("" if non_tty else f"[{project.title}] >")
        except KeyboardInterrupt:
            state = sess.load_state()
            if state is not None and state.status not in TERMINAL_STATUSES:
                pause_run(sess, "Ctrl+C")
                print("已暂停（Ctrl+C）。可 /resume 续跑。")
                continue
            print()
            _join_background(pending_dir=store.root / ".harness")
            return
        except EOFError:
            print()
            _join_background(pending_dir=store.root / ".harness")
            return
        cmd = line.strip()
        if not cmd:
            continue
        if cmd == "/quit":
            _join_background(pending_dir=store.root / ".harness")
            return
        name, _, arg = cmd.partition(" ")
        if name == "/task":
            raw, deliverables, user_specified = _parse_task_arg(arg.strip())
            if not raw:
                print("/task 需要需求文本，如 /task chapters 写前三章细纲")
                continue
            _submit_brief(sess, raw, deliverables, user_specified=user_specified)
            continue
        handler = commands.get(name)
        if handler is not None:
            # **命令失败不许带栈退出会话**（2026-11-15 独立审核 D1）：
            # 任何未预料的异常（坏台账、坏文件、跨 loop 的 RuntimeError…）→ 如实报一句，
            # 继续下一条命令；会话不该因为一条命令崩掉。
            try:
                handler(arg.strip())
            except KeyboardInterrupt:
                print("已中断（Ctrl+C）。")
            except Exception as exc:                 # noqa: BLE001 —— 命令层兜底
                _print_command_error(exc)
        elif cmd.startswith("/"):
            print(f"未知命令：{name}（/help 查看全部命令）")
        else:
            _submit_brief(sess, cmd, ["outline"])

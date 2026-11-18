# P6 验收链（设计 §1.5）：验收上下文组装（模板变量逐字对齐）
"""把项目现状渲染成三个验收模板的**变量槽**。

**变量集与模板逐字一致**（Task 1 已钉死，模板改了就崩）：

- ``task.auto_plot_check`` 六变量：goals / constraints / plot_plans /
  chapter_summaries / scan_issues / stage_checklist_items；
- ``task.auto_global_check`` 九变量：premise / goals / constraints / tree /
  chapters / samples / open_issues / open_promises / stage_checklist_items；
- ``task.auto_editor_review`` 八变量：同上去掉 stage_checklist_items
  （``editor_mode=True`` 时**不含**该槽）。

各槽的数据来源（逐条照计划裁定）：

| 槽 | 来源 |
|---|---|
| premise | run brief 的 raw（``RunState.load(store.root).brief``）——无则「（未提供）」（**用户原话**，不做模型改写） |
| goals | **brief 的 raw（用户原话）** → 缺则 brief 的 goal → 再缺 premise → 再无「（未提供）」。**用户原话优先**：验收的锚点必须是外部的、不是模型自己写的概括（独立复审 D1） |
| constraints | brief 的 ``constraints[]`` 逐行「- xxx」，空则「（无）」 |
| tree | ``harness.run.digest.progress_digest(store)``（卷/情节树） |
| chapters / chapter_summaries | 每章一行「第N章《title》｜目标：objective｜状态：status」 |
| samples | **抽样**章的正文开头 ``SAMPLE_CHARS_PER_CHAPTER``（500）字，每章一块 |
| open_issues / scan_issues | issues 台账的 pending 条目，每条一行「[id] title（source）」 |
| open_promises | **未兑现承诺**：章记忆 promise_updates（status 未明确回收） |
| plot_plans | 每卷的情节：每条一行「卷N 情节seq《title》｜状态：status｜概要：summary」 |
| stage_checklist_items | **P7 已兑现**：编号条目清单（`N. 标题：提示`，提示压一行、截 160 字）；来源 = 内置默认 + 用户级 `~/.harness/checklists/<stage>/` + 项目级 `<项目>/.harness/checklists/<stage>/`（同名项目级覆盖）；无清单 → 「（无）」 |

**未兑现承诺口径唯一实现**在 ``harness.memory.inject.unresolved_promises_text``
（P5 审计的同名实现已上提到那里——copy-propagation 纪律：同一口径不留两份）。

**只读**：本模块只组装文本，不写任何文件、不调 LLM。
"""
from __future__ import annotations

import logging

from harness.check.sampling import (
    SAMPLE_CHARS_PER_CHAPTER,
    SAMPLE_MAX_CHAPTERS,
    sample_chapters,
)
from harness.memory.inject import unresolved_promises_text
from harness.run.digest import progress_digest
from harness.run.issues import IssuesStore
from harness.run.state import RunState
from harness.store.frontmatter import FileFormatError
from harness.store.project import ChapterDoc, ProjectStore
from harness.text import as_text, strip_edges

__all__ = [
    "NO_CHECKLIST",
    "NO_VALUE",
    "brief_context",
    "chapter_lines",
    "done_chapters",
    "global_check_vars",
    "issues_text",
    "plot_check_vars",
    "plot_lines",
    "samples_text",
]

log = logging.getLogger("check.context")

#: 槽内无内容时的占位（清单为空 / 约束为空 / 无待处理 issue / 无抽样章）
NO_CHECKLIST = "（无）"
#: brief 缺字段时的占位（premise / goals）
NO_VALUE = "（未提供）"
#: 槽内字段为空时的占位（目标 / 状态 / 概要）
_EMPTY = "—"


def _warn_broken(path, exc: FileFormatError) -> None:
    """坏章只降级当前章：点名文件 + 原因，其余照常（容错口径同 audit.runner）。"""
    log.warning("check 跳过坏章 %s: %s", path, exc.reason)


def done_chapters(store: ProjectStore) -> list[ChapterDoc]:
    """已定稿章（``meta.status == "done"``；坏章逐文件容错跳过）。"""
    return [d for d in store.list_chapters(on_error=_warn_broken)
            if d.meta.get("status") == "done"]


def brief_context(store: ProjectStore) -> dict:
    """run brief → ``{"premise", "goals", "constraints"}``（三槽来源逐条照计划）。

    - premise = brief 的 raw（无则「（未提供）」）——**用户原话**，不做模型改写
      （验收的「创作前提」用用户自己的话最稳）；
    - goals = **brief 的 raw（用户原话）** → 缺则 brief 的 goal → 再缺 premise →
      再无「（未提供）」（**用户原话优先**，见下方 D1 说明）；
    - constraints = brief 的 ``constraints[]`` 逐行「- xxx」（空则「（无）」）。

    state.json 缺失或损坏 → 三个槽各取占位（``RunState.load`` 对坏文件返回 None；
    缺键的极端情形也不让验收炸——组装层不因元数据缺失失败）。
    """
    brief: dict = {}
    try:
        state = RunState.load(store.root)
    except (KeyError, TypeError, AttributeError):   # pragma: no cover - 极端坏文件
        state = None
    if state is not None and isinstance(state.brief, dict):
        brief = state.brief
    raw = (as_text(brief.get("raw")) or "").strip()
    premise = (as_text(brief.get("premise")) or "").strip()
    goal = (as_text(brief.get("goal")) or "").strip()
    # **用户原话优先**（2026-11-15 独立复审 D1）：
    # P10 之前 brief 的 premise 从没人写过 → goals 恒等于 raw（用户原话）。
    # P10 一度改成「goal（模型写的概括）优先」→ **回归**：
    # ① `task.auto_plot_check` 的变量集**没有 premise 槽** → 判官手上唯一的意图陈述
    #    就是 goals → 用户明写的硬设定（「只写前三章」「主角不能死」）根本不在提示词里；
    # ② 写 goal 的模型与判「与目标对齐」的模型是同一族 → **自证偏差**，失去独立锚点；
    # ③ seed 自己就警告过：intake 的 premise 是**改写的摘要**，长需求会**压缩丢硬设定**
    #    （seed 的做法是「改写版给规划，原文给验收」）。
    # → 锚点永远是**用户原话**；模型的 goal 只作为 brief 里的数据留着（P11 再用）。
    goals = raw or goal or premise or NO_VALUE
    rows = []
    for c in brief.get("constraints") or []:
        text = (as_text(c) or "").strip()
        if text:
            rows.append(f"- {text}")
    constraints = "\n".join(rows) if rows else NO_CHECKLIST
    return {"premise": raw or NO_VALUE, "goals": goals, "constraints": constraints}


def chapter_lines(chapters: list[ChapterDoc]) -> str:
    """每章一行「第N章《title》｜目标：objective｜状态：status」。"""
    lines = []
    for d in chapters or []:
        title = (as_text(d.meta.get("title")) or "").strip()
        objective = (as_text(d.meta.get("objective")) or "").strip()
        status = (as_text(d.meta.get("status")) or "").strip()
        lines.append(f"第{d.seq}章《{title}》"
                     f"｜目标：{objective or _EMPTY}｜状态：{status or _EMPTY}")
    return "\n".join(lines) if lines else NO_CHECKLIST


def samples_text(chapters: list[ChapterDoc]) -> str:
    """抽样章的正文开头 ``SAMPLE_CHARS_PER_CHAPTER`` 字，每章一块。

    块头带章号与标题——模型据此知道这段正文属于哪一章（正文逐字截取，不改一个字）。
    """
    blocks = []
    for d in chapters or []:
        title = (as_text(d.meta.get("title")) or "").strip()
        body = strip_edges((d.body or "")[:SAMPLE_CHARS_PER_CHAPTER])
        blocks.append(f"【第{d.seq}章《{title}》】\n{body}")
    return "\n\n".join(blocks) if blocks else NO_CHECKLIST


def issues_text(store: ProjectStore) -> str:
    """issues 台账的 pending 条目：每条一行「[id] title（source）」。

    open_issues 与 scan_issues 同源——本地没有情节级扫描产物（计划裁定）。
    """
    rows = [f"[{i.id}] {i.title}（{i.source}）"
            for i in IssuesStore(store.root).list("pending")]
    return "\n".join(rows) if rows else NO_CHECKLIST


def _vol_numbers(arcs: list) -> list[int]:
    """卷号列表（真 int 才认——bool 是 int 子类，排除；升序去重）。"""
    vols: list[int] = []
    for arc in arcs or []:
        if not isinstance(arc, dict):
            continue
        vol = arc.get("vol")
        if isinstance(vol, int) and not isinstance(vol, bool) and vol not in vols:
            vols.append(vol)
    return sorted(vols)


def plot_lines(store: ProjectStore) -> str:
    """每卷的情节：每条一行「卷N 情节seq《title》｜状态：status｜概要：summary」。

    卷号取自 arcs.md（卷骨架是卷的唯一名册）；坏 arcs.md 只降级为空（不中断验收）。
    """
    try:
        arcs = store.read_arcs()
    except FileFormatError as exc:
        log.warning("check 跳过坏 arcs.md: %s", exc)
        arcs = []
    lines = []
    for vol in _vol_numbers(arcs):
        for plot in store.read_plots(vol):
            title = (as_text(plot.get("title")) or "").strip()
            status = (as_text(plot.get("status")) or "").strip()
            summary = (as_text(plot.get("summary")) or "").strip()
            lines.append(f"卷{vol} 情节{plot.get('seq')}《{title}》"
                         f"｜状态：{status or _EMPTY}｜概要：{summary or _EMPTY}")
    return "\n".join(lines) if lines else NO_CHECKLIST


def _memory_datas(mem) -> dict[int, dict]:
    """全部章记忆的解析结果（章号 → dict；坏章跳过，不炸）。"""
    datas: dict[int, dict] = {}
    for seq in mem.list_chapter_memories():
        data = mem.read_chapter_memory(seq)
        if data is not None:
            datas[seq] = data
    return datas


def _sample_seed(store: ProjectStore) -> str:
    """抽样种子（同项目同进度可复现——抽样器据此做确定性伪随机）。"""
    return store.root.name


def load_stage_checklist(store: ProjectStore, stage: str) -> list[tuple[str, str]]:
    """本 stage 的**清单条目**（来源层读盘结果）：``[(标题, 提示), …]``。

    来源见 :mod:`harness.checklists`（内置默认 + 用户级/项目级目录，项目级覆盖同名）。
    **只读文件、无缓存** → 用户改完清单，下次验收立即生效。

    编排层（:mod:`harness.check.runner`）把这一份结果**同时**用于「渲染槽」与
    「把模型回的条目名映射回规范标题」——只读一次盘，两处口径一致。
    """
    from harness.checklists import load_checklists

    return load_checklists(stage, project_root=store.root)


def checklist_items_text(items: list[tuple[str, str]]) -> str:
    """``{{stage_checklist_items}}`` 槽的内容：编号条目清单；无清单 → 「（无）」。"""
    from harness.checklists import render_checklist_items

    text = render_checklist_items(items)
    return text or NO_CHECKLIST


def plot_check_vars(*, store: ProjectStore, mem, goals: str, constraints: str,
                    stage: str = "outline",
                    checklist_items: list[tuple[str, str]] | None = None) -> dict:
    """``task.auto_plot_check`` 的六变量。

    变量集（模板逐字）：chapter_summaries / constraints / goals / plot_plans /
    scan_issues / stage_checklist_items。

    ``mem`` 保留在签名里（与 :func:`global_check_vars` 对称；情节级验收不读章记忆）。

    ``stage`` 是清单来源的阶段（P7：情节级用 ``outline``——每卷清单 + 爽点密度）。
    ``checklist_items`` 给了就用它（编排层已读盘 → 只读一次）；不给则按 ``stage`` 现读。
    """
    items = (checklist_items if checklist_items is not None
             else load_stage_checklist(store, stage))
    return {
        "chapter_summaries": chapter_lines(done_chapters(store)),
        "constraints": constraints or NO_CHECKLIST,
        "goals": goals or NO_VALUE,
        "plot_plans": plot_lines(store),
        "scan_issues": issues_text(store),
        "stage_checklist_items": checklist_items_text(items),
    }


def global_check_vars(*, store: ProjectStore, mem, goals: str, constraints: str,
                      premise: str, chapters: list, editor_mode: bool = False,
                      stage: str = "finish",
                      checklist_items: list[tuple[str, str]] | None = None) -> dict:
    """``task.auto_global_check`` 的九变量（或 ``task.auto_editor_review`` 的八变量——
    ``editor_mode=True`` 时**不含** stage_checklist_items）。

    变量集（模板逐字）：premise / goals / constraints / tree / chapters / samples /
    open_issues / open_promises /（stage_checklist_items）。

    ``chapters`` 是本次验收涉及的章（已定稿章或调用方指定的子集）：chapters 槽逐行列
    全部；samples 槽只放**抽样**章的正文开头（分层抽样，最新章必抽）。

    ``stage`` 是清单来源的阶段（P7：全书级用 ``finish``——全书清单 + 伏笔管理，
    与 seed 的 global_check 同款）。``checklist_items`` 给了就用它（编排层已读盘 →
    只读一次）；不给则按 ``stage`` 现读。
    """
    docs = list(chapters or [])
    sampled = sample_chapters(docs, SAMPLE_MAX_CHAPTERS, seed=_sample_seed(store))
    datas = _memory_datas(mem)
    max_seq = max((d.seq for d in docs), default=0)
    vars_ = {
        "premise": premise or NO_VALUE,
        "goals": goals or NO_VALUE,
        "constraints": constraints or NO_CHECKLIST,
        "tree": progress_digest(store),
        "chapters": chapter_lines(docs),
        "samples": samples_text(sampled),
        "open_issues": issues_text(store),
        "open_promises": unresolved_promises_text(datas, max_seq),
    }
    if not editor_mode:
        items = (checklist_items if checklist_items is not None
                 else load_stage_checklist(store, stage))
        vars_["stage_checklist_items"] = checklist_items_text(items)
    return vars_

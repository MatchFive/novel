"""plan_chapters 拆章工具（P2 规划链 段三，设计 §1.3 ⑤ 拆章三步+自查）。

情节 → 章节的三步式（seed outline_service.ai_plan_chapters 的文件库落地）：
① ``plot_units`` 把情节展开成一串**剧情描述段**（粗粒度事件段，带时间/地点/预估字数，
   prev/next 承接由 T4 plot_units_vars 渲染）→ ``clean_units``；段数 < ``min_units`` 时
   附 ``units_thin_note`` 补足二轮（**仅当变多才采用**）。
② ``units_to_chapters`` 按章字数目标**归组**（边界由代码定，绝不切断段）→ ``chapter_headers``
   一次小调用补 title/objective；失败/缺项按所辖段 **derive_title/derive_objective 退化派生**，
   不阻断拆章。
③ 逐章并发（Semaphore(3)）``chapter_beats`` → ``with_shrink``（首轮无效附 ≤4 拍收缩附注）
   → 偏少附 ``beats_thin_note`` 补足二轮（同真变多规则）→ ``clean_beats`` → 写盘 ChapterDoc
   （meta 全字段：seq/title/objective/status/beats/covers/time_span/start_at/end_at/plot_id/
   units(slim)/target_words，body=""）→ ``chapter_selfcheck`` 一轮：fixes 只收已有 seq
   （title 过 strip_chapter_prefix；四 anchor 键；beats 重写**保拍数**——len(fix) >= len(cur)
   才采纳，逐拍替换 event）→ ``content_repeat`` 确定性复核（命中如实附「仍有重复：a/b」，
   只提示不阻断不改写）。

幂等（可断点续拆）：meta ``plot_id`` 与写入的 ``"{vol}-{seq}"`` 完全相等才算命中——
已有章节且全部有 beats → 跳过；有章节缺 beats → 只补 beats；无章节 → 全链。拆章成功后
把 slim units 以 compact JSON 单线 KV 经 ``upsert_plots`` 回写情节卡（**整 dict 合并回写**，
brief 的示例字面 ``[{"seq", "units"}]`` 会冲掉情节概要等字段，故以 read_plots 全量 dict
+ units 覆盖）。LLM 输出里的 need_web/characters 键**忽略不拒绝**（T3 裁定；P2 无联网链路）。

失败观测不 raise（args 指向不存在情节属缺参，返回失败观测）；熔断是 loop 的事。
异步约定同 master_plan：注册 handler 同步签名经 base._run_sync 驱动；事件循环内请直接
await plan_chapters(...)——emit 事件只在这条路径可达（registry 同步边界 emit 为 no-op）。
"""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
import re

from harness.config import Settings
from harness.llm.client import CompletionUsage, OpenAICompatClient
from harness.llm.policy import resolve_max_tokens
from harness.planning.context import (
    _story_settings,
    beats_vars,
    headers_vars,
    plot_units_vars,
    selfcheck_vars,
)
from harness.planning.pure import (
    DEFAULT_TARGET_WORDS,
    DEFAULT_TOTAL_CHAPTERS,
    beats_thin_note,
    clean_beats,
    clean_units,
    content_repeat,
    derive_objective,
    derive_title,
    group_anchors,
    min_beats,
    min_units,
    slim_unit,
    strip_chapter_prefix,
    units_thin_note,
    units_to_chapters,
)
from harness.projects import README_FIELDS
from harness.prompts.loader import PromptLoader
from harness.store.project import ChapterDoc, ProjectStore
from harness.tools.base import (
    EmitFn,
    ToolResult,
    _run_sync,
    complete_structured,
    extract_json,
    usage_add,
    with_shrink,
)
from harness.tools.registry import ToolRegistry

__all__ = ["plan_chapters", "register_chapter_tools"]

log = logging.getLogger("tools.chapters")

_BEATS_CONCURRENCY = 3  # 逐章 beats 并发上限（seed autopilot_plan_concurrency 同款口径）

# 「收缩重试」附注（web_dream outline_service.py:37-42 逐字）：首轮无效时压小产出规模重跑
_UNITS_SHRINK_NOTE = ("\n【收缩重试】上次输出被截断/非法了。本次**最多出 4 段**剧情描述，"
                      "宁可少而完整——务必输出能被 json.loads 解析的完整 JSON。")
_BEATS_SHRINK_NOTE = ("\n【收缩重试】上次输出被截断/非法了。本次节拍**不超过 4 个**，"
                      "宁可少而完整——务必输出能被 json.loads 解析的完整 JSON。")

_ANCHOR_KEYS = ("covers", "time_span", "start_at", "end_at")
_PLOT_REF_RE = re.compile(r"^(\d+)-(\d+)$")
_README_KV = re.compile(r"^([^\s:：]+)[:：]\s*(.*)$")
_LABEL2KEY = {label: key for key, label in README_FIELDS}


def _noop_emit(kind: str, payload: dict) -> None:
    """registry 同步边界的 emit 占位：无 EventLog 句柄可绑，事件落在 await 路径上。"""


# ---------------------------------------------------------------- 参数与口径

def _parse_plot_ref(args: dict) -> tuple[int, int] | None:
    """args.plot_id → (vol, seq)；须为 "{vol}-{seq}"（vol/seq 均 ≥1 的整数），否则 None。"""
    ref = args.get("plot_id")
    if not isinstance(ref, str):
        return None
    m = _PLOT_REF_RE.match(ref.strip())
    if not m:
        return None
    vol, seq = int(m.group(1)), int(m.group(2))
    return (vol, seq) if vol >= 1 and seq >= 1 else None


def _target_words(args: dict) -> int:
    """args.target_words 优先（非法/缺省回退 DEFAULT）——章数由内容量推出时的章目标。"""
    try:
        return max(1, int(args.get("target_words")))
    except (TypeError, ValueError):
        return DEFAULT_TARGET_WORDS


def _readme_int(store: ProjectStore, key: str, default: int) -> int:
    """README 六字段之一个整数值（缺省/非法回退 default）。

    与 planning.context._story_settings 同源但只取单键——T6 口径裁定（README
    planned_volumes 优先、read_arcs 弧数回退，seed outline_service.py:716-719）只在
    本工具落地，不重导出 T4 的私有组装函数。
    """
    readme = store.root / "README.md"
    if not readme.exists():
        return default
    for line in readme.read_text(encoding="utf-8").splitlines():
        m = _README_KV.match(line.strip())
        if m and _LABEL2KEY.get(m.group(1)) == key:
            try:
                value = int(float(m.group(2).strip()))
            except (TypeError, ValueError):
                return default
            return value if value > 0 else default
    return default


def _arc_chapters(store: ProjectStore) -> int:
    """本卷容量参考章数：README planned_volumes 优先、read_arcs 弧数回退（seed :716-719）。

    只进 plot_units 提示词的容量参考；context.plot_units_vars 固定签名内按 read_arcs
    口径重复算了一次（T4 实现为准），调用方在渲染后以此值覆盖 variables["arc_chapters"]。
    """
    total = _readme_int(store, "target_chapters", DEFAULT_TOTAL_CHAPTERS)
    planned = _readme_int(store, "planned_volumes", 0)
    if planned <= 0:
        planned = max(1, len(store.read_arcs()))
    return max(1, round(total / max(1, planned)))


# ---------------------------------------------------------------- extract / valid

def _extract_obj(text: str) -> object | None:
    """结构化输出的默认 extract：首个可解析 JSON 对象（结构校验交给各阶段 valid）。"""
    value = extract_json(text)
    return value if isinstance(value, dict) else None


def _extract_units(text: str) -> object | None:
    """PlotUnitsOut extract：dict 即取值（units 结构交给 valid；need_web 键忽略不拒绝）。"""
    return _extract_obj(text)


def _valid_units(data: object) -> bool:
    return bool(clean_units((data or {}).get("units") if isinstance(data, dict) else None))


def _extract_headers(text: str) -> object | None:
    """ChapterHeadersOut extract：dict 且 chapters 为 list 才取值（缺项 → 退化派生）。"""
    value = _extract_obj(text)
    if isinstance(value, dict) and isinstance(value.get("chapters"), list):
        return value
    return None


def _extract_beats(text: str) -> object | None:
    """beats extract：dict 即取值（plot/beats 键与空拍判定交给 valid）。"""
    return _extract_obj(text)


def _valid_beats(data: object) -> bool:
    raw = (data.get("plot") or data.get("beats")) if isinstance(data, dict) else None
    return bool(clean_beats(raw))


def _extract_selfcheck(text: str) -> object | None:
    return _extract_obj(text)


# ---------------------------------------------------------------- store 容错读取

def _safe_chapters(store: ProjectStore) -> list[ChapterDoc]:
    """全书章节；单个坏章（FileFormatError）经 on_error 收集后跳过，不拖垮列表。"""
    bad: list[tuple] = []
    docs = store.list_chapters(on_error=lambda p, e: bad.append((p, e)))
    for path, exc in bad:
        log.warning("tools.chapters 跳过坏章: %s (%s)", path.name, exc)
    return docs


def _has_beats(meta: dict) -> bool:
    """meta beats 非空 list 才算「有细纲节拍」（空 list/缺失 → 幂等门判缺，可续拆）。"""
    return isinstance(meta.get("beats"), list) and bool(meta["beats"])


# ---------------------------------------------------------------- 主流程

async def _gen_units(llm: OpenAICompatClient, provider, prompts: PromptLoader,
                     variables: dict, note: str,
                     state: dict) -> object | None:
    """plot_units 一轮：附注拼进 batch_note 尾（seed _gen_units 同款），记账进 state。"""
    base_prompt = prompts.render("task.plot_units", **{**variables, "batch_note": variables["batch_note"] + note})
    value, usage, reason = await complete_structured(
        llm, model=provider.standard, temperature=0.5,
        max_tokens=resolve_max_tokens(len(base_prompt)), prompt=base_prompt,
        extract=_extract_units)
    state["usage"] = usage_add(state["usage"], usage)
    state["reason"] = reason
    return value


async def _gen_beats(llm: OpenAICompatClient, provider, prompts: PromptLoader,
                     prompt: str, note: str, state: dict) -> object | None:
    """chapter_beats 一轮：units_block 已在 prompt 内（render_with_blocks），note 拼尾。"""
    value, usage, reason = await complete_structured(
        llm, model=provider.standard, temperature=0.6,
        max_tokens=resolve_max_tokens(len(prompt) + len(note)),
        prompt=prompt + note, extract=_extract_beats)
    state["usage"] = usage_add(state["usage"], usage)
    state["reason"] = reason
    return value


async def _expand_beats(store: ProjectStore, llm: OpenAICompatClient, provider,
                        prompts: PromptLoader, *, plot: dict, docs: list[ChapterDoc],
                        units_of: dict[int, list[dict]], target_words: int,
                        siblings: list[ChapterDoc] | None = None,
                        ) -> tuple[list[int], list[tuple[int, str]], CompletionUsage | None]:
    """逐章并发（Semaphore(3)）扩写节拍；返回 (成功 seq 列表, 失败 (seq, reason), usage)。

    单章流程逐 seed _expand_chapter_beats：with_shrink 两轮（首轮无效附 ≤4 拍收缩附注）→
    偏少附 beats_thin_note 补足二轮（**仅当变多才采用**）→ clean_beats 写回 meta。
    失败的章保留骨架（beats 空 list），由幂等门下次续拆。
    ``siblings`` 供 beats_vars 定位相邻章 anchors：缺省=docs 本身；续跑补拍分支须传
    本情节**全部**章节（docs 只有缺 beats 的子集，否则中章会被当成首/末章）。
    """
    sem = asyncio.Semaphore(_BEATS_CONCURRENCY)
    need = min_beats(target_words)
    sibs = docs if siblings is None else siblings

    async def _one(doc: ChapterDoc) -> tuple[int, CompletionUsage | None, str]:
        variables = beats_vars(store, plot=plot, chapter=doc,
                               siblings=sibs, units=units_of.get(doc.seq) or [])
        prompt = prompts.render_with_blocks("task.chapter_beats", variables,
                                            [variables["units_block"]])
        state: dict = {"usage": None, "reason": ""}

        async def _run(note_suffix: str) -> object | None:
            return await _gen_beats(llm, provider, prompts, prompt, note_suffix, state)

        async with sem:
            data = await with_shrink(_run, note=_BEATS_SHRINK_NOTE, valid=_valid_beats)
            beats = clean_beats((data or {}).get("plot") or (data or {}).get("beats"))
            if beats and len(beats) < need:      # 太少 → 补足二轮（真变多才采用）
                data2 = await _gen_beats(llm, provider, prompts, prompt,
                                         beats_thin_note(len(beats), need), state)
                beats2 = clean_beats((data2 or {}).get("plot") or (data2 or {}).get("beats"))
                if len(beats2) > len(beats):
                    data, beats = data2, beats2
        if not beats:
            return doc.seq, state["usage"], state["reason"] or "bad_json"
        doc.meta["beats"] = beats
        return doc.seq, state["usage"], ""

    results = await asyncio.gather(*(_one(d) for d in docs))
    usage: CompletionUsage | None = None
    ok, failed = [], []
    for seq, use, reason in results:
        usage = usage_add(usage, use)
        (ok if not reason else failed).append(seq if not reason else (seq, reason))
    return ok, failed, usage


def _apply_fixes(docs: list[ChapterDoc], fixes: object) -> tuple[int, int]:
    """应用 selfcheck fixes（就地改 meta）：返回 (采纳条数, 丢弃条数)。

    - 只收已有 seq（未知 seq 丢弃，绝不新增章）；
    - title 过 strip_chapter_prefix；objective 与四 anchor 键独立采纳（非空才改）；
    - beats 重写**保拍数**：len(fix) >= len(cur) 才采纳，逐拍替换 event（k >= len(cur) 即停，
      多出的忽略），原拍其它键（pov/location/weight）保留。
    """
    by_seq = {d.seq: d for d in docs}
    applied = dropped = 0
    for fix in fixes if isinstance(fixes, list) else []:
        if not isinstance(fix, dict):
            continue
        doc = by_seq.get(fix.get("seq"))
        if doc is None:
            dropped += 1
            continue
        applied += 1
        meta = doc.meta
        title = str(fix.get("title") or "").strip()
        if title:
            meta["title"] = strip_chapter_prefix(title) or meta["title"]
        objective = str(fix.get("objective") or "").strip()
        if objective:
            meta["objective"] = objective
        for key in _ANCHOR_KEYS:
            val = str(fix.get(key) or "").strip()
            if val:
                meta[key] = val
        beats_fix = fix.get("beats")
        cur = meta.get("beats")
        if (isinstance(beats_fix, list) and isinstance(cur, list) and cur
                and len(beats_fix) >= len(cur)):
            for k, ev in enumerate(beats_fix):
                if k >= len(cur):
                    break
                ev = str(ev or "").strip()
                if ev and isinstance(cur[k], dict):
                    cur[k] = {**cur[k], "event": ev}
            meta["beats"] = cur
    return applied, dropped


def _still_repeat(docs: list[ChapterDoc]) -> list[str]:
    """确定性复核：相邻章 covers（退化 objective）字集重叠 → 「a/b」，只提示不阻断。"""
    ordered = sorted(docs, key=lambda d: d.seq)
    still = []
    for prev, cur in itertools.pairwise(ordered):
        prev_covers = str(prev.meta.get("covers") or prev.meta.get("objective") or "")
        cur_covers = str(cur.meta.get("covers") or cur.meta.get("objective") or "")
        if content_repeat(prev_covers, cur_covers):
            still.append(f"{prev.seq}/{cur.seq}")
    return still


async def plan_chapters(store: ProjectStore, llm: OpenAICompatClient, prompts: PromptLoader,
                        settings: Settings, emit: EmitFn, args: dict) -> ToolResult:
    """拆章三步+自查（幂等续拆）：见模块 docstring。失败观测不 raise。"""
    parsed = _parse_plot_ref(args)
    if parsed is None:
        return ToolResult(
            text=f"plan_chapters 失败（参数）：args.plot_id 缺参或格式非法——须为 \"{{vol}}-{{seq}}\""
                 f"（如 \"1-2\"，卷号/情节号均 ≥1 的整数），当前为 {args.get('plot_id')!r}",
            usage=None)
    vol, plot_seq = parsed
    ref = f"{vol}-{plot_seq}"

    arcs = store.read_arcs()
    arc = next((a for a in arcs if isinstance(a, dict) and a.get("vol") == vol), None)
    if arc is None:
        return ToolResult(text=f"plan_chapters 失败（无骨架）：未找到第 {vol} 卷骨架"
                             f"（plot_id {ref} 指向的卷不存在，请先 plan_arcs）", usage=None)
    plots = store.read_plots(vol)
    plot = next((p for p in plots if p.get("seq") == plot_seq), None)
    if plot is None:
        known = "、".join(str(p.get("seq")) for p in plots) or "（无）"
        return ToolResult(text=f"plan_chapters 失败（无情节）：未找到情节 {ref}"
                             f"（第 {vol} 卷现有情节 seq：{known}）", usage=None)

    # ---------------- 幂等门：meta plot_id 与 "{vol}-{seq}" 完全相等才算命中 ----------------
    chapters = _safe_chapters(store)
    mine = [c for c in chapters if str(c.meta.get("plot_id") or "") == ref]
    missing_beats = [c for c in mine if not _has_beats(c.meta)]
    if mine and not missing_beats:
        return ToolResult(
            text=f"plan_chapters：情节「{plot['title']}」({ref}) 已拆 {len(mine)} 章"
                 f"且全部有节拍——跳过（幂等）", usage=None)

    target_words = _target_words(args)
    plot_title = str(plot.get("title") or f"情节{plot_seq}")
    provider = settings.providers.get(settings.default_provider)
    if provider is None:
        known = "、".join(sorted(settings.providers)) or "（空）"
        return ToolResult(
            text=f"plan_chapters 失败（配置）：default_provider={settings.default_provider!r}"
                 f" 未配置（providers：{known}）", usage=None)

    # 全书 seq：max(现有)+1 递增（无章节从 1 起）
    next_seq = max((c.seq for c in chapters), default=0) + 1
    total_usage: CompletionUsage | None = None
    lines: list[str] = []
    units_by_seq: dict[int, list[dict]] = {}   # beats 变量组装用：章 seq → 所辖 slim 段

    if missing_beats:
        # 只补 beats：units 从各章 meta 取（写盘契约所存 slim 段）
        docs = missing_beats
        for d in docs:
            raw = d.meta.get("units")
            units_by_seq[d.seq] = raw if isinstance(raw, list) else []
        lines.append(f"已拆 {len(mine)} 章，其中 {len(docs)} 章缺节拍——只补 beats")
    else:
        # ---------------- 5.1 units：情节 → 剧情描述段（prev/next 承接由 T4 渲染） ----------------
        variables = plot_units_vars(store, arc=arc, plot=plot, plots=plots,
                                    target_words=target_words)
        # T6 口径裁定：README planned_volumes 优先、read_arcs 回退（seed :716-719），
        # 覆盖 context.py 固定签名内的 read_arcs 口径——只进本提示词容量参考。
        variables["arc_chapters"] = str(_arc_chapters(store))
        state: dict = {"usage": None, "reason": ""}

        async def _run_units(note_suffix: str) -> object | None:
            return await _gen_units(llm, provider, prompts, variables, note_suffix, state)

        data = await with_shrink(_run_units, note=_UNITS_SHRINK_NOTE, valid=_valid_units)
        units = clean_units((data or {}).get("units"))
        need_units = min_units(target_words)
        if units and len(units) < need_units:    # 太少 → 补足二轮（真变多才采用）
            data2 = await _gen_units(llm, provider, prompts, variables,
                                     units_thin_note(len(units), need_units), state)
            units2 = clean_units((data2 or {}).get("units"))
            if len(units2) > len(units):
                data, units = data2, units2
        total_usage = usage_add(total_usage, state["usage"])
        if not units:
            return ToolResult(
                text=f"plan_chapters 失败（无产出）：情节「{plot_title}」({ref}) 未产出剧情描述"
                     f"（{state['reason'] or 'bad_json'}）——零章落盘，可重试", usage=total_usage)
        emit("chapters_planned", {"plot_id": ref, "plot_title": plot_title,
                                  "phase": "units", "units": len(units)})

        # ---------------- 5.2 归组 + 5.2b headers：边界代码定，章头失败退化派生不阻断 ----------------
        groups = units_to_chapters(units, target_words)
        hvars = headers_vars(arc=arc, plot=plot, groups=groups,
                             story=_story_settings(store))
        hprompt = prompts.render("task.chapter_headers", **hvars)
        hdata, husage, hreason = await complete_structured(
            llm, model=provider.standard, temperature=0.4,
            max_tokens=resolve_max_tokens(len(hprompt)), prompt=hprompt,
            extract=_extract_headers)
        total_usage = usage_add(total_usage, husage)
        headers: dict[int, dict] = {}
        for c in ((hdata or {}).get("chapters") if isinstance(hdata, dict) else []) or []:
            if isinstance(c, dict):
                try:
                    headers[int(c.get("index"))] = c
                except (TypeError, ValueError):
                    continue
        if not headers and hreason:
            lines.append(f"章头拟制失败（{hreason}）——按所辖段退化派生标题/目标（不阻断）")
        elif hdata is None:
            lines.append("章头拟制无结果——按所辖段退化派生标题/目标（不阻断）")

        docs = []
        for group in groups:
            anchors = group_anchors(group)
            hdr = headers.get(len(docs) + 1) or {}
            title = strip_chapter_prefix(str(hdr.get("title") or "")) or derive_title(group)
            objective = (str(hdr.get("objective") or "").strip()
                         or derive_objective(group))
            slim = [slim_unit(u) for u in group]
            docs.append(ChapterDoc(seq=next_seq, meta={
                "seq": next_seq, "title": title, "objective": objective,
                "status": "planned", "beats": [],
                **anchors, "plot_id": ref, "units": slim,
                "target_words": target_words,
            }, body=""))
            units_by_seq[next_seq] = slim
            next_seq += 1
        lines.append(f"剧情描述 {len(units)} 段 → 归组 {len(docs)} 章（目标 {target_words} 字/章）")

    # ---------------- 5.3 beats：逐章并发（Semaphore(3)）→ 写盘 ChapterDoc ----------------
    ok, failed, busage = await _expand_beats(store, llm, provider, prompts, plot=plot,
                                             docs=docs, units_of=units_by_seq,
                                             target_words=target_words,
                                             siblings=mine if missing_beats else docs)
    total_usage = usage_add(total_usage, busage)
    for d in docs:
        store.write_chapter(d)   # 失败的章也落骨架（beats 空），幂等门下次续拆
        if _has_beats(d.meta):
            lines.append(f"第{d.seq}章「{d.meta.get('title') or ''}」：{len(d.meta['beats'])} 拍")
    for seq, reason in failed:
        lines.append(f"第{seq}章：未成功（{reason}）——保留骨架，重跑本工具可补")
    emit("chapters_planned", {"plot_id": ref, "plot_title": plot_title, "phase": "beats",
                              "chapters": len(docs), "ok": len(ok),
                              "missing": [s for s, _ in failed]})

    # ---------------- units 回写情节卡：compact JSON 单线 KV（全 dict 合并，不冲概要） ----------------
    if not missing_beats:
        slim_all = [slim_unit(u) for u in units]
        store.upsert_plots(vol, [{**plot, "seq": plot_seq, "units": json.dumps(
            slim_all, ensure_ascii=False)}])
        lines.append("units 已回写情节卡")

    # ---------------- 5.4 selfcheck：一轮审核 → 保拍数修复 → content_repeat 确定性复核 ----------------
    ready = [d for d in docs if _has_beats(d.meta)]
    n_issues = n_fixed = n_dropped = 0
    still: list[str] = []
    if not ready:
        lines.append("自查跳过：本情节暂无带节拍的章")
    else:
        svars = selfcheck_vars(plot=plot, chapters=ready)
        sprompt = prompts.render("task.chapter_selfcheck", **svars)
        sdata, susage, sreason = await complete_structured(
            llm, model=provider.standard, temperature=0.7,
            max_tokens=resolve_max_tokens(len(sprompt)), prompt=sprompt,
            extract=_extract_selfcheck)
        total_usage = usage_add(total_usage, susage)
        if not isinstance(sdata, dict):
            lines.append(f"自查未完成（{sreason or 'bad_json'}）——跳过修复（不阻断）")
        else:
            issues = [i for i in (sdata.get("issues") or []) if isinstance(i, dict)]
            n_issues = len(issues)
            n_fixed, n_dropped = _apply_fixes(ready, sdata.get("fixes"))
            for d in ready:   # 应用修复后写盘（含 beats 重写）
                store.write_chapter(d)
            still = _still_repeat(ready)
        emit("chapters_planned", {"plot_id": ref, "plot_title": plot_title,
                                  "phase": "selfcheck", "issues": n_issues,
                                  "fixed": n_fixed, "still_repeat": still})
        check_line = f"自查：问题 {n_issues} 处，修正 {n_fixed} 条"
        if n_dropped:
            check_line += f"（丢弃未知 seq {n_dropped} 条）"
        if still:
            check_line += "；仍有重复：" + "、".join(still)   # 只提示不阻断不改写
        lines.append(check_line)

    head = (f"plan_chapters：情节「{plot_title}」({ref}) "
            + ("补 beats 完成" if missing_beats else "拆章完成"))
    return ToolResult(text="\n".join([head, *lines]), usage=total_usage)


def register_chapter_tools(reg: ToolRegistry, *, store: ProjectStore, llm: OpenAICompatClient,
                           prompts: PromptLoader, settings: Settings) -> None:
    """注册 plan_chapters（read_only=False）：规划写盘工具，白名单外/并行批均由 registry 拒绝。"""

    def handler(store_: ProjectStore, args: dict) -> str:
        return _run_sync(plan_chapters(store_, llm, prompts, settings, _noop_emit, args)).text

    reg.register("plan_chapters", handler, read_only=False)

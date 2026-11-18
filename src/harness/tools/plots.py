"""master_plan 段二：逐卷并发情节规划（P2 规划链）。

plan_arcs 产卷骨架，本工具为每卷填 3~6 个情节：读 arcs → 逐卷渲染 task.auto_plan_plots
（pro 档，T4 plan_plots_vars 组装变量、README 六字段拼装 instruction，args.instruction 可覆盖）
→ 生成阶段**并发**（Semaphore(3)，模型调用是耗时大头）经 with_shrink 两轮（首轮无效 →
附收缩附注再跑）→ 落库与事件按卷**串行**（seed 同款口径：生成并发、DB 写串行）。
标题入库前剥「第N章」前缀与「（第N—M章）」区间标注，seq 按成功条数重编 1..N，status=planned。
输出契约里的 characters/need_web 键**忽略不拒绝**（T3 review 裁定；P2 无联网链路）。

单卷失败只跳过该卷（其余卷照常），观测如实报成功/失败/跳过卷数，不 raise——熔断是 loop 的事。

异步约定同 plan_arcs：注册进 ToolRegistry 的 handler 是同步签名（registry 契约 str），
内部经 base._run_sync 驱动异步核心；已处于事件循环中的调用方（Task 12 主循环）请直接
await master_plan(...)——emit 事件只在这条路径可达（registry 同步边界无 EventLog 句柄，
handler 以 no-op emit 调用，事件不丢观测正文）。
"""
from __future__ import annotations

import asyncio
import logging

from harness.checklists import stage_checklist_block
from harness.config import Settings
from harness.llm.client import CompletionUsage, OpenAICompatClient
from harness.llm.policy import resolve_max_tokens
from harness.planning.context import plan_plots_vars
from harness.prompts.loader import PromptLoader, insert_before_output_section
from harness.run.scope import read_brief_scope
from harness.skills import skill_injection
from harness.store.frontmatter import FileFormatError
from harness.store.project import ProjectStore
from harness.text import as_text, strip_chapter_prefix, strip_chapter_range
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

__all__ = ["master_plan", "register_plot_tools"]

log = logging.getLogger("tools.plots")

_PLAN_CONCURRENCY = 3  # 逐卷生成并发上限（seed autopilot_plan_concurrency 同款口径）

# 「收缩重试」附注（web_dream autopilot_runner.py:76-78 逐字）：首轮无效时二轮收缩规模重跑
_PLOT_SHRINK_NOTE = ("\n【收缩重试】上次输出被截断/非法了。本次**只出 4 个情节**，"
                     "每个 summary 不超过 120 字、beats 不超过 3 个，"
                     "务必输出能被 json.loads 解析的完整 JSON。")


def _noop_emit(kind: str, payload: dict) -> None:
    """registry 同步边界的 emit 占位：无 EventLog 句柄可绑，事件落在 await 路径上。"""


def _extract_plots(text: str) -> object | None:
    """PlotsOut extract：首个可解析 JSON 且 plots 为 list 即取值（空列表也算取到值——
    「plots 为空」的判定交给 with_shrink 的 valid，由它触发收缩二轮）。characters/need_web
    等其余键原样透传、不校验（need_web 解析忽略不拒绝）。"""
    value = extract_json(text)
    if isinstance(value, dict) and isinstance(value.get("plots"), list):
        return value
    return None


def _valid_plots(data: object) -> bool:
    """收缩重试有效性判定：plots 非空 list 即有效（T3 review 裁定）。"""
    return isinstance(data, dict) and bool(data.get("plots"))


def _clean_plots(raw: object) -> list[dict]:
    """单卷 plots 规整落库形态（seed _save_arc_plots:283-320 同口径）。

    - 整条空（{} / 全空串）直接丢——落库就是 title/summary 全空的垃圾行；
    - 标题空但有内容 → summary[:20] 兜底，**绝不写空标题**；
    - 标题剥「第N章」前缀 + 「（第N—M章）」区间标注（规划估算，拆章按内容量推章数必然对不上）；
    - seq 按成功条数重编 1..N（上面可能丢空条目，seq 留洞会让「情节#N」对不上）。
    """
    cleaned: list[dict] = []
    for p in raw if isinstance(raw, list) else []:
        if not isinstance(p, dict):
            continue
        title = (strip_chapter_range(strip_chapter_prefix(as_text(p.get("title")) or "")) or "").strip()
        summary = (as_text(p.get("summary")) or "").strip()
        if not (title or summary or p.get("conflict") or p.get("beats")):
            continue
        beats = p.get("beats")
        gags = p.get("gags")
        cleaned.append({
            "seq": len(cleaned) + 1,
            "title": title or summary[:20] or "（未命名情节）",
            "summary": summary,
            "conflict": as_text(p.get("conflict")),
            "resolution": as_text(p.get("resolution")),
            "beats": beats if isinstance(beats, list) else None,
            "gags": gags if isinstance(gags, list) and gags else None,
            "status": "planned",
        })
    return cleaned


async def master_plan(store: ProjectStore, llm: OpenAICompatClient, prompts: PromptLoader,
                      settings: Settings, emit: EmitFn, args: dict) -> ToolResult:
    """逐卷情节规划：并发生成（Semaphore(3)）+ 串行落库；失败卷如实上报，不 raise。"""
    vol_arg = args.get("vol")
    if vol_arg is not None and (isinstance(vol_arg, bool) or not isinstance(vol_arg, int)):
        return ToolResult(
            text=f"master_plan 失败（参数）：args.vol 须为 int（卷号），当前为 {vol_arg!r}",
            usage=None)
    provider = settings.providers.get(settings.default_provider)
    if provider is None:
        known = "、".join(sorted(settings.providers)) or "（空）"
        return ToolResult(
            text=f"master_plan 失败（配置）：default_provider={settings.default_provider!r}"
                 f" 未配置（providers：{known}）", usage=None)
    # 坏 arcs.md（用户手改坏了）→ **分类观测**，不抛栈（独立审核 M7）
    try:
        arcs = store.read_arcs()
    except FileFormatError as exc:
        return ToolResult(
            text=f"master_plan 失败（文件损坏）：卷骨架文件解析失败（{exc.reason}）"
                 "——请手工修正 outline/arcs.md 后重试（骨架不会丢，LLM 也没白跑）",
            usage=None)
    if not arcs:
        return ToolResult(
            text="master_plan 失败（无骨架）：尚无卷骨架——请先调用 plan_arcs 产卷骨架再规划情节",
            usage=None)
    # 范围口径（P11）：brief 里的 plan_arcs = 「先规划前 N 卷」。读一次盘。
    scope = await asyncio.to_thread(read_brief_scope, store)
    merged_dup = 0
    if vol_arg is not None:
        # **明确指定压过 brief 的范围**（P11 裁定 5）→ 此时**不注入**限卷行，也不报范围
        # （否则提示词里「只为下面这一卷」与「只规划前 N 卷」互斥，观测也会说假话
        # ——2026-11-15 独立审核 M2）。
        scope_block = scope.block(include_volume_limit=False)
        todo = [a for a in arcs if a.get("vol") == vol_arg]
        skipped = 0
        skipped_scope = 0
        if not todo:
            return ToolResult(
                text=f"master_plan 失败（无骨架）：未找到第 {vol_arg} 卷骨架"
                     "（请先 plan_arcs 产出，或核对卷号）", usage=None)
    else:
        scope_block = scope.block(include_volume_limit=True)
        valid = [a for a in arcs
                 if not isinstance(a.get("vol"), bool) and isinstance(a.get("vol"), int)]
        skipped = len(arcs) - len(valid)  # vol 非 int 的骨架条目：如实报跳过，绝不互相覆盖
        skipped_scope = 0
        # **按卷号去重**（2026-11-15 独立审核 M3）：「前 N 卷」是**卷集合**，
        # 不是「前 N 个条目」——重复卷号会让同一卷被规划两次（后者覆盖前者），
        # 而观测报「成功 N 卷」。
        by_vol: dict[int, dict] = {}
        for a in valid:
            by_vol.setdefault(a["vol"], a)      # 同卷号取第一条
        merged_dup = len(valid) - len(by_vol)
        valid = list(by_vol.values())
        if scope.plan_arcs:
            # **按卷号排序取前 N 卷**（read_arcs 是文件顺序，不等于卷号顺序）
            valid = sorted(valid, key=lambda a: a["vol"])
            todo = valid[:scope.plan_arcs]
            skipped_scope = len(valid) - len(todo)   # 范围外的卷：不调 LLM、不写盘
        else:
            todo = valid

    instruction = str(args.get("instruction") or "").strip()
    # **预计总卷数**（P13）：优先用模型给的 planned_volume_count（落盘在 arcs.md 的
    # front-matter 同级）→ 此前一律用**实际弧数**顶替 → 「预计 20 卷、只规划了 3 卷」
    # 时参考章数被算成 1/3（分母错了）。没有则回落弧数。
    arcs_planned = store.read_planned_volume_count() or len(arcs)
    characters = store.list_characters()  # 全量传 T4（[:20] 截断在 plan_plots_vars 内）
    sem = asyncio.Semaphore(_PLAN_CONCURRENCY)
    # 生成侧清单块（P9）：读一次盘，**逐卷共用同一份**（清单与验收侧同源）。
    checklist = await asyncio.to_thread(stage_checklist_block, store.root, "outline")

    async def _plan_one(arc: dict) -> tuple[object | None, CompletionUsage | None, str]:
        """单卷：渲染（instruction 可经 args 覆盖）→ with_shrink 两轮 → (data, usage, reason)。"""
        variables = plan_plots_vars(store, arc=arc, arcs_planned=arcs_planned,
                                    characters=characters)
        if instruction:
            variables["instruction"] = instruction
        # 范围块**追加在最终 instruction 之后**（P11 裁定 4）：agent 的 args.instruction
        # 会**整体替换** instruction 变量 → 范围是**约束**不是建议，不能被覆盖掉。
        if scope_block:
            variables["instruction"] = (
                f"{variables['instruction']}\n\n{scope_block}" if variables["instruction"]
                else scope_block)
        base_prompt = insert_before_output_section(
            prompts.render("task.auto_plan_plots", **variables), checklist) \
            + skill_injection(store.root, "task.auto_plan_plots")
        state: dict = {"usage": None, "reason": ""}

        async def _run(note_suffix: str) -> object | None:
            prompt = base_prompt + note_suffix
            value, usage, reason = await complete_structured(
                llm, model=provider.pro, temperature=0.5,
                max_tokens=resolve_max_tokens(len(prompt)), prompt=prompt,
                extract=_extract_plots)
            state["usage"] = usage_add(state["usage"], usage)  # 两轮都记账，重试不丢
            state["reason"] = reason
            return value

        async with sem:
            data = await with_shrink(_run, note=_PLOT_SHRINK_NOTE, valid=_valid_plots)
        if data is None:
            return None, state["usage"], state["reason"] or "bad_json"
        return data, state["usage"], ""

    results = await asyncio.gather(*(_plan_one(a) for a in todo))

    # 落库与事件按卷串行（生成并发、DB 写串行——seed 段二同款）
    done = failed = total_plots = 0
    total_usage: CompletionUsage | None = None
    lines: list[str] = []

    def _emit_progress(vol) -> None:
        emit("planning_progress", {"stage": "master_plan", "vol": vol,
                                   "done": done + failed, "total": len(todo),
                                   "ok": done, "failed": failed, "skipped": skipped})

    for arc, (data, usage, reason) in zip(todo, results):
        total_usage = usage_add(total_usage, usage)  # 跨卷聚合（含失败卷的半截用量）
        vol = arc.get("vol")
        title = str(arc.get("title") or f"第{vol}卷").strip() or f"第{vol}卷"
        if data is None:
            failed += 1
            lines.append(f"第{vol}卷：未成功（{reason}）")
            _emit_progress(vol)  # 失败卷也报进度（带失败计数），plan_arc 只在落库后发
            continue
        plots = _clean_plots(data.get("plots"))
        if not plots:
            failed += 1
            lines.append(f"第{vol}卷：未成功（plots 条目全空）")
            _emit_progress(vol)
            continue
        try:
            store.upsert_plots(vol, plots)
        except FileFormatError as exc:
            failed += 1
            lines.append(f"第{vol}卷：落盘失败（卷骨架文件解析失败：{exc.reason}）")
            _emit_progress(vol)
            continue
        done += 1
        total_plots += len(plots)
        emit("plan_arc", {"vol": vol, "title": title, "done": done, "total": len(todo),
                          "plots": [p["title"] for p in plots]})
        _emit_progress(vol)

    # **0 成功不能说「完成」**（2026-11-15 独立审核 L4）：观测里没有「失败（」字样
    # → loop 的瞬时词/失败分类会判成 **ok** → agent 以为规划成功了。
    if done == 0 and (failed or skipped or skipped_scope):
        head = (f"master_plan 段二：逐卷情节规划**未成功**——成功 0 卷 / 失败 {failed} 卷"
                + (f" / 跳过 {skipped} 卷（骨架 vol 非 int）" if skipped else "")
                + (f" / 合并 {merged_dup} 条重复卷号" if merged_dup else "")
                + (f" / 按范围跳过 {skipped_scope} 卷" if skipped_scope else "")
                + f"，共落 {total_plots} 个情节")
        return ToolResult(text="\n".join([head, *lines]), usage=total_usage)

    head = (f"master_plan 段二：逐卷情节规划完成——成功 {done} 卷 / 失败 {failed} 卷"
            + (f" / 跳过 {skipped} 卷（骨架 vol 非 int）" if skipped else "")
            + (f" / 合并 {merged_dup} 条重复卷号" if merged_dup else "")
            + (f" / 按范围跳过 {skipped_scope} 卷（本次只规划前 {scope.plan_arcs} 卷）"
               if skipped_scope else "")
            + f"，共落 {total_plots} 个情节")
    if vol_arg is not None:
        head += f"（按指定卷号规划第 {vol_arg} 卷）"
    elif scope.plan_arcs and not skipped_scope:
        head += f"（本次范围：前 {scope.plan_arcs} 卷）"
    return ToolResult(text="\n".join([head, *lines]), usage=total_usage)


def register_plot_tools(reg: ToolRegistry, *, store: ProjectStore, llm: OpenAICompatClient,
                        prompts: PromptLoader, settings: Settings) -> None:
    """注册 master_plan（read_only=False）：规划写盘工具，白名单外/并行批均由 registry 拒绝。"""

    def handler(store_: ProjectStore, args: dict) -> str:
        return _run_sync(master_plan(store_, llm, prompts, settings, _noop_emit, args)).text

    reg.register("master_plan", handler, read_only=False)

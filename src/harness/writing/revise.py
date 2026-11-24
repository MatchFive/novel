# 来源：语义移植自 web_dream（d:/project/web_dream/backend/app/workflows/draft_workflow.py
# 的 _revise/_revise_by_beat/_rewrite_beat/_revise_whole/_flavor_targets/_deaify/
# _route_after_review/revise_until_converged，行号锚点见 task-7-brief.md；判据/公式/
# 常量值不改，逐项偏离见 task-7-report.md）。
"""修正收敛循环 + deaify 两阶段收口（P3 写作链 Task 7）。

`revise_until_converged`：blocking 意见三级路由——零 LLM 定点替换（`apply_replace_issues`）
→ 按拍重写（`locate_for_target`+`beat_index_at` 半开口径定位，≤MAX_BEAT_REWRITES 拍，
新拍 <50% 保留原拍，`splice_with_spans` 保 spans）→ 整章重写（仅 outline/length_over
类结构性问题；常规 <70% 保留原版，length_over 压缩档下限换目标 ×0.85、spans 作废）；
每轮复审（重跑 draft_review），**收敛守卫**：blocking 计数不再严格减少即停（P41 放宽：
length_over 在压且正文严格变短时同数也收）、硬顶 MAX_REVISE，全程 best 跟踪（返回问题
最少的那一版）。

`deaify_if_needed`：定点去味（≤DEAIFY_ROUNDS 轮，`report.targets(20)` → `flavor_brief`
→ draft_deaify_edits，>1.8×+40 的过长改写丢弃，逐轮 `detect` 择优不劣化）→ 整章润色兜底
（仍超标或定点零落地时 draft_deaify 流式，空稿/<70%/分数反升全保留原版）。

持久化归 writing.py：本模块只改内存中的 body/spans/doc.meta，**绝不落盘**；LLM 调用失败
→ 该轮放弃、保留本轮前正文（reason 绝不穿透到观测层——revise 不拼观测）。
"""
from __future__ import annotations

import logging

from harness.llm.client import OpenAICompatClient
from harness.prompts.loader import PromptLoader
from harness.store.project import ChapterDoc, ProjectStore
from harness.text import count_words
from harness.tools.base import UsageMeter, complete_structured, extract_json, stream_collected
from harness.writing.context import (
    build_deaify_edits_vars,
    build_deaify_vars,
    build_review_vars,
    build_revise_vars,
    build_rewrite_beat_vars,
    project_style_body,
)
from harness.writing.flavor import (
    THRESHOLD,
    FlavorHit,
    FlavorReport,
    detect,
    flavor_brief,
)
from harness.writing.pure import (
    DEAIFY_ROUNDS,
    EDIT_LEN_FACTOR,
    EDIT_LEN_PAD,
    FULL_REWRITE_TYPES,
    KEEP_BEAT_FACTOR,
    LENGTH_LONG_FACTOR,
    LENGTH_SHORT_FACTOR,
    MAX_BEAT_REWRITES,
    MAX_REVISE,
    REPORT_ONLY_TYPES,
    WRITING_ROUTING,
    apply_replace_issues,
    beat_index_at,
    is_keep_whole,
    length_cap,
    locate_for_target,
    max_chars_deaify,
    max_chars_rewrite_beat,
    normalize_edges,
    normalize_issues,
    splice_with_spans,
)

__all__ = ["deaify_if_needed", "revise_until_converged"]

log = logging.getLogger("writing.revise")

# deaify_if_needed 的签名（Task 6 冻结）不含 settings，provider 从最近一次
# revise_until_converged 调用缓存（管线内两者同章同 provider 紧邻调用）；独立调用时
# 回退取 client 构造时的 provider（OpenAICompatClient 私有属性，仅作兜底）。
_LAST_PROVIDER: list = [None]


class _RoundFailed(Exception):
    """本轮 revise/deaify 的 LLM 调用失败：放弃该轮，收敛循环停用当前最佳稿。。"""


def _model(provider, tier: str) -> str:
    """WRITING_ROUTING 的 tier 属性名 → 模型名；tier 缺失回退 standard（writing.py 同款）。"""
    return getattr(provider, tier, None) or provider.standard


def _system(prompts: PromptLoader) -> str:
    """创作类任务 system = global.base_prompt（模板缺失不炸，seed load_prompt 同款容错）。"""
    try:
        return prompts.get("global.base_prompt")
    except (KeyError, OSError):
        return ""


def _extract_obj(text: str) -> object | None:
    """结构化输出的默认 extract：首个可解析 JSON 对象（结构校验交给调用方）。"""
    value = extract_json(text)
    return value if isinstance(value, dict) else None


def _issues_text(issues: list[dict]) -> str:
    """按拍/整章重写送审的意见清单（seed `f"· [{type}] {issue} → {suggestion}"` 同款）。"""
    return "\n".join(
        f"· [{i.get('type', '')}] {i.get('issue', '')} → {i.get('suggestion', '')}"
        for i in issues)


def _length_issue(short: bool, words: int, target: int) -> dict:
    """字数护栏问题（tools/writing.py `_length_issue` 同文案；length ∈ REPORT_ONLY_TYPES）。

    复审后重算——快照的 issues 要带上最新字数判定；不复用 tools.writing 私有名以保持
    依赖单向（tools.writing 惰性 import 本模块）。
    """
    if short:
        return {"type": "length",
                "issue": (f"字数不足：目标约 {target} 字，实际约 {words} 字"
                          "（多半是本章细纲内容不够，不是写得不够长）"),
                "suggestion": ("补本章细纲的节拍（或重新拆章让情节展开得更充分）后再写；"
                               "**不要注水**——注水会让问题被掩盖、看不出来。")}
    return {"type": "length",
            "issue": (f"字数超标：目标约 {target} 字，实际约 {words} 字"
                      "（逐拍写作没按每拍字数上限收敛，或该情节该拆成更多章）"),
            "suggestion": ("按每拍字数上限收紧明显超长的节拍（不整章注水/堆砌细节），"
                           "或在拆章阶段把该情节拆成更多章后再写。")}


def _length_over_issue(words: int, target: int, cap: int) -> dict:
    """超章长硬顶问题（P41；tools/writing.py `_length_over_issue` 同文案，依赖单向不互引）。

    type=length_over ∈ FULL_REWRITE_TYPES → blocking，复审后仍超顶时重新生成，
    驱动下一轮整章压缩；与 report-only 的 `_length_issue` 分工见 writing.py 注释。
    """
    return {"type": "length_over",
            "issue": (f"字数超硬顶：本章硬顶 {cap} 字（目标约 {target} 字），"
                      f"实际约 {words} 字——必须压缩到硬顶以内"),
            "suggestion": (f"整章压缩到 {cap} 字以内：收紧明显超长的节拍、删并重复"
                           "描写与冗余过渡，保留情节骨架与关键伏笔；不得砍成梗概"
                           f"（低于约 {int(target * LENGTH_SHORT_FACTOR)} 字视为过度压缩）。")}


def _edit_tuples(targets: list, edits: list) -> list[tuple[int, int, str]]:
    """定点去味的「编号 → 新片段」→ `(start, end, 替换文本)`（seed 318-348 逐字判据）。

    **长度护栏**：replacement 明显长于原文（>EDIT_LEN_FACTOR× 且多 EDIT_LEN_PAD 字以上）
    时丢弃该条——模型偶发把「改写一个片段」写成一整句/一整段，与邻近原文撞车后正文出现
    重复句。丢弃即保留原文，宁少勿滥。
    """
    repl: dict[int, str] = {}
    for item in edits:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        new = str(item.get("replacement") or "").strip()
        if new:
            repl[idx] = new
    out: list[tuple[int, int, str]] = []
    for idx, new in repl.items():
        if not 1 <= idx <= len(targets):
            continue
        hit = targets[idx - 1]
        quote = hit.quote or ""
        if quote and len(new) > len(quote) * EDIT_LEN_FACTOR + EDIT_LEN_PAD:
            log.info("定点去味：第 %d 条改写明显变长（%d → %d 字），丢弃以免与上下文重复",
                     idx, len(quote), len(new))
            continue
        out.append((hit.start, hit.end or (hit.start + len(quote)), new))
    return out


def _splice_edits(text: str, edits: list[tuple[int, int, str]]) -> str:
    """从后往前就地替换若干 edit（前面偏移不失效）；重叠/越界的 edit 跳过（seed 218-240 判据）。

    与已替换区间重叠的跳过——套路/引文会互相嵌套；一条都没换成时原样返回。
    """
    out = text
    last_start = len(out) + 1
    for start, end, repl in sorted(edits, key=lambda e: e[0], reverse=True):
        if not (0 <= start < end <= len(out)) or end > last_start:
            continue
        out = out[:start] + repl + out[end:]
        last_start = start
    return out


# ---------------------------------------------------------------- 三级修正（单轮）

async def _rewrite_beat(*, provider, llm, prompts, emit, doc, materials,
                        beat_text: str, prev_text: str, next_text: str,
                        issues: list[dict],
                        meter: UsageMeter | None = None) -> str:
    """让模型只重写一拍（task.draft_rewrite_beat，std/0.6 流式；seed 1153-1169）。"""
    tier, temp, mt = WRITING_ROUTING["task.draft_rewrite_beat"]
    prompt = prompts.render(
        "task.draft_rewrite_beat",
        **build_rewrite_beat_vars(materials, beat_text, prev_text, next_text, issues))
    text, use, reason = await stream_collected(
        llm, model=_model(provider, tier), temperature=temp, max_tokens=mt,
        system=_system(prompts), prompt=prompt,
        max_chars=max_chars_rewrite_beat(len(beat_text)))
    if meter is not None:
        meter.add(use)   # usage 入账（拿到即记：失败的调用也烧了 token）
    if reason:
        raise _RoundFailed(reason)
    emit("draft_progress", {"seq": doc.seq, "beat": 0, "chars": len(text),
                            "phase": "revising"})
    return text


async def _revise_by_beat(*, provider, llm, prompts, emit, doc, materials,
                          text: str, spans: list[list[int]],
                          remaining: list[dict],
                          meter: UsageMeter | None = None) -> tuple[str, list[list[int]]] | None:
    """只重写出问题的那几拍（每拍一次调用），其余原文一字不动（seed 1100-1151）。

    定位：拿意见里的 `quote` 去正文里找（`locate_for_target` 放宽定位），落在哪一拍
    （`beat_index_at`，半开口径 `s<=pos<e`）就改哪拍。定位不到/全失败返回 None，
    调用方退回整章。
    """
    targets: dict[int, list[dict]] = {}
    for item in remaining:
        found = locate_for_target(text, item.get("quote") or "")
        if found is None:
            continue
        idx = beat_index_at(spans, found[0])
        if idx is None:
            continue
        targets.setdefault(idx, []).append(item)
    if not targets:
        log.info("按拍修复：没有意见能定位到具体节拍，退回整章/上报")
        return None
    picked = sorted(targets)[:MAX_BEAT_REWRITES]
    if len(targets) > len(picked):
        log.info("按拍修复：%d 拍有问题，本轮只改前 %d 拍（其余留给下一轮/上报）",
                 len(targets), len(picked))
    out, span_state = text, [list(s) for s in spans]
    changed: list[int] = []
    for idx in picked:
        s, e = span_state[idx]
        beat_text = out[s:e]
        new_beat = await _rewrite_beat(
            provider=provider, llm=llm, prompts=prompts, emit=emit, doc=doc,
            materials=materials, beat_text=beat_text,
            prev_text=out[max(0, s - 600):s], next_text=out[e:e + 400],
            issues=targets[idx], meter=meter)
        # 截断/敷衍防护：空稿或明显过短就保留原拍
        if not new_beat.strip() or len(new_beat) < len(beat_text) * KEEP_BEAT_FACTOR:
            log.warning("按拍修复：第 %d 拍改写稿异常（%d 字 vs 原 %d 字），保留原拍",
                        idx + 1, len(new_beat), len(beat_text))
            continue
        out, span_state = splice_with_spans(out, span_state, s, e, new_beat)
        changed.append(idx)
    if not changed:
        log.info("按拍修复：没有一拍改写成功，退回整章/上报")
        return None
    log.info("按拍修复：重写 %d 拍（%s），共 %d 字",
             len(changed), "、".join(f"第{i + 1}拍" for i in changed), len(out))
    return out, span_state


async def _revise_whole(*, provider, llm, prompts, doc, materials,
                        text: str, issues: list[dict], target_words: int,
                        meter: UsageMeter | None = None) -> tuple[str, None]:
    """整章重写（task.draft_revise，std/0.7 流式；seed 1171-1206）。

    max_chars 照 seed `_revise_whole`：`_stream_llm` 未传上限（None，靠 max_tokens 封顶）。
    截断防护分两档：常规（outline 等）新稿 <70% 保留原版；**length_over 压缩**（P41）
    保留下限换成目标 ×0.85——真压缩必然远短于原稿（12000→4800 只剩 40%），按原稿 70%
    判会把一切有效压缩误判成截断。整章重写 ⇒ 逐拍区间作废（返回 None，调用方/writing.py 置）。
    """
    tier, temp, mt = WRITING_ROUTING["task.draft_revise"]
    prompt = prompts.render(
        "task.draft_revise",
        **build_revise_vars(materials, issues, detect(text), text))
    new, use, reason = await stream_collected(
        llm, model=_model(provider, tier), temperature=temp, max_tokens=mt,
        system=_system(prompts), prompt=prompt, max_chars=None)
    if meter is not None:
        meter.add(use)   # usage 入账
    if reason:
        raise _RoundFailed(reason)
    if new and text:
        if any(str(i.get("type")) == "length_over" for i in issues):
            floor = int(target_words * LENGTH_SHORT_FACTOR)
            if len(new) < floor:
                log.warning("压缩稿过度（%d 字 < 目标下限 %d 字），保留原稿",
                            len(new), floor)
                new = text
        elif not is_keep_whole(new, text):
            log.warning("修正稿疑似被截断（%d 字 < 原稿 %d 字的 70%%），保留原稿",
                        len(new), len(text))
            new = text
    return new or text, None


async def _recheck(*, provider, llm, prompts, doc, materials, text: str,
                   spans, target_words: int,
                   meter: UsageMeter | None = None) -> tuple[str, list | None, list[dict]]:
    """复审一轮（task.draft_review 重跑全章）→ (定点置换后正文, spans, 全量 issues)。

    seed `_review` 同款：normalize → apply_replace_issues（零 LLM 定点置换）→ 字数护栏
    （length 只上报不门禁）；issues 全量（含 report-only）供快照与下一轮 blocking 过滤。
    """
    tier, temp, mt = WRITING_ROUTING["task.draft_review"]
    prompt = prompts.render("task.draft_review",
                            **build_review_vars(materials, doc.seq, text))
    data, use, reason = await complete_structured(
        llm, model=_model(provider, tier), temperature=temp, max_tokens=mt,
        prompt=prompt, extract=_extract_obj)
    if meter is not None:
        meter.add(use)   # usage 入账
    if reason:
        raise _RoundFailed(reason)
    llm_issues = normalize_issues((data or {}).get("issues"))
    fixed, fixed_spans, issues = apply_replace_issues(text, spans, llm_issues)
    words = count_words(fixed)
    cap = length_cap(target_words)
    if words < int(target_words * LENGTH_SHORT_FACTOR):
        issues = list(issues) + [_length_issue(True, words, target_words)]
    elif words > cap:
        # P41：超硬顶 → blocking（length_over），驱动下一轮整章压缩（同 writing.py ③）
        issues = list(issues) + [_length_over_issue(words, target_words, cap)]
    elif words > int(target_words * LENGTH_LONG_FACTOR):
        issues = list(issues) + [_length_issue(False, words, target_words)]
    return fixed, fixed_spans, issues


async def _revise_round(*, provider, llm, prompts, emit, doc, materials,
                        body: str, spans, blocking: list[dict], target_words: int,
                        meter: UsageMeter | None = None
                        ) -> tuple[str, list | None]:
    """单轮三级修正（seed `_revise` 1066-1098）：返回 (新正文, 新区间|None)。

    ① 零 LLM 定点替换（replace 就地改）→ ② 能定位到拍 → 按拍重写 → ③ 剩余 outline 类
    （结构性）→ 整章重写；定位不到且非结构 → 不动正文、issue 留在台账。
    """
    fixed, spans2, remaining = apply_replace_issues(body, spans, blocking)
    if not remaining:
        return fixed, spans2
    if spans2:
        scoped = await _revise_by_beat(
            provider=provider, llm=llm, prompts=prompts, emit=emit, doc=doc,
            materials=materials, text=fixed, spans=spans2, remaining=remaining,
            meter=meter)
        if scoped is not None:
            return scoped
    if any(str(i.get("type")) in FULL_REWRITE_TYPES for i in remaining):
        return await _revise_whole(provider=provider, llm=llm, prompts=prompts,
                                   doc=doc, materials=materials, text=fixed,
                                   issues=remaining, target_words=target_words,
                                   meter=meter)
    log.info("按拍修复：%d 条意见定位不到节拍且非结构性，本轮不改写（如实上报）",
             len(remaining))
    return fixed, spans2


# ---------------------------------------------------------------- 对外接口

async def revise_until_converged(*, store: ProjectStore, llm: OpenAICompatClient,
                                 prompts: PromptLoader, settings, emit, doc: ChapterDoc,
                                 materials: dict, body: str, spans, blocking: list[dict],
                                 issues: list[dict], target_words: int,
                                 meter: UsageMeter | None = None,
                                 ) -> tuple[str, list | None, int, list, list]:
    """按审核意见**反复修到收敛**（seed `revise_until_converged` 1480-1536）。

    停止条件（任一命中即停）：① 无逻辑类问题；② 本轮 blocking 计数未**严格减少**
    （seed `_route_after_review` 567-580：再修也是白改——整章重写会洗牌全文、问题反而变多）；
    ③ 到达 MAX_REVISE；④ 本轮正文没变化（改不动了，省调用）。全程 best 跟踪，
    返回 (新正文, 新 spans|None, revise_count, 复审后 blocking, 复审后 issues)。

    失败处置：任一轮 LLM 调用失败 → 该轮放弃、保留本轮前正文（绝不让裸 reason 穿透到
    writing.py 的观测层）；持久化（write_chapter/快照）一律归 writing.py，本函数不落盘。

    ``meter`` 可选（P5 usage 入账）：各 LLM 子调用的 usage 记进计量器；None=不记账，
    行为与返回形状（5 元组）与从前逐字相同。
    """
    provider = settings.providers.get(settings.default_provider) if settings else None
    if provider is None or not blocking:
        return body, spans, 0, blocking, issues
    _LAST_PROVIDER[0] = provider

    best_body, best_spans = body, spans
    best_blocking: list[dict] = list(blocking)
    best_issues: list[dict] = list(issues)
    best_count = len(blocking)
    cur_body, cur_spans, cur_blocking = body, spans, list(blocking)
    revise_count = 0
    for round_no in range(MAX_REVISE):
        try:
            new_body, new_spans = await _revise_round(
                provider=provider, llm=llm, prompts=prompts, emit=emit, doc=doc,
                materials=materials, body=cur_body, spans=cur_spans,
                blocking=cur_blocking, target_words=target_words, meter=meter)
            new_body, new_spans, re_issues = await _recheck(
                provider=provider, llm=llm, prompts=prompts, doc=doc,
                materials=materials, text=new_body, spans=new_spans,
                target_words=target_words, meter=meter)
        except Exception:  # noqa: BLE001 - seed :1480-1536 每轮保最佳稿不拖垮调用方
            log.warning("修正第 %d 轮 LLM 调用失败，保留当前最佳稿", round_no + 1)
            break
        re_blocking = [i for i in re_issues
                       if i.get("type") not in REPORT_ONLY_TYPES]
        cnt = len(re_blocking)
        revise_count += 1
        changed = new_body != cur_body
        # P41：length_over（超硬顶）压缩途中计数不降是正常的（一条压一条）——只要正文
        # **严格变短**就收下继续压（12000→6000→4800），否则收敛守卫第一轮就停在半路，
        # 永远到不了硬顶以内。其余类型维持「计数未严格减少即停」原语义。
        shorter = (cnt == best_count and cnt > 0
                   and any(i.get("type") == "length_over" for i in cur_blocking)
                   and len(new_body) < len(best_body))
        if cnt < best_count or shorter:
            best_body, best_spans = new_body, new_spans
            best_blocking, best_issues = re_blocking, re_issues
            best_count = cnt
            cur_body, cur_spans, cur_blocking = new_body, new_spans, re_blocking
        else:
            log.info("修正第 %d 轮问题数未严格减少（%d → %d），停止",
                     round_no + 1, best_count, cnt)
            break
        if best_count == 0 or not changed:
            break
    out_body, out_spans = normalize_edges(best_body, best_spans)
    log.info("修正收敛：共 %d 轮，剩余逻辑类问题 %d 条", revise_count, best_count)
    return out_body, out_spans, revise_count, best_blocking, best_issues


def _merge_review_quotes(targets: list[FlavorHit], quotes: list[str],
                         text: str) -> list[FlavorHit]:
    """把**审核判出的 ai_flavor 引文**并入定点名单（定位得到、且不在名单里的才加）。

    定位用 ``locate_quote``（精确 + 折叠两级，**不做模糊匹配**）——定位不到就跳过
    （引文可能是模型**改写**过的，或正文已被零 LLM 置换过 → 宁可不改）。
    """
    if not quotes:
        return targets
    from harness.text import locate_quote

    out = list(targets)
    seen = {(h.start, h.end) for h in out}
    for quote in quotes:
        q = (quote or "").strip()
        if not q:
            continue
        pos = locate_quote(text, q)
        if pos is None:
            continue
        start, end = pos
        if (start, end) in seen:
            continue
        seen.add((start, end))
        out.append(FlavorHit(
            rule="审核判出 AI 味", weight=0.0, quote=text[start:end],
            analysis="审核模型判定此处有 AI 味（确定性检测未命中）",
            fix="改写得自然些，去掉套路化的表达",
            score_only=False, start=start, end=end))
    return out


async def deaify_if_needed(*, store: ProjectStore, llm: OpenAICompatClient,
                           prompts: PromptLoader, emit, doc: ChapterDoc,
                           report: FlavorReport,
                           flavor_quotes: list[str] | None = None,
                           meter: UsageMeter | None = None) -> bool:
    """去 AI 味两阶段收口（seed `_deaify` 1253-1345）：原地改 doc.body，返回是否改写。

    **触发 = report.serious（确定性超标且有可改写命中）**或**审核判出 ai_flavor**
    （设计 §1.4 的**双信号**）。P3 本地实现只有前者，理由写的是「review 的 ai_flavor
    意见在调用点数据流不可得，双信号随 P4 记忆/检索统一后恢复」——**P4 早已落地**，
    那条理由**已失效**：`task.draft_review` 的意见**带 quote**，且就在调用点同函数内
    （独立审核 L1）。→ 恢复双信号。

    ``flavor_quotes`` = 审核判出的 ai_flavor **引文**：它们会被定位进正文、**并入定点
    名单**（与 detect() 的命中一起改）——否则第二个信号触发了却没东西可改。

    ① 定点（≤DEAIFY_ROUNDS 轮）：targets → flavor_brief → draft_deaify_edits
    （std/0.3 结构化）→ 过长改写丢弃、从后往前 splice → 每轮 detect **择优不劣化**；
    ② 兜底：best_score > THRESHOLD 或（serious 且定点零落地）→ draft_deaify
    （std/0.5 流式，max_chars_deaify）；空稿/<70%/分数反升全保留上一版。
    任何改写都使逐拍区间失效 → doc.meta 的 beat_spans 就地作废；落盘归 writing.py。

    ``meter`` 可选（P5 usage 入账）：两阶段的 LLM 子调用 usage 记进计量器；None=
    不记账，行为与从前逐字相同。
    """
    draft = doc.body or ""
    if not draft.strip():
        return False
    extra_quotes = [q for q in (flavor_quotes or []) if (q or "").strip()]
    if not report.serious and not extra_quotes:
        return False
    # P5 T7：项目级 style skill 正文注入去味两阶段（文风只认项目级；无 skill → ""）
    style_guide = project_style_body(store)
    provider = _LAST_PROVIDER[0]
    if provider is None:
        provider = getattr(llm, "_provider", None)
    if provider is None:
        log.warning("deaify：无 provider 可用（settings/client 均未提供），跳过")
        return False

    best_text, best_score = draft, report.score
    emit("draft_progress", {"seq": doc.seq, "beat": 0, "chars": len(best_text),
                            "phase": "deaify"})

    # ① 定点改写：只动命中片段（seed `_flavor_targets` 的确定性命中部分
    #   + **审核判出的 ai_flavor 引文**——设计 §1.4 的第二个信号要真的能改到东西）
    targeted_changed = False
    for round_no in range(DEAIFY_ROUNDS):
        targets = detect(best_text).targets(20)
        targets = _merge_review_quotes(targets, extra_quotes, best_text)
        if not targets:
            break
        brief = flavor_brief(best_text, targets)
        tier, temp, mt = WRITING_ROUTING["task.draft_deaify_edits"]
        prompt = prompts.render("task.draft_deaify_edits",
                                **build_deaify_edits_vars(brief, style_guide=style_guide))
        data, use, reason = await complete_structured(
            llm, model=_model(provider, tier), temperature=temp, max_tokens=mt,
            prompt=prompt, extract=_extract_obj)
        if meter is not None:
            meter.add(use)   # usage 入账
        if reason:
            log.warning("定点去味第 %d 轮调用失败，保留上一版", round_no + 1)
            break
        edits = (data or {}).get("edits")
        tuples = _edit_tuples(targets, edits if isinstance(edits, list) else [])
        if not tuples:
            break  # 全被丢弃 = 零落地，交给兜底
        spliced = _splice_edits(best_text, tuples)
        if spliced == best_text:
            break  # 全被回滚/跳过 = 零落地，交给兜底
        after = detect(spliced)
        if after.score > best_score:
            log.info("定点去味第 %d 轮分数变差（%.1f → %.1f），保留上一版",
                     round_no + 1, best_score, after.score)
            break
        best_text, best_score = spliced, after.score
        targeted_changed = True
        emit("draft_progress", {"seq": doc.seq, "beat": 0, "chars": len(best_text),
                                "phase": "deaify"})

    # ② 兜底：仍超标，或**报味但定点没能落地**（此时只能整章来）
    if best_score > THRESHOLD or (report.serious and not targeted_changed):
        hits = detect(best_text)
        tier, temp, mt = WRITING_ROUTING["task.draft_deaify"]
        prompt = prompts.render("task.draft_deaify",
                                **build_deaify_vars(hits, best_text, style_guide=style_guide))
        text, use, reason = await stream_collected(
            llm, model=_model(provider, tier), temperature=temp, max_tokens=mt,
            system=_system(prompts), prompt=prompt,
            max_chars=max_chars_deaify(len(best_text)))
        if meter is not None:
            meter.add(use)   # usage 入账
        if reason:
            log.warning("整章去味调用失败，保留上一版")
        elif not text.strip():
            log.warning("整章去味返回空稿，保留上一版")
        elif not is_keep_whole(text, best_text):
            log.warning("整章去味稿疑似被截断（%d 字 < 上一版 %d 字的 70%%），保留上一版",
                        len(text), len(best_text))
        else:
            new_score = detect(text).score   # 一次求值复用（判劣化与采纳同一份）
            if new_score > best_score:
                log.info("整章去味分数变差（%.1f → %.1f），保留上一版",
                         best_score, new_score)
            else:
                # 整章重写 ⇒ 原来的拍区间作废（正文已不是逐拍拼出来的那版）
                best_text, best_score = text, new_score
                emit("draft_progress", {"seq": doc.seq, "beat": 0,
                                        "chars": len(best_text), "phase": "deaify"})

    if best_text != draft:
        doc.body = best_text
        doc.meta.pop("beat_spans", None)   # 任何去味改写都使逐拍区间失效
        return True
    return False

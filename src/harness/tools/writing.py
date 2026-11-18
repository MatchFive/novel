"""write_plot 写作工具主体（P3 写作链 Task 6：章循环 + 逐拍流式 + 审核 + 落盘快照）。

plot_id → 章集（本情节且 status != "done"）→ 逐章串行五节点：
① context_select（task.context_select，lite/0.2；失败 → selected={} + 观测注，不阻断）
② build_draft_context 组装（P4 Task 7 起传 mem/index：记忆五块 + 本卷 digest 前情提要
   注入、角色卡死亡过滤/last_seen 降权/现状行；语料索引 write_plot 开头一次全量
   （to_thread）+ 每章落盘/记忆提取后 path 级精确失效+重建 _refresh_corpus_paths，
   防上一章记忆提取后索引陈旧）→ 逐拍 stream_collected（WRITING_ROUTING 取模型/温度；
   拍预算 beat_word_budgets 摊分、max_chars=max_chars_beat；recent_draft=已写尾
   TAIL_BEAT；draft_progress 经 stream_collected on_text 节流上屏）→ 空拍跳过 →
   全空单发兜底（max_chars_single_shot）→ normalize_edges 收口 + 记 spans+sha。
   ①b 伏笔回指块（P4 Task 8）：章目标+beats 全文+context_select keywords →
   terms_from_texts → backrefs（仅 BM25/正文语料/top4/未来剔除/locate_quote 回验，
   引文逐字存在——Review Focus #5），ctx.user 尾部注入【伏笔回指】块。
③ review（task.draft_review，std/0.2，送**全章**——seed 旧「末尾 6000 字」口径导致
   长章开头从未被审，附录 C.2 已补钉）→ normalize_issues → apply_replace_issues
   （零 LLM 定点置换）→ detect()（report-only，AI 味分数进快照）→ 字数检查
   （<0.85× / >1.6× 记 length，只上报不门禁）→ blocking = type ∉ REPORT_ONLY_TYPES
   → pass_review。
④ 落盘：meta 全量合并（保 beats/covers 等键）+ status="done" +
   word_count=count_words(body) → write_chapter → 快照 <root>/.harness/review/
   <seq>.json（seed review_snapshot draft_workflow.py:464-474 同款键；at=UTC ISO）
   → 记忆提取（P4 挂点：extract_chapter_memory，LLM 在锁外、先清后建幂等；
   失败不阻断，观测注「记忆提取失败」）→ chapter_done{memory_status:
   "extracted"/"failed"}（提取返回值，裁定 7）。
⑤ blocking 非空 → revise_until_converged（T7 实现；函数内惰性 import，模块缺失时
   如实观测、不崩）→ 文本变更则重走 detect/落盘/快照 + chapter_revised 事件；
   **revise 重落盘后同样重跑记忆提取**（裁定 2：改写后自动重提取，复用同一调用点）；
⑥ deaify_if_needed 对**每章**统一执行（pass / revise 两路径——seed 状态图口径，
   审核通过的章照样去味；触发判定 report.serious 在 deaify 内部，不 serious 零
   LLM 调用）→ 改写成功则刷新 word_count、对终稿重跑 detect 后更新快照
   （deaified / flavor_score / flavor_items / at，防失真口径不回退）；
   **deaify 重落盘后同样重跑记忆提取**（裁定 2）。
⑦ 卷级 digest（P4 Task 6）：逐章循环里检测「本次完成后该 plot 无 status!=done 章」
   → DigestKeeper.maintain 增量维护 memory/digests/vol-N.md（先清后建幂等；超 2000
   字压最早旧节，压缩失败降级只追加不压缩——宁可冗长不可丢）；digest 是投影不是
   事实源，维护失败如实观测、不阻断章节成功。

失败不 raise（分类契约）：plot 未找到 → 「write_plot 失败（参数）：…」；无待写章 →
失败（无产出）；章内任何失败 → 该章不落盘、**后续章不动**，观测「第N章：未成功
（reason）」（刻意不匹配「失败（」）；LLM 层 reason（truncated/empty/api:{kind}）
先过 _REASON_LABELS 确定性映射（→ 无产出/调用失败），瞬时词绝不原样上屏；
整步 ok iff ≥1 章落库。LLM 调用全部在 store 锁外；
write_chapter 与快照写盘串行。

异步约定同 plan_chapters：注册 handler 同步签名经 base._run_sync 驱动；事件循环内
请直接 await write_plot(...)——emit 事件只在这条路径可达（registry 同步边界 emit
为 no-op）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC, datetime

from harness.config import Settings
from harness.llm.client import CompletionUsage, OpenAICompatClient
from harness.memory.digest import DigestKeeper
from harness.memory.extract import (
    REEXTRACT_FAIL_PREFIX,
    cast_roster,
    reextract_chapter,
    reextract_failed,
)
from harness.memory.store import MemoryStore
from harness.planning.pure import DEFAULT_TARGET_WORDS
from harness.prompts.loader import PromptLoader
from harness.reasons import REASON_LABELS as _REASON_LABELS  # noqa: F401 共享词表 re-export（裁定 R-A）：既有引用点/结构测试经 writing._REASON_LABELS 到达
from harness.reasons import reason_label as _reason_label
from harness.retrieval.bm25 import terms_from_texts
from harness.retrieval.corpus import (
    Index,
    build_corpus,
    chapter_file_items,
    memory_file_items,
)
from harness.run.approval import ApprovalCancelled, ask_ui_async
from harness.run.scope import read_brief_scope
from harness.store.project import ChapterDoc, ProjectStore
from harness.text import content_sha, count_words
from harness.tools.base import (
    EmitFn,
    ToolResult,
    UsageMeter,
    _run_sync,
    complete_structured,
    extract_json,
    stream_collected,
    usage_add,
)
from harness.tools.registry import ToolRegistry
from harness.tools.search import backrefs
from harness.writing.context import (
    build_draft_context,
    build_review_vars,
    catalog,
    chapter_memory_bundle,
    context_select_vars,
)
from harness.writing.flavor import FlavorReport, detect
from harness.writing.pure import (
    LENGTH_LONG_FACTOR,
    LENGTH_SHORT_FACTOR,
    REPORT_ONLY_TYPES,
    TAIL_BEAT,
    TAIL_PREV_CHAPTER,
    WRITING_ROUTING,
    apply_replace_issues,
    beat_word_budgets,
    max_chars_beat,
    max_chars_single_shot,
    normalize_edges,
    normalize_issues,
)

__all__ = ["register_writing_tools", "write_plot"]

log = logging.getLogger("tools.writing")

_PLOT_REF_RE = re.compile(r"^(\d+)-(\d+)$")

# LLM 层 reason → 观测确定性类别：_REASON_LABELS/_reason_label 已提升共享
# （src/harness/reasons.py，裁定 R-A），此处 re-export 私有别名保持既有引用点不动；
# 词表语义与分类契约见 harness.reasons 模块 docstring。


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


def _seq_arg(args: dict, key: str) -> int | None:
    """args.seq_from/seq_to：只认 int（bool/浮点/字符串一律视为缺省，不参与夹取）。"""
    value = args.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _target_of(meta: dict) -> int:
    """章目标字数：meta.target_words 优先（非法/缺省回退 DEFAULT_TARGET_WORDS=3000）。"""
    try:
        return max(1, int(meta.get("target_words")))
    except (TypeError, ValueError):
        return DEFAULT_TARGET_WORDS


def _tier_model(provider, tier: str) -> str:
    """WRITING_ROUTING 的 tier 属性名 → 模型名；tier 缺失回退 standard。"""
    return getattr(provider, tier, None) or provider.standard


def _safe_chapters(store: ProjectStore) -> list[ChapterDoc]:
    """全书章节；单个坏章（FileFormatError）经 on_error 收集后跳过，不拖垮列表。"""
    bad: list[tuple] = []
    docs = store.list_chapters(on_error=lambda p, e: bad.append((p, e)))
    for path, exc in bad:
        log.warning("tools.writing 跳过坏章: %s (%s)", path.name, exc)
    return docs


def _beats_of(meta: dict) -> list[dict]:
    """meta beats 的 dict 子集（「有细纲」= 非空 list；元素只留 dict）。"""
    raw = meta.get("beats")
    return [b for b in raw if isinstance(b, dict)] if isinstance(raw, list) else []


# ---------------------------------------------------------------- 记忆提取（P4 挂点）

async def _extract_memory(*, store: ProjectStore, mem: MemoryStore, roster: list[dict],
                          llm, prompts, settings: Settings, seq: int) -> ToolResult:
    """记忆提取调用点（初写/revise/deaify 三处复用）：失败不阻断章节成功。

    走统一入口 ``reextract_chapter``（以盘上正文为准重提取，先清后建幂等）。返回
    ToolResult：失败判定一律走 ``reextract_failed(result.text)``（单一判定源），
    绝不让裸 reason/堆栈穿透到观测层（分类契约同 _reason_label 铁律）。
    """
    try:
        return await reextract_chapter(
            store=store, mem=mem, llm=llm, prompts=prompts, settings=settings,
            seq=seq, cast_cards=roster)
    except Exception:  # 提取是章节成功后的增强步骤，任何失败如实降级
        log.warning("记忆提取异常（第%s章），已按失败处理", seq, exc_info=True)
        # 意外异常归「调用失败」：走 reason_label 映射（唯一权威词表），禁裸 reason 上屏
        return ToolResult(text=f"{REEXTRACT_FAIL_PREFIX}{_reason_label('api:error')}",
                          usage=None)


def _refresh_corpus_paths(store: ProjectStore, index: Index, seq: int) -> None:
    """path 级精确失效+重建：仅本章正文/章记忆两个文件的语料项（设计 §2.3）。

    替代逐章全量 build_corpus（trigram 分词 CPU 密集、阻塞事件循环——评审 Important-1）。
    章标题改名会换文件名——index 里旧 path 的残留项按 ``chap:<seq>`` id 前缀找全后一并
    剔除；记忆文件名固定 ``<seq:04d>.md``；文件被清（purge）时失效后无新项可挂。
    digest/state.md 不进语料（T3 口径），无需失效。
    """
    mem_rel = f"memory/{seq:04d}.md"
    mem_items = memory_file_items(store.root, seq)
    stale_mem = {it.path for it in index.items
                 if it.id.startswith(f"mem:{seq:04d}:")}
    for path in stale_mem | {mem_rel}:
        index.invalidate(path)
    if mem_items:
        index.add(mem_rel, mem_items)

    ch_items = chapter_file_items(store.root, seq)
    ch_paths = {it.path for it in ch_items} | {
        it.path for it in index.items if it.id.startswith(f"chap:{seq:04d}")}
    for path in ch_paths:
        index.invalidate(path)
        items = [it for it in ch_items if it.path == path]
        if items:
            index.add(path, items)


# ---------------------------------------------------------------- 卷级 digest（P4 Task 6 挂点）

def _plot_all_done(store: ProjectStore, ref: str) -> bool:
    """本次 run 完成后该 plot 已无 status!=done 章（逐章循环里逐次检测）。"""
    return not any(str(c.meta.get("plot_id") or "") == ref
                   and c.meta.get("status") != "done"
                   for c in _safe_chapters(store))


async def _maintain_digest(*, mem: MemoryStore, store: ProjectStore, llm,
                           prompts, settings: Settings, vol: int, ref: str,
                           plot_seq: int, plot_title: str,
                           meter: UsageMeter | None = None) -> str:
    """plot 全章 done 后调 DigestKeeper.maintain 增量维护本卷 digest。

    返回 "maintained"/"failed"（不 raise）：压缩失败在 keeper 内部已降级为「只追加
    不压缩」（宁可冗长不可丢），这里只兜文件层异常，观测行由调用方如实上报且不阻断。
    ``meter`` 可选：digest 压缩的 LLM 调用 usage 记进计量器（并入 write_plot 的
    total_usage）。
    """
    try:
        seqs = sorted(c.seq for c in _safe_chapters(store)
                      if str(c.meta.get("plot_id") or "") == ref)
        await DigestKeeper(mem).maintain(llm=llm, prompts=prompts, settings=settings,
                                         vol=vol, plot_seq=plot_seq,
                                         plot_title=plot_title, chapter_seqs=seqs,
                                         meter=meter)
        return "maintained"
    except Exception:   # digest 是投影：维护失败不得反噬章节成功（如实降级）
        log.warning("卷级前情提要维护失败（情节 %s），digest 未更新", ref,
                    exc_info=True)
        return "failed"


# ---------------------------------------------------------------- extract / 快照

def _extract_obj(text: str) -> object | None:
    """结构化输出的默认 extract：首个可解析 JSON 对象（结构校验交给调用方）。"""
    value = extract_json(text)
    return value if isinstance(value, dict) else None


def _selected_of(data: object) -> dict:
    """context_select 产物规整：world_titles/character_names/keywords 三个名单。

    keywords 是 context_select 模板输出格式的契约键（此前丢弃，P4 Task 8 起保留：
    回指通道查询词源之一）。其余键（need_web 等）忽略。
    """
    data = data if isinstance(data, dict) else {}

    def _str_list(key: str) -> list[str]:
        raw = data.get(key)
        return ([str(x).strip() for x in raw if str(x).strip()]
                if isinstance(raw, list) else [])

    return {"world_titles": _str_list("world_titles"),
            "character_names": _str_list("character_names"),
            "keywords": _str_list("keywords")}


def _beat_label(beat: dict, idx: int) -> str:
    """一拍的可读标签（提示词 target_beat 与观测用；seed _beat_label 同款口径）。"""
    parts = [str(beat.get(k) or "").strip() for k in ("pov", "location", "event")]
    return "｜".join(p for p in parts if p) or f"第{idx}拍"


def _flavor_items(report: FlavorReport, limit: int = 6) -> list[dict]:
    """AI 味命中 → 快照条目（seed review_snapshot 的 flavor_items[:6]）。"""
    return [{"rule": h.rule, "quote": h.quote, "weight": h.weight,
             "analysis": h.analysis, "fix": h.fix, "score_only": h.score_only,
             "start": h.start, "end": h.end} for h in report.hits[:limit]]


# P5 T10：现实参照块的选取上限（seed facts_block 口径）
_WEB_FACTS_TOP_K = 3
_WEB_FACTS_MAX_CHARS = 1200


def _web_facts_block(store: ProjectStore, doc: ChapterDoc, beats: list) -> str:
    """【现实参照】块：research.md 条目经 BM25 选 top-3（≤1200 字）。

    查询文本 = 章标题 + 各拍事件；比较文本 = query + conclusion。research.md 不存在
    或为空 → 空串（不渲染该块）。**纯 CPU（读盘 + 分词）——调用方须在 to_thread 内调**。
    """
    from harness.retrieval.bm25 import bm25_rank, terms_from_texts
    from harness.websearch import load_research

    entries = load_research(store.root)
    if not entries:
        return ""
    query_text = " ".join(
        [str(doc.meta.get("title") or "")]
        + [str(b.get("event") or "") for b in (beats or [])])
    # BM25 是**字符 trigram** 词面（retrieval/bm25.py）——中文查询里的空格会把三元组
    # 切碎（"内政 屯田" → "内政 " / "政 屯"，与无空格侧永不相交）。两侧一律去空格对齐。
    query_text = query_text.replace(" ", "").replace("　", "")
    terms = terms_from_texts([query_text])
    if not terms:
        return ""

    class _Item:
        """BM25 需要 .id（打分表用）——薄包装，不改 ResearchEntry 的形状。"""
        __slots__ = ("entry", "id")

        def __init__(self, idx: int, entry):
            self.id = idx
            self.entry = entry

    def _text_of(item) -> str:
        raw = f"{item.entry.query}{item.entry.conclusion}"
        return raw.replace(" ", "").replace("　", "")

    items = [_Item(i, e) for i, e in enumerate(entries)]
    ranked, _scores = bm25_rank(items, terms, _text_of, limit=_WEB_FACTS_TOP_K)
    if not ranked:
        return ""
    block = "\n".join(f"· {it.entry.query}\n{it.entry.conclusion}" for it in ranked)
    return block[:_WEB_FACTS_MAX_CHARS]


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _write_snapshot(store: ProjectStore, seq: int, snapshot: dict) -> None:
    """审核快照落盘 <root>/.harness/review/<seq>.json（seed review_snapshot 同款键）。"""
    path = store.root / ".harness" / "review" / f"{seq}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")


def _length_issue(short: bool, words: int, target: int) -> dict:
    """字数护栏问题（seed :1001-1018 语义；length ∈ REPORT_ONLY_TYPES，只上报不门禁）。"""
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


# ---------------------------------------------------------------- 单章五节点

async def _write_one_chapter(store: ProjectStore, llm: OpenAICompatClient, provider,
                             prompts: PromptLoader, settings: Settings, emit: EmitFn,
                             *, doc: ChapterDoc, plot: dict, beats: list[dict],
                             prev_tail: str, target: int, mem: MemoryStore,
                             roster: list[dict], index: Index, intent_block: str = "",
                             ) -> tuple[bool, str, CompletionUsage | None, str, str]:
    """单章五节点（策划/逐拍/审核/落盘/修正调用点）。

    返回 (ok, reason, usage, 观测行, body)。失败时 reason 为确定性分类串，观测行
    由调用方按「第N章：未成功（reason）」拼；body 仅供调用方记上一章尾部衔接。
    """
    seq = doc.seq
    title = str(doc.meta.get("title") or "")
    usage_all: CompletionUsage | None = None
    notes: list[str] = []
    # 子调用 usage 计量（P5 Task 4）：revise / deaify / 三处提取的 LLM 调用此前把
    # usage 丢掉——统一记进本 meter，函数返回前并入 usage_all。
    meter = UsageMeter()

    def _add(use: CompletionUsage | None) -> None:
        nonlocal usage_all
        usage_all = usage_add(usage_all, use)

    # ---------------- ① 上下文策划（lite/0.2；失败降级空注入 + 观测注，不阻断） ----------------
    tier, temp, mt = WRITING_ROUTING["task.context_select"]
    sprompt = prompts.render("task.context_select",
                             **context_select_vars(doc.meta, beats, catalog(store)))
    data, use, reason = await complete_structured(
        llm, model=_tier_model(provider, tier), temperature=temp, max_tokens=mt,
        prompt=sprompt, extract=_extract_obj)
    _add(use)
    selected = _selected_of(data)
    if data is None:
        notes.append(f"上下文策划失败（{_reason_label(reason or 'bad_json')}）"
                     "——按无额外注入继续（不阻断）")

    # ---------------- ①b 伏笔回指块（P4 Task 8）：章目标+beats 全文+keywords → 仅 BM25
    # 检索正文语料（裁定 4），逐命中经 locate_quote 在章正文回验（Review Focus #5），
    # 晚于本章的命中剔除（时间锚点纪律同 T7）。index=None（无索引）跳过。
    backref_block = ""
    if index is not None:
        bterms = terms_from_texts(
            [str(doc.meta.get("objective") or "")]
            + [str(b.get("event") or "") for b in beats]
            + list(selected.get("keywords") or []))
        # 同步 BM25（每章对全部正文语料重新分词）CPU 密集——挪出事件循环（终审 I-1a）
        bhits = await asyncio.to_thread(backrefs, index, bterms,
                                        top_k=4, cur_seq=seq)
        if bhits:
            backref_block = "\n".join(
                f"· 第{int(it.id.split(':', 1)[1])}章《{it.anchor}》：「{quote}」"
                for it, quote in bhits)

    def _inject_backrefs(ctx):
        """回指块注入三个 build_draft_context 调用点（draft/review 同一 ctx 复用）。"""
        if backref_block and backref_block not in ctx.user:
            block = f"【伏笔回指】\n{backref_block}"
            ctx.user = f"{ctx.user}\n\n{block}" if ctx.user else block
        return ctx

    # I-1b：bundle 每章只建一次、逐拍共享——拍循环内输入完全不变，逐拍重建会让
    # _gather+_promise_made_map 把全部章记忆各读两遍（O(N²) 文件读，终审实测 ≈31s）。
    chapter_bundle = (chapter_memory_bundle(doc, beats, selected, mem, index)
                      if mem is not None else None)

    # P5 T10：现实参照块（research.md → BM25 选 top-3）——**每章算一次**（不是每拍），
    # 且读盘 + 分词是 CPU 密集 → 挪出事件循环（P4 I-1 纪律，同 backrefs 一款）。
    web_facts = await asyncio.to_thread(_web_facts_block, store, doc, beats)

    # ---------------- ② 逐拍流式写作（draft_progress 节流上屏；空拍跳过） ----------------
    budgets = beat_word_budgets(target, beats)
    tier, temp, mt = WRITING_ROUTING["task.draft_continue"]
    model = _tier_model(provider, tier)
    text = ""
    spans: list[list[int]] = []
    progress = {"beat": 0}

    def _on_text(acc: str) -> None:
        emit("draft_progress", {"seq": seq, "beat": progress["beat"],
                                "chars": len(acc), "phase": "draft"})

    ctx = None
    for i, beat in enumerate(beats, 1):
        ctx = _inject_backrefs(build_draft_context(
            store, doc, plot, beats, prev_tail, selected,
            target_beat=_beat_label(beat, i),
            recent_draft=text[-TAIL_BEAT:],
            mem=mem, index=index, bundle=chapter_bundle, web_facts=web_facts,
            intent_block=intent_block))
        progress["beat"] = i
        budget = budgets[i - 1] if i - 1 < len(budgets) else target
        beat_text, use, reason = await stream_collected(
            llm, model=model, temperature=temp, max_tokens=mt,
            system=ctx.system, prompt=ctx.user,
            max_chars=max_chars_beat(budget), on_text=_on_text)
        _add(use)
        if reason:  # api/timeout 等确定性分类：该章未成功，不重试不兜底
            return False, reason, usage_all, "", ""
        if not beat_text.strip():
            continue
        start = len(text)
        text += beat_text
        spans.append([start, len(text)])
    if not text.strip():
        # 一拍都没写出来：整章单发兜底（seed _write_draft 同款退化路径）
        ctx = _inject_backrefs(build_draft_context(
            store, doc, plot, beats, prev_tail, selected,
            mem=mem, index=index, bundle=chapter_bundle, web_facts=web_facts,
            intent_block=intent_block))
        progress["beat"] = 0
        text, use, reason = await stream_collected(
            llm, model=model, temperature=temp, max_tokens=mt,
            system=ctx.system, prompt=ctx.user,
            max_chars=max_chars_single_shot(target), on_text=_on_text)
        _add(use)
        if reason:
            return False, reason, usage_all, "", ""
        if not text.strip():
            return False, "empty", usage_all, "", ""
        spans = []
    body, spans = normalize_edges(text, spans)   # 首尾空白收口 + 区间平移（宁缺毋滥）

    # ---------------- ③ 审核（std/0.2，送全章）→ 定点置换 → detect → 字数护栏 ----------------
    ctx = ctx or _inject_backrefs(build_draft_context(
        store, doc, plot, beats, prev_tail, selected,
        mem=mem, index=index, bundle=chapter_bundle, web_facts=web_facts,
            intent_block=intent_block))
    tier, temp, mt = WRITING_ROUTING["task.draft_review"]
    rprompt = prompts.render("task.draft_review",
                             **build_review_vars(ctx.materials, seq, body))
    rdata, use, reason = await complete_structured(
        llm, model=_tier_model(provider, tier), temperature=temp, max_tokens=mt,
        prompt=rprompt, extract=_extract_obj)
    _add(use)
    if rdata is None:
        notes.append(f"审核未完成（{_reason_label(reason or 'bad_json')}）"
                     "——按无逻辑问题处理（不阻断）")
    llm_issues = normalize_issues((rdata or {}).get("issues"))
    # 局部可替换的小问题零 LLM 就地置换（引文定位不到的意见原样剩给「未落实」）
    body, spans, issues = apply_replace_issues(body, spans, llm_issues)
    # 确定性 AI 味检测对全篇补齐（模型对自身套路有盲区）：report-only，不参与门禁
    report = detect(body)
    words = count_words(body)
    if words < int(target * LENGTH_SHORT_FACTOR):
        issues = list(issues) + [_length_issue(True, words, target)]
    elif words > int(target * LENGTH_LONG_FACTOR):
        issues = list(issues) + [_length_issue(False, words, target)]
    # 降门槛：只有逻辑类问题算「没过」；ai_flavor/length 只上报（REPORT_ONLY_TYPES）
    blocking = [i for i in issues if i.get("type") not in REPORT_ONLY_TYPES]
    pass_review = not blocking

    # ---------------- ④ 落盘：meta 全量合并 + write_chapter + 快照 + 记忆提取 + chapter_done ----------------
    meta = dict(doc.meta)
    # **先写「writing」，全流程跑完才置「done」**（2026-11-15 独立审核 L2）：
    # 原先第一次落盘就写 status="done"，而 ⑤ 修正 / ⑥ 去味 还没跑 →
    # 中途崩溃（Ctrl+C / 进程被杀）会留下**半成品**（未修正、未去味）却标着 done →
    # 而幂等门是「status=done 的章不重演」→ **这一章永远不会被再处理**。
    # 改成中间态后：崩溃留下 status="writing" → 下次 run **会捡起来重做** ✓
    # （且 done_chapters 只认 done → 半成品不会被验收抽样当定稿章）。
    meta["status"] = "writing"
    meta["word_count"] = words
    if spans:
        meta["beat_spans"] = {"sha": content_sha(body), "spans": spans}
    else:
        meta.pop("beat_spans", None)
    doc.meta = meta
    doc.body = body
    store.write_chapter(doc)
    snapshot = {"pass_review": pass_review, "issues": issues, "revise_count": 0,
                "deaified": False, "flavor_score": report.score,
                "flavor_items": _flavor_items(report), "at": _utcnow()}
    _write_snapshot(store, seq, snapshot)
    # 统一提取入口：判定/状态映射单一源（reextract_failed）——memory_status 事件值、
    # notes 追加条件、chapter_done 载荷三处共用这一个判定
    result = await _extract_memory(store=store, mem=mem, roster=roster, llm=llm,
                                   prompts=prompts, settings=settings, seq=seq)
    meter.add(result.usage)
    memory_status = "failed" if reextract_failed(result.text) else "extracted"
    if memory_status == "failed":
        notes.append("记忆提取失败")   # 不阻断：章已落盘，reason 已在提取层过映射表进日志
    emit("chapter_done", {"seq": seq, "title": title, "words": words,
                          "pass_review": pass_review, "flavor_score": report.score,
                          "memory_status": memory_status})

    # ---------------- ⑤ 修正调用点（T7 实现 revise.py；本任务只接调用点） ----------------
    revise_mod = None
    try:
        from harness.writing import revise as revise_mod
    except ImportError:
        # revise.py 缺失：如实上报未处理问题，不崩（直通路径不受影响）
        if blocking:
            notes.append("修正链未就绪（harness.writing.revise 缺失）"
                         f"——未处理 {len(blocking)} 条如实留在快照")
    if revise_mod is not None and blocking:
        # 调用签名以 Task 7 落地为准：返回 (新正文, 新区间, revise_count,
        # 复审后 blocking, 复审后 issues)。
        new_body, new_spans, revise_count, re_blocking, re_issues = \
            await revise_mod.revise_until_converged(
                store=store, llm=llm, prompts=prompts, settings=settings,
                emit=emit, doc=doc, materials=ctx.materials, body=body,
                spans=spans, blocking=blocking, issues=issues,
                target_words=target, meter=meter)
        if new_body != body:  # 文本变更 → 重走 detect/落盘/快照 + chapter_revised
            body = new_body
            report = detect(body)
            words = count_words(body)
            meta["word_count"] = words
            if new_spans:
                meta["beat_spans"] = {"sha": content_sha(body), "spans": new_spans}
            else:
                meta.pop("beat_spans", None)
            doc.meta = meta
            doc.body = body
            store.write_chapter(doc)
            snapshot.update(pass_review=not re_blocking, issues=re_issues,
                            revise_count=revise_count, flavor_score=report.score,
                            flavor_items=_flavor_items(report), at=_utcnow())
            _write_snapshot(store, seq, snapshot)
            emit("chapter_revised",
                 {"seq": seq, "revise_count": revise_count, "words": words,
                  "pass_review": not re_blocking, "flavor_score": report.score})
            # 裁定 2：改写后自动重提取（先清后建幂等，复用同一调用点）
            result = await _extract_memory(store=store, mem=mem, roster=roster, llm=llm,
                                           prompts=prompts, settings=settings, seq=seq)
            meter.add(result.usage)
            if reextract_failed(result.text):
                notes.append("记忆提取失败")
            # 复审结论翻正 verdict 口径（与落库快照同）：末行如实显示「通过」
            pass_review, blocking = not re_blocking, re_blocking

    # ---------------- ⑥ deaify 调用点：pass / revise 两路径统一执行 ----------------
    # seed 状态图 deaify 对每章都跑（审核通过的章照样去味）；触发判定（report.serious）
    # 在 deaify_if_needed 内部——不 serious 时零 LLM 调用原样返回，调用点无需自判。
    if revise_mod is not None:
        # **双信号**（设计 §1.4）：确定性超标（report.serious）**或**审核判出 ai_flavor
        # → 都走去味。审核的 ai_flavor 引文一并传下去（它们会被定位进正文、并入定点名单）
        # ——P3 只用前者的理由（「review 的 ai_flavor 意见在调用点不可得」）已随 P4 失效
        # （独立审核 L1）。
        flavor_quotes = [str(i.get("quote") or "") for i in issues
                         if i.get("type") == "ai_flavor"]
        deaified = await revise_mod.deaify_if_needed(
            store=store, llm=llm, prompts=prompts, emit=emit, doc=doc, report=report,
            flavor_quotes=flavor_quotes, meter=meter)
        if deaified:
            body = doc.body                    # M-7：deaify 原地改写——末行/返回值用新正文
            words = count_words(body)          # 去味后字数（meta 与逐章行同口径刷新）
            meta["word_count"] = words
            store.write_chapter(doc)           # deaify 已就地作废 beat_spans
            # 快照声称已去味：分数/条目必须是对终稿重跑 detect 的值——
            # 用 deaify 前旧 report 会持久化失真分数（P5 消费方被误导）。
            report = detect(body)
            snapshot.update(deaified=True, flavor_score=report.score,
                            flavor_items=_flavor_items(report), at=_utcnow())
            _write_snapshot(store, seq, snapshot)
            # 裁定 2：改写后自动重提取（先清后建幂等，复用同一调用点）
            result = await _extract_memory(store=store, mem=mem, roster=roster, llm=llm,
                                           prompts=prompts, settings=settings, seq=seq)
            meter.add(result.usage)
            if reextract_failed(result.text):
                notes.append("记忆提取失败")
    elif report.serious:
        notes.append("去味链未就绪（harness.writing.revise 缺失）——AI 味超标未处理")

    # ---------------- ⑦ 定稿：全部节点跑完才把 status 置 "done" ----------------
    # 中间态是 "writing"（L2）：中途崩溃 → 留在 "writing" → 下次 run 会重做这一章，
    # 而不是留下一份标着 done 的半成品被幂等门永远跳过。
    if meta.get("status") != "done":
        meta["status"] = "done"
        meta["word_count"] = words
        doc.meta = meta
        doc.body = body
        store.write_chapter(doc)

    verdict = "通过" if pass_review else f"未通过（{len(blocking)} 条待处理）"
    line = f"第{seq}章「{title}」：{words} 字，审核{verdict}"
    if notes:
        line += "（" + "；".join(notes) + "）"
    # 子调用 usage 计量并入本章累计（revise/deaify/三处提取）。早退路径（逐拍失败/
    # 单发失败/空稿）发生在任何计量调用之前，meter.total 必为 None，故只在正常
    # 出口并入。
    usage_all = usage_add(usage_all, meter.total)
    # 章落盘+记忆提取（含 revise/deaify 重提取）均已落定 → 索引按 path 精确失效
    # 本章的两个文件（正文+章记忆），下一章注入看到的是本章最终记忆。
    _refresh_corpus_paths(store, index, seq)
    return True, "", usage_all, line, body


# ---------------------------------------------------------------- 主流程

async def _confirm_out_of_range(ui, vol: int, lo_vol: int, hi_vol: int,
                                write_target: str) -> bool:
    """范围外写入的人确认门（P25/A2，ask_ui_async 三端复用：终端卡 / pending.md / web）。

    返回 True = 用户放行这一次；False = 拒绝 / 取消 / ``ui=None``（非交互上下文
    不征求、维持 P12 原拦截）。选项固定两项、单选。
    """
    if ui is None:
        return False
    options = [f"放行：允许写第 {vol} 卷（这一次）", "拒绝：维持范围拦截"]
    question = (f"write_plot 申请写第 {vol} 卷，不在本次写作范围"
                f"（第 {lo_vol}–{hi_vol} 卷；依据需求解析的「{write_target}」）内。"
                f"是否放行这一次？")
    try:
        choice = await ask_ui_async(ui, question, options, multi=False)
    except ApprovalCancelled:
        return False
    return 0 in choice.indices


async def write_plot(store: ProjectStore, llm: OpenAICompatClient, prompts: PromptLoader,
                     settings: Settings, emit: EmitFn, args: dict, ui=None) -> ToolResult:
    """逐章串行写作（幂等：status=done 的章不重演）：见模块 docstring。失败观测不 raise。

    ``ui``（P25/A2）：写作范围闸的**人确认门**——范围外时征求「放行这一次 / 维持
    拦截」；``ui=None``（非交互上下文）不征求、直接维持拦截（P12 原行为）。
    """
    parsed = _parse_plot_ref(args)
    if parsed is None:
        return ToolResult(
            text=f"write_plot 失败（参数）：args.plot_id 缺参或格式非法——须为 \"{{vol}}-{{seq}}\""
                 f"（如 \"1-2\"，卷号/情节号均 ≥1 的整数），当前为 {args.get('plot_id')!r}",
            usage=None)
    vol, plot_seq = parsed
    ref = f"{vol}-{plot_seq}"

    plots = store.read_plots(vol)
    plot = next((p for p in plots if p.get("seq") == plot_seq), None)
    if plot is None:
        known = "、".join(str(p.get("seq")) for p in plots) or "（无）"
        return ToolResult(text=f"write_plot 失败（参数）：未找到情节 {ref}"
                              f"（第 {vol} 卷现有情节 seq：{known}）", usage=None)
    # **写作范围闸**（P12；P25/A2 补人确认门）：brief 的 write_target 解析出的**卷区间**
    # → 范围外的卷**默认不写**（零写盘、零 LLM 调用）。对齐 seed「员工不会写到范围之外」。
    # 解析不出确定区间 → **不限制**（fail-open：用户要写却写不了，比拦错更糟）。
    # A2：范围外 → 有 ui 先征求用户（放行这一次 / 维持拦截；取消视同拒绝）——seed
    # 口径本来就是「提示并让人确认」，P12 期的「直接拒绝」是无审批基础设施时的临时
    # 取舍；ui=None（非交互上下文）不征求、维持拦截（原行为）。
    scope = await asyncio.to_thread(read_brief_scope, store)
    write_range = scope.write_range
    if write_range is not None:
        lo_vol, hi_vol = write_range
        if not (lo_vol <= vol <= hi_vol):
            if await _confirm_out_of_range(ui, vol, lo_vol, hi_vol,
                                           str(scope.write_target or "")):
                pass   # 用户放行 → 落穿闸，后续流程不变
            else:
                # **NOPROGRESS 标记**（独立复审 M3）：范围闸 0 条落库 → 观测**不得**被判 `ok`
                # （ok 会计有效步、清 noop → agent 反复试范围外的卷，一路烧到 MAX_STEPS=60
                #  却不触发 noop 暂停）。
                # **不能**写成「失败（…）」——那会被判 failed → 进 P13 的**按工具分桶冷却**
                # → 连**合法的卷**也调不了（独立复审 X1 澄清：那条挂死路径不存在，
                #   但改成 failed 就会把它造出来）。→ 用 loop 认得的 **noop** 语义。
                return ToolResult(
                    text=(f"NOPROGRESS: write_plot 未执行：第 {vol} 卷不在本次写作范围内"
                          f"（本次范围：第 {lo_vol}–{hi_vol} 卷；"
                          f"依据需求解析的「{scope.write_target}」）"),
                    usage=None)

    provider = settings.providers.get(settings.default_provider)
    if provider is None:
        known = "、".join(sorted(settings.providers)) or "（空）"
        return ToolResult(
            text=f"write_plot 失败（配置）：default_provider={settings.default_provider!r}"
                 f" 未配置（providers：{known}）", usage=None)


    # **本次意图与范围块**（P12）：brief 的目标/前提/限制/正文范围 → 写作链的 core 段。
    # 用 ``for_writing=True``：不含「只规划前 N 卷」（那是规划链的范围，对写作是噪音）。
    intent_block = scope.block(for_writing=True)

    # ---------------- 章集：plot_id 匹配且 status != done；seq 范围取交集 + 越界夹取 ----------------
    chapters = [c for c in _safe_chapters(store)
                if str(c.meta.get("plot_id") or "") == ref
                and c.meta.get("status") != "done"]
    chapters.sort(key=lambda c: c.seq)
    note = ""
    if chapters:
        lo, hi = chapters[0].seq, chapters[-1].seq
        seq_from, seq_to = _seq_arg(args, "seq_from"), _seq_arg(args, "seq_to")
        eff_from = max(seq_from, lo) if seq_from is not None else lo
        eff_to = min(seq_to, hi) if seq_to is not None else hi
        # 越界含两种：夹取（from<lo / to>hi）与「全在范围外」（from>hi / to<lo）——
        # 后者交集必为空，note 同样要产生（M-5）。
        if ((seq_from is not None and not lo <= seq_from <= hi)
                or (seq_to is not None and not lo <= seq_to <= hi)):
            req = f"seq {seq_from if seq_from is not None else ''}~" \
                  f"{seq_to if seq_to is not None else ''}"
            note = (f"写作范围 {req} 越界，已夹取为 {eff_from}~{eff_to}"
                    f"（本情节待写章 {lo}~{hi}）")
        chapters = [c for c in chapters if eff_from <= c.seq <= eff_to]
    if not chapters:
        text = (f"write_plot 失败（无产出）：情节 {ref} 没有待写章节"
                f"（章节不存在或已全部 status=done）")
        if note:  # 交集为空时夹取说明不得丢：拼进失败行如实上报（M-5）
            text = f"{note}——与待写章无交集\n{text}"
        return ToolResult(text=text, usage=None)

    # **章粒度范围闸**（P27/A1）：brief 的章区间（**全书全局章号**，章文件
    # chapters/{seq:04d}-*.md 扁平命名 → 无需章→卷映射）→ 待写章集按 seq 过滤：
    # 范围外的章**逐章跳过**（skip 行，同写作闸口径）；全越界 → NOPROGRESS
    # （0 产出的 noop 语义，同 P12 卷闸：不得判 ok，也不能判 failed 进冷却）。
    scope_lines: list[str] = []
    chap_range = scope.chapter_range
    if chap_range is not None and chapters:
        lo_c, hi_c = chap_range
        out_docs = [c for c in chapters if not lo_c <= c.seq <= hi_c]
        if out_docs:
            for doc in out_docs:
                scope_lines.append(
                    f"第{doc.seq}章：不在本次写作范围（第 {lo_c}–{hi_c} 章）内，跳过")
            chapters = [c for c in chapters if lo_c <= c.seq <= hi_c]
            if not chapters:
                text = (f"NOPROGRESS: write_plot 未执行：情节 {ref} 待写的 "
                        f"{len(out_docs)} 章全部不在本次写作范围"
                        f"（第 {lo_c}–{hi_c} 章；依据需求解析的「{scope.write_target}」）内")
                if note:  # 夹取说明不得丢（M-5 同款）：拼在 NOPROGRESS 之前如实上报
                    text = f"{note}\n{text}"
                return ToolResult(text=f"{text}\n" + "\n".join(scope_lines), usage=None)

    # **写作闸**（spec §4.7 / X3）：本情节待写章里用到「无卡主要角色」的章 → 按章硬拦。
    # 判定唯一源 = harness.characters（Task 3：分层/阈值/集合名词/alias 口径全在那里）；
    # 本工具不得再实现一套。闸 = 纯文件读（量小，不上 to_thread）；被拦章零 LLM、零写盘。
    # 部分被拦不整步拒绝（写其余章 + 逐章 skip 行）；全拦 → NOPROGRESS（loop noop 语义
    # 同 P12 范围闸：0 条落库不得判 ok，也不能判 failed——那会被按工具分桶冷却连累合法情节）。
    gate_lines: list[str] = []
    blocked_n = 0
    # 惰性 import（断 characters ↔ tools 模块级循环：characters → tools.base →
    # tools.__init__ → writing → characters；仅写作闸路径使用，零行为变化）。
    from harness.characters import (
        blocked_by_chapter,
        carded_names,
        collect_candidates,
    )

    analysis = collect_candidates(store)
    carded = carded_names(store)
    missing = analysis.uncarded_major(carded)
    if missing:
        blocked = blocked_by_chapter(store, analysis)
        blocked_docs = [c for c in chapters if c.seq in blocked]
        if blocked_docs:
            member_of = {m.name: m for m in missing}
            role_of = {m.name: "主角" for m in analysis.protagonists}
            role_of.update({m.name: "重要配角" for m in analysis.candidates})
            for doc in blocked_docs:
                names = blocked[doc.seq]
                gate_lines.append(
                    f"第{doc.seq}章：待卡（{'、'.join(names)} 无角色卡）——去角色视图建卡")
            chapters = [c for c in chapters if c.seq not in blocked]
            if not chapters:
                first = blocked_docs[0]
                detail = " ".join(
                    f"{n}（{role_of.get(n, '主要角色')}，{member_of[n].chapters}章"
                    f"/{member_of[n].beats}拍，首次出场第{member_of[n].first_chapter:04d}章）"
                    for n in blocked[first.seq])
                text = (f"NOPROGRESS: write_plot 未执行：第{first.seq}章有 "
                        f"{len(blocked[first.seq])} 个角色还没有角色卡"
                        f"——他们会出现在第{first.seq}章"
                        f"「{first.meta.get('title') or ''!s}」，但还没有基础设定。"
                        f"按你的规矩：戏份多的角色要先建卡再写正文。 {detail}")
                if note:  # 夹取说明不得丢（M-5 同「无产出」路径）：拼在 NOPROGRESS 之前如实上报
                    text = f"{note}\n{text}"
                return ToolResult(text=f"{text}\n" + "\n".join(gate_lines), usage=None)
            blocked_n = len(blocked_docs)   # 部分被拦：首行如实报拦下章数

    plot_title = str(plot.get("title") or f"情节{plot_seq}")
    total_usage: CompletionUsage | None = None
    lines: list[str] = [*scope_lines, *gate_lines]   # 范围/闸拦 skip 行在前，逐章成功行随后
    done = failed = skipped = 0
    # 记忆提取挂点用料：MemoryStore（章记忆/现状投影唯一写盘方）+ 全书角色名册
    # （名册组装单一权威源 = harness.memory.extract.cast_roster；一次构建、全章复用）
    mem = MemoryStore(store.root)
    roster = cast_roster(store)
    # 上一章尾部衔接（TAIL_PREV_CHAPTER）：已落库章的 body + 本步刚写完的章
    bodies: dict[int, str] = {c.seq: c.body for c in _safe_chapters(store)}
    # P4 注入端用料：全书语料索引（相关历史事件检索用 mem: 语料）。全量 build 只此
    # 一次且走 asyncio.to_thread（trigram 分词 CPU 密集，别堵事件循环——评审 Important-1）；
    # 之后逐章 path 级精确失效+重建（_refresh_corpus_paths，设计 §2.3）。
    index = await asyncio.to_thread(lambda: Index(build_corpus(store.root)))

    for doc in chapters:
        beats = _beats_of(doc.meta)
        if not beats:
            skipped += 1
            lines.append(f"第{doc.seq}章：无细纲，跳过")
            continue
        prevs = [bodies[s] for s in bodies if s < doc.seq and bodies[s].strip()]
        prev_tail = prevs[-1][-TAIL_PREV_CHAPTER:] if prevs else ""
        target = _target_of(doc.meta)
        emit("chapter_start", {"seq": doc.seq,
                               "title": str(doc.meta.get("title") or ""),
                               "target_words": target})
        ok, reason, usage, line, body = await _write_one_chapter(
            store, llm, provider, prompts, settings, emit,
            doc=doc, plot=plot, beats=beats, prev_tail=prev_tail, target=target,
            mem=mem, roster=roster, index=index, intent_block=intent_block)
        total_usage = usage_add(total_usage, usage)
        if not ok:
            failed += 1
            lines.append(f"第{doc.seq}章：未成功（{_reason_label(reason)}）")  # 后续章不动（break）
            break
        done += 1
        bodies[doc.seq] = body
        lines.append(line)
        # ---- P4 Task 6 挂点：本次完成后该 plot 全章 done → 卷级 digest 增量维护 ----
        if _plot_all_done(store, ref):
            digest_meter = UsageMeter()   # digest 压缩的 LLM 用量单独计量
            digest_status = await _maintain_digest(
                mem=mem, store=store, llm=llm, prompts=prompts, settings=settings,
                vol=vol, ref=ref, plot_seq=plot_seq, plot_title=plot_title,
                meter=digest_meter)
            total_usage = usage_add(total_usage, digest_meter.total)
            if digest_status == "failed":
                lines.append(f"卷级前情提要维护失败（情节 {ref}）"
                             "——digest 未更新（不阻断）")

    head = (f"write_plot：情节「{plot_title}」({ref}) 写作完成——成功 {done} 章"
            + (f" / 失败 {failed} 章" if failed else "")
            + (f" / 拦下 {blocked_n} 章（角色待卡）" if blocked_n else "")
            + (f" / 跳过 {skipped} 章（无细纲）" if skipped else ""))
    if done == 0:
        head = (f"write_plot 失败（无产出）：情节「{plot_title}」({ref}) "
                f"未成功写出任何章节（跳过 {skipped} 章无细纲）")
    return ToolResult(text="\n".join(([note] if note else []) + [head] + lines),
                      usage=total_usage)


def register_writing_tools(reg: ToolRegistry, *, store: ProjectStore, llm: OpenAICompatClient,
                           prompts: PromptLoader, settings: Settings, ui=None) -> None:
    """注册 write_plot（read_only=False）：写作写盘工具，白名单外/并行批均由 registry 拒绝。

    ``ui`` 透传范围闸确认门（P25/A2）；loop 的 ``_async_tool`` 按名字永远走 await
    分支（本 handler 是同步边界的防御性注册）。
    """

    def handler(store_: ProjectStore, args: dict) -> str:
        return _run_sync(write_plot(store_, llm, prompts, settings, _noop_emit, args,
                                    ui=ui)).text

    reg.register("write_plot", handler, read_only=False)

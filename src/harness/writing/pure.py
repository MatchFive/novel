# 来源：语义逐字移植自 web_dream（d:/project/web_dream/backend/app/workflows/draft_workflow.py，
# 行号锚点见 task-1-brief.md；标注「seed N-M 逐字」的函数判据/公式/常量值不改）
"""写作链纯函数层（P3 Task 1）。

拍预算 / span 手术 / issue 规范化 / 定位放宽 / 出口收口等纯函数去掉
LLM/DB/流式耦合后收口于此，供后续任务拼提示词与工具。**全部纯函数，无 I/O**；
`locate_quote`/`strip_edges`/`collapse_repeats`/`as_list`/`as_text` 已在 harness.text
逐字复制，直接复用。
"""
from __future__ import annotations

from harness.text import as_list, as_text, collapse_repeats, locate_quote, strip_edges

__all__ = [
    "BEAT_MIN_WORDS",
    "BUDGET_CHARS",
    "DEAIFY_ROUNDS",
    "EDIT_LEN_FACTOR",
    "EDIT_LEN_PAD",
    "FULL_REWRITE_TYPES",
    "KEEP_BEAT_FACTOR",
    "KEEP_WHOLE_FACTOR",
    "LENGTH_LONG_FACTOR",
    "LENGTH_SHORT_FACTOR",
    "MAX_BEAT_REWRITES",
    "MAX_REVISE",
    "REPORT_ONLY_TYPES",
    "STREAM_CHECK_INTERVAL",
    "TAIL_BEAT",
    "TAIL_PREV_CHAPTER",
    "WRITING_ROUTING",
    "adjust_spans",
    "apply_replace_issues",
    "apply_spans",
    "beat_index_at",
    "beat_word_budgets",
    "is_keep_whole",
    "locate_for_target",
    "max_chars_beat",
    "max_chars_deaify",
    "max_chars_rewrite_beat",
    "max_chars_single_shot",
    "normalize_edges",
    "normalize_issues",
    "splice_with_spans",
]

# 审核不通过时最多修正几轮（seed 130）。原取值 2，实测「修一轮反而修坏」的章没有
# 二次机会（见 `_route_after_review`），所以放宽到 4——让「越修越好」的章能继续收敛，
# 同时兜住极端情况（防死循环）。
MAX_REVISE = 4
# 一轮里最多重写几拍（按拍修复比整章便宜，但仍要封顶）（seed 132）。
MAX_BEAT_REWRITES = 3
# **只上报、不门禁**的问题类型：不进「要不要改」的判断，也不占改写轮次，而是如实落
# review_json 交给用户自行判断（seed 139）。
#   · ai_flavor（文字层的 AI 味）：已有确定性检测 + 定点去味单独收口；
#   · length（字数不足）：多半是细纲内容不够，正解是补细纲/重拆章，**不靠注水凑字数**。
REPORT_ONLY_TYPES = frozenset({"ai_flavor", "length"})
# 只有这些**结构性**问题允许「整章重写」兜底（跨拍/与细纲整体不符，按拍改不了）。
# 其它问题定位不到节拍时不再整章重写——整章重写会洗牌全文、审核问题反而越修越多
# （真实事故：用户点「再修一轮」6 条 → 8 条），改为**如实上报**未落实的意见（seed 143）。
FULL_REWRITE_TYPES = frozenset({"outline"})
# 去味最多改写轮数（每轮复检、择优不劣化）（seed 144）。
DEAIFY_ROUNDS = 2

# 逐拍字数下限：单拍至少给这么多（防「拍很多、每拍被摊成几十字」把模型逼成概述）（seed 153）。
BEAT_MIN_WORDS = 300

# 跨章上下文尾部：上一章末尾带多少字（P3 写作链消费口径）。
TAIL_PREV_CHAPTER = 2000
# 本拍参考上下文：已写正文的尾部带多少字（seed 804 的 written[-1500:]）。
TAIL_BEAT = 1500
# 单拍写作预算上限（防流式失控的硬顶）。
BUDGET_CHARS = 40000

# 审核长度护栏：当前字数 < 目标 × 0.85 记「偏短」；> 目标 × 1.6 记「偏长」（seed 1001/1009）。
LENGTH_SHORT_FACTOR = 0.85
LENGTH_LONG_FACTOR = 1.6
# 截断防护：整章路径的新稿 < 旧稿 × 0.7 大概率是 max_tokens 截断残稿，保留旧稿
# （seed 1198/1330）。
KEEP_WHOLE_FACTOR = 0.7
# 按拍重写的新拍 < 原拍 × 0.5 视为截断残稿，弃用（seed 1137）。
KEEP_BEAT_FACTOR = 0.5
# 定点去味的长度护栏：replacement 明显长于原文（> 1.8× 且多 40 字以上）时丢弃该条（seed 343）。
EDIT_LEN_FACTOR = 1.8
EDIT_LEN_PAD = 40
# 流式输出每累积 512 字做一次重复循环检测（seed 742/754）。
STREAM_CHECK_INTERVAL = 512


def beat_word_budgets(target_words: int, beats: list[dict]) -> list[int]:
    """把章目标字数**按拍权重**摊分到每一拍（缺省权重=1 ⇒ 与原「等分」完全一致）。

    权重取 `weight`，没有就看 `expected_words`；两者都缺省视为 1。**只看相对比例**，
    所以模型把每拍标 800/800/… 与 1/1/… 完全等价——它不必会估算绝对字数，只需判断
    「哪一拍内容多」（高潮/名场面给大值、过渡给小值），分配比例就不会跑偏。
    这也顺带避开「模型字数自估不准」这个老毛病：估算值只当权重用，不直接当预算。
    每拍下限 `BEAT_MIN_WORDS`（seed 156-176 逐字，floor 收为模块常量）。
    """
    weights: list[float] = []
    for b in beats:
        raw = b.get("weight") if b.get("weight") is not None else b.get("expected_words")
        try:
            w = float(raw)
        except (TypeError, ValueError):
            w = 0.0
        weights.append(w if w > 0 else 1.0)
    total_w = sum(weights)
    if not weights or total_w <= 0:
        return []
    return [max(BEAT_MIN_WORDS, round(target_words * w / total_w)) for w in weights]


def apply_spans(text: str, spans: list[tuple[int, int, str]]) -> list[str]:
    """把若干 `(start, end, 替换文本)` 就地拼进正文，返回**结果分段列表**。

    分段按正文顺序排列，未触碰块与替换文本交替（`[前文, 替换1, 中段, 替换2, 后文]`），
    便于调用方按需重组/上屏。**从后往前**判定（前面偏移不失效，seed 179-196 同款判据）；
    与已替换区间重叠或越界的 edit 跳过。一条都没换成返回 `[text]`。
    """
    if not spans:
        return [text]
    accepted: list[tuple[int, int, str]] = []
    last_start = len(text) + 1
    for start, end, repl in sorted(spans, key=lambda s: s[0], reverse=True):
        if not (0 <= start < end <= len(text)) or end > last_start:
            continue
        accepted.append((start, end, repl))
        last_start = start
    if not accepted:
        return [text]
    out: list[str] = []
    cursor = 0
    for start, end, repl in reversed(accepted):  # 转回正文正序拼段
        if start > cursor:
            out.append(text[cursor:start])
        if repl:
            out.append(repl)
        cursor = end
    if cursor < len(text):
        out.append(text[cursor:])
    return out


def adjust_spans(spans: list[tuple[int, int]], start: int, delta: int) -> list[tuple[int, int]]:
    """在 `start` 处文本净增/净减 `delta` 字之后，把区间平移回去（区间随文本一起动）。

    seed 199-215 语义取 `end=start, new_len=0` 的插入/删除特例：
    - 完全在 `start` 之前：不动；
    - 完全在 `start` 之后（含边界 `s == start`）：整体平移 delta；
    - 跨过 `start` 的区间：收缩/扩张到 `(s, max(start, e+delta))`，不会塌到 `start` 以左。
    """
    out: list[tuple[int, int]] = []
    for s, e in spans:
        if e <= start:
            out.append((s, e))
        elif s >= start:
            out.append((s + delta, e + delta))
        else:
            out.append((min(s, start), max(start, e + delta)))
    return out


def _adjust_after_replace(spans: list[list[int]], start: int, end: int,
                          new_len: int) -> list[list[int]]:
    """一次 `[start, end) → new_len` 的替换之后，把拍区间平移回去（seed 199-215 逐字）。

    - 完全在替换点之前：不动；
    - 完全在替换点之后：整体平移 delta；
    - 与替换区间相交（splice 落在某一拍内部）：把该拍边界收缩/扩张到新边界。
    """
    delta = new_len - (end - start)
    out: list[list[int]] = []
    for s, e in spans:
        if e <= start:
            out.append([s, e])
        elif s >= end:
            out.append([s + delta, e + delta])
        else:
            out.append([min(s, start), max(start + new_len, e + delta)])
    return out


def _splice_edits(text: str, spans: list[list[int]] | None,
                  edits: list[tuple[int, int, str]]) -> tuple[str, list[list[int]] | None]:
    """就地替换若干 edit 并**同步平移拍区间**；一条都没换成时原样返回（seed 218-240 逐字）。

    **从后往前**替换（前面偏移不失效）；与已替换区间重叠或越界的 edit 跳过（套路/引文会
    互相嵌套）。任何"只动局部"的修复（引文置换、按拍重写、定点去味）都走这一处拼接逻辑，
    区间才不会失效。整章重写不走这里——那种情况区间本就该作废（调用方置 None）。
    """
    if not edits:
        return text, spans
    out = text
    new_spans = [list(s) for s in spans] if spans else None
    applied = 0
    last_start = len(out) + 1
    for start, end, repl in sorted(edits, key=lambda e: e[0], reverse=True):
        if not (0 <= start < end <= len(out)) or end > last_start:
            continue
        out = out[:start] + repl + out[end:]
        if new_spans is not None:
            new_spans = _adjust_after_replace(new_spans, start, end, len(repl))
        last_start = start
        applied += 1
    return (out, new_spans) if applied else (text, spans)


def splice_with_spans(text: str, spans: list[list[int]] | None, start: int, end: int,
                      replacement: str) -> tuple[str, list[list[int]] | None]:
    """就地替换 `[start, end)` 为 `replacement` 并**同步平移拍区间**；非法区间原样返回。

    单 edit 版 `_splice_edits` 入口；多 edit 批处理见 `apply_replace_issues`。
    """
    if not (0 <= start < end <= len(text)):
        return text, spans
    return _splice_edits(text, spans, [(start, end, replacement)])


def normalize_issues(items) -> list[dict]:
    """审核意见规整：补齐 `fix_kind`、字段统一转文本（seed 243-266 逐字）。

    - 只留 dict，空 `issue` 丢弃；
    - 有 `replacement` 即视为 `replace`（局部置换）；
    - `replace` 缺 quote 或 replacement 时降级为 `rewrite`（两件套缺一不可）；
    - `fix_kind` 只认 `replace|rewrite`，其余一律 `rewrite`；
    - `type` 缺省 `"unknown"`，quote/replacement/issue/suggestion 全转 str，杂字段丢弃。
    """
    out: list[dict] = []
    for item in as_list(items):
        if not isinstance(item, dict):
            continue
        issue = as_text(item.get("issue"))
        if not issue:
            continue
        quote = as_text(item.get("quote")) or ""
        replacement = as_text(item.get("replacement")) or ""
        kind = as_text(item.get("fix_kind")) or ("replace" if replacement else "rewrite")
        if kind not in ("replace", "rewrite") or (kind == "replace"
                                                  and not (quote and replacement)):
            kind = "rewrite"
        out.append({"type": as_text(item.get("type")) or "unknown",
                    "fix_kind": kind, "quote": quote, "replacement": replacement,
                    "issue": issue, "suggestion": as_text(item.get("suggestion")) or ""})
    return out


def apply_replace_issues(text: str, spans: list[list[int]] | None, issues: list[dict]
                         ) -> tuple[str, list[list[int]] | None, list[dict]]:
    """把 `fix_kind=replace` 的意见**就地替换**（零 LLM 调用）（seed 269-294 逐字，去日志）。

    引文定位不到就跳过——绝不拿猜出来的偏移去动正文；跳过的原样返回，由调用方
    当作「未处理问题」上报。返回 (新正文, 新区间, 未落实的意见)。
    """
    edits: list[tuple[int, int, str]] = []
    deferred: list[dict] = []
    for item in issues:
        if item.get("fix_kind") != "replace":
            deferred.append(item)
            continue
        found = locate_quote(text, item.get("quote") or "")
        if found is None:
            deferred.append(item)
            continue
        edits.append((found[0], found[1], item.get("replacement") or ""))
    if not edits:
        return text, spans, deferred
    new_text, new_spans = _splice_edits(text, spans, edits)
    return new_text, new_spans, deferred


def locate_for_target(text: str, quote: str) -> tuple[int, int] | None:
    """为「判断落在哪一拍」放宽定位：严格匹配失败时，用**唯一命中**的短前缀再试（seed 297-315 逐字）。

    模型写 review 意见时常把 `quote` 近似复述（尾部被截断、多一个修饰词），严格 locator 匹配
    失败会让整批意见都定位不到节拍 → 退回整章重写（问题越修越多）。这里只用来**决定重写哪一拍**
    （整拍重写，不按偏移改字），且要求前缀在正文里**唯一出现**，避免把意见指到别的拍上；
    真正的定点替换（`fix_kind=replace`）仍然只用严格 `locate_quote`，绝不猜偏移。
    """
    hit = locate_quote(text, quote)
    if hit is not None:
        return hit
    q = (quote or "").strip()
    for n in (32, 24, 16, 12, 10):
        if len(q) <= n:
            continue
        prefix = q[:n]
        if text.count(prefix) == 1:
            return locate_quote(text, prefix)
    return None


def beat_index_at(spans: list[list[int]], pos: int) -> int | None:
    """某个下标落在第几拍（0 基）；不在任何拍内返回 None。

    半开区间判据 `s <= pos < e`（seed 502-507 的 `(start, end)` 双下标版在 P3 只用于
    「引文首字符落在哪一拍」，故收成单 `pos`）。
    """
    for i, (s, e) in enumerate(spans or []):
        if s <= pos < e:
            return i
    return None


def normalize_edges(text: str, spans: list[list[int]] | None
                    ) -> tuple[str, list[list[int]] | None]:
    """去掉正文**首尾空白**，并按删掉的**前导**字符数平移拍区间（保证 spans 仍与正文对齐）。

    逐拍写作 + 整章改写会把开头的空行反复带出并累积（实测一章开头能滚到 20 个换行），
    所以在流水线出口统一收口；平移后越界/空掉的拍直接丢弃——宁缺毋滥，绝不拿错位区间改正文。
    成段重复的内容只留第一份（落库前去重兜底）：触发过收敛 ⇒ 正文长度变了、拍区间不再对应，
    宁缺毋滥地作废区间（seed 510-533 逐字，去日志；strip/collapse 复用 harness.text）。
    """
    clean = strip_edges(text)
    collapsed = collapse_repeats(clean)
    if collapsed != clean:
        clean = collapsed.rstrip()
        spans = None
    if clean == text:
        return text, spans
    lead = len(text) - len(text.lstrip())
    if spans and lead:
        fixed = [[max(0, s - lead), max(0, e - lead)] for s, e in spans]
        fixed = [[s, e] for s, e in fixed if e > s]
        spans = fixed or None
    return clean, spans


def max_chars_beat(budget: int) -> int:
    """逐拍写作 max_tokens：目标字数的 6 倍、保底 1500（seed 928）。"""
    return max(1500, int(budget) * 6)


def max_chars_rewrite_beat(n: int) -> int:
    """单拍重写 max_tokens：原拍的 3 倍、保底 2000（防改写写飞，seed 1169）。"""
    return max(2000, n * 3)


def max_chars_deaify(n: int) -> int:
    """整章去味 max_tokens：原稿的 2 倍、保底 3000（seed 1327）。"""
    return max(3000, n * 2)


def max_chars_single_shot(target: int) -> int:
    """整章一次成稿 max_tokens：目标字数的 3 倍、保底 3000（seed 867）。"""
    return max(3000, int(target) * 3)


def is_keep_whole(new: str, old: str) -> bool:
    """整章路径防截断判据：新稿长度 ≥ 旧稿 × KEEP_WHOLE_FACTOR（seed 1198/1330 的判据）。

    明显更短大概率是 max_tokens 截断的残稿——保留旧稿。
    """
    return len(new) >= len(old) * KEEP_WHOLE_FACTOR


# 写作链 7 个 LLM 调用的路由表（附录 B.4/B.7 钉死，seed 键名同 prompts.json）：
# key → (tier 属性名, temperature, max_tokens)。逐 key 与 builtin 种子 md 的
# 第一行路由注释头一致（tests/test_p3_prompts.py/test_p4_prompts.py 双向钉死，防漂移）。
# P4 起为共享生长表：task.memory_extract（记忆提取，裁定 6）与
# task.digest_compress（卷级 digest 压缩，路由 lite/0.3/2048）随 P4 挂入；
# task.consistency_audit（P5 一致性审计复核，路由 standard/0.2/4096）随 P5 挂入；
# task.auto_plot_check / task.auto_global_check / task.auto_editor_review
# （P6 §1.5 验收链三件套，路由 pro/0.2/4096、pro/0.2/4096、pro/0.3/4096）随 P6 挂入；
# task.check_fix_chapter（P8 按章聚合修订：一次 LLM 产出该章多条意见的替换文本，
# 路由 standard/0.3/4096）随 P8 挂入；
# task.task_intake（P10 需求解析：用户一句话需求 → 结构化 brief，路由 standard/0.3/2048）
# 随 P10 挂入（seed router.py 的 task_intake 同值）。
WRITING_ROUTING: dict[str, tuple[str, float, int]] = {
    "task.context_select": ("lite", 0.2, 8192),
    "task.draft_continue": ("standard", 0.9, 8192),
    "task.draft_review": ("standard", 0.2, 8192),
    "task.draft_revise": ("standard", 0.7, 8192),
    "task.draft_rewrite_beat": ("standard", 0.6, 4096),
    "task.draft_deaify": ("standard", 0.5, 8192),
    "task.draft_deaify_edits": ("standard", 0.3, 4096),
    "task.memory_extract": ("standard", 0.3, 4096),
    "task.digest_compress": ("lite", 0.3, 2048),
    "task.consistency_audit": ("standard", 0.2, 4096),
    "task.consistency_fix": ("standard", 0.3, 2048),
    "task.web_search_verify": ("standard", 0.2, 1024),
    "task.web_search_followup": ("standard", 0.2, 1024),
    "task.auto_ref_check": ("lite", 0.2, 4096),
    "task.auto_plot_check": ("pro", 0.2, 4096),
    "task.auto_global_check": ("pro", 0.2, 4096),
    "task.auto_editor_review": ("pro", 0.3, 4096),
    "task.check_fix_chapter": ("standard", 0.3, 4096),
    "task.task_intake": ("standard", 0.3, 2048),
}

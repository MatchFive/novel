# P8 按章聚合修订（设计 §1.5「fix 按章聚合、每章只修一轮」）：分组、替换集校验、聚合修订链
"""按章聚合修订：**哪些意见能聚合**、**模型给的替换集怎么校验**、**一次改写怎么落地**。

**为什么有这一层**：P5 的「交给 AI 修」是**逐条**的——一条意见一次 LLM 调用、一次确认卡、
一次写盘。设计 §1.5 写的 seed 语义是「fix 时**按章聚合、每章只修一轮**，修完把对应问题置
resolved（防反复喂回）」。P8 把这条补上：同一章的多条意见**一次**改写。

**四个纪律（本模块的判据都从它们来）**：

1. **只有「有 chapter 且有 quote」的章级意见能参与聚合**——全局类意见（无原文片段）没有
   可定位的片段，无章意见是跨章的结构性判定，两者都**改写无从下手**（仍走 ``/issues`` 的
   「已解决/忽略」）。
2. **模型不得决定改哪里**：模型回的是「**意见 id** + 替换文本」，定位永远用**我们自己给的
   quote**（来自已回验过的意见）→ 模型自造 id 一律丢弃，也不接受模型复述/改写引文。
3. **长度护栏**：替换文本超长（原 quote 长度 × ``EDIT_LEN_FACTOR`` + ``EDIT_LEN_PAD``，
   沿用去味的既有口径）→ 丢弃该条（模型把一段扩写成一段），该条意见保持 pending。
4. **改写范围只限引文命中的那一段**：多段替换**倒序**执行（靠前的一段不被靠后的改写带偏），
   未命中的正文逐字不动；**整章重写不在本模块的口径内**（洗牌全文会「越修越多」）。

**只报不改的纪律不放松**：本模块是**人工终裁之后**才走的通道——每一章的每次写盘前必过
确认卡（本仓所有正文写盘都带确认门）；**观测只带章号与计数**，模型原文只出现在给人看的
确认卡里（与 P5 ``audit.fix`` 同款分工）。
"""
from __future__ import annotations

import asyncio
import logging

from harness.audit.fix import _rewrite_spans_in_place
from harness.memory.extract import reextract_chapter, reextract_failed, reextract_reason
from harness.reasons import reason_label
from harness.run.approval import ApprovalCancelled, ApprovalUI, ask_ui_async
from harness.run.issues import Issue, IssuesStore
from harness.store.frontmatter import FileFormatError
from harness.store.project import ChapterDoc, ProjectStore
from harness.text import locate_quote
from harness.tools.modify import _unified_diff
from harness.writing.pure import EDIT_LEN_FACTOR, EDIT_LEN_PAD, WRITING_ROUTING

__all__ = ["fix_chapter", "group_by_chapter", "parse_replacements"]

log = logging.getLogger("check.fix")


def group_by_chapter(issues: list[Issue]) -> dict[int, list[Issue]]:
    """待处理意见 → **按章分组**（只收「有 chapter 且有 quote」的章级意见）。

    - 章号升序、组内按 id 升序（``dict`` 保序 + 显式排序）；
    - **全局类意见（``quote`` 为空）与无章意见不收**——它们没有可定位的原文片段，
      改写无从下手（调用方另行如实提示，不静默吞掉）；
    - **章号不是数字的跳过**（不抛）——台账是用户可编辑的文件，手改成「第一章」不该
      让 REPL 带栈崩掉（独立复审 D7 实测）。
    """
    buckets: dict[int, list[Issue]] = {}
    for issue in issues or []:
        if issue is None:
            continue
        if issue.chapter is None:
            continue
        if not (issue.quote or "").strip():
            continue
        try:
            seq = int(issue.chapter)
        except (TypeError, ValueError):
            log.warning("聚合修订跳过意见 %s：章号不是数字（%r）", issue.id, issue.chapter)
            continue
        buckets.setdefault(seq, []).append(issue)
    return {seq: sorted(buckets[seq], key=lambda i: i.id) for seq in sorted(buckets)}


def _no_replacement_note(stats: dict) -> str:
    """「没有可用替换」的**如实**说明——说清是哪一种，别让用户猜。

    「无可用替换」有四种成因，用户看到同一句话会以为是模型没干活：
    模型没给 / 给了但 id 对不上 / 给了但替换与原文相同 / 给了但空或超长。
    """
    parts = ["无可用替换，意见保持 pending"]
    if stats.get("id_mismatch"):
        parts.append(f"{stats['id_mismatch']} 条的意见编号与台账不匹配")
    if stats.get("no_change"):
        parts.append(f"{stats['no_change']} 条的替换文本与原文相同（等于没改）")
    if stats.get("dropped"):
        parts.append(f"{stats['dropped']} 条为空或超出长度上限")
    if len(parts) == 1 and stats.get("entries"):
        parts.append(f"模型给出 {stats['entries']} 条，均不合规")
    return "；".join(parts)


def _norm_id(value: object) -> str:
    """意见 id 归一：去空白 + **去前导零**（``"0001"`` 与 ``"1"`` 是同一个 id）。

    模型回 id 时**常**去掉零填充（台账里是 ``0001``，模型常写 ``1``）。不归一 → 整批
    替换被当成「模型自造 id」丢弃 → ``/fix`` 空转且**没有任何线索**（独立复审 D4 实测）。
    非纯数字的 id 原样保留（``"abc"`` → ``"abc"``）。
    """
    s = str(value if value is not None else "").strip()
    if not s:
        return ""
    if s.isdigit():
        return s.lstrip("0") or "0"
    return s


def _match_replacements(data: object, issues: list[Issue]) -> tuple[list[dict], dict]:
    """模型的替换集 → ``(可用替换, 诊断计数)``。

    诊断计数供观测**如实**说明「为什么没有可用替换」：
    ``entries``（模型给了几条）/ ``id_mismatch``（id 对不上）/ ``no_change``（替换与原文相同）
    / ``dropped``（空或超长）。
    """
    stats = {"entries": 0, "id_mismatch": 0, "no_change": 0, "dropped": 0}
    if not isinstance(data, dict):
        return [], stats
    raw = data.get("replacements")
    if not isinstance(raw, list):
        return [], stats
    by_id: dict[str, Issue] = {}
    for issue in issues or []:
        if issue is None:
            continue
        by_id.setdefault(_norm_id(issue.id), issue)   # 先到先得：归一后撞车不覆盖
    out: list[dict] = []
    taken: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        stats["entries"] += 1
        key = _norm_id(entry.get("id"))
        issue = by_id.get(key)
        if issue is None:
            stats["id_mismatch"] += 1
            continue                       # 模型自造 id → 丢弃
        if key in taken:
            continue                       # 同 id 只采纳第一条合规的
        replacement = entry.get("replacement")
        if not isinstance(replacement, str) or not replacement.strip():
            stats["dropped"] += 1
            continue
        quote = issue.quote or ""
        if len(replacement) > len(quote) * EDIT_LEN_FACTOR + EDIT_LEN_PAD:
            stats["dropped"] += 1
            continue                       # 超长 → 丢弃（该条意见保持 pending）
        if replacement.strip() == quote.strip():
            # **与原文逐字相同** → 等于没改（P5 同款护栏：audit/fix.py:259）。
            # 不护栏的后果：正文一个字节没动，意见却被标 resolved，观测还谎称
            # 「已修改第 N 章」——正是契约 13 禁止的「静默消失」（独立复审 D2 实测）。
            stats["no_change"] += 1
            continue
        taken.add(key)
        # **id 用我们自己的**（不是模型那串）——下游 set_status 要用它。
        out.append({"id": issue.id, "quote": quote, "replacement": replacement})
    return out, stats


def parse_replacements(data: object, issues: list[Issue]) -> list[dict]:
    """校验模型给的替换集 → ``[{"id", "quote", "replacement"}]``（可用替换逐条）。

    模型的输出形态：``{"replacements": [{"id": "<意见编号>", "replacement": "..."}]}``。

    - 顶层不是 dict / 无 ``replacements`` / 不是 list → 空列表（不炸）；
    - 条目不是 dict → 跳过；
    - ``id`` 必须**归一后等于**本次传入的某个 issue id（``"1"`` 与 ``"0001"`` 同义；
      模型自造 id → 丢弃）；
    - ``replacement`` 必须是非空 str（空 → 丢弃该条）；
    - **长度护栏**：``len(replacement) > len(quote) × EDIT_LEN_FACTOR + EDIT_LEN_PAD`` →
      丢弃该条（模型把一段扩写成一段）；
    - **无变化护栏**：替换文本与引文**逐字相同** → 丢弃该条（等于没改；P5 同款）；
    - **同一 id 只采纳第一条合规的**（后面的同 id 条目忽略）；
    - 返回的 ``id`` 与 ``quote`` 都取自**我们自己的 issue**——不用模型给的（模型不必
      复述引文，也不必原样抄 id）。
    """
    out, _stats = _match_replacements(data, issues)
    return out


# ------------------------------------------------------------------ 聚合修订链

def _warn_broken(path, exc: FileFormatError) -> None:
    """坏章只降级当前章：点名文件 + 原因，其余照常（容错口径同 check.context）。"""
    log.warning("聚合修订跳过坏章 %s: %s", path, exc.reason)


def _issue_block(issue: Issue) -> str:
    """一条意见进提示词的文本块：编号 + 标题 + 描述 + **待替换原文**。

    编号（issue id）是模型**指认**改哪一条的依据；「待替换原文」是**我们自己的 quote**
    ——模型只负责产出替换文本，不负责定位。
    """
    return (f"[{issue.id}] {issue.title}\n"
            f"描述：{issue.detail or '（无）'}\n"
            f"待替换原文：{issue.quote or ''}")


def _issues_block(issues: list[Issue]) -> str:
    """多条意见的文本块（空块之间空一行）。"""
    return "\n\n".join(_issue_block(i) for i in issues or [])


def _extract_replacements(text: str) -> object | None:
    """结构化输出提取：``{"replacements": [...]}`` → 该 dict；其余形态 → None。

    None 交 ``complete_structured`` 的扩额阶梯处理（两轮仍失败 → bad_json）。
    惰性 import ``tools.base``（tools 包顶层 import 链长，顶层再 import 易成环）。
    """
    from harness.tools.base import extract_json
    value = extract_json(text)
    if not isinstance(value, dict):
        return None
    if not isinstance(value.get("replacements"), list):
        return None
    return value


def _locate_spans(doc: ChapterDoc,
                  replacements: list[dict]) -> tuple[list[tuple[int, int, str, str]], int]:
    """逐条按**我们自己的 quote** 在本章正文里定位 → ``(可落地的段, 定位不到的条数)``。

    定位只在**本章**内做（分组键就是章号）：引文落在别的章 → 定位不到 → 跳过并如实
    计数（该条意见保持 pending；等验收重跑刷新章号）。**绝不跨章乱改**——「一章一卡」
    的卡面承诺的是本章。
    """
    spans: list[tuple[int, int, str, str]] = []
    skipped = 0
    for item in replacements:
        pos = locate_quote(doc.body, item["quote"])
        if pos is None:
            skipped += 1
            continue
        spans.append((pos[0], pos[1], item["replacement"], item["id"]))
    return spans, skipped


def _disjoint(spans: list[tuple[int, int, str, str]]) -> list[tuple[int, int, str, str]]:
    """去掉**相互重叠**的段（同一段被两条意见同时指到 → 保留先出现的那条，另一条跳过）。

    重叠的段不能都替换（后一个的偏移会落在已改动的正文上）——宁可不改，也不改错。
    """
    kept: list[tuple[int, int, str, str]] = []
    for span in sorted(spans, key=lambda s: (s[0], s[1])):
        if any(span[0] < k[1] and k[0] < span[1] for k in kept):
            continue
        kept.append(span)
    return kept


def _splice(text: str, spans: list[tuple[int, int, str, str]]) -> str:
    """按段替换出**新正文**（从后往前替换 → 靠前一段的偏移不被后面的改写带偏）。"""
    out = text
    for start, end, repl, _id in sorted(spans, key=lambda s: s[0], reverse=True):
        out = out[:start] + repl + out[end:]
    return out


async def fix_chapter(*, store: ProjectStore, mem, llm, prompts, settings, chapter: int,
                      issues: list[Issue], ui: ApprovalUI | None = None,
                      emit=None) -> str:
    """按章聚合修订：同一章的多条意见**一次**改写（见模块 docstring）。返回观测文本。

    链路：定位章 → 组装三变量 → 渲染 ``task.check_fix_chapter`` → LLM →
    :func:`parse_replacements` 校验 → 逐条在**本章正文**里定位 → 合并成**一张确认卡**
    （本章意见清单 + 合并 diff）→ 确认后**倒序**逐段就地改写 → 记忆重提取 →
    **实际落地**的意见逐条 resolved。

    - ``ui`` 为确认门注入点（默认自建 ``ApprovalUI()``；测试注入假 ask）；
    - ``emit`` 为预留观测口——本轮**不新增事件 kind**（同 P5 ``audit.fix``）；
    - 失败语义：不 raise，一律收敛成观测文本；**零写盘**时意见保持 pending。
    """
    del emit  # 预留口：本轮无消费方（不新增事件 kind）
    ui = ui or ApprovalUI()
    issues = list(issues or [])

    def _assemble() -> ChapterDoc | None:
        """找出本章文档（坏章逐文件容错跳过）——读盘走线程。"""
        docs = store.list_chapters(on_error=_warn_broken)
        return next((d for d in docs if d.seq == chapter), None)

    doc = await asyncio.to_thread(_assemble)
    if doc is None:
        return f"第{chapter}章未找到，意见保持 pending"

    title = str((doc.meta or {}).get("title") or f"第{chapter}章")
    prompt = prompts.render("task.check_fix_chapter",
                            chapter_title=title,
                            chapter_body=doc.body or "",
                            issues=_issues_block(issues))
    tier, temperature, max_tokens = WRITING_ROUTING["task.check_fix_chapter"]
    provider = llm.provider
    model = getattr(provider, tier, None) or provider.standard

    # 惰性 import：tools 包顶层会拉全部工具模块，顶层 import 易随编排接入成环
    from harness.tools.base import complete_structured
    try:
        data, _usage, reason = await complete_structured(
            llm, model=model, temperature=temperature, max_tokens=max_tokens,
            prompt=prompt, extract=_extract_replacements)
    except Exception as exc:                 # noqa: BLE001 —— 修订失败**不得**带崩 REPL
        # `complete_structured` 只捕 LLMError；跨事件循环复用连接池会抛
        # `RuntimeError: Event loop is closed`（**不是** LLMError）→ 曾经一路穿出
        # REPL 命令分发 → 整个会话带 traceback 退出（独立审核 D1）。
        log.warning("聚合修订：LLM 调用异常（零写盘、意见保持 pending）：%s", exc)
        return ("修复失败（调用失败）：未产出可用的替换文本，意见保持 pending")
    if data is None:
        # 失败：零写盘、不 raise、观测如实（标签经 reason_label 映射，无瞬时词）
        return (f"修复失败（{reason_label(reason)}）：未产出可用的替换文本，"
                "意见保持 pending")

    replacements, stats = _match_replacements(data, issues)
    if not replacements:
        return _no_replacement_note(stats)

    located, skipped = _locate_spans(doc, replacements)
    spans = _disjoint(located)
    skipped += len(located) - len(spans)      # 重叠被丢的也算「没改成」
    if not spans:
        return "quote 已不可定位，意见保持 pending"

    new_body = _splice(doc.body, spans)
    diff = _unified_diff(f"chapters/{doc.seq:04d}.md", doc.body, new_body)
    titles = "；".join(f"[{i.id}] {i.title}" for i in issues)
    question = (f"第{doc.seq}章按章聚合修订（{len(spans)} 段）：\n"
                f"待修意见：{titles}\n\n{diff}\n\n确认应用？")
    try:
        choice = await ask_ui_async(ui, question, ["应用改写", "取消"], multi=False)
    except ApprovalCancelled:
        return "已取消，意见保持 pending"
    if not choice.indices or choice.indices[0] != 0:
        return "已取消，意见保持 pending"

    # 写盘：**一次**写回整章新正文（契约「一章一次改写 → 一次写盘」）。
    # 逐段写 N 次时，中途崩溃会留下**半改写**的章：已改的段进了盘、台账一条没动 →
    # 重跑时那些引文定位不到 → 意见永远卡在 pending（独立复审 D8）。
    applied: list[str] = []
    failed = False
    if not _rewrite_spans_in_place(store, doc, [(s[0], s[1], s[2]) for s in spans]):
        failed = True
    else:
        applied = [s[3] for s in spans]
        doc.body = new_body

    result = await reextract_chapter(store=store, mem=mem, llm=llm, prompts=prompts,
                                     settings=settings, seq=doc.seq, doc=doc)
    issues_store = IssuesStore(store.root)
    for issue_id in applied:
        issues_store.set_status(issue_id, "resolved")

    if not applied:
        # 一个字节都没写 → 观测**不能**说「已修改第 N 章」（独立复审 D6）。
        parts = [f"第{chapter}章未改写（章文件定位失败），意见保持 pending"]
    else:
        parts = [(f"已修改第{chapter}章：改写 {len(applied)} 段，"
                  f"标记 resolved {len(applied)} 条意见")]
        if failed:
            parts.append("部分段落未改写（章文件定位失败）")
    if skipped:
        parts.append(f"{skipped} 条意见的引文无法定位，保持 pending")
    if reextract_failed(result.text):
        parts.append(f"记忆重提取失败：{reextract_reason(result.text)}")
    else:
        parts.append("记忆已重提取")
    return "，".join(parts)

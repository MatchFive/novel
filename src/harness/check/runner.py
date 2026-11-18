# P6 验收链（设计 §1.5）：验收编排
"""验收编排：组装上下文 → LLM → 解析 → quote 回验 → 落 issues → supersede → 观测/事件。

**只报不改**（计划裁定 2）：本模块**不改任何章正文**——意见落 issues 台账
（``source="check"``），由人工在 ``/issues`` 通道终裁。

落库口径照 P5 审计（``harness/audit/runner.py``）：

- **source 纪律**：验收意见一律 ``source="check"``（与审计的 "audit" 分开——台账按
  来源分流；supersede 也只清自己来源的）；
- **title/detail**：title = 意见文本前 40 字；detail = ``f"{type}：{issue}；建议：{fix}"``；
- **supersede**：落库前先把**同章**（chapter 相同；全局类意见 chapter=None 不参与）
  的 source="check" 的 pending 全部置 resolved——**重跑不翻倍**；同一次运行内同章
  只清一次（否则本 run 刚落的意见会被后一条清掉——P5 审计同款口径）。

**quote 回验**在 ``harness/check/parser.py`` 那一层已完成（编造的 quote 已被丢弃），
本层拿到的都是回验通过的意见；落库前不再二次回验（回验只需一次，多一次只会漂移）。

**quote 纪律按 kind 分层**（转成 ``parse_check_issues`` 的 ``require_quote``）：
``plot``（情节级）→ **不要求** quote（seed 的情节级输出格式没有 quote 字段——意见
针对情节结构，未必有可引的原文片段；给了仍要回验）；``global``（全书级，含
editor_mode）→ **要求** quote（证据纪律，行为不变）。

失败语义：LLM 调用失败（``complete_structured`` 返回 None）→ 观测如实
「验收失败（<确定性标签>）」、**零写盘**、**不 raise**（标签走
``harness.reasons.reason_label()``——确定性词表，禁瞬时词）。**文案可被 loop 的
分类器捕获**（``"失败（" in obs`` → failed → 计熔断/冷却）——这是本仓惯例
（同 ``修复失败（…）``）。

**上下文组装走线程**（P4 I-1 纪律，同 ``audit/runner.py`` 的全书扫描段）：组装要读
每一个章文件、每一份章记忆（300 章实测约 3 秒），整段塞进**一次**
``await asyncio.to_thread(...)``；**LLM 调用留在 to_thread 之外**（to_thread 里不做
LLM 调用）。

**观测不回显模型原文**：意见行只给「章号 + 维度」（详见 :func:`_issue_note`）——
详情在 issues 台账里。

**usage 计量**：``run_check`` 只返回观测文本（返回形状是 T3 钉死的契约）→ token 用量
经**可选关键字** ``meter``（``harness.tools.base.UsageMeter``）下传给调用方记账——
同 writing/revise、memory/extract 的既有计量口径；``meter=None`` 时行为与从前逐字相同
（不记账、不改返回）。
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from harness.check.context import (
    brief_context,
    chapter_lines,
    done_chapters,
    global_check_vars,
    load_stage_checklist,
    plot_check_vars,
)
from harness.check.parser import (
    parse_check_issues,
    parse_checklist,
    parse_verdict,
    verdict_from_checklist,
)
from harness.checklists import canonical_title
from harness.reasons import reason_label
from harness.run.issues import IssuesStore
from harness.writing.pure import WRITING_ROUTING

if TYPE_CHECKING:   # 惰性 import：tools 包顶层会拉全部工具模块（见函数内 complete_structured 的注释）
    from harness.tools.base import UsageMeter

__all__ = ["run_check"]

log = logging.getLogger("check.runner")

#: issue title 截断长度（同 P5 审计：意见文本前 40 字）
_TITLE_MAX = 40
#: kind → 提示词 key（editor_mode 另走 task.auto_editor_review）
_PROMPT_KEYS = {"plot": "task.auto_plot_check", "global": "task.auto_global_check"}
#: editor_mode 时的提示词 key
_EDITOR_PROMPT_KEY = "task.auto_editor_review"

#: kind → 清单来源的阶段（P7）：
#: - 情节级 → ``outline``（每卷清单 + 爽点密度）。**注意**：seed 的情节级验收写的是
#:   stage ``"draft"``，而 seed 自己的 handbook 映射里**没有** ``draft`` 这个键 →
#:   它实际拿到的是兜底文案（seed 的键名笔误）；本地用语义正确的那一组。
#: - 全书级 → ``finish``（全书清单 + 伏笔管理，与 seed 的 global_check 同款）。
_STAGE_BY_KIND = {"plot": "outline", "global": "finish"}


def _extract_value(text: str) -> object | None:
    """结构化输出的默认 extract：首个可解析 JSON 值（惰性导入，同 audit.review）。"""
    from harness.tools.base import extract_json
    return extract_json(text)


def _supersede(issues: IssuesStore, chapter: int) -> None:
    """同章 ``source == "check"`` 的 pending 全部置 resolved（新意见取代旧意见）。"""
    for old in issues.list("pending"):
        if old.source == "check" and old.chapter == chapter:
            issues.set_status(old.id, "resolved")


def _supersede_title(issues: IssuesStore, title: str) -> None:
    """同条目标题的 ``source == "checklist"`` 的 pending 置 resolved（清单条目按标题取代）。

    清单条目**无章**（跨章的结构性判定）→ 按章 supersede 不适用 → 按标题：
    同一条目重跑是**替换**而不是**累积**（否则每跑一次台账就多一条同款）。
    """
    for old in issues.list("pending"):
        if old.source == "checklist" and old.title == title:
            issues.set_status(old.id, "resolved")


def _wanted_seqs(chapters: list | None) -> set[int] | None:
    """调用方指定的章号集合（None = 不限，即全部已定稿章）。"""
    if chapters is None:
        return None
    wanted: set[int] = set()
    for c in chapters:
        try:
            wanted.add(int(c))
        except (TypeError, ValueError):
            continue
    return wanted


def _issue_note(item) -> str:
    """观测里的意见行：「· 第N章：<维度>」/「· 全局：<维度>」。

    **不回显任何模型原文**（复审 SHOULD 2 + follow-up 2.1）：loop 的分类器会扫描**整条**
    观测里的 ``TRANSIENT_MARKERS``（timeout / 超时 / **连接** / 截断…）——模型写「甲乙两人
    关系**连接**生硬」→ 整条**成功**的验收观测被判 transient → 模型收到自相矛盾的
    ``FAILED-TRANSIENT``、该步不算进展、白烧一个 noop 槽。

    这里用的是**我们自己那张固定词表**派生的维度（人物/情节/爽点/伏笔/世界观/文字），
    不是模型的 ``type`` 字段——``type`` 同样是模型写的自由文本（模型可能给
    ``type="connection timeout"``），回显它等于换了个门继续漏。详情在 issues 台账里
    （用户去 ``/issues`` 看），观测只给「章号 + 维度」。
    """
    where = f"第{item.chapter}章" if item.chapter is not None else "全局"
    return f"· {where}：{item.dimension}"


async def run_check(*, store, mem, llm, prompts, settings, kind: str,
                    chapters: list | None = None, editor_mode: bool = False,
                    emit, meter: UsageMeter | None = None) -> str:
    """验收编排（见模块 docstring）。返回观测文本（首行计数逐字口径）。

    - ``kind`` ∈ {"plot", "global"}；``editor_mode=True`` → global 走
      ``task.auto_editor_review``（责编视角，八变量）；
    - ``chapters`` 非 None → 只从这些章里抽样（且 chapter_summaries 只列这些章）；
      None → 从全部已定稿章抽样；
    - ``settings`` 保留在签名里与工具层对齐（本编排不读配置）；
    - ``meter`` 可选（P6 T4b usage 入账）：把本次 LLM 调用的 usage 记进计量器；
      None=不记账，行为与从前逐字相同。**返回形状不变**（仍是观测文本 str）；
    - LLM 调用在 store 锁外；失败 → 零写盘、不 raise。
    """
    if kind not in _PROMPT_KEYS:
        raise ValueError(f"未知验收 kind：{kind}（只支持 plot / global）")

    wanted = _wanted_seqs(chapters)

    def _assemble() -> tuple[list, dict, str, list[str]]:
        """组装上下文（读章 + 解析章记忆 + 渲染变量槽）——**同步重活**。

        这一段要读**每一个**章文件、**每一份**章记忆（还要再读一遍卷/情节树），
        300 章的书实测约 3 秒——跑在事件循环上会把整个 loop 堵死。

        **清单只读一次盘**：读出的条目既用来渲染 ``{{stage_checklist_items}}`` 槽，
        又用来把模型回的条目名**映射回规范标题**（见 :func:`harness.checklists.canonical_title`）——
        两处口径必须一致（否则映射用的标题与渲染的标题不是同一批）。
        """
        docs = done_chapters(store)
        brief = brief_context(store)
        goals = brief["goals"]
        constraints = brief["constraints"]
        premise = brief["premise"]

        target = docs if wanted is None else [d for d in docs if d.seq in wanted]

        checklist = load_stage_checklist(store, _STAGE_BY_KIND[kind])
        titles = [t for t, _ in checklist]

        if kind == "plot":
            vars_ = plot_check_vars(store=store, mem=mem, goals=goals,
                                    constraints=constraints, stage=_STAGE_BY_KIND["plot"],
                                    checklist_items=checklist)
            if wanted is not None:
                vars_["chapter_summaries"] = chapter_lines(target)
            prompt_key = _PROMPT_KEYS["plot"]
        else:
            vars_ = global_check_vars(store=store, mem=mem, goals=goals, constraints=constraints,
                                      premise=premise, chapters=target, editor_mode=editor_mode,
                                      stage=_STAGE_BY_KIND["global"],
                                      checklist_items=checklist)
            prompt_key = _EDITOR_PROMPT_KEY if editor_mode else _PROMPT_KEYS["global"]
        return target, vars_, prompt_key, titles

    # 组装段整体 to_thread（P4 I-1 纪律，同 audit/runner 的全书扫描段）：
    # **一次** to_thread 包住整段组装；LLM 调用留在锁外、留在 to_thread 之外。
    target, vars_, prompt_key, titles = await asyncio.to_thread(_assemble)

    prompt = prompts.render(prompt_key, **vars_)
    tier, temperature, max_tokens = WRITING_ROUTING[prompt_key]
    provider = llm.provider
    model = getattr(provider, tier, None) or provider.standard

    # 惰性 import：tools 包顶层会拉全部工具模块，顶层 import 易随编排接入成环
    from harness.tools.base import complete_structured

    data, usage, reason = await complete_structured(
        llm, model=model, temperature=temperature, max_tokens=max_tokens,
        prompt=prompt, extract=_extract_value)
    if meter is not None:
        meter.add(usage)   # usage 入账：响应未带 usage 时 meter.add(None) 跳过（不臆造用量）
    if data is None:
        # 失败：零写盘、不 raise、观测如实（标签经 reason_label 映射，无瞬时词）
        # 文案是「验收失败（…）」——loop 的分类器认「失败（」→ 判 failed（计熔断）；
        # 旧文案「验收未完成（…）」既不以 FAILED 开头也不含「失败（」→ 被判 ok：
        # 一次超时的验收被计成**有效步**、清零 noop/fail 计数、不进冷却。
        log.warning("验收失败（%s）：kind=%s", reason_label(reason), kind)
        return f"验收失败（{reason_label(reason)}）"

    verdict, score, checklist = parse_verdict(data)
    # quote 纪律按 kind 分层：情节级验收（plot）的意见针对**情节结构**，seed 的输出
    # 格式没有 quote 字段 → 不强制 quote（给了仍要回验）；全书级验收（global，含
    # editor_mode）章级意见**必须**带可定位 quote（证据纪律，行为不变）。
    # 章集合 = **本次目标章**（target）：指向范围外的意见无从回验 → 丢弃。
    parsed, discarded = parse_check_issues(data, chapters=target,
                                           require_quote=(kind == "global"))
    verdict = verdict_from_checklist(checklist, verdict)

    issues = IssuesStore(store.root)
    # 本轮开始前的 pending（用于观测里报「上轮有、本轮没再报」——见下方 stale 计数）
    old_pending = issues.list("pending")
    # supersede 只清**本轮有新意见的章**（既有语义，刻意保留）：
    # 章级意见的模型输出是「**它能说出依据的问题**」（提示词原话：「说不出依据的问题
    # 不要报」），**不是逐章判定** → 「本轮没报该章」≠「该章已通过」→ 清掉会
    # **静默丢掉真实发现**（违反「不静默消失」的纪律）。
    # **与清单条目不同**：清单条目有逐条 verdict（pass/fail/na），是**穷举判定** →
    # 判 pass/na 时清旧是对的（见下方 `_supersede_title` 的用法）。
    # （独立审核 D2 建议「按 target 全清」——**不采纳**，理由如上；改为**可见性**：
    # 观测里如实报出有几条上轮意见本轮没再出现。）
    superseded: set[int] = set()      # 本 run 已 supersede 的章（同章只清一次）
    landed = []
    for item in parsed:
        if item.chapter is not None and item.chapter not in superseded:
            _supersede(issues, item.chapter)
            superseded.add(item.chapter)
        issues.add("check", item.issue[:_TITLE_MAX],
                   f"{item.type}：{item.issue}；建议：{item.fix}", item.quote,
                   chapter=item.chapter)
        landed.append(item)

    # **上轮有、本轮没再报**的旧意见（本次检查到的章里）→ 如实报数，用户可自行判断
    # 是否过时并在 /issues 里标记忽略（**不自动清**——见上面的理由）。
    target_set = {c.seq for c in target if getattr(c, "seq", None) is not None}
    stale = sum(1 for old in old_pending
                if old.source == "check" and old.chapter in target_set
                and old.chapter not in superseded)

    # 清单驱动（P7）：逐条 verdict=fail 的**清单条目**落成 issue（人类逐条看）——
    # quote 为空 → 属「无原文片段」那一类 → `/issues` 不给「交给 AI 修」（没有可定位的片段）。
    #
    # **独立 source="checklist"**（不与意见的 source="check" 混在一起）：
    # ① `/issues` 里两类候选分组显示（「【验收】」/「【验收清单】」），人一眼分得清；
    # ② 清单条目**无章**（跨章的结构性判定）→ 按章 supersede 不适用 → 改为**按条目标题**
    #    supersede：同一条目重跑是**替换**而不是**累积**（否则每跑一次台账多一条）。
    checklist_items = parse_checklist(data)
    failed_items = [c for c in checklist_items if c["verdict"] == "fail"]
    # **本次判定的每一条清单条目都参与按标题 supersede**（独立复审 D3）：
    # 判 fail → supersede 旧的 + 落新的；判 pass/na → **只** supersede 旧的（不落新的）。
    # 只对 fail 做 supersede 会让「上轮判 fail、本轮判 pass」的条目在台账里**长期留一条
    # 已经不成立的失败**（带的是上一轮的 evidence）——「重跑是替换不是累积」只做了一半。
    for entry in checklist_items:
        # **标题取「规范标题」**：模型回的是它记得的条目名（常带尾句点/前导编号/自己的批注，
        # 或把标题截短）。直接拿那串字当台账标题 → 同一条清单条目在不同轮次落成不同标题
        # → 按标题 supersede 失效 → 台账累积（复审实测）。映射不上（模型自造条目名）时
        # 保留原名——那是**已知取舍**（模型自造的条目照样落 issue，只是不做来源校验）。
        #
        # **规范标题不截断**（独立复审 D5）：截到 _TITLE_MAX 会让两条**前 40 字相同**的
        # 清单条目落成同一个台账标题 → 后一条判 fail 时把前一条的 pending 顶掉 → 前一条的
        # 失败从台账里消失。规范标题本身是清单里的**唯一键**（同名后者覆盖前者），
        # 天然不碰撞。**只有模型自造名**（映射不上、可能很长很脏）才截断。
        canonical = canonical_title(entry["item"], titles)
        title = canonical if canonical else entry["item"][:_TITLE_MAX]
        _supersede_title(issues, title)
        if entry["verdict"] == "fail":
            issues.add("checklist", title,
                       f"清单未通过：{entry['evidence'] or '（未给出依据）'}", None, chapter=None)

    emit("check_report", {"verdict": verdict, "score": score, "issues": len(landed),
                           "discarded": discarded, "kind": kind,
                           "checklist": len(checklist_items),
                           "checklist_failed": len(failed_items)})
    if landed:
        first = (f"验收完成：verdict={verdict}、意见 {len(landed)} 条"
                 f"（丢弃无证据 {discarded} 条），已落 issues")
    else:
        first = f"验收完成：verdict={verdict}、零意见（丢弃无证据 {discarded} 条）"
    # 清单统计另起一行（首行是 T3 钉死的文案，不动）
    stats = f"清单 {len(checklist_items)} 条，其中未通过 {len(failed_items)} 条"
    notes = [_issue_note(item) for item in landed]
    if stale:
        # 上轮有、本轮没再报的意见 → **可见**（不自动清：模型输出不是逐章判定）
        notes.append(f"另有 {stale} 条上轮意见本轮未再出现（已保留待处理；"
                     "如已过时可在 /issues 里标记忽略）")
    return "\n".join([first, stats, *notes])

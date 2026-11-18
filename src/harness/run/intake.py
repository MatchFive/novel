"""需求解析（P10）：用户一句话需求 → **结构化 brief**（落 ``state.json``）。

**为什么有这一层**：设计 §1.1 的流程图把 ``INTAKE[需求解析 task_intake]`` 画在 ReAct
循环**之前**——用户的需求进来先被解析成结构化参数，再驱动后面的规划/写作/验收。
本地此前**从未移植**它：``RunState.brief`` 的 ``premise`` / ``constraints`` 一直是
``None`` / ``[]``（无人写入），于是验收链的 ``premise`` / ``goals`` / ``constraints``
三个槽只能回落到 raw 或恒「（无）」——验收的「目标对齐」这一维**没有靶子**。

**提示词逐字移植** seed 的 ``task.task_intake``（底本 2590 字节，见
``.superpowers/sdd/2026-11-15-p10-brief-intake/intake.seed.md``）。本地只提供变量
``{{message}}``（需求原文）与 ``{{project_state}}``（项目现状），**一个字都不改**。

**落哪些字段**（裁定 3）：本地 ``RunState.brief`` 的 E.3 schema + 新增 ``goal``；
seed 的 ``need_dimensions`` / ``prefer`` / ``avoid`` **丢弃**（本地当前无消费方）。

**deliverables 是权限面**（裁定 6）：它决定 ``allowed_for_run``（能跑哪些工具），
所以必须过**本地**封闭词表，且**用户显式指定优先**于模型推断。
"""
from __future__ import annotations

import logging

__all__ = [
    "DELIVERABLE_VOCAB",
    "parse_intake",
    "project_state_text",
    "run_intake",
]

log = logging.getLogger("run.intake")

#: 本地 deliverable 封闭词表 = ``run.whitelist.DELIVERABLE_TOOLS`` 的键（**直接派生**，
#: 不留第二份字面量——独立复审 S2）。
#: **注意**：seed 的模板让模型从 ``{world, characters, outline, chapters, draft, edit}``
#: 里选；本地**曾没有** ``characters``（P22/A6 已补：draft_character 落地后词表跟上）
#: 与 ``edit``（本地无对应工具链）——后者一旦透传进 ``allowed_for_run`` 会
#: ValueError 响亮失败（fail-closed）→ 本地**过滤掉**。
def _local_deliverable_vocab() -> frozenset[str]:
    from harness.run.whitelist import DELIVERABLE_TOOLS

    return frozenset(DELIVERABLE_TOOLS.keys())


DELIVERABLE_VOCAB: frozenset[str] = _local_deliverable_vocab()

#: 限制条数与单条长度上限（防模型吐一整页）
MAX_CONSTRAINTS = 20
MAX_CONSTRAINT_CHARS = 200


def _norm_deliverable(value: object) -> str:
    """deliverable 值归一：去空白 + 小写。"""
    return str(value if value is not None else "").strip().lower()


def _clean_list(value: object) -> list[str]:
    """只收**非空字符串**的列表（seed ``_clean_list`` 同口径，但更严：数字/对象不硬转成文本）。

    seed 的 ``_clean_list`` 是 ``[str(x).strip() for x in raw if str(x).strip()]``——数字
    ``123`` 会变成字符串 ``"123"`` 进限制列表。限制是给人看的句子，数字不是句子 → 丢弃。
    """
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [x.strip() for x in value if isinstance(x, str) and x.strip()]


def _sanitize_deliverables(value: object) -> list[str]:
    """过**本地**封闭词表 + 去重保序；非法值丢弃（seed ``sanitize_deliverables`` 同款，
    但词表是**本地**的那一份）。"""
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for item in value:
        d = _norm_deliverable(item)
        if d in DELIVERABLE_VOCAB and d not in out:
            out.append(d)
    return out


def parse_intake(data: object, *, raw: str, deliverables: list[str]) -> dict:
    """模型的 intake JSON → **要落盘的 brief 字段字典**。

    返回 ``{goal, premise, constraints, deliverables, plan_arcs, write_target}``。

    - ``goal``：非空 str（否则 ``""``）——「一句话概括用户真正想要的东西」。
      **只作为 brief 里的数据**（P11 再用）：验收的 ``goals`` 槽用**用户原话**，
      不用模型自己的概括（独立复审 D1：``auto_plot_check`` 没有 premise 槽，
      判官手上唯一的意图陈述就是 goals → 换成模型的话 = 被验收者的标准被换掉 +
      自证偏差）。
    - ``premise``：非空 str（否则回落 ``raw``）；
    - ``constraints``：只收非空 str 的列表（≤20 条，每条 ≤200 字）；
    - ``deliverables``：**权限面**，**只由用户决定**——用户显式指定的原样用；
      用户没指定（走默认）时**保持默认**（独立复审 D2：本地 ``deliverables`` 就是
      ``allowed_for_run`` 的权限面，模型推断若能收窄它 → 回 ``["world"]`` 会把
      **规划链三工具物理摘掉**、run 静默卡死；默认 ``outline`` 已是全集，
      收窄只有代价没有收益）。模型的推断另记为 ``suggested_deliverables``（**信息**，
      给 P11 的范围接线用），**不参与权限面**；
    - ``plan_arcs``：``max(0, int(value))``，非整数 → 0（**不**限死 {0,1,3,5}：
      模型回 2（「先写两卷」）时打成 0 等于「按全书推进」，语义比原样接受更糟）；
    - ``write_target``：非空 str（否则 ``""``）。

    **不抛异常**：任何非法输入 → 返回一个「安全最小字典」（只含 raw/deliverables + 回落值）。
    """
    data = data if isinstance(data, dict) else {}

    # **只认字符串**（不把数字/对象硬转成文本——那是在编造内容）
    goal_raw = data.get("goal")
    goal = goal_raw.strip() if isinstance(goal_raw, str) else ""
    premise_raw = data.get("premise")
    premise = (premise_raw.strip() if isinstance(premise_raw, str) else "") \
        or (raw or "").strip()

    constraints: list[str] = []
    for c in _clean_list(data.get("constraints")):
        if len(constraints) >= MAX_CONSTRAINTS:
            break
        constraints.append(c[:MAX_CONSTRAINT_CHARS])

    # **权限面 = 用户的**（用户显式指定的原样用；没指定就保持默认）
    final_deliverables = [d for d in (_norm_deliverable(x) for x in deliverables)
                          if d in DELIVERABLE_VOCAB] or ["outline"]
    # 模型的推断只作**信息**记下（过滤掉本地没有的 characters/edit）
    suggested = _sanitize_deliverables(data.get("deliverables"))

    try:
        plan_arcs = max(0, int(data.get("plan_arcs") or 0))
    except (TypeError, ValueError):
        plan_arcs = 0

    write_raw = data.get("write_target")
    return {
        "goal": goal,
        "premise": premise,
        "constraints": constraints,
        "deliverables": final_deliverables,
        "suggested_deliverables": suggested,
        "plan_arcs": plan_arcs,
        "write_target": write_raw.strip() if isinstance(write_raw, str) else "",
    }


def project_state_text(store) -> str:
    """项目现状摘要（≤500 字）——给 ``{{project_state}}`` 变量。

    seed 用它判断「继续做」还是「新开」；本地 ``/resume`` 续跑场景需要同款信息。

    组成：README **六字段**摘要（复用 ``planning.context.story_settings``，同一口径不留
    两份实现）+ 卷数/章数/世界观条目数/角色数 + 一句判断。

    **计数行无条件给**（独立复审 D3）：新建项目的 README 模板**六字段全空**，
    若把计数行挂在「README 有内容」的条件下 → 一个刚建好、已经有世界观条目的项目
    会被报成「新项目：无既有内容」→ intake 把「继续做」判成「新开」→ 从零重规划，
    与既有设定打架。（这条错判命中的正是**主路径**：新建项目。）

    **坏章只少算一章**（独立复审 D4）：``list_chapters`` 不给 ``on_error`` 时坏章
    （``FileFormatError``）会**上抛** → 整份现状被吞成空串 → intake 对项目状态完全失明。
    → 这里给 ``on_error``（跳过坏章），各读盘逐项降级为 0。

    **读盘在调用方的 ``asyncio.to_thread`` 内**（P4 I-1：IO 密集活不在事件循环上）。
    """
    from harness.planning.context import story_settings  # 同一口径，不复制实现

    story = story_settings(store)
    parts: list[str] = []
    for key, label in (("genre", "题材"), ("tone", "基调"), ("ending", "结局"),
                       ("chapter_words", "每章字数"), ("target_chapters", "目标总章数"),
                       ("planned_volumes", "预计卷数")):
        if story.get(key):
            parts.append(f"{label}：{story[key]}")

    def _safe(fn, default=0):
        """逐项降级：一个读盘失败不该废掉整份现状（FileFormatError 不是 OSError）。"""
        try:
            return fn()
        except Exception as exc:                 # noqa: BLE001 —— 现状读不出来不该阻断
            log.warning("需求解析：项目现状读取失败（该项按 %s 计）：%s", default, exc)
            return default

    arcs = _safe(lambda: len(store.read_arcs()))
    chapters = _safe(lambda: len(store.list_chapters(
        on_error=lambda path, exc: log.warning("需求解析：跳过坏章 %s（%s）", path, exc))))
    worlds = _safe(lambda: len(store.read_world()))
    characters = _safe(lambda: len(store.list_characters()))

    # 计数行**无条件**给（D3）
    parts.append(f"已有世界观条目 {worlds} 条、角色 {characters} 个")
    if arcs or chapters:
        parts.append(f"本项目已有 {arcs} 卷骨架 / {chapters} 章正文——本次是**继续做**还是**新开**？")
    elif worlds or characters:
        parts.append("（本项目暂无卷骨架与章正文，但已有上面的世界观条目与角色）")
    else:
        parts.append("（新项目：无既有内容）")
    return "；".join(parts)[:500]


async def run_intake(*, store, llm, prompts, settings, raw: str,
                     deliverables: list[str], user_specified: bool = False,
                     emit=None) -> tuple[dict, str]:
    """跑一次需求解析 → ``(要落盘的 brief 字段, 观测文本)``。**不 raise**。

    链路：读项目现状（``asyncio.to_thread``）→ 渲染 ``task.task_intake`` → 一次 LLM
    （``complete_structured``）→ :func:`parse_intake` → 返回。

    **失败不阻断**（裁定 1）：需求解析是**增强**不是**前提**——``raw`` 本身已够规划用
    （``instruction`` 槽就是 raw）。失败 → 返回 ``(None, 观测)``，观测
    「需求解析失败（<reason_label>）：按原始需求继续」——调用方据此**不碰 brief**
    （brief 保持「只有 raw/deliverables」，那才是诚实的台账）。
    """
    import asyncio

    from harness.reasons import reason_label
    from harness.tools.base import complete_structured, extract_json
    from harness.writing.pure import WRITING_ROUTING

    del emit  # 预留观测口（本轮不新增事件 kind）

    state_note = ""
    try:
        state_text = await asyncio.to_thread(project_state_text, store)
    except Exception as exc:                     # noqa: BLE001 —— 现状读不出来不该阻断
        log.warning("需求解析：项目现状读取失败（按空现状继续）：%s", exc)
        state_text = ""
        state_note = "（项目现状读取失败，已按空现状继续）"

    try:
        prompt = prompts.render("task.task_intake", message=raw or "",
                                project_state=state_text)
    except Exception as exc:                     # noqa: BLE001
        log.warning("需求解析：提示词渲染失败（按原始需求继续）：%s", exc)
        return None, f"需求解析失败（{reason_label('bad_json')}）：按原始需求继续"

    try:
        # **路由与 provider 的取值也在 try 内**（独立复审 S3）：这两处任一抛
        # （配置缺字段 / 路由键被改名）→ 异常穿出 run_intake → 穿出 REPL 的
        # ``_drive()`` → 而 REPL 主循环对普通命令**没有兜底 except** → 整个会话死掉。
        tier, temperature, max_tokens = WRITING_ROUTING["task.task_intake"]
        provider = llm.provider
        model = getattr(provider, tier, None) or provider.standard
        data, _usage, reason = await complete_structured(
            llm, model=model, temperature=temperature, max_tokens=max_tokens,
            prompt=prompt, extract=extract_json)
    except Exception as exc:                     # noqa: BLE001 —— 解析失败**不得**带崩 run
        log.warning("需求解析：LLM 调用异常（按原始需求继续）：%s", exc)
        return None, f"需求解析失败（{reason_label('api:exception')}）：按原始需求继续"

    if data is None:
        return None, f"需求解析失败（{reason_label(reason)}）：按原始需求继续"

    parsed = parse_intake(data, raw=raw, deliverables=deliverables)
    # 观测**如实**：模型没给目标就别说「目标已记」（独立复审 S5）；
    # **范围限定的现状按期如实说**（2026-11-15 独立审核 M4）：P11 之后 ``plan_arcs``
    # **已经**接到规划链（逐卷情节只规划前 N 卷）→ 再说「暂未生效」是**假话**；
    # 而 ``write_target`` **只注入不拦**（写作链的硬约束是另一件事）→ 那部分仍要说明。
    goal_note = "目标已记" if parsed["goal"] else "未给出目标"
    note = (f"需求已解析：{goal_note}、限制 {len(parsed['constraints'])} 条、"
            f"交付物 {'、'.join(parsed['deliverables'])}")
    if parsed["suggested_deliverables"] and \
            parsed["suggested_deliverables"] != parsed["deliverables"]:
        note += f"（模型推断的交付物：{'、'.join(parsed['suggested_deliverables'])}——仅作参考）"
    if parsed["plan_arcs"]:
        note += f"（本次范围：只规划前 {parsed['plan_arcs']} 卷的详细情节）"
    if parsed["write_target"]:
        note += f"（正文范围「{parsed['write_target']}」**只注入规划提示词**，暂不拦写作）"
    if state_note:
        note += state_note
    return parsed, note

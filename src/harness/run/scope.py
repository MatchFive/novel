"""范围与前提（从 brief 到规划链）：P11。

**为什么有这一层**：P10 的需求解析把 ``plan_arcs`` / ``write_target`` / ``premise`` /
``constraints`` 落进了 ``state.json`` 的 brief，但**规划链一个都没读**——「解析出来却没人用」。
本期把它们接上：范围口径（限卷）由代码判定，范围块由代码渲染，**提示词模板一个字不改**
（走已有的 ``{{instruction}}`` 变量）。

**只读**：本模块只读 ``state.json``，不写盘、不调 LLM。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

__all__ = ["BriefScope", "parse_chapter_range", "parse_write_range",
           "range_unfold_reason", "read_brief_scope", "scope_unfold_reason"]

log = logging.getLogger("run.scope")

#: 中文数字 → 阿拉伯数字（卷区间解析用；「十」= 10）
_CN_NUM: dict[str, int] = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_CN_TEN = {"十": 10, "十一": 11, "十二": 12, "十三": 13, "十四": 14, "十五": 15,
           "十六": 16, "十七": 17, "十八": 18, "十九": 19, "二十": 20}

#: 「第N卷」（N = 阿拉伯数字或中文数字）
_VOL_RE = re.compile(r"第\s*([0-9]+|[零一二三四五六七八九十]+)\s*卷")
#: 「第N章」——**章粒度跨卷歧义**（第 2 章属于哪一卷取决于当前卷）→ 命中即不限制
_CHAP_RE = re.compile(r"第\s*([0-9]+|[零一二三四五六七八九十]+)\s*章")

#: **否定/排除**词（2026-11-17 独立复审 R3）：「不写第一卷」「除了第一卷都写」——
#: 语义是**排除**某个卷，绝不能当成闭区间（曾被判成 (1,1) = 只许写第 1 卷，**正好反了**）。
_NEGATION_WORDS = ("不写", "不要写", "别写", "除了", "跳过", "除了…都", "勿写")

#: **方向/开区间**词：「从第二卷开始写」「第二卷以后」——语义是**第 N 卷及以后**，
#: 闭区间表达不了（上界无穷）→ 不能取 min/max（曾被判成 (2,2) = 只许写第 2 卷）。
_OPEN_END_WORDS = ("开始", "以后", "之后", "往后", "起写", "起", "及以后", "往后写")

#: **累计**方向词（P28）：「写到第三卷」「写至第五章」= **从第 1 卷/章累计到第 N 卷/章**
#: （1–N）；只作用于**单提及**——双提及（「第三卷写到第五卷」「从第二卷写到第五卷」）
#: 一律取 [min, max]，累计规则不越权。
_CUMULATIVE_RE = re.compile(r"写[到至]")


def _cn_number(text: str) -> int | None:
    """中文数字 → 阿拉伯数字（支持 一…十九 / 二十；解析不了 → None）。"""
    t = (text or "").strip()
    if not t:
        return None
    if t.isdigit():
        return int(t)
    if t in _CN_TEN:
        return _CN_TEN[t]
    if len(t) == 1 and t in _CN_NUM:
        return _CN_NUM[t]
    if len(t) == 2 and t[0] == "十":
        return 10 + _CN_NUM.get(t[1], 0)
    if len(t) == 2 and t[1] == "十":
        return _CN_NUM.get(t[0], 0) * 10
    return None


def _closed_interval(raw: str, nums: list[int]) -> tuple[int, int]:
    """提及号 → 闭区间（卷/章**同一收口**，P28 起）：

    - 双提及（含「从…写到…」「第三卷写到第五卷」）→ ``[min, max]``；
    - **单提及 + 「写到/写至」→ ``[1, N]`` 累计**（「写到第三卷」= 第 1–3 卷）；
    - 单提及无方向词（「只写第 3 卷」「第 3 章」）→ ``[N, N]`` 单点。
    """
    if len(nums) >= 2:
        return (min(nums), max(nums))
    if _CUMULATIVE_RE.search(raw):
        return (1, nums[0])
    return (nums[0], nums[0])


def _classify_range(text: str) -> tuple[tuple[int, int] | None, str]:
    """``write_target`` → **(卷区间, 折算不出来的原因)**；能折算时原因为空串。

    **同口径唯一实现**（G7：区间与原因必须**同时**来自这一处，否则「原因」会与
    「区间」各说各话）：``parse_write_range`` 只取本函数的第一个返回值。

    口径（P12 裁定 1；P28 累计语义）：
    - 命中「第N卷」→ 取全部提及的卷号 → **闭区间**（收口见 :func:`_closed_interval`：
      双提及 [min,max]；单提及+「写到/写至」**累计 [1,N]**；单提及无方向词 [N,N]）；
    - 命中「第N章」→ **不限制**（卷通道；章粒度走 :func:`parse_chapter_range`）；
    - **否定/排除**（「不写第一卷」「除了第一卷都写」）→ **不限制**（闭区间表达不了排除语义；
      2026-11-17 独立复审 R3：曾被判成 (1,1) = **只许写第 1 卷，正好反了**）；
    - **方向/开区间**（「从第二卷开始写」「第二卷以后」）→ **不限制**（上界无穷，闭区间表达不了；
      R3：曾被判成 (2,2) = 只许写第 2 卷 → 第 3 卷起全被拦）；
    - 什么都没命中 → **不限制**。

    **fail-open**：宁可**不拦**（用户要写却写不了，比拦错更糟）→ 解析不出就返回 None，
    由调用方**如实说明**「未能解析成确定区间 → 未限制」。
    """
    raw = (text or "").strip()
    if not raw:
        return None, ""
    if _CHAP_RE.search(raw):
        return None, "该范围是章粒度"      # 跨卷歧义 → 不限制
    if any(word in raw for word in _NEGATION_WORDS):
        return None, "该范围含排除语义"     # 闭区间表达不了排除 → 不限制
    if any(word in raw for word in _OPEN_END_WORDS):
        return None, "该范围是开区间"       # 上界无穷 → 不限制
    vols: list[int] = []
    for m in _VOL_RE.finditer(raw):
        n = _cn_number(m.group(1))
        if n is not None and n > 0:
            vols.append(n)
    if not vols:
        return None, "无法从该范围解析出卷区间"
    return _closed_interval(raw, vols), ""


def parse_write_range(text: str) -> tuple[int, int] | None:
    """``write_target`` 自由文本 → **卷区间** ``(from_vol, to_vol)``；解析不出 → ``None``。

    （口径见 :func:`_classify_range`；本函数只取区间，原因另见 ``range_unfold_reason``。）
    """
    return _classify_range(text)[0]


def range_unfold_reason(text: str) -> str:
    """``write_target`` **折算不成卷区间的原因**（能折算 → 空串）。

    给「如实说明」用（2026-11-17 独立复审 P20-1）：说明里必须说清**为什么**折算不出来，
    且**不得**加与原文**相反**的指令（「不写第一卷」是**排除**，「请只写此范围内的内容」
    是**限定** → 语义相反）。
    """
    return _classify_range(text)[1]


def parse_chapter_range(text: str) -> tuple[int, int] | None:
    """``write_target`` → **章区间** ``(from_chap, to_chap)``；不限制/折算不出 → ``None``。

    **口径（P27/A1 裁定）**：章号 = **全书全局 seq**（``chapters/{seq:04d}-*.md`` 扁平
    命名空间，resequence 也按全局重排）→「第 N 章」无跨卷歧义，章闸按 seq 过滤即可，
    不需要章→卷映射。与卷口径同款 fail-open：
    - **章与卷混提**（「第一章到第三卷」）→ None（既有测试钉死）；
    - **否定/排除**、**开区间** → None（闭区间表达不了）；
    - 无「第N章」命中 → None（卷粒度走 :func:`parse_write_range` 通道）。
    单提及的收口与卷**同一 helper**（:func:`_closed_interval`）：「写到第五章」→
    累计 (1,5)；「只写第五章」「第 3 章」→ 单点。
    """
    raw = (text or "").strip()
    if not raw or _VOL_RE.search(raw):
        return None
    if any(word in raw for word in _NEGATION_WORDS):
        return None
    if any(word in raw for word in _OPEN_END_WORDS):
        return None
    chaps: list[int] = []
    for m in _CHAP_RE.finditer(raw):
        n = _cn_number(m.group(1))
        if n is not None and n > 0:
            chaps.append(n)
    if not chaps:
        return None
    return _closed_interval(raw, chaps)


def scope_unfold_reason(text: str) -> str:
    """``write_target`` 折算不成**任何确定范围**（卷或章任一即可）的原因；能折算 → 空串。

    给「如实说明」用：卷与章**同口径唯一出口**（G7）——调用方只问「为什么整个范围
    没生效」，不必关心是哪种粒度。
    """
    if parse_write_range(text) is not None or parse_chapter_range(text) is not None:
        return ""
    raw = (text or "").strip()
    if not raw:
        return ""
    has_vol = bool(_VOL_RE.search(raw))
    has_chap = bool(_CHAP_RE.search(raw))
    if has_vol and has_chap:
        return "章与卷混提，无法折算"
    if has_chap:
        if any(word in raw for word in _NEGATION_WORDS):
            return "该范围含排除语义"
        if any(word in raw for word in _OPEN_END_WORDS):
            return "该范围是开区间"
        return "无法从该范围解析成章区间"
    return _classify_range(raw)[1]


@dataclass
class BriefScope:
    """brief 里的**范围与前提**（规划链消费的口径）。

    - ``plan_arcs``：**只认正整数**（0 / 负数 / 非数字 → None = 不限卷）。
      seed 的语义是「用户说『先规划第一卷』这类才给 1/3/5，否则 0（=全书）」——
      0 就是**全书**，不是「零卷」。
    - ``write_target``：本次正文写到哪（自由文本）→ 解析出的**卷区间**见
      :attr:`write_range`（**P12**：不只是注入，还要当**写盘闸**）。
    - ``premise`` / ``constraints``：需求解析出的前提与限制。
    """

    plan_arcs: int | None = None
    write_target: str = ""
    premise: str = ""
    goal: str = ""
    constraints: list[str] = field(default_factory=list)

    @property
    def write_range(self) -> tuple[int, int] | None:
        """本次正文的**卷区间**（从 ``write_target`` 解析；None = 不限制）。"""
        return parse_write_range(self.write_target)

    @property
    def chapter_range(self) -> tuple[int, int] | None:
        """本次正文的**章区间**（全局章号；A1/P27）。None = 非章粒度/不限制。"""
        return parse_chapter_range(self.write_target)

    def block(self, *, include_volume_limit: bool = True,
              for_writing: bool = False) -> str:
        """渲染范围块（**空则空串**——调用方拼串时字节级零变化）。

        形态固定，**各段只在有内容时出现**：

        ```
        【本次范围与前提（来自需求解析）】
        目标：<goal>
        前提：<premise>
        限制：
        - <每条限制>
        范围：本次只规划前 N 卷的详细情节（卷骨架仍按全书产出）
        正文范围：<write_target>
        ```

        **``include_volume_limit=False`` 时不出现「范围」段**（2026-11-15 独立审核 M1）：
        段一（卷骨架）的模板写着「⑤ 严格遵守规划诉求里的**每一条限制**」→ 若把
        「只规划前 N 卷」放进段一的 instruction，模型会照办 → **全书卷骨架被压成 N 卷**
        （实测）。段一**不限卷**（brief 的 ``plan_arcs`` 是「规划哪几卷做详细情节」，
        不是「骨架只写 N 卷」）→ 段一传 False，段二传 True。

        **``for_writing=True`` 时不出现「范围」段**（P12 裁定 3）：那行是**规划**链的
        范围，对写作链是噪音（写作链写的是具体章，不是「哪几卷做详细情节」）。

        「卷骨架仍按全书产出」那句是给段二看的**消歧**（避免模型把限卷理解成限骨架）。

        **``for_writing=True`` 时：卷区间折算得出 → 无言（卷闸生效）；章区间折算得出
        → 附「已按章拦截」一句（P27/A1）；两者都折算不出 → 正文范围行后附一句**
        **如实说明**（2026-11-17 P20）：模块 docstring 早就承诺「由调用方如实说明
        「未能解析成确定区间 → 未限制」」，此前**没有人说** → 用户的显式范围被**静默**
        丢掉（写盘闸不拦、模型也不知道为什么不拦）→ 写作侧自己按范围取舍。
        """
        lines: list[str] = []
        if self.goal:
            lines.append(f"目标：{self.goal}")
        if self.premise:
            lines.append(f"前提：{self.premise}")
        if self.constraints:
            lines.append("限制：")
            lines.extend(f"- {c}" for c in self.constraints)
        if self.plan_arcs and include_volume_limit and not for_writing:
            lines.append(f"范围：本次只规划前 {self.plan_arcs} 卷的详细情节"
                         "（卷骨架仍按全书产出）")
        if self.write_target:
            line = f"正文范围：{self.write_target}"
            if for_writing and self.write_range is None:
                if self.chapter_range is not None:
                    # A1/P27：章粒度折算得出 → 明说**按章拦截**（章闸会拦，模型不必猜）。
                    lo_c, hi_c = self.chapter_range
                    line += (f"（已按章拦截：只写第 {lo_c}–{hi_c} 章，"
                             "章号为全书全局编号）")
                else:
                    # **如实说明**（P20）+ **不与原文相反**（P21，独立复审 P20-1）：
                    # 「不写第一卷」是**排除**、「从第二卷开始」是**开区间** →
                    # 「请只写此范围内的内容」这种**限定式**指令与原文**语义相反** →
                    # 只说「为什么折算不出来 + 本次不按范围拦截」，**让模型按原文自行取舍**。
                    reason = scope_unfold_reason(self.write_target)
                    line += (f"（{reason}，无法折算成确定范围 → 本次不按范围拦截；"
                             "请按上述范围原文自行取舍）")
            lines.append(line)
        if not lines:
            return ""
        return "\n".join(["【本次范围与前提（来自需求解析）】", *lines])


def read_brief_scope(store) -> BriefScope:
    """读 brief 里的范围与前提；**没有 state.json / 坏文件 → 空 scope**（不抛）。

    ``plan_arcs`` 只认**正整数**：0（=全书）、负数、非数字一律视作「不限卷」。
    """
    from harness.run.state import RunState

    try:
        state = RunState.load(store.root)
    except Exception as exc:                     # noqa: BLE001 —— 坏 state 不该阻断规划
        log.warning("范围口径：state.json 读取失败（按无范围继续）：%s", exc)
        state = None
    if state is None or not isinstance(state.brief, dict):
        return BriefScope()

    brief = state.brief
    raw_arcs = brief.get("plan_arcs")
    plan_arcs: int | None = None
    try:
        value = int(raw_arcs)
    except (TypeError, ValueError):
        value = 0
    if value > 0:
        plan_arcs = value

    constraints: list[str] = []
    raw_constraints = brief.get("constraints")
    if isinstance(raw_constraints, str):
        # **字符串不是列表**（用户手改 brief 的常见形态）→ 当**一条**限制，
        # 绝不逐字拆成单字（独立审核 M6：「每章 3000 字」→ 7 行「- 每 / - 章 / …」）。
        text = raw_constraints.strip()
        if text:
            constraints.append(text)
    else:
        for c in raw_constraints or []:
            text = str(c).strip()
            if text:
                constraints.append(text)

    return BriefScope(
        plan_arcs=plan_arcs,
        write_target=str(brief.get("write_target") or "").strip(),
        premise=str(brief.get("premise") or "").strip(),
        goal=str(brief.get("goal") or "").strip(),
        constraints=constraints,
    )

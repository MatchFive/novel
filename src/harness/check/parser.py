# P6 验收链（设计 §1.5）：LLM 验收输出的解析 + quote 代码层回验
"""验收意见解析、**quote 代码层回验**、verdict/checklist 语义（纯函数）。

**为什么必须回验**（设计 §2.10 与 FlawedFictions 教训）：前沿模型会把动机误读、会把
不存在的句子当原文报上来。**凡能定位到正文的意见，必须带一段能在该章正文里
``locate_quote`` 命中的逐字 quote**——命中不了就丢弃（无证据的意见是噪音）。

**三类处置**（计数口径分开，别混）：

- **跳过**：字段非法（非 dict 条目 / issue 非 str / issue 全空白）——不算「丢弃」，
  那是格式问题不是证据问题；
- **丢弃**：意见指向某章、但 quote 缺失或回验不中——计入丢弃数（观测要如实报）；
- **保留**：回验通过的意见；以及**全局类**意见（定位串剥装饰后是「全局」这类口径，
  或以全局词开头且没提到任何一章）——全局性意见按 seed 设计允许无 quote（它本来就是跨章的）。

**全局判定的顺序**（复审 MUST 1 + follow-up 2.2/2.3，见 :func:`_classify_locator`）：
剥装饰后**恰好**是全局口径 → 全局（**不解析章**，免得被同名章名劫持）；否则**先解析章**
→ 章级（「全书第2章」是章级定位串，必须走 quote 回验）；解析不到但**以全局词开头且没提到
任何一章**（「全局节奏」）→ 全局；**提到了某一章却解析不到**（「全书第99章」）→ 丢弃
（无从回验，不许借「全局」的外壳绕过证据纪律）。

**quote 纪律按 kind 分层**（``parse_check_issues`` 的 ``require_quote``）：全书级验收
（``global_check``）章级意见**必须**带可定位 quote（证据纪律）；情节级验收
（``check_plots``）**不要求**（seed 的情节级输出格式没有 quote 字段——意见针对情节
结构，未必有可引的原文片段），但**给了 quote 仍要回验**。

维度归类照 seed ``autopilot_stages._DIMENSION_BY_TYPE``（人物/情节/爽点/伏笔/世界观/文字）。
"""
from __future__ import annotations

from dataclasses import dataclass

from harness.check.sampling import match_chapter, mentions_chapter
from harness.store.project import ChapterDoc
from harness.text import locate_quote

__all__ = [
    "CheckIssue",
    "parse_check_issues",
    "parse_checklist",
    "parse_verdict",
    "verdict_from_checklist",
]


@dataclass
class CheckIssue:
    """一条验收意见（回验通过或全局类）。``chapter`` 为 None = 全局性意见。"""

    chapter: int | None
    type: str
    dimension: str
    issue: str
    quote: str | None
    fix: str


#: 意见类型 → 方法论维度（seed ``autopilot_stages._DIMENSION_BY_TYPE`` 逐字）
_DIMENSION_BY_TYPE: dict[str, str] = {
    "character": "人物", "personality": "人物", "relation": "人物", "cast": "人物",
    "plot": "情节", "structure": "情节", "pacing": "情节", "goal": "情节",
    "hook": "爽点", "gag": "爽点", "payoff": "爽点", "爽点": "爽点",
    "promise": "伏笔", "foreshadow": "伏笔", "promise_break": "伏笔",
    "world": "世界观", "setting": "世界观",
    "ai_flavor": "文字", "style": "文字", "logic": "文字", "timeline": "文字",
    "memory_conflict": "文字", "knowledge_leak": "文字",
}

#: 全局性意见的定位口径（seed 模板：``"chapter": "第X章 或 「全局」"``）
#: **不是子串判断**（复审 MUST 1）：子串判断会把「全书第2章」「第2章整体」这种
#: **章级**定位串当成全局意见 → quote 永不复验 → 编造的片段混进台账。
#: 但也不能只认「整串严格相等」——模板自己印的就是带书名号的「「全局」」，
#: 模型还会写「全局节奏」「全书主线」这类**以全局词开头**的定位串（复审 follow-up
#: 2.2/2.3）。故判定分五步（见 :func:`_classify_locator`）。
_GLOBAL_MARKERS = ("全局", "全书", "整体")

#: 定位串首尾可能带的装饰（书名号/方括号/圆括号）与空白
_DECORATION = "「」『』【】（）()《》〈〉 \t\r\n"

#: 合法维度（我们自己的固定词表——观测与台账的 dimension 只认这些）
_DIMENSIONS = frozenset(_DIMENSION_BY_TYPE.values())

#: 合法 verdict
_VERDICTS = ("pass", "fix", "replan")

#: 清单条目的合法判定
_CHECKLIST_VERDICTS = ("pass", "fail", "na")


def _dimension_of(issue_type: str | None) -> str:
    """意见类型 → 方法论维度（未知类型 → 情节，seed 同款默认）。"""
    return _DIMENSION_BY_TYPE.get(str(issue_type or "").strip().lower(), "情节")


def _bare_locator(text: str) -> str:
    """剥掉定位串首尾的装饰与空白：「「全局」」→「全局」。"""
    return (text or "").strip().strip(_DECORATION).strip()


def _is_global(chapter_text: str) -> bool:
    """定位串**剥装饰后整串**落在全局口径（「全局」「全书」「整体」）。

    **不是子串判断**（复审 MUST 1）：子串判断会把「全书第2章」「第2章整体」这种
    **章级**定位串当成全局意见 → quote 永不复验 → 编造的片段混进台账。
    """
    return _bare_locator(chapter_text) in _GLOBAL_MARKERS


def _starts_with_global(chapter_text: str) -> bool:
    """定位串是否**以全局词开头**且**没有提到某一章**（「全局节奏」「全书主线」）。

    - 只在**解析不出章**时才用（否则「全书第2章」会先被解析成第 2 章 → 章级口径 →
      quote 回验照做）；
    - **剥装饰**后再判（「「全局节奏」」也算）；
    - **提到了某一章**（「全书第99章」）→ **不算**全局口径：它说的是某一章，只是那章
      解析不到 → 无从回验 → 丢弃（不能借「全局」的外壳绕过 quote 回验）。
    """
    bare = _bare_locator(chapter_text)
    if not bare:
        return False
    if not any(bare.startswith(marker) for marker in _GLOBAL_MARKERS):
        return False
    return not mentions_chapter(bare)


def _classify_locator(chapter_text: str, chapters: list[ChapterDoc]) -> tuple[ChapterDoc | None, bool]:
    """定位串 → (章, 是否全局口径)。

    判定顺序（复审 MUST 1 的修复 + follow-up 2.2/2.3 的放宽）：

    1. **剥装饰后恰好是全局口径**（「全局」「「全局」」）→ **全局**，且**不去解析章**
       ——免得被同名章名劫持（复审 2.3：定位串「全局」命中章名「全局观」）；
    2. 否则**先解析章**（章号 → 标题精确 → 标题包含）→ **章级**
       （保住 MUST 1 的修复：「全书第2章」解析成第 2 章 → 走 quote 回验）；
    3. 解析不到、但**以全局词开头且没提到任何一章**（「全局节奏」「全书主线」）→ **全局**；
    4. **提到了某一章却解析不到**（「全书第99章」）→ 既不是章也不是全局 → 调用方丢弃
       （无从回验，不许借「全局」的外壳绕过证据纪律）；
    5. 都不满足 → 既不是章也不是全局（调用方丢弃）。
    """
    if _is_global(chapter_text):
        return None, True
    chapter = match_chapter(chapter_text, chapters)
    if chapter is not None:
        return chapter, False
    if _starts_with_global(chapter_text):
        return None, True
    return None, False


def _quote_in_any_chapter(quote: str, chapters: list[ChapterDoc]) -> bool:
    """quote 是否命中**任一章**的正文（全局意见的回验口径：全书任一章命中即算证据）。"""
    for c in chapters or []:
        if locate_quote(c.body or "", quote) is not None:
            return True
    return False


def parse_check_issues(data: object, *, chapters: list[ChapterDoc],
                       require_quote: bool = True) -> tuple[list[CheckIssue], int]:
    """解析验收输出 → ``(可用意见, 丢弃数)``。

    ``data`` 是 LLM 返回的 dict：``{"issues":[{chapter,type,dimension,quote,issue,fix}]}``。

    - 顶层不是 dict / 没有 issues 键 / issues 不是 list → ``([], 0)``（不炸）；
    - 条目不是 dict、``issue`` 非 str 或全空白 → **跳过**（不计入丢弃数）；
    - **先解析章**（``match_chapter``）：解析到章 → **章级**口径（``quote`` 必须非空
      **且** ``locate_quote`` 命中**该章正文**——否则**丢弃**，计入丢弃数）；
    - 解析不到章 → 再看**全局口径**（剥装饰后是「全局」「全书」「整体」，或以全局词开头
      且没提到任何一章）：``chapter=None``、quote 可为空 → **保留**；带了 quote 则用
      **全书任一章**回验一次——命中就保留，命中不到就**清掉 quote**（落进「无原文片段」
      那一类）；
    - 解析不到章又不是全局口径 → **丢弃**（计入丢弃数）。

    **quote 纪律按 kind 分层**（``require_quote``）：

    - ``require_quote=True``（默认，**全书级验收** ``global_check`` 用）：章级意见
      没有 quote → **丢弃**（证据纪律：模型会把动机误读）；
    - ``require_quote=False``（**情节级验收** ``check_plots`` 用）：seed 的情节级
      契约里意见针对**情节结构**，输出格式**没有 quote 字段**——没有 quote 的章级
      意见**保留**（``quote=None``）；**带了 quote 仍必须命中**（编造的照丢）。
    """
    if not isinstance(data, dict):
        return [], 0
    raw_items = data.get("issues")
    if not isinstance(raw_items, list):
        return [], 0

    issues: list[CheckIssue] = []
    discarded = 0
    for item in raw_items:
        if not isinstance(item, dict):
            continue                                  # 跳过（格式问题，不计入丢弃）
        issue_text = item.get("issue")
        if not isinstance(issue_text, str) or not issue_text.strip():
            continue                                  # 同上
        issue_text = issue_text.strip()
        quote_text = item.get("quote")
        quote = quote_text.strip() if isinstance(quote_text, str) else ""
        issue_type = str(item.get("type") or "goal").strip() or "goal"
        # 维度：**只认我们自己的固定词表**（人物/情节/爽点/伏笔/世界观/文字）——
        # 模型给的 dimension 字段同样是自由文本（可能写「connection timeout」），
        # 直接回显进观测会撞上 loop 的瞬时词扫描（复审 follow-up 2.1）。不合词表 → 按 type 派生。
        dimension = str(item.get("dimension") or "").strip()
        if dimension not in _DIMENSIONS:
            dimension = _dimension_of(issue_type)
        fix = str(item.get("fix") or "").strip()
        chapter_text = str(item.get("chapter") or "").strip()

        # 定位串判定（复审 MUST 1 + follow-up 2.2/2.3）：五步——
        # 剥装饰后恰好是全局口径 → 全局（且**不解析章**，免得被同名章名劫持）；
        # 否则先解析章 → 章级（「全书第2章」走 quote 回验）；解析不到但以全局词开头
        # → 全局；都不满足 → 丢弃。
        chapter, is_global = _classify_locator(chapter_text, chapters)
        if is_global:
            # 全局口径：quote 若有 → 用**全书任一章**回验一次；命中就保留，
            # 命中不到就**清掉 quote**（于是它落进「无原文片段」那一类 → 台账里
            # 不给「交给 AI 修」；改写无从下手）。全局意见本身**不因 quote 不中而丢弃**。
            if quote and not _quote_in_any_chapter(quote, chapters):
                quote = ""
            issues.append(CheckIssue(chapter=None, type=issue_type, dimension=dimension,
                                     issue=issue_text, quote=quote or None, fix=fix))
            continue
        if chapter is None:
            # 定位不到章又不是全局口径 → 无从回验 → 丢弃
            discarded += 1
            continue
        if not quote:
            if require_quote:
                discarded += 1                      # 章级意见没有 quote = 无证据
                continue
            # 情节级验收：不强制 quote（意见针对情节结构，未必可引原文）
            issues.append(CheckIssue(chapter=chapter.seq, type=issue_type,
                                     dimension=dimension, issue=issue_text,
                                     quote=None, fix=fix))
            continue
        if locate_quote(chapter.body or "", quote) is None:
            discarded += 1                          # quote 编造/不逐字 → 丢弃
            continue
        issues.append(CheckIssue(chapter=chapter.seq, type=issue_type, dimension=dimension,
                                 issue=issue_text, quote=quote, fix=fix))
    return issues, discarded


def parse_verdict(data: object) -> tuple[str, int | None, list[dict]]:
    """解析 verdict + score + checklist。

    - verdict：``pass``/``fix``/``replan``（大小写不敏感）；缺省或非法值 → ``pass``；
    - score：只认 int（bool 不算）；其余 → None；
    - checklist：只保留 dict 条目。
    """
    if not isinstance(data, dict):
        return "pass", None, []
    verdict = str(data.get("verdict") or "").strip().lower()
    if verdict not in _VERDICTS:
        verdict = "pass"
    raw_score = data.get("score")
    score = raw_score if isinstance(raw_score, int) and not isinstance(raw_score, bool) else None
    raw_checklist = data.get("checklist")
    checklist = [item for item in raw_checklist if isinstance(item, dict)] \
        if isinstance(raw_checklist, list) else []
    return verdict, score, checklist


def parse_checklist(data: object) -> list[dict]:
    """解析验收输出里的**清单判定** → ``[{item, verdict, evidence}]``。

    - verdict 只认 ``pass``/``fail``/``na``（大小写不敏感）；非法值 → **跳过该条**
      （不猜、不当 pass）；
    - ``item`` 必须是非空 str（条目标题）；缺失 → 跳过；
    - ``evidence`` 只认 str（模型给的依据）；缺失 → 空串；
    - 顶层不是 dict / 无 checklist / 不是 list → 空列表（不炸）。
    """
    if not isinstance(data, dict):
        return []
    raw = data.get("checklist")
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        item = str(entry.get("item") or "").strip()
        if not item:
            continue
        verdict = str(entry.get("verdict") or "").strip().lower()
        if verdict not in _CHECKLIST_VERDICTS:
            continue
        evidence = str(entry.get("evidence") or "").strip()
        out.append({"item": item, "verdict": verdict, "evidence": evidence})
    return out


def verdict_from_checklist(checklist: list[dict], verdict: str) -> str:
    """清单驱动：任一条 verdict=fail → 总 verdict 不得为 pass（至少 fix）。

    ``replan`` 保持 replan（结构性问题比清单 fail 更重）；``na`` 视为通过。
    """
    for item in checklist or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("verdict") or "").strip().lower() == "fail":
            return "fix" if verdict == "pass" else verdict
    return verdict

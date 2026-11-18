# 验收意见解析 + quote 代码层回验（P6 Task 2）
"""parse_check_issues / parse_verdict / verdict_from_checklist 语义钉死。

回验纪律（与 P5 审计同款硬校验、方案 §2.10「无证据的条目直接丢弃」一致）：

- 意见指向**具体章**时：quote 必须经 ``harness.text.locate_quote`` 命中**该章正文**
  ——命中不了 → **丢弃**（计入丢弃数）；**没有 quote 也丢弃**（无证据的意见是噪音）；
- 意见是**全局类**（chapter 定位不到且落在「全局」口径）→ chapter=None，
  quote 可为空 → **保留**（全局性意见按设计允许无 quote）；
- 字段非法（非 dict 条目 / issue 非 str）→ **跳过**不炸（不计入丢弃数——那是
  「证据不过关」的计数口径）。

verdict 语义：缺省 pass；score 非 int → None；清单里任一条 verdict=fail →
总 verdict 不得为 pass（至少 fix；replan 保持 replan）。
"""
from __future__ import annotations

import pytest

from harness.check.parser import (
    CheckIssue,
    parse_check_issues,
    parse_verdict,
    verdict_from_checklist,
)
from harness.store.project import ChapterDoc

BODY1 = "第一夜，风雪压塌了山门。少年握紧了手里的木剑。"
BODY2 = "第二夜，火光映红了半边天。老者把剑递了过去。"
BODY3 = "第三夜，江河东去。众人立在渡口，谁也没有开口。"
QUOTE2 = "老者把剑递了过去"


def _chapters() -> list[ChapterDoc]:
    return [
        ChapterDoc(seq=1, meta={"seq": 1, "title": "风雪夜", "status": "done"}, body=BODY1),
        ChapterDoc(seq=2, meta={"seq": 2, "title": "递剑", "status": "done"}, body=BODY2),
        ChapterDoc(seq=3, meta={"seq": 3, "title": "渡口", "status": "done"}, body=BODY3),
    ]


def _item(**kw) -> dict:
    item = {"chapter": "第2章", "type": "character", "dimension": "人物",
            "quote": QUOTE2, "issue": "老者动机不明", "fix": "补一句回忆"}
    item.update(kw)
    return item


# ------------------------------------------------------------------ 回验纪律

def test_fabricated_quote_discarded():
    """quote 在正文里不存在 → 丢弃、丢弃数 +1。"""
    data = {"issues": [_item(quote="这句话正文里根本不存在")]}
    issues, discarded = parse_check_issues(data, chapters=_chapters())
    assert issues == []
    assert discarded == 1


def test_issue_without_quote_discarded():
    """章级意见无 quote → 丢弃（无证据的意见是噪音）。"""
    data = {"issues": [_item(quote=""), {"chapter": "第2章", "type": "character",
                                        "dimension": "人物", "issue": "x", "fix": "y"}]}
    issues, discarded = parse_check_issues(data, chapters=_chapters())
    assert issues == []
    assert discarded == 2


def test_quote_verified_against_that_chapter():
    """quote 命中别章正文不算数——必须命中**意见所指那一章**。"""
    data = {"issues": [_item(chapter="第3章", quote=QUOTE2)]}
    issues, discarded = parse_check_issues(data, chapters=_chapters())
    assert issues == []
    assert discarded == 1


def test_kept_issue_carries_fields():
    """回验通过的章级意见：章号/类型/维度/quote/意见/修订指令都带过来。"""
    data = {"issues": [_item()]}
    issues, discarded = parse_check_issues(data, chapters=_chapters())
    assert discarded == 0
    assert len(issues) == 1
    got = issues[0]
    assert isinstance(got, CheckIssue)
    assert got.chapter == 2
    assert got.type == "character"
    assert got.dimension == "人物"
    assert got.quote == QUOTE2
    assert got.issue == "老者动机不明"
    assert got.fix == "补一句回忆"


def test_global_issue_kept():
    """「全局」意见无 quote → 保留、chapter=None。"""
    data = {"issues": [{"chapter": "全局", "type": "goal", "dimension": "情节",
                        "quote": "", "issue": "主线目标没推进", "fix": "调整主线"}]}
    issues, discarded = parse_check_issues(data, chapters=_chapters())
    assert discarded == 0
    assert len(issues) == 1
    assert issues[0].chapter is None
    assert issues[0].quote is None
    assert issues[0].issue == "主线目标没推进"


def test_global_issue_with_quote_kept():
    """「全局」意见带了 quote 也保留（全局类不参与回验）。"""
    data = {"issues": [{"chapter": "全书", "type": "goal", "dimension": "情节",
                        "quote": QUOTE2, "issue": "整体节奏偏慢", "fix": "提速"}]}
    issues, discarded = parse_check_issues(data, chapters=_chapters())
    assert discarded == 0
    assert len(issues) == 1
    assert issues[0].chapter is None
    assert issues[0].quote == QUOTE2


def test_unresolvable_chapter_discarded():
    """指向不存在的章（第99章）→ 不是全局口径 → 回验不了 → 丢弃。"""
    data = {"issues": [_item(chapter="第99章")]}
    issues, discarded = parse_check_issues(data, chapters=_chapters())
    assert issues == []
    assert discarded == 1


def test_bad_items_skipped():
    """非 dict 条目 / issue 非 str / issue 缺失 / issue 全空白 → 跳过不炸。"""
    data = {"issues": [
        "不是 dict",
        123,
        {"chapter": "第1章", "type": "pacing", "issue": 123, "fix": "x"},
        {"chapter": "第1章", "type": "pacing", "fix": "x"},
        {"chapter": "第1章", "type": "pacing", "issue": "   ", "fix": "x"},
    ]}
    issues, discarded = parse_check_issues(data, chapters=_chapters())
    assert issues == []
    # 字段非法是「跳过」，不计入「丢弃数」（那是证据不过关的计数口径）
    assert discarded == 0


def test_bad_items_do_not_stop_good_ones():
    """坏条目跳过、好条目照留（不因一条坏数据丢掉整批意见）。"""
    data = {"issues": ["不是 dict", _item(), {"chapter": "第1章", "issue": 5}]}
    issues, discarded = parse_check_issues(data, chapters=_chapters())
    assert discarded == 0
    assert [i.chapter for i in issues] == [2]


def test_parse_issues_missing_key():
    """顶层没有 issues（或不是 dict）→ 空结果，不炸。"""
    assert parse_check_issues({}, chapters=_chapters()) == ([], 0)
    assert parse_check_issues(None, chapters=_chapters()) == ([], 0)
    assert parse_check_issues("不是 JSON", chapters=_chapters()) == ([], 0)


def test_dimension_filled_from_type():
    """模型没给 dimension → 按 type 归类（模板里 dimension 字段可能缺）。"""
    data = {"issues": [_item(dimension="", type="promise_break")]}
    issues, _ = parse_check_issues(data, chapters=_chapters())
    assert issues[0].dimension == "伏笔"


def test_type_defaults_to_goal():
    """type 缺失 → goal（seed 同款默认）。"""
    data = {"issues": [{"chapter": "第2章", "quote": QUOTE2, "issue": "x", "fix": "y"}]}
    issues, _ = parse_check_issues(data, chapters=_chapters())
    assert issues[0].type == "goal"


# ------------------------------------------------- quote 纪律按 kind 分层

def test_plot_kind_allows_chapter_issue_without_quote():
    """require_quote=False：章级意见没有 quote → **保留**（quote=None）、章号正确。

    情节级验收（seed 契约）针对**情节结构**，意见未必有可引的原文片段；
    **默认行为不变**（require_quote=True 仍要求 quote——全书级验收的证据纪律）。
    """
    data = {"issues": [{"chapter": "第2章", "type": "plot", "dimension": "情节",
                        "quote": "", "issue": "情节推进缺一拍", "fix": "补一拍收束"}]}
    issues, discarded = parse_check_issues(data, chapters=_chapters(), require_quote=False)
    assert discarded == 0
    assert len(issues) == 1
    got = issues[0]
    assert got.chapter == 2
    assert got.quote is None
    assert got.type == "plot"
    assert got.issue == "情节推进缺一拍"
    assert got.fix == "补一拍收束"
    # 默认仍是要求 quote（默认行为不动：T2/T3 的既有测试原样全绿）
    default_issues, default_discarded = parse_check_issues(data, chapters=_chapters())
    assert default_issues == []
    assert default_discarded == 1


def test_plot_kind_still_rejects_fabricated_quote():
    """require_quote=False 但 quote 是编造的（正文里定位不到）→ **照丢**。"""
    data = {"issues": [_item(quote="这句话正文里根本不存在")]}
    issues, discarded = parse_check_issues(data, chapters=_chapters(), require_quote=False)
    assert issues == []
    assert discarded == 1


def test_plot_kind_keeps_verified_quote():
    """require_quote=False 但 quote 命中该章正文 → 照留、quote 原样带过来。"""
    data = {"issues": [_item()]}
    issues, discarded = parse_check_issues(data, chapters=_chapters(), require_quote=False)
    assert discarded == 0
    assert len(issues) == 1
    assert issues[0].chapter == 2
    assert issues[0].quote == QUOTE2


def test_plot_kind_still_discards_unresolvable_chapter():
    """require_quote=False 也救不了定位不到章的意见（无从回验 → 照丢）。"""
    data = {"issues": [_item(chapter="第99章", quote="")]}
    issues, discarded = parse_check_issues(data, chapters=_chapters(), require_quote=False)
    assert issues == []
    assert discarded == 1


# ---------------------------------------- 全局判定：先解析章，解析不到才当全局

@pytest.mark.parametrize("locator", ["全书第2章", "第2章整体", "整体第2章",
                                     "第2章整体节奏偏慢"])
def test_global_marker_inside_chapter_locator_is_not_global(locator):
    """定位串**含**「全书/整体」但能解析出章 → 按**章级**处理（quote 回验照旧）。

    复审实测过这几种写法全都绕过了回验（判定是纯子串且发生在章解析之前）：
    编造的 quote 被当「全局意见」落进台账——全局口径允许无 quote，等于没有证据纪律。
    """
    data = {"issues": [_item(chapter=locator, quote="这句话正文里根本不存在")]}
    issues, discarded = parse_check_issues(data, chapters=_chapters())
    assert issues == []
    assert discarded == 1


@pytest.mark.parametrize("locator", ["全书第2章", "第2章整体", "整体第2章"])
def test_global_marker_inside_chapter_locator_keeps_verified_quote(locator):
    """同一批定位串 + **真** quote → 保留，且章号 = 解析出来的章（2）。"""
    data = {"issues": [_item(chapter=locator)]}
    issues, discarded = parse_check_issues(data, chapters=_chapters())
    assert discarded == 0
    assert len(issues) == 1
    assert issues[0].chapter == 2
    assert issues[0].quote == QUOTE2


@pytest.mark.parametrize("locator", ["全局", "全书", "整体"])
def test_exact_global_markers_kept_as_global(locator):
    """全局口径只认**整串严格相等**（「全局」「全书」「整体」）→ chapter=None。"""
    data = {"issues": [{"chapter": locator, "type": "goal", "dimension": "情节",
                        "quote": "", "issue": "主线目标没推进", "fix": "调整主线"}]}
    issues, discarded = parse_check_issues(data, chapters=_chapters())
    assert discarded == 0
    assert len(issues) == 1
    assert issues[0].chapter is None
    assert issues[0].quote is None


@pytest.mark.parametrize("locator", ["全书主线", "整体节奏"])
def test_global_prefix_marker_kept_as_global(locator):
    """以全局词开头、又**定位不到任何章** → 全局口径（复审 follow-up 2.2）。

    旧口径（整串严格相等）会把这类定位串**静默丢弃**——全局意见从台账里消失，
    用户看不见。模型写「全书主线」「整体节奏」时说的是全局意思，不是章号。
    """
    data = {"issues": [{"chapter": locator, "type": "goal", "dimension": "情节",
                        "quote": "", "issue": "主线弱", "fix": "加重主线"}]}
    issues, discarded = parse_check_issues(data, chapters=_chapters())
    assert discarded == 0
    assert len(issues) == 1
    assert issues[0].chapter is None


def test_locator_naming_a_chapter_is_chapter_level():
    """定位串**指的是某章**（「全局观」＝章名）→ 章级口径，不是全局。

    章级意见没有 quote → 正文级验收（require_quote=True）丢弃它（无从回验）。
    """
    chapters = [ChapterDoc(seq=1, meta={"seq": 1, "title": "全局观", "status": "done"}, body=BODY1)]
    data = {"issues": [{"chapter": "全局观", "type": "goal", "dimension": "情节",
                        "quote": "", "issue": "主线弱", "fix": "加重主线"}]}
    issues, discarded = parse_check_issues(data, chapters=chapters)
    assert issues == []
    assert discarded == 1


# ---------------------------------------- 全局意见带 quote：全书回验一次（M-5）

def test_global_issue_quote_verified_against_any_chapter():
    """全局意见带 quote → 用**全书任一章**回验一次：命中就保留 quote。"""
    data = {"issues": [{"chapter": "全局", "type": "goal", "dimension": "情节",
                        "quote": QUOTE2, "issue": "整体节奏偏慢", "fix": "提速"}]}
    issues, discarded = parse_check_issues(data, chapters=_chapters())
    assert discarded == 0
    assert len(issues) == 1
    assert issues[0].chapter is None
    assert issues[0].quote == QUOTE2


def test_global_issue_fabricated_quote_cleared_but_kept():
    """全局意见带 quote 但全书都定位不到 → **清掉 quote**（仍保留为全局意见）。

    清掉后它落进「无原文片段」那一类 → /issues 不给「交给 AI 修」（改写无从下手）。
    """
    data = {"issues": [{"chapter": "整体", "type": "goal", "dimension": "情节",
                        "quote": "这句话正文里根本不存在",
                        "issue": "整体节奏偏慢", "fix": "提速"}]}
    issues, discarded = parse_check_issues(data, chapters=_chapters())
    assert discarded == 0          # 全局意见不因 quote 不中而丢弃（只是清掉 quote）
    assert len(issues) == 1
    assert issues[0].chapter is None
    assert issues[0].quote is None


def test_global_issue_quote_only_hits_outside_target_dropped():
    """范围限定时，全局意见的 quote 只在**本次目标章**里找（范围外不算证据）。"""
    chapters = [ChapterDoc(seq=1, meta={"seq": 1, "title": "风雪夜", "status": "done"},
                           body=BODY1)]
    data = {"issues": [{"chapter": "全局", "type": "goal", "dimension": "情节",
                        "quote": QUOTE2, "issue": "整体节奏偏慢", "fix": "提速"}]}
    issues, discarded = parse_check_issues(data, chapters=chapters)
    assert discarded == 0
    assert issues[0].quote is None    # 第2章不在目标集合里 → 命中不到 → 清掉


# ------------------------------------------------------------------ verdict

def test_parse_verdict_defaults():
    """缺 verdict → pass；score 非 int → None。"""
    assert parse_verdict({}) == ("pass", None, [])
    assert parse_verdict(None) == ("pass", None, [])
    assert parse_verdict({"verdict": "fix", "score": 8}) == ("fix", 8, [])
    assert parse_verdict({"verdict": "FIX"})[0] == "fix"
    assert parse_verdict({"score": "8"})[1] is None
    assert parse_verdict({"score": 8.5})[1] is None
    assert parse_verdict({"score": None})[1] is None
    assert parse_verdict({"verdict": "胡说"})[0] == "pass"


def test_parse_verdict_checklist():
    """checklist 取 dict 条目（非 dict 条目剔掉）。"""
    v, s, cl = parse_verdict({"verdict": "pass", "score": 9,
                              "checklist": [{"item": "限制遵守", "verdict": "pass",
                                             "evidence": "未见违反"},
                                            "不是 dict",
                                            {"item": "目标达成", "verdict": "fail",
                                             "evidence": "主线跑偏"}]})
    assert (v, s) == ("pass", 9)
    assert len(cl) == 2
    assert cl[1]["verdict"] == "fail"


def test_checklist_fail_downgrades_verdict():
    """清单有 fail → 总 verdict 至少 fix；replan 保持 replan；na 视为通过。"""
    failing = [{"item": "限制遵守", "verdict": "fail", "evidence": "违反了限制"}]
    assert verdict_from_checklist(failing, "pass") == "fix"
    assert verdict_from_checklist(failing, "fix") == "fix"
    assert verdict_from_checklist(failing, "replan") == "replan"
    passing = [{"item": "目标达成", "verdict": "pass"}, {"item": "节奏", "verdict": "na"}]
    assert verdict_from_checklist(passing, "pass") == "pass"
    assert verdict_from_checklist([], "pass") == "pass"


# ------------------------------------------------------------------ 全局定位串（复审 follow-up 2.2/2.3）

def test_global_locator_with_corner_brackets():
    """模板自己印的就是「「全局」」——带书名号的定位串必须是全局口径。"""
    data = {"issues": [{"chapter": "「全局」", "type": "goal", "dimension": "情节",
                        "quote": "", "issue": "主线目标没推进", "fix": "调整主线"}]}
    issues, discarded = parse_check_issues(data, chapters=_chapters())
    assert discarded == 0
    assert len(issues) == 1
    assert issues[0].chapter is None


def test_global_locator_with_prefix():
    """以全局词开头且未指向任何章（「全局节奏」「全书主线」「整体节奏」）→ 全局口径。"""
    for locator in ("全局节奏", "全书主线", "整体节奏", "全局（全书节奏）"):
        data = {"issues": [{"chapter": locator, "type": "goal", "dimension": "情节",
                            "quote": "", "issue": "x", "fix": "y"}]}
        issues, discarded = parse_check_issues(data, chapters=_chapters())
        assert discarded == 0, locator
        assert len(issues) == 1, locator
        assert issues[0].chapter is None, locator


def test_global_locator_not_hijacked_by_chapter_title():
    """定位串「全局」不该被章名「全局观」劫持成章级（否则全局意见被丢）。"""
    chapters = [ChapterDoc(seq=1, meta={"seq": 1, "title": "全局观", "status": "done"}, body=BODY1),
                ChapterDoc(seq=2, meta={"seq": 2, "title": "递剑", "status": "done"}, body=BODY2)]
    data = {"issues": [{"chapter": "全局", "type": "goal", "dimension": "情节",
                        "quote": "", "issue": "整体节奏偏慢", "fix": "提速"}]}
    issues, discarded = parse_check_issues(data, chapters=chapters)
    assert discarded == 0
    assert len(issues) == 1
    assert issues[0].chapter is None


def test_chapter_locator_with_global_prefix_still_resolves():
    """「全书第2章」仍是**章级**定位串 → 编造的 quote 被丢弃（MUST 1 的修复不被削弱）。"""
    data = {"issues": [{"chapter": "全书第2章", "type": "character", "dimension": "人物",
                        "quote": "这句话正文里根本不存在", "issue": "x", "fix": "y"}]}
    issues, discarded = parse_check_issues(data, chapters=_chapters())
    assert issues == []
    assert discarded == 1


def test_model_dimension_outside_vocabulary_is_derived():
    """模型给的 dimension 不在固定词表 → 按 type 派生（不让自由文本进观测）。"""
    data = {"issues": [_item(dimension="connection timeout")]}
    issues, _ = parse_check_issues(data, chapters=_chapters())
    assert issues[0].dimension == "人物"          # type=character → 人物
    assert "connection" not in issues[0].dimension


def test_prefix_marker_mentioning_a_missing_chapter_is_discarded():
    """「全书第99章」：说的是某一章、只是那章解析不到 → **丢弃**，不许借「全局」外壳绕过回验。"""
    data = {"issues": [{"chapter": "全书第99章", "type": "character", "dimension": "人物",
                        "quote": QUOTE2, "issue": "x", "fix": "y"}]}
    issues, discarded = parse_check_issues(data, chapters=_chapters())
    assert issues == []
    assert discarded == 1


def test_bracketed_prefix_marker_kept_as_global():
    """带装饰的全局前缀定位串（「「全局节奏」」）→ 全局口径（剥装饰后再判）。"""
    data = {"issues": [{"chapter": "「全局节奏」", "type": "goal", "dimension": "情节",
                        "quote": "", "issue": "x", "fix": "y"}]}
    issues, discarded = parse_check_issues(data, chapters=_chapters())
    assert discarded == 0
    assert len(issues) == 1
    assert issues[0].chapter is None

"""planning.context 的契约测试：五模板变量组装 + 逐字渲染 + 反向校验（task-4-brief.md）。

夹具全部走真项目（tmp_path + ProjectStore 写盘）——本模块是**文件格式 → LLM 提示词**的
接缝，测试必须钉死「只经 store 读」的取值口径。渲染逐字来源：
web_dream outline_service.py:199-348（units_block/anchors_block/chapter_line）、
:983-994（selfcheck 渲染）、:629-637（headers 组渲染）、autopilot_runner.py:202-268
（plan_plots 变量形态）。
"""
import re

import pytest

from harness.planning import pure
from harness.planning.context import (
    anchors_block,
    beats_vars,
    chapter_line,
    headers_vars,
    plan_plots_vars,
    plot_units_vars,
    selfcheck_vars,
    units_block,
)
from harness.projects import create_project
from harness.prompts.loader import PromptLoader
from harness.store.project import ChapterDoc, ProjectStore

_VAR_RE = re.compile(r"\{\{(\w+)\}\}")

PROMPT_KEYS = (
    "task.auto_plan_plots",
    "task.plot_units",
    "task.chapter_headers",
    "task.chapter_beats",
    "task.chapter_selfcheck",
)

ARC = {"vol": 1, "title": "第一卷", "goal": "拿下青石城", "summary": "卷概要",
       "conflict": "卷冲突", "resolution": "卷收束"}

PLOTS = [
    {"seq": 1, "title": "情节一", "summary": "概要一", "conflict": "冲突一", "resolution": "收束一",
     "beats": ["开", "合"]},
    {"seq": 2, "title": "情节二", "summary": "概要二", "conflict": "冲突二", "resolution": "收束二"},
    {"seq": 3, "title": "情节三", "summary": "概要三", "conflict": "冲突三", "resolution": "收束三"},
]


def _unit(words: int = 1800, event: str = "事件", **kw) -> dict:
    u = {"event": event, "expected_words": words}
    u.update(kw)
    return u


def _chapter(store: ProjectStore, seq: int, title: str, *, objective: str = "",
             covers="", time_span="", start_at="", end_at="",
             beats=None, plot_id: str = "", target_words: int = 3000,
             units=None) -> ChapterDoc:
    meta = {"seq": seq, "title": title, "status": "planned", "objective": objective,
            "covers": covers, "time_span": time_span, "start_at": start_at,
            "end_at": end_at, "plot_id": plot_id, "target_words": target_words}
    if beats is not None:
        meta["beats"] = beats
    if units is not None:
        meta["units"] = units
    store.write_chapter(ChapterDoc(seq=seq, meta=meta, body=""))
    return store.read_chapter(seq)


@pytest.fixture()
def store(tmp_path):
    info = create_project(tmp_path, "测试书", {
        "genre": "玄幻", "tone": "热血", "ending": "大团圆",
        "chapter_words": "3000", "target_chapters": "100", "planned_volumes": "4",
    })
    return ProjectStore(info.path)


@pytest.fixture()
def loader(tmp_path):
    home = tmp_path / "home"
    (home / ".harness" / "prompts").mkdir(parents=True)
    return PromptLoader(home_dir=home)


# ---------------------------------------------------------------- plan_plots_vars

class TestPlanPlotsVars:
    def test_world_first_20_summary_200(self, store):
        store.upsert_world_entries([
            {"title": f"条目{i}", "category": "设定", "importance": "中",
             "summary": f"摘要{i}——" + "字" * 250, "content": "正文"}
            for i in range(25)])
        v = plan_plots_vars(store, arc=ARC, arcs_planned=4, characters=[])
        lines = v["existing_world"].split("\n")
        assert len(lines) == 20                       # 前 20 条（web_dream limit=20 落调用侧）
        assert lines[0].startswith("· 条目0——")
        assert lines[0].endswith("字" * (200 - len("摘要0——")))
        assert not any("条目20" in ln for ln in lines)

    def test_world_empty(self, store):
        v = plan_plots_vars(store, arc=ARC, arcs_planned=4, characters=[])
        assert v["existing_world"] == "（无）"

    def test_character_digest_20_cards_260_bg(self, store):
        cards = [{"name": f"角色{i:02d}", "role_type": "major",
                  "profile": {"background": "底" * 300}} for i in range(25)]
        v = plan_plots_vars(store, arc=ARC, arcs_planned=4, characters=cards)
        lines = v["existing_characters"].split("\n")
        assert len(lines) == 20                       # 调用侧 characters[:20] 补偿（seed limit=20）
        assert lines[0].startswith("角色00(major)：")
        assert lines[0].endswith("底" * 260)          # bg_limit=260 截断

    def test_arc_chapters_and_scale_block(self, store):
        store.upsert_arcs([ARC])
        v = plan_plots_vars(store, arc=ARC, arcs_planned=4, characters=[])
        assert v["arc_chapters"] == "25"              # round(100 / 4)
        assert "本卷容量参考约 25 章" in v["scale_block"]
        assert v["target_words"] == "3000"            # README 每章字数
        assert v["total_chapters"] == "100"           # README 目标总章数
        assert v["research"] == "" and v["meme_bank"] == ""

    def test_arc_five_elements(self, store):
        v = plan_plots_vars(store, arc=ARC, arcs_planned=4, characters=[])
        assert (v["arc_title"], v["arc_goal"], v["arc_summary"],
                v["arc_conflict"], v["arc_resolution"]) == (
            "第一卷", "拿下青石城", "卷概要", "卷冲突", "卷收束")

    def test_instruction_from_readme(self, store):
        v = plan_plots_vars(store, arc=ARC, arcs_planned=4, characters=[])
        assert "题材：玄幻" in v["instruction"]
        assert "预计卷数：4" in v["instruction"]


# ---------------------------------------------------------------- plot_units_vars

class TestPlotUnitsVars:
    def test_prev_next_context_middle_plot(self, store):
        _chapter(store, 1, "甲章", covers="甲覆盖", plot_id="1-1")
        _chapter(store, 2, "乙章", covers="乙覆盖", plot_id="1-1")
        v = plot_units_vars(store, arc=ARC, plot=PLOTS[1], plots=PLOTS, target_words=3000)
        assert v["prev_context"].startswith("【情节一】概要一")
        assert "已拆章节" in v["prev_context"]        # 前一情节已拆章
        assert chapter_line(store.read_chapter(2).meta) in v["prev_context"]
        assert v["next_context"].startswith("【情节三】概要三")
        assert "留钩子" in v["next_context"]

    def test_prev_not_yet_split_falls_back_to_summary(self, store):
        v = plot_units_vars(store, arc=ARC, plot=PLOTS[1], plots=PLOTS, target_words=3000)
        assert "（该情节尚未拆章——开头请与它的概要衔接）" in v["prev_context"]

    def test_first_and_last_plot_no_crash(self, store):
        first = plot_units_vars(store, arc=ARC, plot=PLOTS[0], plots=PLOTS, target_words=3000)
        assert first["prev_context"] == "（本情节是卷首，无上一情节）"
        last = plot_units_vars(store, arc=ARC, plot=PLOTS[2], plots=PLOTS, target_words=3000)
        assert last["next_context"] == "（本情节是卷末，结尾须为下一卷留钩子）"

    def test_plots_text_and_batch_note(self, store):
        v = plot_units_vars(store, arc=ARC, plot=PLOTS[1], plots=PLOTS, target_words=3000)
        assert v["plots"].startswith("【情节二】概要二")
        assert "冲突：冲突二；收束：收束二" in v["plots"]
        assert "本次**只拆「情节二」这一个情节**（全卷待拆 3 个中的第 2 个）" in v["batch_note"]

    def test_existing_chapters_tail_60(self, store):
        v = plot_units_vars(store, arc=ARC, plot=PLOTS[0], plots=PLOTS, target_words=3000)
        assert v["existing_chapters"] == "（无）"
        for i in range(1, 4):
            _chapter(store, i, f"章{i}", covers=f"覆盖{i}")
        v = plot_units_vars(store, arc=ARC, plot=PLOTS[0], plots=PLOTS, target_words=3000)
        assert chapter_line(store.read_chapter(3).meta) in v["existing_chapters"]

    def test_min_units_and_arc_chapters(self, store):
        store.upsert_arcs([ARC])
        v = plot_units_vars(store, arc=ARC, plot=PLOTS[0], plots=PLOTS, target_words=3000)
        assert v["min_units"] == str(pure.min_units(3000))
        assert v["target_words"] == "3000"
        assert v["arc_chapters"] == "100"             # 1 卷：round(100 / 1)

    def test_broken_chapter_collected_not_raised(self, store, caplog):
        _chapter(store, 1, "好章", covers="覆盖")
        (store.root / "chapters" / "0002-坏章.md").write_text("---\nseq: [坏\n---\n正文\n",
                                                              encoding="utf-8")
        with caplog.at_level("WARNING", logger="planning.context"):
            v = plot_units_vars(store, arc=ARC, plot=PLOTS[0], plots=PLOTS, target_words=3000)
        assert "好章" in v["existing_chapters"]       # 坏章跳过，好章照列
        assert "坏章" in caplog.text


# ---------------------------------------------------------------- chapter_line

class TestChapterLine:
    def test_no_anchors_fallback(self):
        assert chapter_line({"seq": 9, "title": "标题", "objective": "目标"}) == "第9章 标题：目标"

    def test_full_anchor_snapshot(self):
        line = chapter_line({"seq": 9, "title": "标题", "objective": "目标",
                             "covers": "覆盖文", "time_span": "当日内",
                             "start_at": "村口｜清晨", "end_at": "村口｜夜晚"})
        assert line == "第9章 标题｜覆盖：覆盖文｜跨度：当日内｜起：村口｜清晨｜止：村口｜夜晚"

    def test_partial_anchors_omit_empty_bits(self):
        line = chapter_line({"seq": 9, "title": "标题", "objective": "",
                             "covers": "", "time_span": "", "start_at": "村口", "end_at": ""})
        assert line == "第9章 标题｜覆盖：｜起：村口"

    def test_covers_list_joins_events(self):
        line = chapter_line({"seq": 1, "title": "t", "objective": "",
                             "covers": [{"event": "甲"}, {"event": "乙"}]})
        assert "覆盖：甲；乙" in line

    def test_covers_list_all_empty_falls_back(self):
        # covers 段清单全空 event = 无锚点 → 退回「第N章 标题：目标」老数据形态
        line = chapter_line({"seq": 1, "title": "t", "objective": "目标文",
                             "covers": [{"event": ""}]})
        assert line == "第1章 t：目标文"


# ---------------------------------------------------------------- anchors_block

def _meta(seq, title, covers, span="当日内", start="村口｜清晨", end="村口｜夜晚",
          objective=""):
    return {"seq": seq, "title": title, "objective": objective, "covers": covers,
            "time_span": span, "start_at": start, "end_at": end}


class TestAnchorsBlock:
    def test_middle_chapter(self):
        block = anchors_block(_meta(2, "中章", "中覆盖"), _meta(1, "上章", "上覆盖"),
                              _meta(3, "下章", "下覆盖"))
        assert "【本章时空位置（骨架已定，必须遵守）】" in block
        assert "  覆盖内容：中覆盖" in block
        assert "  上一章：第1章 上章｜覆盖：上覆盖｜跨度：当日内" in block
        assert "  下一章：第3章 下章｜覆盖：下覆盖" in block

    def test_edge_chapters(self):
        first = anchors_block(_meta(1, "首章", "覆盖"), None, _meta(2, "次章", "覆盖2"))
        assert "上一章：（本情节第一章）" in first
        last = anchors_block(_meta(3, "末章", "覆盖"), _meta(2, "次章", "覆盖2"), None)
        assert "下一章：（本情节最后一章）" in last

    def test_no_anchors_fallback(self):
        block = anchors_block({"seq": 1, "title": "t", "objective": ""}, None, None)
        assert block == "【本章时空位置】骨架未提供时空锚点（老数据），按情节节拍线推进即可。"

    def test_repeat_warning_verbatim(self):
        prev = _meta(9, "上章", "晨雾测产完成")
        cur = _meta(10, "本章", "晨雾测产完成又复核一遍")
        assert pure.content_repeat("晨雾测产完成", cur["covers"])  # 前置：确实命中
        block = anchors_block(cur, prev, None)
        assert "⚠ **与上一章重复**" in block
        assert "上一章已覆盖「晨雾测产完成」" in block
        assert "不得回头把上一章已写完的阶段再写一遍" in block

    def test_no_warning_when_not_repeat(self):
        prev = _meta(9, "上章", "晨雾测产完成")
        cur = _meta(10, "本章", "入城拜会城主")
        assert "⚠" not in anchors_block(cur, prev, None)


# ---------------------------------------------------------------- units_block

class TestUnitsBlock:
    def test_render_verbatim(self):
        units = [_unit(1800, time="清晨", location="村口", covers="埋伏",
                       characters=["甲", "乙"]),
                 _unit(1500, event="动手", covers="")]
        block = units_block(units)
        assert block.startswith("【本章剧情描述（拆章已定：")
        assert "  1. [清晨｜村口]（出场：甲、乙） 事件" in block
        assert "  2. 动手" in block                 # 无 time/location：head 后一个空格接 event
        assert block.endswith("\n")

    def test_empty_and_no_event(self):
        assert units_block([]) == ""
        assert units_block([{"event": ""}]) == ""     # 空 event 段不渲染
        assert units_block(None) == ""


# ---------------------------------------------------------------- beats_vars

class TestBeatsVars:
    def _book(self, store):
        siblings = [
            _chapter(store, 1, "首章", objective="目标1", covers="晨雾测产完成",
                     time_span="当日内", start_at="村口｜清晨", end_at="村口｜午时",
                     plot_id="1-1"),
            _chapter(store, 2, "中章", objective="目标2", covers="晨雾测产完成又复核一遍",
                     time_span="当日内", start_at="水磨｜午后", end_at="水磨｜黄昏",
                     plot_id="1-1"),
            _chapter(store, 3, "末章", objective="目标3", covers="入城",
                     time_span="次日", start_at="城门｜清晨", end_at="城主府｜正午",
                     plot_id="1-1"),
        ]
        _chapter(store, 4, "别章", objective="别目标", covers="别覆盖",
                 time_span="三日后", start_at="山道｜晨", end_at="山道｜暮",
                 plot_id="1-2")
        return siblings

    def test_adjacent_siblings_and_repeat_warning(self, store):
        siblings = self._book(store)
        cur = store.read_chapter(2)
        v = beats_vars(store, plot=PLOTS[0], chapter=cur, siblings=siblings,
                       units=[_unit()])
        block = v["anchors_block"]
        assert "  上一章：第1章 首章｜覆盖：晨雾测产完成" in block
        assert "  下一章：第3章 末章｜覆盖：入城" in block
        assert "⚠ **与上一章重复**" in block          # 「晨雾测产完成」⊂ 本章 covers 命中

    def test_first_chapter_prev_is_none(self, store):
        siblings = self._book(store)
        cur = store.read_chapter(1)
        v = beats_vars(store, plot=PLOTS[0], chapter=cur, siblings=siblings, units=[])
        assert "上一章：（本情节第一章）" in v["anchors_block"]

    def test_all_chapters_is_book_skeleton(self, store):
        siblings = self._book(store)
        v = beats_vars(store, plot=PLOTS[0], chapter=siblings[0], siblings=siblings, units=[])
        lines = v["all_chapters"].split("\n")
        assert len(lines) == 4                        # 全书四章（含别情节的别章）
        assert chapter_line(store.read_chapter(4).meta) in v["all_chapters"]

    def test_plot_five_elements_and_beats(self, store):
        siblings = self._book(store)
        v = beats_vars(store, plot=PLOTS[0], chapter=siblings[0], siblings=siblings, units=[])
        assert v["plot_title"] == "情节一"
        assert v["plot_beats"] == "开 → 合"
        missing = beats_vars(store, plot=PLOTS[1], chapter=siblings[0], siblings=siblings, units=[])
        assert missing["plot_beats"] == "（无）"

    def test_chapter_fields_and_target_words(self, store):
        siblings = self._book(store)
        v = beats_vars(store, plot=PLOTS[0], chapter=siblings[0], siblings=siblings, units=[])
        assert v["chapter_seq"] == "1"
        assert v["chapter_title"] == "首章"
        assert v["chapter_objective"] == "目标1"
        assert v["target_words"] == "3000"            # meta target_words
        bare = _chapter(store, 9, "裸章")
        v = beats_vars(store, plot=PLOTS[0], chapter=bare, siblings=[bare], units=[])
        assert v["target_words"] == str(pure.DEFAULT_TARGET_WORDS)

    def test_units_block_key(self, store):
        siblings = self._book(store)
        units = [_unit(time="清晨", location="村口", covers="埋伏", characters=["甲"])]
        v = beats_vars(store, plot=PLOTS[0], chapter=siblings[0], siblings=siblings, units=units)
        assert v["units_block"] == units_block(units)

    def test_story_block_cast_world(self, store):
        store.upsert_character({"name": "陆昭", "role_type": "protagonist",
                                "profile": {"background": "出身寒门", "personality": "隐忍"}})
        store.upsert_world_entries([{"title": "境界体系", "category": "修炼",
                                     "summary": "摘要", "content": "炼气筑基" * 40,
                                     "importance": "高"}])
        siblings = self._book(store)
        v = beats_vars(store, plot=PLOTS[0], chapter=siblings[0], siblings=siblings, units=[])
        assert "题材：玄幻" in v["story_block"]
        assert "【陆昭】(protagonist) {" in v["cast"]
        assert "出身寒门" in v["cast"]
        assert v["cast"].count("\n") == 0             # 单卡一行
        assert v["world_entries"].startswith("【境界体系】")
        assert len(v["world_entries"]) <= len("【境界体系】") + 150


# ---------------------------------------------------------------- selfcheck_vars

class TestSelfcheckVars:
    def test_render_verbatim(self):
        ch1 = ChapterDoc(seq=1, meta={"seq": 1, "title": "甲章", "objective": "目标1",
                                      "covers": "覆盖1", "time_span": "当日内",
                                      "start_at": "村口", "end_at": "水磨",
                                      "target_words": 4000,
                                      "beats": [{"event": "拍一"}, {"event": ""},
                                                {"pov": "甲"}]},
                         body="")
        ch2 = ChapterDoc(seq=2, meta={"seq": 2, "title": "乙章", "objective": ""},
                         body="")
        v = selfcheck_vars(plot=PLOTS[0], chapters=[ch2, ch1])   # 乱序入，按 seq 排
        lines = v["chapters"].split("\n")
        assert lines[0] == ('第1章「甲章」｜目标：目标1｜覆盖：覆盖1｜跨度：当日内'
                            '｜起：村口｜止：水磨｜节拍 3 个：')
        assert lines[1] == "    1. 拍一"             # 空 event 过滤后重新编号
        assert lines[2] == ('第2章「乙章」｜目标：｜覆盖：｜跨度：（未标）'
                            '｜起：（未标）｜止：（未标）｜节拍 0 个：')
        assert lines[3] == "    （无节拍）"
        assert v["plot_title"] == "情节一"
        assert v["target_words"] == "4000"            # 取首章 meta

    def test_target_words_default(self):
        ch = ChapterDoc(seq=1, meta={"seq": 1, "title": "t"}, body="")
        v = selfcheck_vars(plot=PLOTS[0], chapters=[ch])
        assert v["target_words"] == str(pure.DEFAULT_TARGET_WORDS)


# ---------------------------------------------------------------- headers_vars

class TestHeadersVars:
    def test_groups_header_plot_four_elements_and_arc_goal(self):
        groups = [[_unit(1800, time="清晨", location="村口", covers="埋伏"),
                   _unit(1800, covers="动手")],
                  [_unit(2400, covers="收网")]]
        v = headers_vars(arc=ARC, plot=PLOTS[0], groups=groups, story=None)
        lines = v["groups"].split("\n")
        # Task 3 Medium carry-forward 契约：首部嵌情节四要素 + 卷目标行
        assert lines[0] == ("本情节：情节一｜概要：概要一｜冲突：冲突一"
                            "｜收束：收束一")
        assert lines[1] == "卷目标：拿下青石城"
        # seed :629-637 逐字组渲染
        assert lines[2] == "第1章（含 2 段，预估 3600 字）：清晨｜村口 埋伏 / 动手"
        assert lines[3] == "第2章（含 1 段，预估 2400 字）：收网"
        assert v["story_block"] == ""                 # story 缺省 → 空串

    def test_summary_conflict_truncation(self):
        plot = {"title": "t", "summary": "概" * 100, "conflict": "冲" * 80,
                "resolution": "收" * 80}
        v = headers_vars(arc=ARC, plot=plot, groups=[[]], story=None)
        head = v["groups"].split("\n")[0]
        assert f"概要：{'概' * 80}｜" in head          # summary[:80]
        assert f"冲突：{'冲' * 60}｜" in head          # conflict[:60]
        assert f"收束：{'收' * 60}" in head           # resolution[:60]

    def test_story_block_from_story(self):
        v = headers_vars(arc=ARC, plot=PLOTS[0], groups=[[_unit()]],
                         story={"genre": "科幻", "tone": "冷峻", "ending": ""})
        assert "题材：科幻" in v["story_block"]
        assert "基调：冷峻" in v["story_block"]
        assert "结局走向" not in v["story_block"]     # 空字段不出行

    def test_bad_expected_words_falls_back_unit_words(self):
        groups = [[{"covers": "c", "expected_words": "坏"}]]
        v = headers_vars(arc=ARC, plot=PLOTS[0], groups=groups, story=None)
        assert f"预估 {pure.UNIT_WORDS} 字" in v["groups"]


# ---------------------------------------------------------------- 反向校验

def _vars_cases(store):
    store.upsert_arcs([ARC])
    ch = _chapter(store, 1, "章一", objective="目标", covers="覆盖",
                  time_span="当日内", start_at="村口", end_at="水磨",
                  beats=[{"event": "拍一"}], plot_id="1-1", target_words=3000)
    units = [_unit()]
    return [
        ("task.auto_plan_plots",
         plan_plots_vars(store, arc=ARC, arcs_planned=2, characters=[]), set()),
        ("task.plot_units",
         plot_units_vars(store, arc=ARC, plot=PLOTS[0], plots=PLOTS, target_words=3000), set()),
        ("task.chapter_headers",
         headers_vars(arc=ARC, plot=PLOTS[0], groups=[units], story=None), set()),
        ("task.chapter_beats",
         beats_vars(store, plot=PLOTS[0], chapter=ch, siblings=[ch], units=units),
         {"units_block"}),                              # 附加块：render_with_blocks 追加，非模板变量
        ("task.chapter_selfcheck",
         selfcheck_vars(plot=PLOTS[0], chapters=[ch]), set()),
    ]


@pytest.mark.parametrize("key", PROMPT_KEYS)
def test_reverse_template_coverage(store, loader, key):
    """T3 模板契约反向校验：context 返回的 dict 键与模板 {{var}} 逐键相等（除白名单附加键）。"""
    for k, vars_, extras in _vars_cases(store):
        if k != key:
            continue
        template_vars = set(_VAR_RE.findall(loader.get(k)))
        assert template_vars == set(vars_) - extras, (
            f"{k} 键漂移：多出 {sorted(set(vars_) - extras - template_vars)}，"
            f"缺失 {sorted(template_vars - (set(vars_) - extras))}")
        rendered = loader.render(k, **{vk: str(vv) for vk, vv in vars_.items()
                                       if vk not in extras})
        assert "{{" not in rendered                  # 渲染后不得残留未替换变量


# ------------------------------------------------------------------ P13 预计卷数

def test_plan_plots_vars_use_planned_volume_count(tmp_path):
    """**P13**：「每卷约 X 章」的分母用**预计总卷数**（``arcs_planned``），
    不是**实际弧数**。

    「预计 20 卷、只规划了 3 卷」时，用实际弧数 → 每卷参考章数被算成 total/3
    （分母错了，模型会以为每卷要写三分之一全书）。
    """
    info = create_project(tmp_path, "卷数书")
    store = ProjectStore(info.path)
    (store.root / "README.md").write_text(
        "题材：仙侠\n基调：冷峻\n结局：开放\n每章字数：3000\n目标总章数：600\n"
        "预计卷数：20\n", encoding="utf-8")

    vars20 = plan_plots_vars(store, arc={"vol": 1}, arcs_planned=20, characters=[])
    assert vars20["arc_chapters"] == "30"        # 600 / 20

    vars3 = plan_plots_vars(store, arc={"vol": 1}, arcs_planned=3, characters=[])
    assert vars3["arc_chapters"] == "200"       # 600 / 3（实际弧数 → 错的那个）

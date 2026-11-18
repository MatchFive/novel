# 来源：语义逐函数移植自 web_dream（d:/project/web_dream/backend/app/，见 task-1-brief.md）
"""harness.planning.pure 的 golden 测试：拆章归组 / 清洗 / 锚点 / 重复判定 / 规模口径。"""
from harness.planning import pure


def _unit(w: int, event: str = "事件", **kw) -> dict:
    u = {"event": event, "expected_words": w}
    u.update(kw)
    return u


class TestStripChapterPrefix:
    def test_arabic(self):
        assert pure.strip_chapter_prefix("第14章「献祭权柄」") == "献祭权柄"

    def test_chinese_num(self):
        assert pure.strip_chapter_prefix("第十二回 风波") == "风波"

    def test_no_prefix_unchanged(self):
        assert pure.strip_chapter_prefix("风波") == "风波"

    def test_empty(self):
        assert pure.strip_chapter_prefix("") == ""


class TestStripChapterRange:
    def test_fullwidth_dash(self):
        assert pure.strip_chapter_range("斗法（第3—5章）") == "斗法"

    def test_halfwidth_paren_hyphen(self):
        assert pure.strip_chapter_range("冒名的皮(第1-12章)") == "冒名的皮"

    def test_chinese_nums_tilde(self):
        assert pure.strip_chapter_range("卷一（第三～五章）") == "卷一"

    def test_en_dash(self):
        assert pure.strip_chapter_range("江穗（第48–49章）") == "江穗"

    def test_from_chapter(self):
        assert pure.strip_chapter_range("江穗（第48章起）") == "江穗"

    def test_multiple(self):
        assert pure.strip_chapter_range("斗法（第3—5章）（第6—7章）") == "斗法"

    def test_no_suffix_unchanged(self):
        assert pure.strip_chapter_range("斗法") == "斗法"


class TestMinUnits:
    def test_4000(self):
        assert pure.min_units(4000) == 5

    def test_zero_floor(self):
        assert pure.min_units(0) == 3

    def test_huge_cap(self):
        assert pure.min_units(99999) == 12

    def test_garbage_defaults_4000(self):
        assert pure.min_units("abc") == 5


class TestMinBeats:
    def test_4000(self):
        assert pure.min_beats(4000) == 6

    def test_floor(self):
        assert pure.min_beats(0) == 4

    def test_cap(self):
        assert pure.min_beats(99999) == 10


class TestUnitsToChapters:
    def test_empty(self):
        assert pure.units_to_chapters([], 3000) == []

    def test_single_unit_over_target_own_chapter(self):
        units = [_unit(5000, "独占")]
        groups = pure.units_to_chapters(units, 3000)
        assert groups == [units]

    def test_accumulate_to_target(self):
        units = [_unit(1800, f"u{i}") for i in range(4)]  # 7200 / 3000 → 2+2 章
        groups = pure.units_to_chapters(units, 3000)
        assert [len(g) for g in groups] == [2, 2]

    def test_over_12_closes_before_append(self):
        # 前两段已 3600（≥3000 已闭组）；一段 2000 开新组；再来 1500：1500 单独
        # 不超 1.2×3000=3600，但 2000+1500=3500 ≤3600 → 同组；到 3500≥3000 闭组。
        units = [_unit(1800), _unit(1800), _unit(2000), _unit(1500)]
        groups = pure.units_to_chapters(units, 3000)
        assert [len(g) for g in groups] == [2, 2]

    def test_would_blow_12_closes_first(self):
        # 组内 2200，追加 1800 → 4000 > 3600 → 先闭组（单段未达 3000 也收）。
        units = [_unit(2200), _unit(1800), _unit(1800)]
        groups = pure.units_to_chapters(units, 3000)
        assert [len(g) for g in groups] == [1, 2]

    def test_thin_tail_merged(self):
        # 两组 + 尾巴 <0.4×3000=1200 且并尾 ≤1.6×3000=4800 → 并入上一章。
        units = [_unit(1800), _unit(1800), _unit(800)]
        groups = pure.units_to_chapters(units, 3000)
        assert [len(g) for g in groups] == [3]

    def test_tail_too_big_stays(self):
        # 尾巴 1500 ≥ 1200 → 不并。
        units = [_unit(1800), _unit(1800), _unit(1500)]
        groups = pure.units_to_chapters(units, 3000)
        assert [len(g) for g in groups] == [2, 1]

    def test_tail_merge_over_16_stays(self):
        # 尾巴 800 < 1200，但上一章 4200 + 800 = 5000 > 4800 → 不并。
        units = [_unit(4200), _unit(800)]
        groups = pure.units_to_chapters(units, 3000)
        assert [len(g) for g in groups] == [1, 1]


class TestCleanUnits:
    def test_drops_non_dict_and_empty_event(self):
        raw = [{"event": "  "}, "x", None, {"event": "有内容"}, {"event": 42}]
        out = pure.clean_units(raw)
        assert [u["event"] for u in out] == ["有内容", 42]

    def test_non_list(self):
        assert pure.clean_units({"a": 1}) == []

    def test_expected_words_default(self):
        out = pure.clean_units([{"event": "e"}])
        assert out[0]["expected_words"] == pure.UNIT_WORDS == 1800

    def test_expected_words_coerced(self):
        out = pure.clean_units([{"event": "e", "expected_words": "2500.0"}])
        assert out[0]["expected_words"] == 2500

    def test_does_not_mutate_input(self):
        u = {"event": "e"}
        pure.clean_units([u])
        assert "expected_words" not in u


class TestCleanBeats:
    def test_keeps_only_dict_with_event(self):
        raw = [{"event": "拍"}, {"event": ""}, "拍一", {"event": "  "}, {"event": "二"}]
        assert [b["event"] for b in pure.clean_beats(raw)] == ["拍", "二"]

    def test_non_list(self):
        assert pure.clean_beats("x") == []


class TestSlimUnit:
    def test_whitelist(self):
        u = {"seq": 1, "time": "夜", "location": "山", "event": "e", "covers": "c",
             "expected_words": 1800, "characters": ["甲", "", "  ", 7], "junk": "x"}
        out = pure.slim_unit(u)
        assert out == {"seq": 1, "time": "夜", "location": "山", "event": "e",
                       "covers": "c", "expected_words": 1800, "characters": ["甲", "7"]}

    def test_drops_empty_and_none(self):
        assert pure.slim_unit({"event": "e", "time": "", "location": None}) == {"event": "e"}


class TestGroupAnchors:
    def test_covers_and_span(self):
        group = [
            {"time": "清晨", "location": "水磨", "covers": "A", "event": "e1"},
            {"time": "清晨", "location": "磨坊", "event": "e2"},
            {"time": "次日午后", "location": "水磨", "covers": "C", "event": "e3"},
        ]
        a = pure.group_anchors(group)
        assert a["covers"] == "A；e2；C"  # 无 covers 的段回退用 event
        assert a["time_span"] == "清晨 → 次日午后"
        assert a["start_at"] == "水磨｜清晨"
        assert a["end_at"] == "水磨｜次日午后"

    def test_same_time(self):
        a = pure.group_anchors([{"time": "当日内", "event": "e"}])
        assert a["time_span"] == "当日内"

    def test_covers_truncated(self):
        group = [{"covers": "长" * 400, "event": "e"}]
        assert len(pure.group_anchors(group)["covers"]) == 300


class TestDerive:
    def test_title_from_covers(self):
        assert pure.derive_title([{"covers": "晨雾测产", "event": "e"}]) == "晨雾测产"

    def test_title_strips_prefix_and_caps_20(self):
        long_t = "第3章" + "长" * 30
        assert pure.derive_title([{"covers": long_t}]) == "长" * 20

    def test_title_fallbacks(self):
        assert pure.derive_title([{"event": "ev"}]) == "ev"
        assert pure.derive_title([{}]) == "未命名"

    def test_objective(self):
        group = [{"event": "甲"}, {"event": "乙"}]
        assert pure.derive_objective(group) == "甲；乙"


class TestContentRepeat:
    def test_overlap_threshold(self):
        # 字集：交集/较小者 ≥ 0.5 → 重复。
        assert pure.content_repeat("晨雾测产与开镰", "晨雾测产与归仓") is True
        assert pure.content_repeat("晨雾测产开镰忙", "闭关修炼神功大成") is False

    def test_subset_counts(self):
        assert pure.content_repeat("出关", "出关后赶到青石城") is True

    def test_empty(self):
        assert pure.content_repeat("", "x") is False
        assert pure.content_repeat("x", "") is False


class TestThinNotes:
    def test_units_note(self):
        note = pure.units_thin_note(2, 5)
        assert note.startswith("\n【剧情描述太少·补足】")
        assert "2 段" in note and "5 段" in note and "5~9 段" in note

    def test_beats_note(self):
        note = pure.beats_thin_note(3, 6)
        assert note.startswith("\n【节拍太少·补足】")
        assert "3 个节拍" in note and "6 个" in note and "6~10 个饱满节拍" in note


class TestCharacterDigest:
    def test_empty(self):
        assert pure.character_digest([]) == "（无）"

    def test_line_format(self):
        cards = [{"name": "陆昭", "role_type": "主角", "aliases": "陆哥、昭哥",
                  "profile": {"background": "寒门出身" * 40}}]
        out = pure.character_digest(cards, bg_limit=10)
        assert out.startswith("陆昭(主角｜别名：陆哥、昭哥)：寒门出身寒门出")

    def test_bg_limit_default_260(self):
        cards = [{"name": "A", "profile": {"background": "字" * 300}}]
        line = pure.character_digest(cards)
        assert len(line.split("：", 1)[1]) == 260

    def test_fallbacks(self):
        cards = [{"name": "B", "profile": {"personality": "沉稳"}, "aliases": "  "}]
        assert pure.character_digest(cards) == "B(角色)：沉稳"
        assert pure.character_digest([{"name": "C"}]) == "C(角色)"


class TestScaleBlock:
    def test_without_arc(self):
        out = pure.scale_block(5000, 200)
        assert out == (
            "\n【规模口径（项目级参考规模：用户已设定；**只用于折算容量参考，非硬性配额**）】\n"
            "每章约 5000 字；全书共 200 章（约 1000000 字）。\n"
            "凡出现「X 万字」即折算为「X×10000÷5000 章」作容量参考；不得改动每章字数。"
            "拆章时章数由剧情内容量推出，与参考值有出入是正常的——**偏少不补、不注水、"
            "不为凑章数重拆**。"
        )
        assert "本卷容量参考" not in out

    def test_with_arc(self):
        out = pure.scale_block(3000, 100, arc_chapters=50)
        assert out.endswith("\n本卷容量参考约 50 章（情节按字数容量设计，非硬性配额）。")

    def test_defaults(self):
        assert pure.DEFAULT_TARGET_WORDS == 3000
        assert pure.DEFAULT_TOTAL_CHAPTERS == 100


class TestStoryBlock:
    def test_all_empty(self):
        assert pure.story_block("", "", "") == ""

    def test_full(self):
        out = pure.story_block("玄幻", "纯爽文", "大团圆")
        assert out == (
            "\n【创作基调（项目级设定，用户已定，必须遵守）】\n"
            "题材：玄幻\n"
            "基调：纯爽文（全书风格与爽点节奏以此为纲——角色性格/情节设计/正文文风"
            "都必须与之对齐，不得写出与基调相悖的内容）\n"
            "结局走向：大团圆（情节推进必须朝这个收束方向积累，不得背道而驰）"
        )

    def test_partial(self):
        out = pure.story_block("", "暗黑", "")
        assert "题材" not in out and "基调：暗黑" in out and "结局走向" not in out

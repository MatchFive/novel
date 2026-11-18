# 来源：语义逐函数移植自 web_draft draft_workflow.py（d:/project/web_dream/backend/app/workflows/，
# 行号锚点见 task-1-brief.md）
"""harness.writing.pure 的 golden 测试：拍预算 / span 手术 / issue 规范化 / 定位放宽 / 出口收口。"""
from harness.text import locate_quote
from harness.writing import pure


def test_constants():
    assert pure.MAX_REVISE == 4
    assert pure.MAX_BEAT_REWRITES == 3
    assert pure.DEAIFY_ROUNDS == 2
    assert pure.REPORT_ONLY_TYPES == frozenset({"ai_flavor", "length"})
    assert pure.FULL_REWRITE_TYPES == frozenset({"outline"})
    assert pure.BEAT_MIN_WORDS == 300
    assert pure.TAIL_PREV_CHAPTER == 2000
    assert pure.TAIL_BEAT == 1500
    assert pure.BUDGET_CHARS == 40000
    assert pure.LENGTH_SHORT_FACTOR == 0.85
    assert pure.LENGTH_LONG_FACTOR == 1.6
    assert pure.KEEP_WHOLE_FACTOR == 0.7
    assert pure.KEEP_BEAT_FACTOR == 0.5
    assert pure.EDIT_LEN_FACTOR == 1.8
    assert pure.EDIT_LEN_PAD == 40
    assert pure.STREAM_CHECK_INTERVAL == 512


class TestBeatWordBudgets:
    def test_even_and_weighted(self):
        # 6 拍无 weight：等分
        beats = [{} for _ in range(6)]
        assert pure.beat_word_budgets(3000, beats) == [500] * 6
        # 第一拍 weight 加倍：占比 2/7，其余 1/7，且每拍 ≥ BEAT_MIN_WORDS
        weighted = [{"weight": 2}] + [{} for _ in range(5)]
        budgets = pure.beat_word_budgets(3000, weighted)
        assert budgets[0] == round(3000 * 2 / 7)
        assert budgets[1] == round(3000 * 1 / 7)
        assert budgets[0] == 2 * budgets[1] - 1  # round(857.14)=857, round(428.57)=429
        assert all(b >= pure.BEAT_MIN_WORDS for b in budgets)

    def test_floor(self):
        # 每拍摊不到 300 时按下限兜住
        assert pure.beat_word_budgets(600, [{}, {}]) == [300, 300]
        assert pure.beat_word_budgets(400, [{}, {}]) == [300, 300]

    def test_weight_source_and_fallback(self):
        # weight 缺省看 expected_words（只当相对权重）；非法/非正 weight 视为 1
        assert pure.beat_word_budgets(3000, [{"expected_words": 2}, {}]) == [2000, 1000]
        assert pure.beat_word_budgets(3000, [{"weight": "abc"}, {"weight": -5}, {}]) == [1000] * 3
        # 空列表 → 空预算
        assert pure.beat_word_budgets(3000, []) == []


class TestSpans:
    def test_apply_spans_segments(self):
        # 分段交替：未触碰块 + 替换文本
        assert pure.apply_spans("hello world", [(0, 5, "hi"), (6, 11, "there")]) == [
            "hi", " ", "there"]
        # 从后往前应用，与已替换区间重叠的前序 edit 跳过
        assert pure.apply_spans("hello world", [(0, 5, "A"), (3, 8, "B")]) == [
            "hel", "B", "rld"]
        # 非法区间跳过；一条都没换成时原样返回整段
        assert pure.apply_spans("abc", [(5, 9, "x")]) == ["abc"]
        assert pure.apply_spans("abc", []) == ["abc"]

    def test_adjust_shifts_only_after(self):
        spans = [(0, 5), (5, 10), (10, 15)]
        assert pure.adjust_spans(spans, 5, 3) == [(0, 5), (8, 13), (13, 18)]
        # 负 delta（删除）同样只动 start 之后的区间
        assert pure.adjust_spans(spans, 5, -2) == [(0, 5), (3, 8), (8, 13)]
        # 边界：e == start 不动；s == start 随 delta
        assert pure.adjust_spans([(4, 5), (5, 10)], 5, 4) == [(4, 5), (9, 14)]
        # 跨过 start 的区间收缩/扩张到新边界
        assert pure.adjust_spans([(4, 12)], 5, 3) == [(4, 15)]

    def test_splice_with_spans_mid(self):
        text, spans = "0123456789", [[0, 10]]
        new_text, new_spans = pure.splice_with_spans(text, spans, 2, 5, "XY")
        assert new_text == "01XY56789"
        assert new_spans == [[0, 9]]
        # 替换点落在拍内部：拍区间扩张到新边界
        new_text, new_spans = pure.splice_with_spans(text, spans, 2, 5, "LONGER")
        assert new_text == "01LONGER56789"
        assert new_spans == [[0, 13]]
        # spans=None 原样保留 None
        assert pure.splice_with_spans(text, None, 2, 5, "XY") == ("01XY56789", None)
        # 非法区间原样返回
        assert pure.splice_with_spans(text, spans, 5, 3, "x") == (text, spans)
        assert pure.splice_with_spans(text, spans, 8, 20, "x") == (text, spans)


class TestNormalizeEdges:
    def test_strips_and_shifts_spans(self):
        # 前导 3 个换行 → spans 同步左移 3；已干净时原样返回
        assert pure.normalize_edges("\n\n\nabc def\n\n", [[5, 8]]) == ("abc def", [[2, 5]])
        assert pure.normalize_edges("abc", [[0, 3]]) == ("abc", [[0, 3]])

    def test_shifted_away_beats_dropped(self):
        # 平移后空掉的拍丢弃； spans=None 原样
        assert pure.normalize_edges("  abc", [[0, 2]]) == ("abc", None)
        assert pure.normalize_edges("  abc", None) == ("abc", None)

    def test_collapse_kills_spans(self):
        # 成段重复被收敛（只留第一份）⇒ 区间作废
        # （unit 取非周期 30 字串；全同字符的 KMP 最小周期是 1，会被 min_unit 判据放行）
        unit = "abcdefghijklmnopqrstuvwxyzABCD"
        assert len(unit) == 30
        clean, spans = pure.normalize_edges(unit * 3, [[0, 90]])
        assert clean == unit
        assert spans is None


class TestLocateForTarget:
    TEXT = ("风从巷口灌进来，灯影摇晃，像一尾鱼游过深巷。"
            "他站了很久，久到影子被月光钉在墙上，像一尊沉默的雕像，连风都绕开了他。")

    def test_strict_hit_first(self):
        assert pure.locate_for_target(self.TEXT, "他站了很久") == locate_quote(
            self.TEXT, "他站了很久")

    def test_prefix_fallback_32(self):
        # 尾部被模型改写（第 33 字起分歧）→ 严格定位失败；32 字唯一前缀兜回
        quote = "他站了很久，久到影子被月光钉在墙上，像一尊沉默的雕像，连风都绕开了这座城"
        hit = pure.locate_for_target(self.TEXT, quote)
        assert hit is not None
        assert hit == locate_quote(self.TEXT, quote[:32])
        assert self.TEXT[hit[0]:hit[0] + 5] == "他站了很久"

    def test_prefix_fallback_16(self):
        # 16 < len(quote) ≤ 24：跳过 32/24 档，16 字前缀唯一命中
        quote = "他站了很久，久到影子被月光钉在墙，像旧照片"
        hit = pure.locate_for_target(self.TEXT, quote)
        assert hit is not None
        assert hit == locate_quote(self.TEXT, quote[:16])

    def test_non_unique_prefix_none(self):
        # 所有短前缀都命中 ≥2 次 → 如实返回 None（绝不猜偏移）
        block = "这个故事开头平淡无奇，没有任何新意可言，就像一杯白开水。"
        text = block + "转折来得突然。" + block + "转折来得突然。"
        quote = block + "转折来得突然，但读者已经弃书"
        assert pure.locate_for_target(text, quote) is None

    def test_empty_quote_none(self):
        assert pure.locate_for_target(self.TEXT, "") is None


class TestBeatIndexAt:
    def test_contained(self):
        spans = [[0, 10], [10, 20]]
        assert pure.beat_index_at(spans, 5) == 0
        assert pure.beat_index_at(spans, 10) == 1
        assert pure.beat_index_at(spans, 19) == 1
        assert pure.beat_index_at(spans, 20) is None
        assert pure.beat_index_at(spans, 25) is None
        assert pure.beat_index_at([], 5) is None


class TestNormalizeIssues:
    def test_typing(self):
        raw = [
            "噪声",                                        # 非 dict 丢弃
            {"issue": "节奏拖沓"},                          # 缺 type/fix_kind/quote
            {"type": "logic", "issue": 42, "quote": ["a", "b"],
             "replacement": "x", "fix_kind": "weird", "junk": 1},
            {"type": "length", "issue": "太短", "fix_kind": "replace",
             "replacement": "y"},                          # replace 缺 quote → 降级
            {"issue": ""},                                 # 空 issue 丢弃
        ]
        out = pure.normalize_issues(raw)
        assert len(out) == 3
        assert out[0] == {"type": "unknown", "fix_kind": "rewrite", "quote": "",
                          "replacement": "", "issue": "节奏拖沓", "suggestion": ""}
        assert out[1]["type"] == "logic"
        assert out[1]["issue"] == "42"            # 全转 str
        assert out[1]["quote"] == "a\nb"
        assert out[1]["fix_kind"] == "rewrite"    # 非法 fix_kind 归 rewrite
        assert "junk" not in out[1]               # 杂字段丢弃
        assert out[2]["fix_kind"] == "rewrite"    # replace 缺 quote 降级 rewrite

    def test_replace_requires_pair(self):
        # 有 replacement 无 quote → rewrite；两件套齐全 → replace
        out = pure.normalize_issues([
            {"issue": "a", "replacement": "r"},
            {"issue": "b", "quote": "q", "replacement": "r"},
        ])
        assert [i["fix_kind"] for i in out] == ["rewrite", "replace"]


class TestApplyReplaceIssues:
    TEXT = "风从巷口灌进来。他站了很久。灯影摇晃。"

    def test_only_replace_kind(self):
        spans = [[0, 8], [8, 14]]
        issues = [
            {"type": "word", "fix_kind": "replace", "quote": "他站了很久",
             "replacement": "他站了整整一夜", "issue": "i", "suggestion": ""},
            {"type": "logic", "fix_kind": "rewrite", "quote": "灯影摇晃",
             "replacement": "", "issue": "j", "suggestion": ""},
            {"type": "word", "fix_kind": "replace", "quote": "不存在的引文xyz",
             "replacement": "r", "issue": "k", "suggestion": ""},
        ]
        new_text, new_spans, remaining = pure.apply_replace_issues(self.TEXT, spans, issues)
        assert "他站了整整一夜" in new_text
        assert "他站了很久" not in new_text
        # rewrite 与不可定位的 replace 原样进 remaining（保持顺序）
        assert remaining == [issues[1], issues[2]]
        # 拍区间同步：第二拍起点 = 替换点（quote 恰好从拍首开始），终点随 delta = 7-5 = +2
        assert new_spans == [[0, 8], [8, 16]]

    def test_no_edits_returns_original(self):
        issues = [{"type": "logic", "fix_kind": "rewrite", "quote": "",
                   "replacement": "", "issue": "j", "suggestion": ""}]
        assert pure.apply_replace_issues(self.TEXT, None, issues) == (
            self.TEXT, None, issues)


class TestMaxChars:
    def test_formulas(self):
        assert pure.max_chars_beat(3000) == 18000
        assert pure.max_chars_beat(100) == 1500
        assert pure.max_chars_rewrite_beat(1000) == 3000
        assert pure.max_chars_rewrite_beat(500) == 2000
        assert pure.max_chars_deaify(2000) == 4000
        assert pure.max_chars_deaify(1000) == 3000
        assert pure.max_chars_single_shot(3000) == 9000
        assert pure.max_chars_single_shot(500) == 3000


class TestIsKeepWhole:
    def test_threshold(self):
        old = "x" * 100
        assert pure.is_keep_whole("x" * 70, old) is True
        assert pure.is_keep_whole("x" * 69, old) is False

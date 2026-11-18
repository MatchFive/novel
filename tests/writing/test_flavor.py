# 来源：AI 味 detect() 18 条规则的 golden 测试（d:/project/local_dream/.superpowers/sdd/
# 2026-11-13-p3-writing-pipeline/task-2-brief.md；样例为自造短句，断言值均由规则定义推导）
"""harness.writing.flavor 的 golden 测试：18 条规则命中 / 只报不改 / 引号内不判 / 误杀防线 / 分数对拍。"""
import pytest

from harness.writing import flavor

RULE_NAMES_EXPECTED = [
    "否定对偶", "二元并置", "同构递进", "动作套话", "空泛副词", "弱化副词",
    "书面套词", "总结升华", "能力宣告", "过渡套话",
    "短句金句化", "字数对不上", "字数元描述", "编号枚举",
    "短句独段", "比喻堆叠", "机械复读", "句长过匀",
]

# 每条规则一条自造样例（必命中该规则；允许顺带命中别的规则）
SAMPLES: dict[str, str] = {
    "否定对偶": "这不是结局，是另一个开始。",
    "二元并置": "她是那么温柔，那么安静。",
    "同构递进": "风越刮越大，越吹越急。",
    "动作套话": "他深吸一口气，重新站起来。",
    "空泛副词": "他极其冷静地看着这一切。",
    "弱化副词": "她缓缓转过身去。",
    "书面套词": "月色恍若流水。",
    "总结升华": "她知道，真正的较量才刚刚开始。",
    "能力宣告": "这是他老本行，干这行他熟。",
    "过渡套话": "总而言之，这一局他赢了。",
    "短句金句化": "不是感激。是算账。那眼神他认得。",
    "字数对不上": "他盯着「炭窑能抵」这三个字，半晌没说话。",
    "字数元描述": "他写下三个字，折好纸条。",
    "编号枚举": "第一个念头还没落下，第二个念头又冒了出来。",
    "短句独段": "烫。\n字灭了。\n他转身走了。\n夜还长。",
    "比喻堆叠": "他跑得像风，站得像松，卧得像弓，坐得像钟。",
    "机械复读": "他揉太阳穴。太阳穴突突地跳。他按住太阳穴，太阳穴还是疼。",
    "句长过匀": "他推开那扇斑驳的木门走了进去。" * 5,
}


def test_constants():
    assert flavor.THRESHOLD == 6.0
    assert flavor.rule_names() == RULE_NAMES_EXPECTED
    assert len(flavor._SCORE_ONLY) == 11
    assert len(flavor._SKIP_IN_QUOTE) == 4
    assert len(flavor.LEGIT_SAMPLES) == 5


@pytest.mark.parametrize("rule", flavor.rule_names())
def test_detect_each_rule_hits(rule):
    # 每条例句必命中对应规则
    report = flavor.detect(SAMPLES[rule])
    assert rule in {h.rule for h in report.hits}


def test_score_only_rules_not_in_targets():
    # 11 条只报不改：命中照计数，但一律不进定点改写名单
    for rule in flavor._SCORE_ONLY:
        report = flavor.detect(SAMPLES[rule])
        hits = [h for h in report.hits if h.rule == rule]
        assert hits, f"{rule} 样例未命中"
        assert all(h.score_only for h in hits)
        assert report.score > 0
        assert report.targets() == []


def test_serious_requires_actionable_hit():
    # 分数超阈值但全是只报不改 → 不算 serious
    text = "他缓缓走着，又缓缓停下，缓缓转身，缓缓坐下，缓缓点头。"
    report = flavor.detect(text)
    assert report.score >= flavor.THRESHOLD
    assert all(h.score_only for h in report.hits)
    assert report.serious is False


def test_serious_true_with_actionable_hit():
    text = "他深吸一口气。" * 30  # 30 处动作套话 ×0.5，超阈值且可改写
    report = flavor.detect(text)
    assert report.serious is True
    assert len(report.targets()) >= 1


def test_legit_samples_never_hit_by_actionable_rules():
    # 误杀防线（附录 D.3）：可改写规则不命中任何合法样本
    actionable = set(flavor.rule_names()) - set(flavor._SCORE_ONLY)
    assert len(actionable) == 7
    for sample in flavor.LEGIT_SAMPLES:
        report = flavor.detect(sample)
        bad = [h.rule for h in report.hits if h.rule in actionable]
        assert bad == []


def test_skip_in_quote_rules():
    # 4 条引号内不判：包进「」后该规则不再命中
    for rule in flavor._SKIP_IN_QUOTE:
        quoted = f"「{SAMPLES[rule].strip()}」"
        assert rule not in {h.rule for h in flavor.detect(quoted).hits}
    # 不在白名单的规则引号内照判
    assert "动作套话" in {h.rule for h in flavor.detect("「他深吸一口气。」").hits}


def test_score_math():
    # 手工对拍：动作套话 0.5 + 弱化副词 0.2，Σweight/字数×1000 保留 1 位
    text = "他深吸一口气，又缓缓坐下。"
    assert len(text) == 13
    report = flavor.detect(text)
    assert report.score == round((0.5 + 0.2) / 13 * 1000, 1) == 53.8


def test_empty_text():
    report = flavor.detect("")
    assert report.score == 0.0
    assert report.hits == []
    assert report.serious is False


def test_targets_dedup_and_cap():
    # 同规则同原句多处命中：计数全保留，定点目标去重
    report = flavor.detect("他深吸一口气。他又深吸一口气。")
    assert len([h for h in report.hits if h.rule == "动作套话"]) == 2
    targets = report.targets()
    assert len(targets) == 1
    # 封顶 n：25 条例句各命中一次（quote 互不相同）→ 可改写命中 25 条，targets 只给 20 条
    words = ["温柔", "安静", "从容", "坚定", "淡然", "专注", "轻盈", "沉稳",
             "清冷", "炽热", "决绝", "坦荡", "谨慎", "敏捷", "耐心", "固执",
             "洒脱", "警觉", "贪婪", "疲惫", "木讷", "敏锐", "憨厚", "孤傲", "虔诚"]
    long_report = flavor.detect("".join(f"她那么{w}，那么动人。" for w in words))
    assert len([h for h in long_report.hits if h.rule == "二元并置"]) == 25
    assert len(long_report.targets()) == 20
    assert len(long_report.targets(n=5)) == 5


def test_flavor_brief_format():
    text = "夜色很深。他深吸一口气，转身走进巷子里。"
    report = flavor.detect(text)
    out = flavor.flavor_brief(text, report.targets())
    assert out.startswith("1. 原文「深吸一口气」")
    assert "（上文…夜色很深。他｜下文，转身走进巷子里。…）" in out
    assert "｜判定：动作套话——" in out
    assert "｜处理：" in out


def test_flavor_brief_truncates_long_quote():
    long_quote = "长" * 50
    hit = flavor.FlavorHit(rule="测试", weight=1.0, quote=long_quote,
                           analysis="因为", fix="改法", score_only=False,
                           start=2, end=2 + len(long_quote))
    out = flavor.flavor_brief("前缀" + long_quote + "后缀", [hit])
    assert out.startswith(f"1. 原文「{'长' * 40}…」")

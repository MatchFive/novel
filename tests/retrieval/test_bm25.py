# 来源：移植自 web_dream backend/app/llm/retrieval.py（纯函数，见 task-1-brief.md）
"""harness.retrieval.bm25 的契约测试：查询词构造 / BM25 排序 / RRF 融合 /
相关性门槛 + 语义重排 / 词面 0 命中兜底 / to_thread 异步包装与同步版一致。"""
from types import SimpleNamespace

import pytest

from harness.retrieval.bm25 import (
    DEFAULT_SETTINGS,
    GRAM,
    RetrievalSettings,
    bm25_rank,
    bm25_rank_async,
    count_coverage,
    fuse_rrf,
    is_meaningful,
    ngrams,
    normalize_text,
    rank_candidates,
    rank_candidates_async,
    rank_fallback,
    rank_fallback_async,
    terms_from_texts,
)


class FakeEmbedder:
    """模拟 seed 的 embedder 协议：enabled + rank_texts_scored(query, texts, top_k)。scores 按 texts 下标。"""

    enabled = True

    def __init__(self, scores: dict[int, float]):
        self._scores = scores

    def rank_texts_scored(self, query, texts, top_k):
        return [(i, self._scores.get(i, 0.0)) for i in range(len(texts))][:top_k]


def _items(*texts: str) -> list:
    return [SimpleNamespace(id=i + 1, text=t) for i, t in enumerate(texts)]


def _text_of(it) -> str:
    return it.text


# ---------------------------------------------------------------- 查询词构造


def test_normalize_text_collapses_whitespace_and_strips_punct():
    assert normalize_text("  第一章　开宗明义。  ") == "第一章 开宗明义"
    assert normalize_text("") == ""
    assert normalize_text(None) == ""


def test_stop_chars_filtered():
    assert not is_meaningful("的了")
    assert is_meaningful("剑道")
    # 整串都是停用字 → 一个 term 都出不来
    assert terms_from_texts(["的了吗呢"]) == []


def test_terms_prefers_full_phrase():
    terms = terms_from_texts(["青云剑诀", "天外飞仙术"])
    # 完整短语（≤8 字）排在 3 字滑窗之前
    assert terms[0] == "青云剑诀"
    assert "青云剑" in terms and "云剑诀" in terms


def test_ngrams_defaults_to_trigram():
    assert GRAM == 3
    assert ngrams("剑道纪要") == ["剑道纪", "道纪要"]
    assert ngrams("剑道") == ["剑道"]  # 短于 size 原样返回
    assert ngrams("") == []


# ---------------------------------------------------------------- BM25 / RRF


def test_bm25_rank_scores_and_limits():
    # tf 随 id 递增（"道纪要" 出现 i+1 次），30 条语料限 24
    items = _items(*["剑道纪要" * (i + 1) for i in range(30)])
    hits, scores = bm25_rank(items, ["道纪要"], _text_of, limit=24)
    assert len(hits) == 24 and len(scores) == 24
    vals = [scores[h.id] for h in hits]
    assert all(v > 0 for v in vals)
    assert vals == sorted(vals, reverse=True)  # 按相关度降序
    assert hits[0].id == 30  # tf 最大者居首
    # 空输入短路
    assert bm25_rank([], ["x"], _text_of) == ([], {})
    assert bm25_rank(items, [], _text_of) == ([], {})


def test_fuse_rrf_k60():
    fused = fuse_rrf([[10, 20, 30], [30, 20]])  # 默认 k=60
    assert fused[10] == pytest.approx(1 / 61)
    assert fused[20] == pytest.approx(1 / 62 + 1 / 62)  # 两个列表里都是第 2 名
    assert fused[30] == pytest.approx(1 / 63 + 1 / 61)  # 次列表第 1 名：1/(60+0+1)
    assert fused[10] < fused[20] < fused[30]


# ---------------------------------------------------------------- rank_candidates


def test_rank_candidates_threshold_and_order():
    cfg = RetrievalSettings(bm25_min_ratio=0.35)
    terms = ["剑道", "心法"]
    # B(coverage 1) 故意排在 A(coverage 2) 前面：语义把 B 在 dense 名单里换到 A 前，
    # 两组名次相邻互换 → A/B 的 RRF 恰好打平。此时 coverage 键必须负载：
    # 缺 coverage 键的稳定排序会留下 pool 顺序 [B, A] → 断言 [2, 1] 必红。
    pool = _items("剑道入门", "剑道与心法修炼纪要", " totally unrelated ")
    scores = {1: 10.0, 2: 10.0, 3: 1.0}
    embedder = FakeEmbedder({0: 0.2, 1: 0.9, 2: 0.1})  # dense 名单 [2, 1, 3]
    picked, ranked = rank_candidates(pool=pool, scores=scores, query="剑道心法",
                                     text_of=_text_of, cfg=cfg, top_k=10,
                                     embedder=embedder, terms=terms)
    assert [it.id for it in picked] == [2, 1]
    by_id = {r.key: r for r in ranked}
    assert by_id[1].rrf == pytest.approx(by_id[2].rrf)  # 钉死「打平」构造
    assert by_id[1].coverage == 1 and by_id[2].coverage == 2
    assert by_id[1].lexical_ok and by_id[2].lexical_ok
    assert not by_id[3].lexical_ok and not by_id[3].semantic_ok
    assert not by_id[3].relevant  # 门槛以下且无语义 → 被砍


def test_rank_candidates_zero_bm25_goes_coverage_branch():
    # 词面全 0 分（best ≤ _EPS，不区分）→ 走进重排块：pool 按 coverage 降序重排，
    # lexical_ok 退化为 covered>=1；无语义时余者按 RRF 取，有语义时按 RRF/dense 融合。
    cfg = RetrievalSettings()
    terms = ["剑道", "心法"]
    # pool 故意乱序：丙（coverage 0）打头、甲（coverage 2）末尾
    pool = _items("完全无关的丙", "剑道入门乙", "剑道与心法纪要甲")
    scores = {1: 0.0, 2: 0.0, 3: 0.0}

    picked, ranked = rank_candidates(pool=pool, scores=scores, query="剑道心法",
                                     text_of=_text_of, cfg=cfg, top_k=10,
                                     embedder=None, terms=terms)
    # 重排块生效：coverage 最高的甲（id=3）顶到明细首位
    assert [r.key for r in ranked] == [3, 2, 1]
    assert [r.coverage for r in ranked] == [2, 1, 0]
    assert [it.id for it in picked] == [3, 2]  # 丙 coverage 0 且无语义 → 不相关被砍
    assert all(r.lexical_ok for r in ranked[:2])
    assert not ranked[2].lexical_ok

    # 同分支 + 语义：甲余弦达标（semantic_ok），RRF 融合后按 (rrf, coverage) 决序
    embedder = FakeEmbedder({0: 0.9, 1: 0.1, 2: 0.1})  # 重排后 texts=[甲,乙,丙]
    picked2, ranked2 = rank_candidates(pool=pool, scores=scores, query="剑道心法",
                                       text_of=_text_of, cfg=cfg, top_k=10,
                                       embedder=embedder, terms=terms)
    assert [r.key for r in ranked2] == [3, 2, 1]  # 重排块仍生效
    assert [it.id for it in picked2] == [3, 2]  # 甲 dense 头名 RRF 居首；丙仍不相关被砍
    by_id = {r.key: r for r in ranked2}
    assert by_id[3].semantic_ok and by_id[3].cosine == pytest.approx(0.9)
    assert not by_id[1].semantic_ok and not by_id[2].semantic_ok


def test_rank_candidates_semantic_rerank():
    cfg = RetrievalSettings()
    pool = _items("甲之剑道", "乙之心法", "丙之秘籍")
    scores = {1: 5.0, 2: 5.0, 3: 5.0}  # 词面全同分，全靠语义重排
    # C 余弦最高：dense 名次 [C, A, B]，把 C 从词面末位提前
    embedder = FakeEmbedder({0: 0.30, 1: 0.20, 2: 0.90})
    picked, ranked = rank_candidates(pool=pool, scores=scores, query="剑道心法",
                                     text_of=_text_of, cfg=cfg, top_k=10,
                                     embedder=embedder, terms=["剑道"])
    assert [it.id for it in picked] == [1, 3, 2]
    by_id = {r.key: r for r in ranked}
    assert by_id[3].semantic_ok and by_id[3].cosine == pytest.approx(0.90)


def test_rank_candidates_respects_top_k_and_disabled():
    pool = _items("甲之剑道", "乙之心法", "丙之秘籍")
    scores = {1: 5.0, 2: 4.0, 3: 3.0}
    cfg = RetrievalSettings()
    picked, _ = rank_candidates(pool=pool, scores=scores, query="q",
                                text_of=_text_of, cfg=cfg, top_k=2, terms=["剑道"])
    assert len(picked) == 2
    # mode=off / enabled=False / top_k<=0 → 空
    off = RetrievalSettings(mode="off")
    assert rank_candidates(pool=pool, scores=scores, query="q", text_of=_text_of,
                           cfg=off, top_k=5) == ([], [])
    disabled = RetrievalSettings(enabled=False)
    assert rank_candidates(pool=pool, scores=scores, query="q", text_of=_text_of,
                           cfg=disabled, top_k=5) == ([], [])


# ---------------------------------------------------------------- rank_fallback


def test_rank_fallback_zero_lexical():
    candidates = _items("甲之剑道", "乙之心法", "丙之秘籍")
    cfg = RetrievalSettings()  # dense_fallback=True, fallback_top_n=0
    # 语义兜底：余弦达 min_cosine 者按分降序
    embedder = FakeEmbedder({0: 0.90, 1: 0.50, 2: 0.10})
    picked, ranked = rank_fallback(candidates=candidates, query="剑道",
                                   text_of=_text_of, cfg=cfg, top_k=5,
                                   embedder=embedder)
    assert [it.id for it in picked] == [1, 2]
    assert all(r.semantic_ok for r in ranked)
    # 兜底关闭（fallback_top_n=0）且无语义 → 空结果，不注入无关内容
    no_dense = RetrievalSettings(dense_fallback=False)
    assert rank_fallback(candidates=candidates, query="q", text_of=_text_of,
                         cfg=no_dense, top_k=5, embedder=None) == ([], [])
    # 无语义时按 fallback_top_n 注入
    inject = RetrievalSettings(dense_fallback=False, fallback_top_n=2)
    picked2, ranked2 = rank_fallback(candidates=candidates, query="q",
                                     text_of=_text_of, cfg=inject, top_k=5,
                                     embedder=None)
    assert [it.id for it in picked2] == [1, 2]
    assert all(r.lexical_ok for r in ranked2)


# ---------------------------------------------------------------- 异步包装


async def test_async_wrappers_match_sync():
    items = _items(*["剑道纪要" * (i + 1) for i in range(10)])
    terms = terms_from_texts(["剑道纪要"])

    sync_hits, sync_scores = bm25_rank(items, terms, _text_of, limit=8)
    a_hits, a_scores = await bm25_rank_async(items, terms, _text_of, limit=8)
    assert [h.id for h in a_hits] == [h.id for h in sync_hits]
    assert a_scores == sync_scores

    pool = _items("甲之剑道", "乙之心法", "丙之秘籍")
    scores = {1: 5.0, 2: 4.0, 3: 3.0}
    cfg = RetrievalSettings()
    kw = {"pool": pool, "scores": scores, "query": "剑道", "text_of": _text_of,
          "cfg": cfg, "top_k": 2, "embedder": None, "terms": terms}
    s_picked, s_ranked = rank_candidates(**kw)
    a_picked, a_ranked = await rank_candidates_async(**kw)
    assert [it.id for it in a_picked] == [it.id for it in s_picked]
    assert [(r.key, r.bm25, r.coverage) for r in a_ranked] == \
           [(r.key, r.bm25, r.coverage) for r in s_ranked]

    f_kw = {"candidates": pool, "query": "q", "text_of": _text_of, "cfg": cfg,
            "top_k": 2, "embedder": None}
    s_fpicked, s_franked = rank_fallback(**f_kw)
    a_fpicked, a_franked = await rank_fallback_async(**f_kw)
    assert [it.id for it in a_fpicked] == [it.id for it in s_fpicked]
    assert [r.key for r in a_franked] == [r.key for r in s_franked]


# ---------------------------------------------------------------- settings


def test_default_settings_singleton_matches_seed_defaults():
    again = DEFAULT_SETTINGS
    assert again is DEFAULT_SETTINGS  # 模块级单例
    assert DEFAULT_SETTINGS.recall_k == 24
    assert DEFAULT_SETTINGS.rrf_k == 60
    assert DEFAULT_SETTINGS.bm25_min_ratio == 0.35
    assert DEFAULT_SETTINGS.min_cosine == 0.35
    assert DEFAULT_SETTINGS.dense_fallback is True
    assert DEFAULT_SETTINGS.fallback_top_n == 0
    assert DEFAULT_SETTINGS.semantic_enabled is True
    assert DEFAULT_SETTINGS.backend == "none"
    assert DEFAULT_SETTINGS.embedding_model == ""


def test_count_coverage():
    assert count_coverage(["剑道", "心法"], "剑道与心法") == 2
    assert count_coverage([], "任意文本") == 0
    assert count_coverage(["剑道"], "") == 0

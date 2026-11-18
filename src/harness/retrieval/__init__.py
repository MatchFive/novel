"""检索纯函数层（P4 Task 1）：查询词构造 / BM25 / RRF 融合 / 相关性门槛 / 异步包装。

语义逐函数移植自 web_dream backend/app/llm/retrieval.py；配置由调用方注入
`RetrievalSettings`（本仓不从 config.py 读）。
"""
from harness.retrieval.bm25 import (
    DEFAULT_SETTINGS,
    GRAM,
    Ranked,
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
from harness.retrieval.corpus import CorpusItem, Index, build_corpus, chapter_items
from harness.retrieval.embedder import (
    Embedder,
    NoopEmbedder,
    OpenAIEmbedder,
    cosine,
    get_embedder,
    peek_embedder,
)

__all__ = [
    "DEFAULT_SETTINGS",
    "GRAM",
    "CorpusItem",
    "Embedder",
    "Index",
    "NoopEmbedder",
    "OpenAIEmbedder",
    "Ranked",
    "RetrievalSettings",
    "bm25_rank",
    "bm25_rank_async",
    "build_corpus",
    "chapter_items",
    "cosine",
    "count_coverage",
    "fuse_rrf",
    "get_embedder",
    "is_meaningful",
    "ngrams",
    "normalize_text",
    "peek_embedder",
    "rank_candidates",
    "rank_candidates_async",
    "rank_fallback",
    "rank_fallback_async",
    "terms_from_texts",
]

"""混合检索纯函数层（P4 Task 1）：BM25（词面）+ 语义嵌入（词义）融合，带相关性门槛。

语义逐函数移植自 web_dream backend/app/llm/retrieval.py（纯函数，原样保留；去掉了
`resolve_settings`——本仓不从 config.py 读，调用方传 `RetrievalSettings`）。
召回层口径：分词 = 字符 trigram，打分是进程内 Python Okapi BM25（单项目语料规模小，
DB 只负责按作用域取候选）。

```
查询文本 ──┬─→ 3 字 n-gram + 短语  → Python BM25 召回（项目作用域）→ 词面排名
           └─→ 原始文本            → 嵌入模型对召回池重排          → 语义排名
                                              ↓
                                    RRF 倒数排名融合 → 相关性门槛 → top-k
```
"""
from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

# BM25 分数低于该值视为「无法区分」（小语料/公共词会导致分数趋近 0）
_EPS = 1e-9

# 查询词 n-gram 长度：恰好 3 字可与 trigram 语料 token 对齐
GRAM = 3

_STOP_CHARS = set("的 了 是 在 和 与 也 都 就 而 及 或 把 被 让 给 对 从 向 到 于 为 以 之 其 这 那 "
                  "有 无 不 没 很 更 最 会 能 要 可 需 将 已 又 再 还 才 只 才 便 则 若 如 因 由 "
                  "我 你 他 她 它 们 咱 谁 什 么 怎 吗 呢 吧 啊 呀 哦 嗯 "
                  "一 二 三 四 五 六 七 八 九 十 个 些 种 次 时 处 里 上 下 中 前 后 左 右 内 外 "
                  "， 。 、 ； ： ！ ？ “ ” ‘ ’ （ ） 《 》 【 】 — … · · ～ , . ; : ! ? ( ) [ ] { } "
                  "\" ' - _ / \\ | + = * & ^ % $ # @ ~ `")
_WS_RE = re.compile(r"\s+")


@dataclass
class RetrievalSettings:
    """检索行为参数（dataclass；调用方注入，默认值照 seed config.py 的 RetrievalSettings
    与 EmbeddingConfig 默认值）。"""

    enabled: bool = True
    mode: str = "hybrid"             # hybrid / lexical / off
    recall_k: int = 24               # BM25 召回池大小
    rrf_k: int = 60
    bm25_min_ratio: float = 0.35     # 词面相对分数门槛（相对本批最优）
    min_cosine: float = 0.35         # 语义相似度门槛
    dense_fallback: bool = True      # 词面 0 命中时用语义兜底
    fallback_top_n: int = 0          # 两路都没命中时注入条数（0=不注入无关内容）
    semantic_enabled: bool = True
    backend: str = "none"            # none / fastembed / openai
    embedding_model: str = ""


DEFAULT_SETTINGS = RetrievalSettings()


# ---------------------------------------------------------------- 查询词构造（移植）
def normalize_text(text: str) -> str:
    text = _WS_RE.sub(" ", (text or "").replace("　", " ")).strip()
    return text.strip("，。、；：！？…—·,.;:!?\"'()（）【】《》[]")


def is_meaningful(term: str) -> bool:
    return any(ch not in _STOP_CHARS for ch in term)


def ngrams(text: str, size: int = GRAM) -> list[str]:
    text = normalize_text(text)
    if not text:
        return []
    if len(text) <= size:
        return [text]
    return [text[i:i + size] for i in range(len(text) - size + 1)]


def terms_from_texts(texts: Iterable[str], *, size: int = GRAM,
                     max_terms: int = 16, min_len: int = 2) -> list[str]:
    """先放完整短语，再补 3 字滑窗，截断到 max_terms；2 字词保留。"""
    out: list[str] = []
    seen: set[str] = set()

    def add(term: str) -> None:
        term = term.strip()
        if len(term) < min_len or term in seen or not is_meaningful(term):
            return
        seen.add(term)
        out.append(term)

    for text in texts:
        clean = normalize_text(text)
        if not clean:
            continue
        if len(clean) <= 8:
            add(clean)
        for gram in ngrams(clean, size):
            add(gram)
    return out[:max_terms]


# ---------------------------------------------------------------- Python Okapi BM25（召回层）
def _tokenize(text: str) -> list[str]:
    """语料分词：字符 trigram（与查询 n-gram 对齐）+ 完整短语本身（≤8 字）。"""
    text = normalize_text(text)
    if not text:
        return []
    tokens = ngrams(text, GRAM)
    if len(text) <= 8:
        tokens.append(text)
    return tokens


def bm25_rank(items: Sequence[Any], terms: Sequence[str],
              text_of: Callable[[Any], str], *,
              k1: float = 1.5, b: float = 0.75,
              limit: int = 24) -> tuple[list[Any], dict[int, float]]:
    """对候选集做 Okapi BM25 排序。

    :param items: 项目作用域内的候选对象
    :param terms: 查询术语（短语 + n-gram）
    :param text_of: 取候选的比较文本（含标题，标题可在外层加权重复）
    :return: (按相关度降序的对象列表, {id: bm25 分数})
    """
    if not items or not terms:
        return [], {}
    docs = [_tokenize(text_of(it)) for it in items]
    n = len(docs)
    avgdl = sum(len(d) for d in docs) / max(n, 1) or 1.0
    # df / idf
    df: dict[str, int] = {}
    for d in docs:
        for tok in set(d):
            df[tok] = df.get(tok, 0) + 1
    idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    scored: list[tuple[int, float]] = []
    for idx, d in enumerate(docs):
        if not d:
            continue
        tf: dict[str, int] = {}
        for tok in d:
            tf[tok] = tf.get(tok, 0) + 1
        dl = len(d)
        score = 0.0
        for t in terms:
            f = tf.get(t)
            if not f:
                continue
            score += idf.get(t, 0.0) * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / avgdl))
        if score > 0:
            scored.append((idx, score))
    scored.sort(key=lambda x: -x[1])
    picked = scored[:limit]
    return [items[i] for i, _ in picked], {int(items[i].id): s for i, s in picked}


# ---------------------------------------------------------------- 融合与门槛（移植）
def fuse_rrf(rank_lists: Sequence[Sequence[int]], *, k: int = 60) -> dict[int, float]:
    """倒数排名融合：score = Σ 1/(k + rank)；只用名次，免疫分数尺度不可比。"""
    fused: dict[int, float] = {}
    for ranks in rank_lists:
        for pos, key in enumerate(ranks):
            fused[key] = fused.get(key, 0.0) + 1.0 / (k + pos + 1)
    return fused


@dataclass
class Ranked:
    key: int
    item: Any
    bm25: float = 0.0
    cosine: float | None = None
    coverage: int = 0
    lexical_ok: bool = False
    semantic_ok: bool = False
    rrf: float = 0.0

    @property
    def relevant(self) -> bool:
        return self.lexical_ok or self.semantic_ok


def count_coverage(terms: Sequence[str], text: str) -> int:
    if not terms or not text:
        return 0
    return sum(1 for t in terms if t and t in text)


def _dense_scores(embedder, query: str, texts: list[str]) -> dict[int, float] | None:
    if embedder is None or not getattr(embedder, "enabled", False) or not query.strip():
        return None
    try:
        scored = embedder.rank_texts_scored(query, texts, top_k=len(texts))
    except Exception:  # noqa: BLE001 - 嵌入失败不能让生成失败
        return None
    if not scored:
        return None
    return {int(i): float(s) for i, s in scored}


async def bm25_rank_async(items, terms, text_of, *, limit: int = 24):
    """`bm25_rank` 的异步版：分词 + 打分是**同步 CPU**（候选多时是明显的循环阻塞）。

    asyncio 单线程，直接在协程里跑会卡住整个服务——挪进线程。

    事故教训（seed web_dream）：同步嵌入在协程里跑会把整个事件循环**冻住**
    （`search_handbook` 触发嵌入后 `/api/health` 都超时、所有数据加载不出来，
    而机器其它核还空着）——凡同步 CPU 密集活必须 `asyncio.to_thread`。
    """
    import asyncio

    return await asyncio.to_thread(bm25_rank, items, terms, text_of, limit=limit)


async def rank_fallback_async(**kwargs):
    """`rank_fallback` 的异步版（同样会用嵌入做语义兜底 → 同步 CPU）。

    同 `bm25_rank_async` 的事故教训：同步嵌入冻结事件循环，必须挪出协程。
    """
    import asyncio

    return await asyncio.to_thread(lambda: rank_fallback(**kwargs))


async def rank_candidates_async(**kwargs):
    """`rank_candidates` 的异步版：语义重排走嵌入模型（**同步 CPU**），必须挪出事件循环。

    事故教训（seed web_dream）：`search_handbook` 在协程里直接跑同步嵌入，
    整个 uvicorn 冻住——`/api/health` 超时、所有数据加载不出来，机器其它核却空着。
    凡同步 CPU 密集活一律 `asyncio.to_thread`。
    """
    import asyncio

    return await asyncio.to_thread(lambda: rank_candidates(**kwargs))


def rank_candidates(
    *,
    pool: Sequence[Any],
    scores: dict[int, float],
    query: str,
    text_of: Callable[[Any], str],
    cfg: RetrievalSettings,
    top_k: int,
    embedder=None,
    terms: Sequence[str] | None = None,
) -> tuple[list[Any], list[Ranked]]:
    """对召回池做「语义重排 + RRF 融合 + 相关性门槛」，返回 (选中对象, 全部明细)。"""
    if not pool or top_k <= 0 or cfg.mode == "off" or not cfg.enabled:
        return [], []

    texts = [text_of(it) for it in pool]
    keys = [int(it.id) for it in pool]
    coverages = [count_coverage(terms or (), t) for t in texts]

    best = max(scores.values()) if scores else 0.0
    discriminative = best > _EPS

    if not discriminative and any(coverages):
        order = sorted(range(len(pool)), key=lambda i: -coverages[i])
        pool = [pool[i] for i in order]
        texts = [texts[i] for i in order]
        keys = [keys[i] for i in order]
        coverages = [coverages[i] for i in order]

    dense = _dense_scores(embedder, query, texts) if cfg.semantic_enabled else None
    dense_order = sorted(dense, key=lambda i: dense[i], reverse=True) if dense else []

    ranked: list[Ranked] = []
    for pos, item in enumerate(pool):
        key = keys[pos]
        bm25 = float(scores.get(key, 0.0))
        covered = coverages[pos]
        cosine = dense.get(pos) if dense else None
        if discriminative:
            lexical_ok = best > 0 and bm25 >= best * cfg.bm25_min_ratio
        else:
            lexical_ok = covered >= 1 or not any(coverages)
        semantic_ok = cosine is not None and cosine >= cfg.min_cosine
        ranked.append(Ranked(key=key, item=item, bm25=bm25, cosine=cosine,
                             coverage=covered, lexical_ok=lexical_ok,
                             semantic_ok=semantic_ok))

    fused = fuse_rrf([keys, [keys[i] for i in dense_order]], k=cfg.rrf_k)
    for r in ranked:
        r.rrf = fused.get(r.key, 0.0)

    kept = [r for r in ranked if r.relevant]
    kept.sort(key=lambda r: (r.rrf, r.coverage), reverse=True)
    return [r.item for r in kept[:top_k]], ranked


def rank_fallback(
    *,
    candidates: Sequence[Any],
    query: str,
    text_of: Callable[[Any], str],
    cfg: RetrievalSettings,
    top_k: int,
    embedder=None,
) -> tuple[list[Any], list[Ranked]]:
    """词面 0 命中时的兜底：语义达标者；无语义则按 fallback_top_n（默认 0=不注入）。"""
    if top_k <= 0 or cfg.mode == "off" or not cfg.enabled or not candidates:
        return [], []

    dense = _dense_scores(embedder, query, [text_of(c) for c in candidates]) if cfg.semantic_enabled else None
    if dense is not None and cfg.dense_fallback:
        picked = [(i, s) for i, s in dense.items() if s >= cfg.min_cosine]
        picked.sort(key=lambda x: x[1], reverse=True)
        ranked = [Ranked(key=int(candidates[i].id), item=candidates[i],
                         cosine=s, semantic_ok=True) for i, s in picked]
        return [r.item for r in ranked[:top_k]], ranked

    n = min(cfg.fallback_top_n, top_k, len(candidates))
    if n <= 0:
        return [], []
    ranked = [Ranked(key=int(c.id), item=c, lexical_ok=True) for c in candidates[:n]]
    return [r.item for r in ranked], ranked

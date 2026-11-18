"""正文回指通道 + search_chapters 只读工具（P4 Task 8）。

``backrefs``：伏笔回指检索——只查正文语料（pool=chapter_items）、**仅 BM25**
（embedder=None——裁定 4：正文语料不进嵌入）、top_k 钉 4；查询词由调用方经
``terms_from_texts`` 构造（章目标+beats 全文+context_select 的 keywords——设计 §2.3）。
每命中取「最长命中行」作候选引文，经 ``locate_quote`` 在**章正文**（非标题）回验：
**Review Focus #5**——引文必须逐字存在于该章正文，回验失败丢弃该命中（标题命中
而正文无据的章不得产出引文）。``cur_seq`` 给定时剔除晚于当前章的命中
（时间锚点纪律同 T7）。

``search_chapters``：registry 同步只读工具，handler 签名 (store, args) -> str，
**零 LLM**——不进 loop._async_tool（无 _FALLBACK_SIG 分支），白名单内任意范围
（含并行批）可用；观测词表确定性：参数错 → 「search_chapters 失败（参数）：…」
（照 write_plot 口径）；零命中 → 「search_chapters：无命中」。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from harness.retrieval.bm25 import (
    DEFAULT_SETTINGS,
    bm25_rank,
    rank_candidates,
    terms_from_texts,
)
from harness.retrieval.corpus import CorpusItem, Index, build_corpus, chapter_items
from harness.text import locate_quote

__all__ = ["backrefs", "search_chapters"]

log = logging.getLogger("tools.search")

_BACKREF_TOP_K = 4           # 裁定：top_k 钉 4（brief 区间 3~5 取钉值）


@dataclass
class _Cand:
    """BM25 引擎内部包装（rank_candidates 要 int(id) 键，同 T3 _Entry / inject.py _Cand）。"""

    id: int
    item: CorpusItem

    @property
    def text(self) -> str:
        return self.item.text


def _seq_of(item: CorpusItem) -> int:
    """章语料项的章号（id=``chap:0009``）。"""
    return int(str(item.id).split(":", 1)[1])


def _chapter_body(item: CorpusItem) -> str:
    """章语料项的**正文**部分：build_corpus 口径 text=「标题\n正文」，去掉首行标题。"""
    _title, sep, body = str(item.text or "").partition("\n")
    return body if sep else ""


def _longest_hit_line(item: CorpusItem, terms: list[str]) -> str | None:
    """最长命中片段：含 ≥1 查询词的最长行（同长取先——稳定排序确定性）。"""
    hits = [ln.strip() for ln in str(item.text or "").splitlines()
            if ln.strip() and any(t in ln for t in terms)]
    hits.sort(key=len, reverse=True)
    return hits[0] if hits else None


def backrefs(index: Index, terms: list[str], *, top_k: int = _BACKREF_TOP_K,
             cur_seq: int | None = None) -> list[tuple[CorpusItem, str]]:
    """正文回指通道：返回 [(章语料项, 逐字引文)]。

    - pool=chapter_items（world/char/mem 语料不进回指通道）；
    - 仅 BM25（embedder=None——裁定 4），管线同 T3 Index.search（bm25_rank →
      rank_candidates，仅去异步包装）；
    - ``cur_seq`` 给定时先剔除章号 > cur_seq 的命中（时间锚点纪律：未来章的
      「伏笔」尚未发生，不得回指）；
    - 每命中取最长命中行经 locate_quote 在章**正文**回验，失败丢弃（Review
      Focus #5）并 log.warning（含章号），绝不产出无法在正文逐字定位的引文。
    """
    terms = [t for t in (terms or []) if t]
    if top_k <= 0 or not terms:
        return []
    pool = chapter_items(index.items)
    if cur_seq is not None:
        pool = [it for it in pool if _seq_of(it) <= cur_seq]
    if not pool:
        return []
    cands = [_Cand(i, it) for i, it in enumerate(pool)]
    recalled, scores = bm25_rank(cands, terms, lambda c: c.text,
                                 limit=DEFAULT_SETTINGS.recall_k)
    if not recalled:
        return []
    picked, _ranked = rank_candidates(
        pool=recalled, scores=scores, query=" ".join(terms),
        text_of=lambda c: c.text, cfg=DEFAULT_SETTINGS, top_k=top_k,
        embedder=None, terms=terms)
    out: list[tuple[CorpusItem, str]] = []
    for cand in picked:
        quote = _longest_hit_line(cand.item, terms)
        if quote is None:
            continue    # 理论上 bm25 命中必有命中行；防御性跳过
        if locate_quote(_chapter_body(cand.item), quote) is None:
            log.warning("回指引文回验失败（第%s章《%s》）：%r 不在章正文，丢弃该命中",
                        _seq_of(cand.item), cand.item.anchor, quote[:40])
            continue
        out.append((cand.item, quote))
    return out


def _top_k_arg(args: dict) -> int | None:
    """args.top_k：缺省 4；bool/浮点/字符串/ <1 一律 None（参数错，观测照 write_plot 口径）。"""
    raw = args.get("top_k")
    if raw is None:
        return _BACKREF_TOP_K
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        return None
    return raw


def search_chapters(store, args: dict) -> str:
    """全文检索章正文：返回逐条「第N章《title》：「逐字引文」」，零命中如实报。

    只读、零 LLM：每次调用按当前文件建内存索引（build_corpus 单文件容错，
    坏文件跳过 + 警告）。观测词表确定性（分类契约）：参数错 → 失败（参数）；
    零命中 → 「search_chapters：无命中」。
    """
    args = args or {}
    raw = args.get("query")
    query = raw.strip() if isinstance(raw, str) else ""
    if not query:
        return (f"search_chapters 失败（参数）：args.query 缺参或为空——须为非空"
                f"检索词字符串，当前为 {raw!r}")
    top_k = _top_k_arg(args)
    if top_k is None:
        return (f"search_chapters 失败（参数）：args.top_k 缺省为 {_BACKREF_TOP_K}；"
                f"给出时须为 ≥1 的整数（bool/浮点/字符串一律不认），"
                f"当前为 {args.get('top_k')!r}")
    index = Index(build_corpus(store.root))
    hits = backrefs(index, terms_from_texts([query]), top_k=top_k)
    if not hits:
        return "search_chapters：无命中"
    return "\n".join(f"第{_seq_of(it)}章《{it.anchor}》：「{quote}」"
                     for it, quote in hits)

"""语料与索引层（P4 Task 3）：文件→带定位语料项→内存索引→隔离层 retrieve。

设计 §2.3 落地：「文件是源，索引在内存」。``build_corpus`` 把项目文件读成
带定位的 ``CorpusItem``（id 稳定唯一、path 源文件相对路径、anchor 小节锚点、
text 检索文本）；``Index`` 维护语料 + trigram 倒排，支持 path 级失效与增量
重建；``Index.search`` 就是设计预留的隔离层 ``retrieve(corpus, query, top_k)``
——检索接口与存储解耦，将来换 sqlite-vec / chroma 调用方无感。

``search`` 内部管线（与计划钉死一致）：``terms_from_texts`` →
``bm25_rank_async`` → ``rank_candidates_async``；倒排只用于召回预筛
（命中 ≥1 查询项的语料项——与 bm25_rank 的 score>0 召回集等价，idf 恒正），
打分/融合/门槛全在 T1 引擎内。

记忆文件格式以计划钉死的 Task 4 节区结构为准：章记忆 ``memory/<seq:04d>.md``
按「## 节」切成多条（## 事件 / ## 角色经历 / ## 信息项 / ## 知晓关系 /
## 承诺与伏笔），anchor=节名:序号；``state.md`` 与 ``digests/`` 不进通用语料
（现状经注入端直读，digest 经注入端直读）。本模块**不依赖 Task 4 的实现**，
只依赖节区名这一格式事实。
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from harness.retrieval.bm25 import (
    DEFAULT_SETTINGS,
    RetrievalSettings,
    _tokenize,
    bm25_rank_async,
    rank_candidates_async,
    terms_from_texts,
)
from harness.retrieval.embedder import Embedder, cosine
from harness.store.frontmatter import FileFormatError, split_document, split_file
from harness.store.project import ProjectStore

__all__ = ["CorpusItem", "Index", "build_corpus", "chapter_file_items",
           "chapter_items", "memory_file_items"]

log = logging.getLogger("retrieval.corpus")

_CHAPTER_NAME = re.compile(r"^(\d{4})-.*\.md$")
_MEMORY_NAME = re.compile(r"^(\d{4})\.md$")
_HINT_INT = re.compile(r"(\d+)")

# 单文件隔离口径（评审 Important-1）：坏 YAML / front-matter 结构错 / 非 UTF-8，
# 一律跳过该文件 + log.warning（含 path），不拖垮全量 build。
_BAD_FILE = (ValueError, UnicodeDecodeError, yaml.YAMLError)


@dataclass
class CorpusItem:
    """一条带定位的语料项。

    - ``id``：稳定唯一，``world:条目名`` / ``char:名`` / ``mem:0009:事件:3`` /
      ``chap:0009``
    - ``path``：源文件相对项目根路径（posix 分隔符）——失效定位的单位
    - ``anchor``：小节锚点（world=条目名；memory=节名:序号；chap=章题；
      char=角色名）——整文件失效时 anchor 留作展示/将来细粒度失效
    - ``text``：参与检索的文本
    """

    id: str
    path: str
    anchor: str
    text: str


# ---------------------------------------------------------------- build_corpus

def build_corpus(root, *, vol: int | None = None) -> list[CorpusItem]:
    """读项目根下的 world.md / characters / chapters / memory，建成语料项列表。

    - ``world.md``：条目块逐块（复用 store 层 ``ProjectStore._parse_world``）
    - ``characters/*.md``：每文件一条（坏卡跳过 + 警告，同 store 容错风格）
    - ``chapters/*.md``：按章整条一条，**不切块**（正文语料项定位=章文件路径）
    - ``memory/*.md``：章记忆按「## 节」切多条；state.md / digests/ 不进语料
    - ``vol``：给定时只取该卷章号范围内的章与章记忆（卷→章号段由 arcs 的
      章数容量提示累计算出）；arcs 无容量信息时无法划段 → 不过滤（不猜）
    """
    root = Path(root)
    items: list[CorpusItem] = []
    items += _world_items(root)
    items += _character_items(root)
    seqs = _vol_seqs(root, vol)
    items += _chapter_items(root, seqs)
    items += _memory_items(root, seqs)
    return items


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _world_items(root: Path) -> list[CorpusItem]:
    world = root / "world.md"
    if not world.exists():
        return []
    try:
        # 复用 store 层的 world 解析（静态方法，区间块逐字保留）
        text = world.read_text(encoding="utf-8")
    except _BAD_FILE as exc:
        log.warning("build_corpus 跳过坏 world.md: %s", exc)
        return []
    _prefix, blocks = ProjectStore._parse_world(text)
    items = []
    for block in blocks:
        if block[0] != "entry":
            continue
        _, _category, title, raw_lines = block
        items.append(CorpusItem(id=f"world:{title}", path="world.md",
                                anchor=title, text="\n".join(raw_lines)))
    return items


def _character_items(root: Path) -> list[CorpusItem]:
    chars = root / "characters"
    if not chars.is_dir():
        return []
    items = []
    for path in sorted(chars.iterdir()):
        if not (path.is_file() and path.name.endswith(".md")):
            continue
        try:
            meta, body = split_file(path)
        except (FileFormatError, UnicodeDecodeError) as exc:  # 坏卡跳过 + 警告，不拖垮整个语料
            log.warning("build_corpus 跳过坏角色卡: %s", exc)
            continue
        name = str(meta.get("name") or path.stem)
        lines = [f"# {name}"]
        aliases = meta.get("aliases")
        if isinstance(aliases, str):
            aliases = [aliases]
        if aliases:
            lines.append("别名：" + "、".join(str(a) for a in aliases))
        for key, value in meta.items():
            if key in ("name", "aliases") or value is None:
                continue
            lines.append(f"{key}：{value}")
        if body.strip():
            lines.append(body)
        items.append(CorpusItem(id=f"char:{name}", path=_rel(path, root),
                                anchor=name, text="\n".join(lines)))
    return items


def _chapter_items(root: Path, seqs: set[int] | None) -> list[CorpusItem]:
    chapters = root / "chapters"
    if not chapters.is_dir():
        return []
    items = []
    for path in sorted(chapters.iterdir()):
        m = _CHAPTER_NAME.match(path.name)
        if not (m and path.is_file()):
            continue
        seq = int(m.group(1))
        if seqs is not None and seq not in seqs:
            continue
        try:
            meta, body = split_document(path.read_text(encoding="utf-8"))
        except _BAD_FILE as exc:
            log.warning("build_corpus 跳过坏章节: %s: %s", _rel(path, root), exc)
            continue
        title = str(meta.get("title") or path.stem)
        text = f"{title}\n{body}" if body.strip() else title
        items.append(CorpusItem(id=f"chap:{seq:04d}", path=_rel(path, root),
                                anchor=title, text=text))
    return items


def _memory_items(root: Path, seqs: set[int] | None) -> list[CorpusItem]:
    mem = root / "memory"
    if not mem.is_dir():
        return []
    items = []
    for path in sorted(mem.iterdir()):
        m = _MEMORY_NAME.match(path.name)   # 只认 <seq>.md：天然排除 state.md 与 digests/
        if not (m and path.is_file()):
            continue
        seq = int(m.group(1))
        if seqs is not None and seq not in seqs:
            continue
        try:
            items += _split_memory_file(seq, path, _rel(path, root))
        except _BAD_FILE as exc:
            log.warning("build_corpus 跳过坏章记忆: %s: %s", _rel(path, root), exc)
    return items


def _split_memory_file(seq: int, path: Path, rel: str) -> list[CorpusItem]:
    """章记忆按「## 节」切多条；### 小节留在所属 ## 节内。"""
    lines = path.read_text(encoding="utf-8").split("\n")
    items: list[CorpusItem] = []
    heading: list[str] = []      # 文件头（# 第N章记忆…）
    sections: list[tuple[str, list[str]]] = []
    cur_name: str | None = None
    cur_lines: list[str] = []
    for line in lines:
        if line.startswith("## "):
            if cur_name is not None:
                sections.append((cur_name, cur_lines))
            cur_name = line[3:].strip()
            cur_lines = [line]
        elif cur_name is None:
            heading.append(line)
        else:
            cur_lines.append(line)
    if cur_name is not None:
        sections.append((cur_name, cur_lines))
    if not sections:             # 无 ## 节：整文件一条，anchor 空（格式外容错）
        text = "\n".join(heading).strip()
        if text:
            items.append(CorpusItem(id=f"mem:{seq:04d}:全文:1", path=rel,
                                    anchor="全文:1", text=text))
        return items
    ordinals: dict[str, int] = {}
    for name, sec_lines in sections:
        ordinals[name] = ordinals.get(name, 0) + 1
        ordinal = ordinals[name]
        items.append(CorpusItem(id=f"mem:{seq:04d}:{name}:{ordinal}", path=rel,
                                anchor=f"{name}:{ordinal}",
                                text="\n".join(sec_lines)))
    return items


def _vol_seqs(root: Path, vol: int | None) -> set[int] | None:
    """卷→章号段：按 arcs 章数容量提示（capacity_hint / chapters）累计算。

    返回 None 表示无法划段（vol 未给 / arcs 缺失 / 无容量信息）→ 调用方不过滤。
    """
    if vol is None:
        return None
    arcs_path = root / "outline" / "arcs.md"
    if not arcs_path.exists():
        return None
    try:
        meta, _ = split_document(arcs_path.read_text(encoding="utf-8"))
    except _BAD_FILE as exc:
        log.warning("build_corpus arcs.md 划段失败，vol 过滤退化为不过滤: %s: %s",
                    _rel(arcs_path, root), exc)
        return None
    arcs = [a for a in (meta.get("arcs") or [])
            if isinstance(a, dict) and isinstance(a.get("vol"), int)
            and not isinstance(a.get("vol"), bool)]
    counts: dict[int, int] = {}
    for arc in sorted(arcs, key=lambda a: a["vol"]):
        n = arc.get("chapters")
        if isinstance(n, bool) or not isinstance(n, int):
            hint = _HINT_INT.search(str(arc.get("capacity_hint") or ""))
            n = int(hint.group(1)) if hint else None
        if not n:
            return None          # 某卷容量未知 → 整段不可信，不猜
        counts[int(arc["vol"])] = n
    if vol not in counts:
        return None
    start = 1
    for v in sorted(counts):
        if v == vol:
            return set(range(start, start + counts[v]))
        start += counts[v]
    return None


# ---------------------------------------------------------------- 正文语料子集

def chapter_items(items: list[CorpusItem]) -> list[CorpusItem]:
    """正文语料子集（回指通道只查正文用）：id 以 ``chap:`` 开头。"""
    return [it for it in items if it.id.startswith("chap:")]


# ---------------------------------------------------------------- 单文件语料（path 级精确失效用，
# 设计 §2.3「文件是源，索引在内存」的增量口径：T7 修复轮——注入端章后只重建本章两个
# 文件的语料项，不逐章全量 build_corpus（trigram 分词 CPU 密集，堵事件循环））

def chapter_file_items(root, seq: int) -> list[CorpusItem]:
    """单章文件 → 语料项（path 精确失效/重建用；文件缺失/坏文件 → []，坏文件告警）。

    与 build_corpus 的 _chapter_items 同解析口径（整条不切块、标题+正文）；一章
    至多一个文件（write_chapter 改名时删旧名），命中即返回。
    """
    root = Path(root)
    chapters = root / "chapters"
    if not chapters.is_dir():
        return []
    prefix = f"{seq:04d}-"
    for path in sorted(chapters.iterdir()):
        if not (path.is_file() and path.name.startswith(prefix)
                and path.name.endswith(".md")):
            continue
        try:
            meta, body = split_document(path.read_text(encoding="utf-8"))
        except _BAD_FILE as exc:
            log.warning("chapter_file_items 跳过坏章节: %s: %s", _rel(path, root), exc)
            return []
        title = str(meta.get("title") or path.stem)
        text = f"{title}\n{body}" if body.strip() else title
        return [CorpusItem(id=f"chap:{seq:04d}", path=_rel(path, root),
                           anchor=title, text=text)]
    return []


def memory_file_items(root, seq: int) -> list[CorpusItem]:
    """单章记忆文件 → ``## 节`` 切分语料项（path 精确失效/重建用；缺失/坏文件 → []）。

    与 build_corpus 的 _split_memory_file 同切分口径；state.md / digests/ 文件名
    天然不匹配 ``<seq:04d>.md``，不会被本函数误收。
    """
    root = Path(root)
    path = root / "memory" / f"{seq:04d}.md"
    if not path.is_file():
        return []
    try:
        return _split_memory_file(seq, path, _rel(path, root))
    except _BAD_FILE as exc:
        log.warning("memory_file_items 跳过坏章记忆: %s: %s", _rel(path, root), exc)
        return []


# ---------------------------------------------------------------- T2→T1 bridge

class _DenseAdapter:
    """把 T2 的 async Embedder 适配成 T1 ``rank_candidates`` 期望的同步
    ``rank_texts_scored(query, texts, top_k)``。

    前提（控制器裁定 Important-1）：``rank_candidates_async`` 已把
    ``rank_candidates`` 挪进 worker 线程（``asyncio.to_thread``），该线程
    **没有事件循环**——这里 ``asyncio.run`` 临时起循环跑 ``embed`` 合法。
    反过来，若在事件循环内的协程里直调同步 ``rank_candidates`` 并把 Embedder
    直传进去，``embed`` 的 await 会直接炸（再被 ``_dense_scores`` 的裸 except
    吞掉）→ 语义重排静默失效、退化成纯 BM25 无人察觉。**Embedder 禁止直传
    rank_candidates，必须经过本适配器。**
    """

    enabled = True

    def __init__(self, emb: Embedder) -> None:
        self._emb = emb

    def rank_texts_scored(self, query, texts, top_k):
        vecs = asyncio.run(self._emb.embed([query, *texts]))
        qv, dvs = vecs[0], vecs[1:]
        scored = [(i, cosine(qv, dv)) for i, dv in enumerate(dvs)]
        scored.sort(key=lambda x: -x[1])
        return scored[:top_k]


# ---------------------------------------------------------------- Index

@dataclass
class _Entry:
    """bm25 引擎内部包装：T1 用 ``int(it.id)`` 当 key，CorpusItem.id 是 str。"""

    id: int
    item: CorpusItem

    @property
    def text(self) -> str:
        return self.item.text


def _text_of(entry: _Entry) -> str:
    return entry.text


def _norm_path(path: str) -> str:
    return path.replace("\\", "/").removeprefix("./")


class Index:
    """内存索引：语料项 + trigram 倒排 + path 级失效 + 隔离层 retrieve。

    - ``invalidate(path)``：按 path 精确剔除该文件全部语料项（正文语料项定位=
      章文件路径，整章失效重建）
    - ``add(path, items)``：按 path 幂等重写该文件语料项（path 不一致响亮 raise）
    - ``search(...)``：隔离层 retrieve——倒排召回预筛 → bm25_rank_async 打分
      → rank_candidates_async 语义重排+融合+门槛；embedder 非 None 时经
      ``_DenseAdapter`` 桥接（见类 docstring 上方 Important-1 裁定）
    """

    def __init__(self, items: list[CorpusItem]) -> None:
        self._items: list[CorpusItem] = list(items)
        self._rebuild()

    @property
    def items(self) -> list[CorpusItem]:
        """当前语料快照（副本，调用方随意筛/改不影响索引）。"""
        return list(self._items)

    def _rebuild(self) -> None:
        self._entries = [_Entry(i, it) for i, it in enumerate(self._items)]
        self._postings: dict[str, set[int]] = {}
        for i, it in enumerate(self._items):
            for tok in set(_tokenize(it.text)):
                self._postings.setdefault(tok, set()).add(i)

    def invalidate(self, path: str) -> None:
        """按 path 精确剔除该文件全部语料项。"""
        norm = _norm_path(path)
        self._items = [it for it in self._items if it.path != norm]
        self._rebuild()

    def add(self, path: str, items: list[CorpusItem]) -> None:
        """按 path 幂等重写该文件的语料项（先剔除旧 path 项再插入）。

        fail-loud（评审 Important-2）：传入项的 path 与 ``path`` 不一致时响亮
        raise——静默改挂 path 会把记忆项挂到 world.md 名下、破坏
        ``invalidate(path)`` 精确剔除契约（实测：整 corpus 传入后 id 重复、
        path 归属错乱）。caller 错误必须在调用点暴露，不允许进索引。
        """
        norm = _norm_path(path)
        new = list(items)
        bad = [it.id for it in new if it.path != norm]
        if bad:
            raise ValueError(
                f"Index.add({norm!r}): 拒绝 path 不一致的语料项 ×{len(bad)}"
                f"（如 {bad[:3]}）——invalidate+add 应按文件重解析，别传整 corpus")
        self._items = [it for it in self._items if it.path != norm] + new
        self._rebuild()

    async def search(self, query: str, *, top_k: int, embedder: Embedder | None = None,
                     cfg: RetrievalSettings = DEFAULT_SETTINGS,
                     pool: list[CorpusItem] | None = None) -> list[CorpusItem]:
        """隔离层 retrieve：返回 top-k 相关语料项（无相关项时 []，不注入无关内容）。"""
        if top_k <= 0 or cfg.mode == "off" or not cfg.enabled:
            return []
        terms = terms_from_texts([query])
        if not terms:
            return []
        if pool is not None:
            entries = [_Entry(i, it) for i, it in enumerate(pool)]
        else:
            entries = self._entries
        if not entries:
            return []
        # 倒排召回预筛：命中 ≥1 查询项的语料项（与 bm25_rank score>0 集等价，
        # 因为 idf 恒正——零命中时 bm25_rank 同样返空召回池）
        if pool is None:
            hit: set[int] = set()
            for term in terms:
                hit |= self._postings.get(term, set())
            entries = [self._entries[i] for i in sorted(hit)] if hit else []
            if not entries:
                return []
        recalled, scores = await bm25_rank_async(entries, terms, _text_of,
                                                 limit=cfg.recall_k)
        if not recalled:
            return []
        dense = _DenseAdapter(embedder) if embedder is not None else None
        picked, _ranked = await rank_candidates_async(
            pool=recalled, scores=scores, query=query, text_of=_text_of,
            cfg=cfg, top_k=top_k, embedder=dense, terms=terms)
        return [entry.item for entry in picked]

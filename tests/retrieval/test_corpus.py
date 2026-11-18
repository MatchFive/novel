"""语料与索引层（P4 Task 3）：文件→带定位语料项→内存索引→隔离层 retrieve。

fixture 的 memory/0001.md 按**本计划钉死的 Task 4 节区结构**造（## 事件 /
## 角色经历 / ## 信息项 / ## 知晓关系 / ## 承诺与伏笔），本任务只依赖节区名，
不 import Task 4 的代码（T4 未实现）。

控制器钉死的 T2→T1 bridge（评审 Important-1）：Embedder（async embed）禁止直传
rank_candidates——bm25.py 的 _dense_scores 期望同步 rank_texts_scored(query, texts, top_k)，
直传会被裸 except 吞掉导致语义重排静默失效。Index.search 在 embedder 非 None 时
包 _DenseAdapter；test_search_dense_adapter_genuinely_reorders 证明 dense 真正参与排序。
"""
from __future__ import annotations

import asyncio

import pytest

from harness.retrieval import bm25 as bm25_mod
from harness.retrieval.bm25 import RetrievalSettings
from harness.retrieval.corpus import CorpusItem, Index, build_corpus, chapter_items
from harness.retrieval.embedder import Embedder

# ---------------------------------------------------------------- 项目根 fixture
# 按 Task 4 钉死格式的章记忆（节区名与 plan 逐字一致）。

_WORLD = """# 世界观

## 地理

### 青云山
- 重要度：高

青云山脉主峰，灵气充裕，常有剑修御空而过。

### 黑水河
- 重要度：低

黑水河流域瘴气弥漫，妖兽横行。

## 势力

### 天玄宗
- 重要度：高

天玄宗是正道魁首，坐镇青云山巅。
"""

_CHAR = """---
name: 林凡
role_type: protagonist
aliases: [小凡]
---

# 林凡

山村少年，身怀逆天血脉，性格坚韧。
"""

_CHAPTER = """---
seq: 1
title: 起程
status: done
---

# 起程

林凡离开青云山，踏上修行路。

他在山门口回头望了一眼故乡，转身走向远方。
"""

_MEMORY = """# 第1章记忆《起程》

## 事件
- [全知] 林凡启程 — 离开山村前往宗门（青云山脚；清晨；人物：林凡）

## 角色经历
### 林凡
- 告别乡亲独自上路，心中忐忑（逐字：「他背起行囊，没有回头」）

## 信息项
- 逆天血脉：林凡体内血脉初醒，尚不知来历（revealed_to：林凡；源自第1章）

## 知晓关系
- 林凡 知道「逆天血脉」（第1章起）

## 承诺与伏笔
- [foreshadow] 血脉秘密终将引来追杀——林凡（第1章埋；到期第5章；状态 pending；）
"""

_STATE = """# 现状快照

## 角色现状

## 世界现状

## 未兑现承诺
"""


def _write(root, rel: str, text: str):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def project(tmp_path):
    _write(tmp_path, "world.md", _WORLD)
    _write(tmp_path, "characters/林凡.md", _CHAR)
    _write(tmp_path, "chapters/0001-起程.md", _CHAPTER)
    _write(tmp_path, "memory/0001.md", _MEMORY)
    _write(tmp_path, "memory/state.md", _STATE)
    _write(tmp_path, "memory/digests/vol-01.md", "# 卷一摘要\n\n## 情节 1. 起程\n\n占位\n")
    return tmp_path


def _ids(items: list[CorpusItem]) -> list[str]:
    return [it.id for it in items]


# ---------------------------------------------------------------- build_corpus

def test_build_corpus_item_counts_and_anchors(project):
    items = build_corpus(project)
    world = [it for it in items if it.id.startswith("world:")]
    chars = [it for it in items if it.id.startswith("char:")]
    assert _ids(world) == ["world:青云山", "world:黑水河", "world:天玄宗"]
    assert [it.anchor for it in world] == ["青云山", "黑水河", "天玄宗"]
    assert all(it.path == "world.md" for it in world)
    assert all("灵气充裕" in it.text for it in world if it.anchor == "青云山")
    assert _ids(chars) == ["char:林凡"]
    assert chars[0].path == "characters/林凡.md"
    assert "逆天血脉" in chars[0].text          # 角色卡正文进语料文本


def test_chapter_is_single_item_not_chunked(project):
    _write(project, "chapters/0002-远行.md", "---\nseq: 2\ntitle: 远行\n---\n\n"
           "# 远行\n\n第一段，讲林凡上路。\n\n第二段，讲夜宿荒庙。\n\n第三段，讲遇袭。\n")
    items = build_corpus(project)
    chaps = [it for it in items if it.id.startswith("chap:")]
    assert _ids(chaps) == ["chap:0001", "chap:0002"]   # 每章恰好一条，不切块
    assert chaps[0].path == "chapters/0001-起程.md"
    assert chaps[0].anchor == "起程"
    assert "转身走向远方" in chaps[0].text             # 全文都在一条里


def test_memory_sections_become_items(project):
    items = build_corpus(project)
    mem = [it for it in items if it.id.startswith("mem:")]
    assert _ids(mem) == [
        "mem:0001:事件:1",
        "mem:0001:角色经历:1",
        "mem:0001:信息项:1",
        "mem:0001:知晓关系:1",
        "mem:0001:承诺与伏笔:1",
    ]
    assert all(it.path == "memory/0001.md" for it in mem)
    assert [it.anchor for it in mem] == ["事件:1", "角色经历:1", "信息项:1", "知晓关系:1", "承诺与伏笔:1"]
    # ## 节整块成文本（### 小节留在所属 ## 节内）
    assert "他背起行囊" in mem[1].text


def test_memory_state_and_digests_not_in_corpus(project):
    items = build_corpus(project)
    assert not [it for it in items if "state.md" in it.path]
    assert not [it for it in items if "digests" in it.path]
    assert not [it for it in items if it.id.startswith("mem:") and "现状" in it.id]


def test_build_corpus_vol_filter_from_arc_capacity(project):
    """vol 语义：按 arcs 的章数容量提示累计划段，只取该卷章号（章 + 章记忆）。

    arcs 无容量信息时无法划段 → 不过滤（不猜）。
    """
    _write(project, "chapters/0002-远行.md", "---\nseq: 2\ntitle: 远行\n---\n\n# 远行\n\n远行内容。\n")
    _write(project, "memory/0002.md", _MEMORY.replace("第1章", "第2章"))
    _write(project, "outline/arcs.md", "---\narcs:\n- vol: 1\n  title: 崛起\n  capacity_hint: 约1章\n"
           "- vol: 2\n  title: 征战\n  capacity_hint: 约1章\n---\n\n")
    items = build_corpus(project, vol=1)
    assert "chap:0001" in _ids(items)
    assert "chap:0002" not in _ids(items)
    assert "mem:0001:事件:1" in _ids(items)
    assert not [it for it in items if it.id.startswith("mem:0002")]
    assert [it for it in items if it.id.startswith("world:")]   # 世界/角色不受 vol 限
    assert [it for it in items if it.id.startswith("char:")]


# ---------------------------------------------------------------- Index 失效/增量

def test_invalidate_path_removes_only_that_file(project):
    index = Index(build_corpus(project))
    assert len(index.items) == 3 + 1 + 1 + 5
    index.invalidate("world.md")
    assert not [it for it in index.items if it.path == "world.md"]
    assert [it for it in index.items if it.path == "memory/0001.md"]
    assert [it for it in index.items if it.path.startswith("chapters/")]
    index.invalidate("chapters/0001-起程.md")   # Windows/Posix 分隔符都认
    assert not [it for it in index.items if it.id.startswith("chap:")]


def test_add_incremental_after_invalidate(project):
    index = Index(build_corpus(project))
    index.invalidate("world.md")
    _write(project, "world.md", _WORLD.replace("天玄宗", "天玄宗（已更名玄天宗）"))
    index.add("world.md", [it for it in build_corpus(project) if it.path == "world.md"])
    assert len([it for it in index.items if it.path == "world.md"]) == 3
    assert any("玄天宗" in it.text for it in index.items)


def test_add_rejects_mismatched_path_loudly(project):
    """add 的 path 强制对齐会把 caller 错误静默改成索引损坏（评审 Important-2：
    记忆项挂到 world.md path 下、invalidate 精确剔除契约被破坏）→ 改为响亮 raise。"""
    index = Index(build_corpus(project))
    total = len(index.items)
    with pytest.raises(ValueError, match="world.md"):
        index.add("world.md", build_corpus(project))   # 整 corpus 混入非 world.md 项
    # raise 后索引原样未动
    assert len(index.items) == total
    ids = [it.id for it in index.items]
    assert len(ids) == len(set(ids))
    assert {it.path for it in index.items if it.id.startswith("mem:")} == {"memory/0001.md"}


def test_add_is_idempotent_rewrite(project):
    """重复 add 同一批语料项：项数/id 唯一性/path 归属不变；invalidate 后精确清零。"""
    index = Index(build_corpus(project))
    world_items = [it for it in build_corpus(project) if it.path == "world.md"]
    index.add("world.md", world_items)
    index.add("world.md", world_items)          # 不经 invalidate 重复 add 也不膨胀
    assert len([it for it in index.items if it.path == "world.md"]) == 3
    ids = [it.id for it in index.items]
    assert len(ids) == len(set(ids))
    index.invalidate("world.md")
    assert not [it for it in index.items if it.path == "world.md"]


# ---------------------------------------------------------------- 逐文件容错

def test_build_corpus_tolerates_bad_files(project, caplog):
    """坏 YAML 章节 / 坏 arcs / 非 UTF-8 文件：单文件隔离跳过 + 记 warning，
    不拖垮全量 build（评审 Important-1——角色卡路径已有的容错补全到全文件类型）。"""
    _write(project, "chapters/0002-远行.md",
           "---\nseq: 2\ntitle: 远行\n---\n\n# 远行\n\n林凡远行，途经黑水河。\n")
    _write(project, "chapters/0003-坏章.md",
           "---\nseq: [坏掉的 yaml\n---\n\n# 坏章\n\n不该进索引。\n")
    _write(project, "chapters/0004-乱码.md", "占位")   # 随后覆写为非 UTF-8 字节
    (project / "chapters/0004-乱码.md").write_bytes(b"\xff\xfe\x00\x01bad \xba\xba")
    _write(project, "characters/坏人.md", "---\nname: [坏\n---\n\n# 坏人\n")
    _write(project, "memory/0002.md", "占位")
    (project / "memory/0002.md").write_bytes(b"\xff\xfe bad \xba\xba")   # 非 UTF-8 章记忆
    _write(project, "outline/arcs.md", "---\narcs: [坏掉的\n")   # 坏 arcs → vol 划段失效
    with caplog.at_level("WARNING", logger="retrieval.corpus"):
        items = build_corpus(project)
        items_vol = build_corpus(project, vol=1)   # 坏 arcs 只在 vol 划段时被读到
    warned = " ".join(r.getMessage() for r in caplog.records)
    assert "chapters/0003-坏章.md" in warned
    assert "chapters/0004-乱码.md" in warned
    assert "arcs.md" in warned
    # 好文件照常入索引；坏文件被隔离
    ids = _ids(items)
    assert "chap:0002" in ids
    assert "chap:0003" not in ids
    assert "chap:0004" not in ids
    assert "char:林凡" in ids
    assert not [i for i in ids if i.startswith("char:坏人")]
    assert "mem:0001:事件:1" in ids
    assert not [i for i in ids if i.startswith("mem:0002")]
    # 坏 arcs → vol 划段退化为不过滤，不炸
    assert len(items_vol) == len(items)


# ---------------------------------------------------------------- search 隔离层

async def test_search_top_k_and_pool(project):
    _write(project, "chapters/0002-黑水河遇险.md",
           "---\nseq: 2\ntitle: 黑水河遇险\n---\n\n# 黑水河遇险\n\n林凡在黑水河遭遇妖兽。\n")
    index = Index(build_corpus(project))
    # top_k 截断（「逆天血脉」命中角色卡 + 信息项/知晓关系等多条）
    assert len(await index.search("逆天血脉", top_k=2)) == 2
    # pool 限定正文语料：结果只含 chap: 项
    result = await index.search("黑水河", top_k=5,
                                pool=chapter_items(index.items))
    assert result and all(it.id.startswith("chap:") for it in result)


async def test_search_reflects_file_rewrite(project):
    """Review Focus #4：改 world.md 后索引必须反映新内容（path 级失效不陈旧）。"""
    index = Index(build_corpus(project))
    before = await index.search("玄冥珠", top_k=3)
    assert before == []                          # 新设定旧索引检不出
    total = len(index.items)
    _write(project, "world.md", _WORLD + "\n## 法宝\n\n### 玄冥珠\n- 重要度：高\n\n玄冥珠是上古寒冰法宝。\n")
    index.invalidate("world.md")
    index.add("world.md", [it for it in build_corpus(project) if it.path == "world.md"])
    after = await index.search("玄冥珠", top_k=3)
    assert [it.id for it in after] == ["world:玄冥珠"]
    # 全局一致性：项数 = 旧总量 - 3 + 4（重写后 world 条目变 4），id 全局唯一、
    # path 归属正确——防「查询选择性强所以假绿」的盲区
    assert len(index.items) == total + 1
    ids = [it.id for it in index.items]
    assert len(ids) == len(set(ids))
    assert {it.path for it in index.items if it.id.startswith("mem:")} == {"memory/0001.md"}


async def test_search_unrelated_query_returns_empty(project):
    index = Index(build_corpus(project))
    assert await index.search("量子力学引力波", top_k=5) == []


async def test_search_async_not_blocking(project, monkeypatch):
    """to_thread 纪律：BM25 打分在 worker 线程，事件循环不被同步睡眠卡死。"""
    real = bm25_mod.bm25_rank

    def _slow_rank(*args, **kwargs):
        import time
        time.sleep(0.3)                          # 同步 CPU 睡眠模拟大语料打分
        return real(*args, **kwargs)

    monkeypatch.setattr(bm25_mod, "bm25_rank", _slow_rank)
    index = Index(build_corpus(project))
    ticks = 0

    async def ticker():
        nonlocal ticks
        for _ in range(5):
            await asyncio.sleep(0.02)
            ticks += 1

    task = asyncio.create_task(index.search("逆天血脉", top_k=3))
    await ticker()
    result = await asyncio.wait_for(task, timeout=0.5)
    assert ticks == 5                            # 搜索期间循环照常跑
    assert result


# ---------------------------------------------------------------- T2→T1 bridge

class _FakeEmbedder(Embedder):
    """录请求的假嵌入器：按文本内容取向量（不依赖语料项的具体渲染文本）。"""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        out = []
        for t in texts:
            if "玄冥" in t:
                out.append([0.0, 1.0])     # 寒冰系 → 与查询同向
            elif "烈焰" in t:
                out.append([1.0, 0.0])     # 火系 → 与查询正交
            elif "寒冰" in t:
                out.append([0.0, 1.0])
            else:
                out.append([0.5, 0.5])
        return out

async def test_search_dense_adapter_genuinely_participates(project):
    """embedder 给定时 dense 真正参与排序（而非静默退回纯 BM25）。

    构造：A=烈焰剑词面强命中（best）；B=玄冥珠词面勉强进召回池但低于
    bm25_min_ratio 门槛 → 纯 BM25 被门槛刷掉；给它高 cosine 后经 semantic_ok
    回到结果里——且假嵌入器确实被调用过（dense 非静默失效）。
    """
    _write(project, "world.md", _WORLD + "\n## 法宝\n\n### 烈焰剑\n- 重要度：高\n\n"
           "烈焰剑经寒冰法宝淬炼，又经寒冰法宝温养，终成极品寒冰法宝飞剑。\n\n"
           "### 玄冥珠\n- 重要度：高\n\n"
           "玄冥珠性寒，传闻出自寒冰法宝宗师之手。\n")
    index = Index(build_corpus(project))

    emb = _FakeEmbedder()
    cfg = RetrievalSettings(bm25_min_ratio=0.9, min_cosine=0.5)

    # 纯词面：玄冥珠低于 0.9 门槛被刷掉 → 只剩烈焰剑
    assert [it.id for it in await index.search("寒冰法宝", top_k=5, cfg=cfg)] == ["world:烈焰剑"]

    # dense 参与后玄冥珠经 semantic_ok 回到结果；embed 真被调过（非静默退回）
    result = await index.search("寒冰法宝", top_k=5, embedder=emb, cfg=cfg)
    assert [it.id for it in result] == ["world:烈焰剑", "world:玄冥珠"]
    assert emb.calls, "embedder 未被调用——dense 静默失效"

    # 对照：同 embedder 但 semantic_enabled=False → 退回纯 BM25（embed 不再多调）
    calls_before = len(emb.calls)
    cfg_lex = RetrievalSettings(bm25_min_ratio=0.9, min_cosine=0.5, semantic_enabled=False)
    assert [it.id for it in await index.search("寒冰法宝", top_k=5, embedder=emb, cfg=cfg_lex)] \
        == ["world:烈焰剑"]
    assert len(emb.calls) == calls_before


async def test_search_embedder_exception_falls_back_silently(project):
    """嵌入失败不炸检索：_dense_scores 裸 except → 退回纯 BM25 结果。"""

    class _BoomEmbedder(Embedder):
        async def embed(self, texts):
            raise RuntimeError("嵌入后端挂了")

    index = Index(build_corpus(project))
    result = await index.search("瘴气弥漫", top_k=3, embedder=_BoomEmbedder())
    assert [it.id for it in result] == ["world:黑水河"]


# ---------------------------------------------------------------- chapter_items

def test_chapter_items_selector(project):
    items = build_corpus(project)
    chaps = chapter_items(items)
    assert [it.id for it in chaps] == ["chap:0001"]
    assert all(it.id.startswith("chap:") for it in chaps)

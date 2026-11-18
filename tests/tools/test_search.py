"""正文回指通道（backrefs）+ search_chapters 只读工具（P4 Task 8）测试。

裁定 4：回指通道仅 BM25（embedder=None，正文语料不进嵌入），pool=chapter_items，
top_k 钉 4；Review Focus #5：回指引文必须逐字存在于章正文（locate_quote 回验，
回验失败丢弃该命中）；晚于当前章的命中剔除（时间锚点纪律同 T7）。
search_chapters：registry 同步 handler（(store,args)->str，零 LLM），观测词表确定性。
"""
import re

import pytest

from harness.projects import create_project
from harness.retrieval.bm25 import terms_from_texts
from harness.retrieval.corpus import Index, build_corpus
from harness.store.project import ChapterDoc, ProjectStore
from harness.text import locate_quote
from harness.tools.search import backrefs, search_chapters

_LINE = re.compile(r"^第(\d+)章《(.+?)》：「(.+)」$")


@pytest.fixture
def store(tmp_path):
    info = create_project(tmp_path, "回指书")
    s = ProjectStore(info.path)
    s.upsert_arcs([{"vol": 1, "title": "卷甲", "goal": "g", "summary": "s",
                    "conflict": "c", "resolution": "r"}])
    return s


def _chapter(seq, title, body, plot_id="1-1"):
    return ChapterDoc(seq=seq, meta={"seq": seq, "title": title, "status": "done",
                                     "plot_id": plot_id}, body=body)


def _index(store):
    return Index(build_corpus(store.root))


# ---------------------------------------------------------------- search_chapters

def test_search_chapters_verbatim_quotes(store):
    """Review Focus #5：每条命中的引文都能经 locate_quote 在对应章正文逐字定位。"""
    store.write_chapter(_chapter(
        1, "开端", "山道夜雨。\n\n陆昭捡到了一枚青铜钥匙，钥匙上刻着云纹。"))
    store.write_chapter(_chapter(
        2, "碑林", "山门前的碑林静默无声，风穿过石缝。"))
    store.write_chapter(_chapter(
        3, "旧箱", "多年后，那枚青铜钥匙再次开启旧箱，箱中藏着图谱。"))
    out = search_chapters(store, {"query": "青铜钥匙"})
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) >= 2                      # 第1、3章都命中
    assert "第1章《开端》" in out and "第3章《旧箱》" in out
    for ln in lines:
        m = _LINE.match(ln)
        assert m, f"观测行格式不符：{ln!r}"
        seq, quote = int(m.group(1)), m.group(3)
        body = store.read_chapter(seq).body
        assert locate_quote(body, quote) is not None, \
            f"第{seq}章引文未通过 locate_quote 逐字回验：{quote!r}"


def test_search_chapters_no_hit(store):
    store.write_chapter(_chapter(1, "开端", "山道夜雨。"))
    out = search_chapters(store, {"query": "量子核桃玄学"})
    assert out == "search_chapters：无命中"


def test_search_chapters_top_k(store):
    store.write_chapter(_chapter(1, "开端", "陆昭捡到了一枚青铜钥匙，刻着云纹。"))
    store.write_chapter(_chapter(2, "碑林", "山门前的碑林静默无声。"))
    store.write_chapter(_chapter(3, "旧箱", "那枚青铜钥匙再次开启旧箱。"))
    out = search_chapters(store, {"query": "青铜钥匙", "top_k": 1})
    assert len([ln for ln in out.splitlines() if ln.strip()]) == 1


def test_search_top_k_pool_only_chapters(store):
    """pool=chapter_items：world/char 语料含查询词也不得混进结果。"""
    store.write_chapter(_chapter(1, "开端", "陆昭捡到了一枚青铜钥匙，刻着云纹。"))
    store.upsert_world_entries([{"title": "古物志", "category": "器物",
                                 "content": "青铜钥匙传说是上古遗物"}])
    store.upsert_character({"name": "陆昭", "role_type": "protagonist",
                            "aliases": [], "持有": "青铜钥匙"})
    out = search_chapters(store, {"query": "青铜钥匙"})
    assert "第1章《开端》" in out
    assert "古物志" not in out and "上古遗物" not in out
    assert "持有" not in out


def test_search_chapters_param_errors(store):
    out = search_chapters(store, {})
    assert "search_chapters 失败（参数）" in out and "args.query" in out
    out = search_chapters(store, {"query": "  "})
    assert "search_chapters 失败（参数）" in out
    out = search_chapters(store, {"query": "青铜钥匙", "top_k": True})
    assert "search_chapters 失败（参数）" in out and "args.top_k" in out
    out = search_chapters(store, {"query": "青铜钥匙", "top_k": 0})
    assert "search_chapters 失败（参数）" in out


# ---------------------------------------------------------------- backrefs

def test_backrefs_excludes_future_chapters(store):
    """时间锚点纪律：晚于当前章的命中剔除（cur_seq 过滤在回验之前）。"""
    store.write_chapter(_chapter(1, "开端", "山道夜雨，他撑伞独行。"))
    store.write_chapter(_chapter(2, "碑林", "山门前的碑林静默无声。"))
    store.write_chapter(_chapter(
        3, "旧箱", "那枚青铜钥匙终于现身。\n\n青铜钥匙开启旧箱，箱中藏着图谱。"
                   "\n\n青铜钥匙的秘密即将揭晓。"))
    index = _index(store)
    terms = terms_from_texts(["青铜钥匙"])
    assert [int(it.id.split(":")[1]) for it, _ in backrefs(index, terms)] == [3]
    got = backrefs(index, terms, cur_seq=2)
    assert got == []
    got = backrefs(index, terms, cur_seq=3)
    assert [int(it.id.split(":")[1]) for it, _ in got] == [3]


def test_backrefs_quote_verify_failure_dropped(store):
    """标题含查询词、正文不含：命中行的最长命中片段是标题行——在正文里回验失败
    → 该命中丢弃（引文绝不凭空出标题，Review Focus #5）。"""
    store.write_chapter(_chapter(
        1, "青铜钥匙", "山道夜雨，风穿过石缝，他在碑林前驻足良久。"))
    index = _index(store)
    terms = terms_from_texts(["青铜钥匙"])
    assert backrefs(index, terms) == []


def test_backrefs_top_k_default(store):
    for seq in range(1, 7):
        store.write_chapter(_chapter(
            seq, f"章{seq}", f"那枚青铜钥匙在火光中发亮，第{seq}次被提起。"))
    index = _index(store)
    hits = backrefs(index, terms_from_texts(["青铜钥匙"]))
    assert 1 <= len(hits) <= 4          # top_k 钉 4
    for it, quote in hits:
        seq = int(it.id.split(":")[1])
        assert locate_quote(store.read_chapter(seq).body, quote) is not None

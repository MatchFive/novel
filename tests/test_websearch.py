"""联网核查服务层（P5 Task 9）：双 provider 适配 / verify 流程 / verify_deep 补搜 /
research.md 去重与落账 / ``[search]`` 配置解析。

口径钉死：
- kimi_api：POST ``{base_url}/tools/search``，body ``{text_query, limit, timeout_seconds}``
  → 响应 ``search_results[]``；
- minimax：POST ``{base_url}/v1/coding_plan/search``，body ``{"q"}`` → 响应 ``organic[]``；
- HTTP 失败 → **空列表不 raise**（联网失败一律降级，不阻断写作）；
- digest：每条 ``[site] title（date）\\n snippet[:400]\\n url``，总量截 4000 字；
- verify：自由文本结论（不是 JSON）；「未能核实」也是一条结论，照样落 entry；
- verify_deep：research.md 命中直接复用（零 HTTP）；否则 verify + ≤followup_rounds 轮补搜；
- research.md 去重两级：query_key 精确命中 → hits+1；字符集重叠 ≥0.9 → 同样 hits+1。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx
import pytest

from harness.config import ProviderConfig, SearchSettings, load_settings
from harness.prompts.loader import PromptLoader
from harness.websearch import (
    RESULTS_MAX_CHARS,
    ResearchEntry,
    WebSearchService,
    append_research,
    load_research,
    query_key_of,
)


class FakeLLM:
    """顺序吐脚本的假 llm（complete 返回自由文本或 JSON body）。"""

    def __init__(self, script):
        self._script = list(script)
        self.calls: list[str] = []
        self.provider = ProviderConfig(base_url="https://api.test", api_key="k",
                                       lite="m-lite", standard="m-std", pro="m-pro")

    async def complete(self, messages, *, model, temperature=0.7, max_tokens=8192,
                       thinking=False):
        self.calls.append(messages[-1]["content"])
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return type("R", (), {"text": item, "usage": None})()


def make_http(responses, *, status=200):
    """httpx.MockTransport：按调用次序返回 responses 里的 JSON。"""
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request):
        calls.append(req)
        item = responses[len(calls) - 1] if len(calls) <= len(responses) else {}
        if isinstance(item, Exception):
            raise item
        if isinstance(item, int):
            return httpx.Response(status, json={})
        return httpx.Response(status, json=item)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                               base_url="https://api.test")
    return client, calls


def make_service(settings, llm_script, http_responses, *, status=200, tmp_path=None):
    llm = FakeLLM(llm_script)
    http, http_calls = make_http(http_responses, status=status)
    svc = WebSearchService(settings, llm, PromptLoader(home_dir=tmp_path or Path(".")),
                            http_client=http)
    return svc, llm, http_calls


def kimi_results():
    return {"search_results": [
        {"title": "率土之滨内政系统", "url": "https://a.test/1", "snippet": "内政含屯田",
         "date": "2024-01-02", "site_name": "百科"},
    ]}


def minimax_results():
    return {"organic": [
        {"title": "文明6尤里卡", "url": "https://b.test/2", "content": "尤里卡触发条件",
         "site": "wiki", "date": "2024-03"},
    ]}


# ------------------------------------------------------------------ provider 适配

async def test_kimi_provider_request_shape(tmp_path):
    """kimi_api：POST {base_url}/tools/search，body {text_query,limit,timeout_seconds}。"""
    settings = SearchSettings(provider="kimi_api", base_url="https://api.test/v1",
                              api_key="k", limit=3, timeout_seconds=7)
    svc, _llm, calls = make_service(settings, [], [kimi_results()], tmp_path=tmp_path)

    results = await svc.search("率土之滨 内政")

    assert len(calls) == 1
    assert calls[0].method == "POST"
    assert str(calls[0].url) == "https://api.test/v1/tools/search"
    body = json.loads(calls[0].content)
    assert body["text_query"] == "率土之滨 内政"
    assert body["limit"] == 3
    assert body["timeout_seconds"] == 7
    # 归一
    assert results == [{"title": "率土之滨内政系统", "url": "https://a.test/1",
                        "snippet": "内政含屯田", "date": "2024-01-02",
                        "site_name": "百科"}]


async def test_minimax_provider_request_shape(tmp_path):
    """minimax：POST {base_url}/v1/coding_plan/search，body {"q"} → organic[] 归一。"""
    settings = SearchSettings(provider="minimax", base_url="https://api.test")
    svc, _llm, calls = make_service(settings, [], [minimax_results()], tmp_path=tmp_path)

    results = await svc.search("文明6 尤里卡")

    assert str(calls[0].url) == "https://api.test/v1/coding_plan/search"
    assert json.loads(calls[0].content) == {"q": "文明6 尤里卡"}
    assert results[0]["title"] == "文明6尤里卡"
    assert results[0]["snippet"] == "尤里卡触发条件"
    assert results[0]["site_name"] == "wiki"


async def test_minimax_link_field_normalized_to_url(tmp_path):
    """minimax 的 ``link`` 字段必须归一到 url（seed ``_search_minimax`` 用 link）。

    漏映射的后果：url/site_name 全空 → digest 无链接、sources 无出处 → research.md
    丢掉全部溯源信息，「出处与时间」那一步无从核对。
    """
    settings = SearchSettings(provider="minimax", base_url="https://api.test")
    payload = {"organic": [{"title": "文明6尤里卡", "link": "https://b.test/2",
                            "content": "尤里卡触发条件", "site": "wiki"}]}
    svc, _llm, _calls = make_service(settings, [], [payload], tmp_path=tmp_path)

    results = await svc.search("文明6 尤里卡")

    assert results[0]["url"] == "https://b.test/2"
    assert results[0]["site_name"] == "wiki"


async def test_minimax_sources_keep_link(tmp_path):
    """minimax 结果的 link 要进 ResearchEntry.sources（溯源不丢）。"""
    settings = SearchSettings(provider="minimax", base_url="https://api.test")
    payload = {"organic": [{"title": "文明6尤里卡", "link": "https://b.test/2",
                            "content": "尤里卡触发条件", "site": "wiki"}]}
    svc, _llm, _calls = make_service(settings, ["已核实结论。"], [payload], tmp_path=tmp_path)

    entry = await svc.verify("文明6 尤里卡")

    assert entry is not None
    assert entry.sources == ["https://b.test/2"]


async def test_search_failure_returns_empty(tmp_path):
    """HTTP 500 → 空列表，不 raise（联网失败一律降级）。"""
    settings = SearchSettings(provider="kimi_api", base_url="https://api.test")
    svc, _llm, _calls = make_service(settings, [], [{}], status=500, tmp_path=tmp_path)

    assert await svc.search("任意") == []


async def test_search_without_settings_zero_http(tmp_path):
    """未配 [search]（settings=None）→ 零 HTTP 零 LLM。"""
    svc, llm, calls = make_service(None, [], [kimi_results()], tmp_path=tmp_path)

    assert await svc.search("任意") == []
    assert calls == []
    assert llm.calls == []


async def test_digest_truncated_4000(tmp_path):
    """digest 总量截 4000 字（长摘要被截）。"""
    from harness.websearch import _digest

    results = [{"title": "T", "url": "u", "snippet": "字" * 500, "date": "", "site_name": "s"}
               for _ in range(20)]
    digest = _digest(results)
    assert len(digest) <= RESULTS_MAX_CHARS
    assert "T" in digest


# ------------------------------------------------------------------ verify / verify_deep

async def test_verify_flow(tmp_path):
    """verify：检索 → digest → LLM 自由文本 → entry.conclusion = LLM 文本。"""
    settings = SearchSettings(provider="kimi_api", base_url="https://api.test")
    svc, llm, _calls = make_service(settings, ["这是一条核实结论。"],
                                    [kimi_results()], tmp_path=tmp_path)

    entry = await svc.verify("率土之滨 内政")

    assert entry is not None
    assert entry.conclusion == "这是一条核实结论。"
    assert entry.query_key == query_key_of("率土之滨 内政")
    assert entry.sources == ["https://a.test/1"]
    # 提示词带 query 与检索结果
    assert "率土之滨 内政" in llm.calls[0]
    assert "率土之滨内政系统" in llm.calls[0]


async def test_verify_failure_returns_none(tmp_path):
    """LLM 调用失败 → None（不是「无问题」）。"""
    settings = SearchSettings(provider="kimi_api", base_url="https://api.test")
    svc, _llm, _calls = make_service(settings, [RuntimeError("boom")],
                                    [kimi_results()], tmp_path=tmp_path)

    assert await svc.verify("率土之滨 内政") is None


async def test_verify_deep_followup(tmp_path):
    """首轮结论 → followup 给出补充查询 → 第二轮 search 被调。"""
    settings = SearchSettings(provider="kimi_api", base_url="https://api.test",
                              followup_rounds=1)
    svc, _llm, calls = make_service(
        settings,
        ["首轮结论", json.dumps({"queries": ["率土之滨 屯田 机制"]}), "补搜结论"],
        [kimi_results(), kimi_results()], tmp_path=tmp_path)

    entries = await svc.verify_deep(root=tmp_path, queries=["率土之滨 内政"])

    assert len(entries) == 1
    assert "首轮结论" in entries[0].conclusion
    assert "补搜结论" in entries[0].conclusion
    assert len(calls) == 2                      # 首轮检索 + 补搜检索
    assert json.loads(calls[1].content)["text_query"] == "率土之滨 屯田 机制"


async def test_followup_no_gap_stops(tmp_path):
    """followup 返回空 queries → 不再搜（宁缺毋滥）。"""
    settings = SearchSettings(provider="kimi_api", base_url="https://api.test",
                              followup_rounds=2)
    svc, _llm, calls = make_service(
        settings, ["首轮结论", json.dumps({"queries": []})],
        [kimi_results()], tmp_path=tmp_path)

    entries = await svc.verify_deep(root=tmp_path, queries=["率土之滨 内政"])

    assert len(entries) == 1
    assert len(calls) == 1                      # 只有首轮检索


async def test_verify_deep_reuses_research(tmp_path):
    """research.md 已命中 → 零 HTTP 直接复用（hits+1）。"""
    settings = SearchSettings(provider="kimi_api", base_url="https://api.test")
    append_research(tmp_path, ResearchEntry(query="率土之滨 内政",
                                            query_key=query_key_of("率土之滨 内政"),
                                            kind="fact", conclusion="已有结论",
                                            sources=["https://a.test/1"], hit_count=1))
    svc, llm, calls = make_service(settings, [], [], tmp_path=tmp_path)

    entries = await svc.verify_deep(root=tmp_path, queries=["率土之滨 内政"])

    assert calls == []                          # 零 HTTP
    assert llm.calls == []                      # 零 LLM
    assert entries[0].conclusion == "已有结论"
    assert entries[0].hit_count == 2
    assert load_research(tmp_path)[0].hit_count == 2


# ------------------------------------------------------------------ research.md 落账

def test_research_roundtrip(tmp_path):
    """append → load 字段一致。"""
    append_research(tmp_path, ResearchEntry(query="甲问题", query_key=query_key_of("甲问题"),
                                            kind="fact", conclusion="结论正文",
                                            sources=["https://a.test/1", "https://b.test/2"],
                                            hit_count=1))
    entries = load_research(tmp_path)
    assert len(entries) == 1
    assert entries[0].query == "甲问题"
    assert entries[0].kind == "fact"
    assert entries[0].conclusion == "结论正文"
    assert entries[0].sources == ["https://a.test/1", "https://b.test/2"]
    assert entries[0].hit_count == 1


def test_research_dedup_exact(tmp_path):
    """同 query 两次 append → 第二次只 hits+1，不追加新节。"""
    for _ in range(2):
        append_research(tmp_path, ResearchEntry(query="甲问题",
                                                query_key=query_key_of("甲问题"),
                                                kind="fact", conclusion="结论正文",
                                                sources=[], hit_count=1))
    entries = load_research(tmp_path)
    assert len(entries) == 1
    assert entries[0].hit_count == 2


def test_research_dedup_near_overlap(tmp_path):
    """字符集重叠 ≥0.9 → 合并（hits+1）。"""
    append_research(tmp_path, ResearchEntry(query="率土之滨内政系统",
                                            query_key=query_key_of("率土之滨内政系统"),
                                            kind="fact", conclusion="结论", sources=[], hit_count=1))
    append_research(tmp_path, ResearchEntry(query="率土之滨内政系",
                                            query_key=query_key_of("率土之滨内政系"),
                                            kind="fact", conclusion="结论", sources=[], hit_count=1))
    entries = load_research(tmp_path)
    assert len(entries) == 1
    assert entries[0].hit_count == 2


def test_research_distinct_kept(tmp_path):
    """不同 query → 各自成节。"""
    append_research(tmp_path, ResearchEntry(query="甲问题", query_key=query_key_of("甲问题"),
                                            kind="fact", conclusion="A", sources=[], hit_count=1))
    append_research(tmp_path, ResearchEntry(query="乙问题", query_key=query_key_of("乙问题"),
                                            kind="fact", conclusion="B", sources=[], hit_count=1))
    assert len(load_research(tmp_path)) == 2


# ------------------------------------------------------------------ 无损往返（I-2）

#: 结论里同时含 '- ' 开头行与 '## ' 开头行（旧行前缀启发解析会把这两种行误当
#: sources / 新条目 → 一次写坏就永久损坏）
TRICKY_CONCLUSION = ("结论第一行\n"
                     "- 这是一条以 '- ' 开头的行\n"
                     "## 这是一条以 '## ' 开头的行\n"
                     "结论末行")


def test_research_roundtrip_lossless(tmp_path):
    """结论含 '- ' / '## ' 行 → append → load 往返**逐字相同**（sources/hit_count 同）。"""
    append_research(tmp_path, ResearchEntry(query="甲问题", query_key=query_key_of("甲问题"),
                                            kind="fact", conclusion=TRICKY_CONCLUSION,
                                            sources=["https://a.test/1", "https://b.test/2"],
                                            hit_count=3))

    entries = load_research(tmp_path)

    assert len(entries) == 1
    assert entries[0].conclusion == TRICKY_CONCLUSION
    assert entries[0].sources == ["https://a.test/1", "https://b.test/2"]
    assert entries[0].hit_count == 3
    assert entries[0].query == "甲问题"
    assert entries[0].kind == "fact"


def test_research_two_entries_no_pollution(tmp_path):
    """连写两条（结论各含 '- ' / '## ' 行）→ 互不污染（append 会 load→改→整文件重写）。"""
    append_research(tmp_path, ResearchEntry(query="甲问题", query_key=query_key_of("甲问题"),
                                            kind="fact", conclusion=TRICKY_CONCLUSION,
                                            sources=["https://a.test/1"], hit_count=1))
    append_research(tmp_path, ResearchEntry(query="乙问题", query_key=query_key_of("乙问题"),
                                            kind="ref", conclusion=TRICKY_CONCLUSION,
                                            sources=["https://b.test/2"], hit_count=2))

    entries = load_research(tmp_path)

    assert len(entries) == 2
    assert entries[0].conclusion == TRICKY_CONCLUSION
    assert entries[1].conclusion == TRICKY_CONCLUSION
    assert entries[0].sources == ["https://a.test/1"]
    assert entries[1].sources == ["https://b.test/2"]
    assert entries[1].hit_count == 2


def test_research_meta_line_carries_sources(tmp_path):
    """sources 走元信息行的 JSON 数组（结论里的 '- ' 行不再被当 sources）。"""
    append_research(tmp_path, ResearchEntry(query="甲问题", query_key=query_key_of("甲问题"),
                                            kind="fact", conclusion=TRICKY_CONCLUSION,
                                            sources=["https://a.test/1"], hit_count=1))

    text = (tmp_path / ".harness" / "research.md").read_text(encoding="utf-8")

    assert 'sources: ["https://a.test/1"]' in text
    assert "- 这是一条以 '- ' 开头的行" in text


# ------------------------------------------------------------------ 空行隔开元信息行

def test_research_meta_after_blank_line(tmp_path):
    """``## 标题`` 与元信息行之间**隔了空行** → 元信息仍被解析，且**不进结论**。

    判定逻辑（``_next_is_meta``）会跳过空行去找元信息行，取的时候也必须跳过
    ——否则 key/kind/hits/sources 全丢，且元信息行被吞进结论。
    """
    path = tmp_path / ".harness" / "research.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('## A\n\n<!-- key: kA kind: fact hits: 2 sources: ["https://a.test/1"] -->\n'
                    '结论A\n', encoding="utf-8")

    entries = load_research(tmp_path)

    assert len(entries) == 1
    assert entries[0].query == "A"
    assert entries[0].query_key == "kA"
    assert entries[0].kind == "fact"
    assert entries[0].hit_count == 2
    assert entries[0].sources == ["https://a.test/1"]
    assert entries[0].conclusion == "结论A"
    assert "key: kA" not in entries[0].conclusion


# ------------------------------------------------------------------ 坏 sources JSON

def test_research_bad_sources_json_warns(tmp_path, caplog):
    """sources 是**坏 JSON** → 解析不抛、条目不丢，且**有警告**（出处不得静默丢失）。

    样本用 ``["a", ]``（真非法 JSON）。**不要**用 ``["a]b"]``——那其实是合法 JSON
    （字符串 "a]b"），贪婪正则 ``\\[.*\\]`` 会正确解析出 ``["a]b"]``（出处里带 "]" 的
    URL 才不会被截断）。
    """
    path = tmp_path / ".harness" / "research.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('## A\n<!-- key: kA kind: fact hits: 1 sources: ["a", ] -->\n结论A\n',
                    encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="websearch"):
        entries = load_research(tmp_path)

    assert len(entries) == 1
    assert entries[0].conclusion == "结论A"
    assert entries[0].sources == []
    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warns, "坏 sources 必须有警告（不得静默丢出处）"
    assert "sources" in caplog.text


# ------------------------------------------------------------------ 去重收紧（I-3）

def test_research_dedup_subset_not_merged(tmp_path):
    """「内政」vs「内政系统」→ **不合并**（共享仅 2 字；min() 分母使重叠率恒为 1.0）。"""
    append_research(tmp_path, ResearchEntry(query="内政", query_key=query_key_of("内政"),
                                            kind="fact", conclusion="A", sources=[], hit_count=1))
    append_research(tmp_path, ResearchEntry(query="内政系统",
                                            query_key=query_key_of("内政系统"),
                                            kind="fact", conclusion="B", sources=[], hit_count=1))

    entries = load_research(tmp_path)

    assert len(entries) == 2
    assert [e.query for e in entries] == ["内政", "内政系统"]


def test_research_dedup_near_overlap_still_merged(tmp_path):
    """「率土之滨内政系统」vs「率土之滨内政系」→ 仍合并（共享 7 字 ≥3、重叠率 1.0）。"""
    append_research(tmp_path, ResearchEntry(query="率土之滨内政系统",
                                            query_key=query_key_of("率土之滨内政系统"),
                                            kind="fact", conclusion="A", sources=[], hit_count=1))
    append_research(tmp_path, ResearchEntry(query="率土之滨内政系",
                                            query_key=query_key_of("率土之滨内政系"),
                                            kind="fact", conclusion="B", sources=[], hit_count=1))

    entries = load_research(tmp_path)

    assert len(entries) == 1
    assert entries[0].hit_count == 2


def test_research_empty_when_absent(tmp_path):
    """文件不存在 → 空列表。"""
    assert load_research(tmp_path) == []


def test_query_key_normalizes():
    """query_key：去空白与标点 + 小写。"""
    assert query_key_of("率土之滨  内政！") == query_key_of("率土之滨内政")


# ------------------------------------------------------------------ 鉴权头（C-2）

async def test_post_sends_bearer_authorization(tmp_path):
    """[search] api_key 非空 → 请求带 ``Authorization: Bearer <key>``（此前从未发送 → 一律 401）。"""
    settings = SearchSettings(provider="kimi_api", base_url="https://api.test", api_key="sk-test")
    svc, _llm, calls = make_service(settings, [], [kimi_results()], tmp_path=tmp_path)

    await svc.search("率土之滨 内政")

    assert calls[0].headers.get("authorization") == "Bearer sk-test"
    assert calls[0].headers.get("content-type", "").startswith("application/json")


async def test_post_without_api_key_sends_no_authorization(tmp_path):
    """api_key 为空 → **不带** Authorization 头（不发空的 ``Bearer ``）。"""
    settings = SearchSettings(provider="kimi_api", base_url="https://api.test", api_key="")
    svc, _llm, calls = make_service(settings, [], [kimi_results()], tmp_path=tmp_path)

    await svc.search("率土之滨 内政")

    assert "authorization" not in calls[0].headers


# ------------------------------------------------------------------ 零结果不核实（C-3）

async def test_verify_returns_none_on_empty_results(tmp_path):
    """检索零结果 → verify 返回 None 且**零 LLM 调用**（不凭训练记忆编结论）。"""
    settings = SearchSettings(provider="kimi_api", base_url="https://api.test")
    svc, llm, _calls = make_service(settings, ["本该被编造出来的结论"], [{}], tmp_path=tmp_path)

    entry = await svc.verify("率土之滨 内政")

    assert entry is None
    assert llm.calls == []


async def test_verify_deep_zero_results_writes_no_research(tmp_path):
    """检索零结果 → verify_deep 不产出条目、**不写 research.md**（编造结论不得被缓存成「已核实」）。"""
    settings = SearchSettings(provider="kimi_api", base_url="https://api.test")
    svc, llm, _calls = make_service(settings, ["本该被编造出来的结论"], [{}], tmp_path=tmp_path)

    entries = await svc.verify_deep(root=tmp_path, queries=["率土之滨 内政"])

    assert entries == []
    assert llm.calls == []
    assert load_research(tmp_path) == []
    assert not (tmp_path / ".harness" / "research.md").exists()


# ------------------------------------------------------------------ 无出处不缓存

#: provider 返回了条目但**没有 url/link**（minimax 的 link 缺失等）→ sources 为空
NO_URL_RESULTS = {"search_results": [{"title": "率土之滨内政系统", "snippet": "内政含屯田",
                                       "date": "2024-01-02", "site_name": "百科"}]}


async def test_verify_returns_none_without_sources(tmp_path):
    """检索**有结果但无 url** → verify 返回 None、**零 LLM 调用**（无出处不核实）。

    与「检索零结果」同口径（C-3）：结论没有出处 = 无法核对 = 不产出。
    """
    settings = SearchSettings(provider="kimi_api", base_url="https://api.test")
    svc, llm, _calls = make_service(settings, ["本该被编造出来的结论"],
                                    [NO_URL_RESULTS], tmp_path=tmp_path)

    entry = await svc.verify("率土之滨 内政")

    assert entry is None
    assert llm.calls == []


async def test_verify_deep_without_sources_writes_no_research(tmp_path):
    """检索有结果但无 url → verify_deep 不落 research.md（无出处结论不得进缓存复用）。"""
    settings = SearchSettings(provider="kimi_api", base_url="https://api.test")
    svc, llm, _calls = make_service(settings, ["本该被编造出来的结论"],
                                    [NO_URL_RESULTS], tmp_path=tmp_path)

    entries = await svc.verify_deep(root=tmp_path, queries=["率土之滨 内政"])

    assert entries == []
    assert llm.calls == []
    assert load_research(tmp_path) == []
    assert not (tmp_path / ".harness" / "research.md").exists()


# ------------------------------------------------------------------ 读侧：无出处不复用（P5 复审遗留 4）

async def test_verify_deep_skips_cached_entry_without_sources(tmp_path):
    """research.md 里手写的**无出处**条目 → verify_deep **不复用**它：
    不返回、不重新落盘（读侧与写侧同口径——无出处不缓存）。

    写侧已钉死「无 sources → 不落库」；读侧（缓存命中分支）不得把一条**无出处**的旧
    条目（手写或旧版本留下的）当成「已核实」复用并重新落盘。
    """
    query = "率土之滨 内政"
    research = tmp_path / ".harness" / "research.md"
    research.parent.mkdir(parents=True, exist_ok=True)
    research.write_text(
        f"## {query}\n<!-- key: {query_key_of(query)} kind: fact hits: 1 sources: [] -->\n"
        "手写结论（无出处）\n", encoding="utf-8")
    before = research.read_bytes()
    settings = SearchSettings(provider="kimi_api", base_url="https://api.test")
    svc, llm, calls = make_service(settings, [], [], tmp_path=tmp_path)

    entries = await svc.verify_deep(root=tmp_path, queries=[query])

    assert entries == []                       # 不返回那条无出处的条目
    assert llm.calls == []                     # 零 LLM
    assert calls == []                         # 零 HTTP
    assert research.read_bytes() == before     # 不重新落盘（hits 不得被顶上去）


# ------------------------------------------------------------------ 坏 base_url 降级（M-10）

@pytest.mark.parametrize("base_url", ["ftp://api.test", "http://example.com:notaport"])
async def test_search_bad_base_url_degrades_to_empty(base_url, tmp_path):
    """坏 base_url（协议不支持 / 端口非法）→ 降级为空列表，**不抛**（联网失败一律降级）。"""
    llm = FakeLLM([])
    svc = WebSearchService(SearchSettings(provider="kimi_api", base_url=base_url), llm,
                           PromptLoader(home_dir=tmp_path))
    try:
        assert await svc.search("任意") == []
    finally:
        await svc.aclose()


# ------------------------------------------------------------------ [search] 配置

def test_config_search_section(tmp_path):
    """[search] 段解析；缺省 → None。"""
    global_path = tmp_path / "global.toml"
    global_path.write_text(
        '[search]\nprovider = "kimi_api"\napi_key = "k"\nlimit = 3\n', encoding="utf-8")

    s = load_settings(global_path=global_path)
    assert s.search is not None
    assert s.search.provider == "kimi_api"
    assert s.search.api_key == "k"
    assert s.search.limit == 3
    assert s.search.base_url == "https://api.moonshot.cn/v1"    # 未写的字段取默认
    assert s.search.followup_rounds == 2

    # 段缺省 → None
    assert load_settings(global_path=tmp_path / "none.toml").search is None


def test_config_search_project_overrides(tmp_path):
    """项目级 [search] 整表替换全局表（两级浅合并的既有语义）。"""
    global_path = tmp_path / "global.toml"
    global_path.write_text('[search]\nprovider = "kimi_api"\napi_key = "gk"\n', encoding="utf-8")
    project_path = tmp_path / "project.toml"
    project_path.write_text('[search]\nprovider = "minimax"\n', encoding="utf-8")

    s = load_settings(global_path=global_path, project_path=project_path)
    assert s.search.provider == "minimax"
    assert s.search.api_key == ""      # 整表替换 → 全局表键不再继承


def test_config_search_unknown_field_raises(tmp_path):
    """[search] 段含未知字段 → 响亮失败（防拼写错静默失效）。"""
    path = tmp_path / "bad.toml"
    path.write_text('[search]\napi_ky = "typo"\n', encoding="utf-8")
    with pytest.raises(ValueError):
        load_settings(global_path=path)

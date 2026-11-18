"""嵌入器抽象（P4 Task 2）：none/openai 分派、batch 契约、cosine、单例锁、fastembed 守卫。

消费端约定（控制器裁定）：T1 ``rank_candidates(embedder=None)`` 即纯 BM25 路径；
本文件只钉 OpenAIEmbedder 行为契约（batch 整批一次调用、model 参数传递）。
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import httpx
import pytest

from harness.config import ProviderConfig
from harness.llm.client import OpenAICompatClient
from harness.retrieval import embedder as embedder_mod
from harness.retrieval.bm25 import (
    RetrievalSettings,
    bm25_rank,
    rank_candidates,
    terms_from_texts,
)
from harness.retrieval.embedder import (
    OpenAIEmbedder,
    cosine,
    get_embedder,
    peek_embedder,
)


@pytest.fixture(autouse=True)
def _reset_singleton(monkeypatch):
    """get_embedder 是模块级单例：每个用例前重置，避免用例间泄漏。"""
    monkeypatch.setattr(embedder_mod, "_instance", None)


class _FakeEmbeddings:
    """录下 create 调用的假 AsyncOpenAI.embeddings。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create(self, *, model, input):
        self.calls.append({"model": model, "input": list(input)})
        return SimpleNamespace(
            data=[SimpleNamespace(embedding=[float(i), 1.0]) for i, _ in enumerate(input)]
        )


class _FakeAsyncOpenAI:
    """最小 AsyncOpenAI 替身：只有 embeddings.create。"""

    def __init__(self) -> None:
        self.embeddings = _FakeEmbeddings()


def _make_client() -> OpenAICompatClient:
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={}))
    http = httpx.AsyncClient(transport=transport, base_url="https://api.test")
    return OpenAICompatClient(
        ProviderConfig(base_url="https://api.test", api_key="k",
                       lite="m", standard="std-model", pro="m"),
        http_client=http)


def test_noop_backend_returns_none_embedder():
    """backend='none' → get_embedder 返回 None（调用方纯 BM25）；peek 不触发加载。"""
    assert get_embedder(RetrievalSettings(backend="none"), provider=None) is None
    assert peek_embedder() is None


async def test_openai_embedder_batch():
    """契约：整批 texts 一次调用、model 原样传递、返回与输入等长的向量列表。"""
    fake = _FakeAsyncOpenAI()
    emb = OpenAIEmbedder(fake, "bge-m3")
    vecs = await emb.embed(["甲文", "乙文", "丙文"])
    assert len(fake.embeddings.calls) == 1            # batch 整批一次调用
    assert fake.embeddings.calls[0]["input"] == ["甲文", "乙文", "丙文"]
    assert fake.embeddings.calls[0]["model"] == "bge-m3"
    assert vecs == [[0.0, 1.0], [1.0, 1.0], [2.0, 1.0]]


def test_cosine_known_values():
    assert cosine([1.0, 0.0], [2.0, 0.0]) == pytest.approx(1.0)
    assert cosine([1.0, 0.0], [-3.0, 0.0]) == pytest.approx(-1.0)
    assert cosine([1.0, 0.0], [0.0, 5.0]) == pytest.approx(0.0)


def test_embedder_singleton_locked():
    """两次 get_embedder 返回同一实例；复用 OpenAICompatClient 的 AsyncOpenAI。"""
    client = _make_client()
    settings = RetrievalSettings(backend="openai", embedding_model="bge-m3")
    first = get_embedder(settings, client)
    second = get_embedder(settings, client)
    assert first is second
    assert isinstance(first, OpenAIEmbedder)
    assert first._model == "bge-m3"
    assert first._client is client._client


def test_openai_backend_model_fallback_to_standard_tier():
    """embedding_model 未配置时回退 provider 的 standard 档模型名。"""
    client = _make_client()
    emb = get_embedder(RetrievalSettings(backend="openai", embedding_model=""), client)
    assert emb._model == "std-model"


def test_fastembed_missing_raises_clear_error(monkeypatch):
    """未安装 fastembed 且 backend=fastembed → 报错信息含 pip 安装提示。"""
    monkeypatch.setitem(sys.modules, "fastembed", None)  # 强制 import fastembed 抛 ImportError
    with pytest.raises(RuntimeError, match="pip install fastembed"):
        get_embedder(RetrievalSettings(backend="fastembed"), provider=None)


def test_fastembed_installed_still_backlog():
    """已安装也不实现（控制器裁定：本期只留 import 守卫）——报 NotImplementedError。"""
    pytest.importorskip("fastembed")
    with pytest.raises(NotImplementedError, match="backlog"):
        get_embedder(RetrievalSettings(backend="fastembed"), provider=None)


def _pool():
    return [SimpleNamespace(id=1, text="角色林晚在雨夜出走"),
            SimpleNamespace(id=2, text="菜谱红烧肉的糖色火候"),
            SimpleNamespace(id=3, text="林晚与旧友在车站重逢")]


def test_rank_candidates_with_none_embedder_pure_bm25():
    """T1 消费端约定：embedder=None 时结果与 semantic_enabled=False 的纯 BM25 序一致。"""
    pool = _pool()
    text_of = lambda it: it.text
    terms = terms_from_texts(["林晚 出走"])
    _, scores = bm25_rank(pool, terms, text_of)

    def run(cfg):
        return rank_candidates(pool=pool, scores=scores, query="林晚 出走",
                               text_of=text_of, cfg=cfg, top_k=10, embedder=None)

    picked, ranked = run(RetrievalSettings(semantic_enabled=True))
    picked_off, _ = run(RetrievalSettings(semantic_enabled=False))
    assert [it.id for it in picked] == [it.id for it in picked_off]
    assert all(r.cosine is None for r in ranked)  # 未走 dense 路

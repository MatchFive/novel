"""嵌入器抽象（P4 Task 2）：none/openai 后端分派 + cosine + 单例。

行为对拍 web_dream backend/app/llm/embedder.py（三后端 / cosine / batch 整批 /
单例锁 / peek 不触发加载），差异：

- ``embed`` 是 async——本仓 LLM 客户端是 AsyncOpenAI（seed 是同步 OpenAI）；
  契约不变：**整批一次调用**，单条失败整批 raise（由调用方分类）
- backend="none" 时 ``get_embedder`` 返回 None（调用方纯 BM25），不留 NullEmbedder 占位
- fastembed 本期只留 import 守卫，不实现（backlog）

消费端约定（已钉）：T1 ``rank_candidates(embedder=None)`` 即纯 BM25 路径。
"""
from __future__ import annotations

import math
import threading
from abc import ABC, abstractmethod

from harness.llm.client import OpenAICompatClient
from harness.retrieval.bm25 import RetrievalSettings


class Embedder(ABC):
    """语义嵌入抽象：批量取向量。"""

    enabled: bool = True

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class NoopEmbedder(Embedder):
    """backend="none" 的显式空实现：返回空向量，``enabled=False``（消费端据此跳过 dense）。"""

    enabled = False

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[] for _ in texts]


class OpenAIEmbedder(Embedder):
    """OpenAI 兼容 ``/v1/embeddings``：复用 OpenAICompatClient 的 AsyncOpenAI 实例。

    ``client`` 按 AsyncOpenAI 鸭子类型注入（测试用假实例录请求）；``embed`` 整批一次
    ``embeddings.create`` 调用，``model`` 原样传递，单条失败整批 raise 由调用方分类。
    """

    def __init__(self, client, model: str) -> None:
        self._client = client
        self._model = model
        self.enabled = True

    async def embed(self, texts: list[str]) -> list[list[float]]:
        resp = await self._client.embeddings.create(model=self._model, input=texts)
        return [list(d.embedding) for d in resp.data]


def cosine(a: list[float], b: list[float]) -> float:
    """纯 Python 余弦相似度（seed _cosine:58）。"""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1e-12
    nb = math.sqrt(sum(y * y for y in b)) or 1e-12
    return dot / (na * nb)


_instance: Embedder | None = None
_init_lock = threading.Lock()   # 首次初始化加锁：避免并发触发两次加载


def peek_embedder() -> Embedder | None:
    """**已初始化**的嵌入器；未就绪返回 None（**绝不触发加载**）。"""
    return _instance


def get_embedder(settings: RetrievalSettings,
                 provider: OpenAICompatClient | None) -> Embedder | None:
    """按 ``settings.backend`` 分派的全局嵌入器（单例 + 锁）。

    - ``none``：返回 None——调用方纯 BM25（T1 ``rank_candidates(embedder=None)``）
    - ``openai``：复用 ``provider`` 的 AsyncOpenAI 实例打 /v1/embeddings；模型取
      ``settings.embedding_model``，缺省回退 provider 的 standard 档模型名
    - ``fastembed``：本期只留 import 守卫（未安装 → 响亮报错含 pip 提示），实现是 backlog

    初始化只是内存里建对象（openai 后端无网络/模型加载），不堵事件循环；
    真正的嵌入调用在 ``embed`` 的 await 里。
    """
    global _instance
    if _instance is not None:
        return _instance
    backend = settings.backend
    if backend == "none":
        return None
    with _init_lock:
        if _instance is not None:      # 并发下第二个调用者直接拿结果
            return _instance
        if backend == "fastembed":
            try:
                import fastembed  # noqa: F401
            except ImportError as exc:
                raise RuntimeError(
                    "backend='fastembed' 需要安装 fastembed：pip install fastembed"
                ) from exc
            raise NotImplementedError("fastembed 后端本期未实现（backlog）")
        if backend == "openai":
            if provider is None:
                raise ValueError("backend='openai' 需要 provider（OpenAICompatClient）")
            model = settings.embedding_model or provider.provider.standard
            _instance = OpenAIEmbedder(provider._client, model)
            return _instance
    raise ValueError(f"未知检索嵌入后端：{backend!r}（none / openai / fastembed）")

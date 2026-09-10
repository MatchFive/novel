"""语义嵌入检索服务（上下文组装用）。

两种后端（按配置选择）：
- fastembed（内置，进程内）：ONNX Runtime + 量化模型，无需另开服务、不依赖 PyTorch；
  首次运行自动下载模型（all-MiniLM-L6-v2 ≈ 90MB），之后完全离线；
- HTTP：本地服务的 OpenAI 兼容 /v1/embeddings 接口（LM Studio / Ollama / vLLM），
  用于挂自定义嵌入模型。

配置（[llm.embedding]）：provider="__fastembed__" 用内置后端；provider=厂商名走 HTTP。
未配置但安装了 fastembed 时自动启用内置后端；都不可用则优雅返回 None（回退关键词/FTS）。
"""
from __future__ import annotations

import hashlib
import os
import threading

# HF 直连在国内常超时，默认走镜像站（必须在 huggingface_hub 被任何模块 import 之前设置——
# 它的 ENDPOINT 常量是 import 时定死的；用户已自行配置 HF_ENDPOINT 则不覆盖）
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

from app.core.config import AppConfig
from app.core.logger import get_logger

log = get_logger("services.embedding")

# 参与嵌入的文本截断长度（MiniLM 类模型上下文短，长文本截前段即可）
_EMBED_TEXT_CAP = 512

# 内置 fastembed 后端的默认模型（中文内容建议换成 BAAI/bge-small-zh-v1.5 或
# sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2）
FASTEMBED_PROVIDER = "__fastembed__"
DISABLED_PROVIDER = "__disabled__"  # 显式禁用（区别于「未配置=自动启用内置后端」）
DEFAULT_FASTEMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def _fastembed_available() -> bool:
    try:
        import fastembed  # noqa: F401
        return True
    except ImportError:
        return False


class EmbeddingService:
    def __init__(self, cfg: AppConfig | None):
        self._cfg = cfg
        self._cache: dict[str, list[float]] = {}
        self._fe_model = None           # fastembed 模型（懒加载）
        self._fe_lock = threading.Lock()

    # ---------------- 后端选择 ----------------
    def _backend(self) -> tuple[str, str] | None:
        """返回 (backend, model)；backend ∈ {"fastembed", "http"}。不可用返回 None。

        未显式配置（[llm.embedding] 为空）且安装了 fastembed → 自动启用内置后端；
        cfg=None（测试桩/无配置上下文）→ 一律禁用，避免测试时意外下载模型。
        """
        if self._cfg is None:
            return None
        emb = self._cfg.embedding
        if emb.provider == DISABLED_PROVIDER:
            return None  # 用户显式禁用
        if emb.provider == FASTEMBED_PROVIDER:
            return ("fastembed", emb.model or DEFAULT_FASTEMBED_MODEL)
        if emb.provider and emb.model and emb.provider in self._cfg.providers:
            return ("http", emb.model)
        # 未显式配置：装了 fastembed 就自动用内置后端
        if _fastembed_available():
            return ("fastembed", DEFAULT_FASTEMBED_MODEL)
        return None

    @property
    def enabled(self) -> bool:
        return self._backend() is not None

    # ---------------- 嵌入 ----------------
    def embed(self, texts: list[str]) -> list[list[float]] | None:
        """批量嵌入；失败返回 None（调用方回退）。顺序与输入一一对应。"""
        backend = self._backend()
        if backend is None or not texts:
            return None
        keyed = [t[:_EMBED_TEXT_CAP] for t in texts]
        # 命中缓存的直接取
        out: list[list[float] | None] = [self._cache.get(self._key(t)) for t in keyed]
        missing_idx = [i for i, v in enumerate(out) if v is None]
        if missing_idx:
            kind, model = backend
            if kind == "fastembed":
                fetched = self._embed_fastembed([keyed[i] for i in missing_idx], model)
            else:
                fetched = self._call_api([keyed[i] for i in missing_idx])
            if fetched is None or len(fetched) != len(missing_idx):
                return None
            for i, vec in zip(missing_idx, fetched):
                self._cache[self._key(keyed[i])] = vec
                out[i] = vec
        return [v for v in out if v is not None]

    def _embed_fastembed(self, texts: list[str], model_name: str) -> list[list[float]] | None:
        """fastembed 进程内嵌入（懒加载模型，首次运行会下载模型文件）。"""
        try:
            with self._fe_lock:
                if self._fe_model is None or self._fe_model_name != model_name:
                    from fastembed import TextEmbedding
                    # 模型缓存放到数据目录下（默认 temp 目录会被系统清理）
                    cache_dir = None
                    if self._cfg is not None and self._cfg.data_dir:
                        cache_dir = str(self._cfg.data_dir / "embedding_models")
                    log.info("fastembed 加载模型 %s（首次运行会下载模型文件）……", model_name)
                    self._fe_model = TextEmbedding(model_name=model_name, cache_dir=cache_dir)
                    self._fe_model_name = model_name
            vecs = list(self._fe_model.embed(texts))
            return [list(map(float, v)) for v in vecs]
        except Exception as exc:
            log.warning("fastembed 嵌入失败（%s）：%s", model_name, exc)
            return None

    def _call_api(self, texts: list[str]) -> list[list[float]] | None:
        emb = self._cfg.embedding
        provider = self._cfg.providers.get(emb.provider)
        if provider is None:
            return None
        try:
            import httpx
            url = provider.base_url.rstrip("/") + "/embeddings"
            headers = {"Content-Type": "application/json"}
            if provider.api_key:
                headers["Authorization"] = f"Bearer {provider.api_key}"
            resp = httpx.post(
                url, headers=headers, timeout=30.0,
                json={"model": emb.model, "input": texts},
            )
            resp.raise_for_status()
            data = resp.json()
            items = sorted(data.get("data") or [], key=lambda d: d.get("index", 0))
            return [list(map(float, it.get("embedding") or [])) for it in items]
        except Exception as exc:
            log.warning("嵌入服务不可用（%s/%s）：%s", emb.provider, emb.model, exc)
            return None

    @staticmethod
    def _key(text: str) -> str:
        return hashlib.sha1(text.encode("utf-8")).hexdigest()

    # ---------------- 相似度排序 ----------------
    @staticmethod
    def rank(query_vec: list[float], cand_vecs: list[list[float]],
             top_k: int = 6) -> list[tuple[int, float]]:
        """余弦相似度排序，返回 [(候选索引, 分数)] 降序，取前 top_k。"""
        try:
            import numpy as np
            q = np.asarray(query_vec, dtype=float)
            qn = np.linalg.norm(q)
            if qn == 0:
                return []
            scores = []
            for i, cv in enumerate(cand_vecs):
                c = np.asarray(cv, dtype=float)
                cn = np.linalg.norm(c)
                scores.append((i, float(q @ c / (qn * cn)) if cn else 0.0))
        except ImportError:
            # 无 numpy 的兜底：纯 Python 点积
            import math
            qn = math.sqrt(sum(x * x for x in query_vec))
            if qn == 0:
                return []
            scores = []
            for i, cv in enumerate(cand_vecs):
                cn = math.sqrt(sum(x * x for x in cv))
                dot = sum(a * b for a, b in zip(query_vec, cv))
                scores.append((i, dot / (qn * cn) if cn else 0.0))
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]

    # ---------------- 面向业务的语义检索 ----------------
    def rank_texts(self, query: str, candidates: list[str], top_k: int = 6) -> list[int] | None:
        """对候选文本按与 query 的语义相关度排序，返回候选索引列表（相关度从高到低）。

        嵌入不可用/失败时返回 None（调用方回退关键词/FTS）。
        """
        if not query.strip() or not candidates:
            return None
        vecs = self.embed([query] + candidates)
        if vecs is None or len(vecs) != len(candidates) + 1:
            return None
        ranked = self.rank(vecs[0], vecs[1:], top_k=top_k)
        return [i for i, _score in ranked]

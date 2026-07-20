"""助手历史摘要的 embedding 生成与相似度检索。"""
from __future__ import annotations

import logging

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.llm_client import LLMClient
from app.models import AssistantSummaryEmbedding

logger = logging.getLogger(__name__)


def _normalize(v: np.ndarray) -> np.ndarray:
    """L2 归一化向量；零向量保持为零向量。"""
    norm = float(np.linalg.norm(v))
    if norm == 0:
        return v
    return v / norm


async def store_summary_embedding(
    db: AsyncSession,
    session_id: str,
    turn_range: str,
    summary_text: str,
    client: LLMClient,
    model_name: str,
    dimension: int,
) -> None:
    """为一条历史摘要生成 embedding 并持久化。失败只记录日志，不抛异常。"""
    try:
        vectors = await client.embed(
            [summary_text],
            model=model_name,
            dimensions=dimension if dimension > 0 else None,
        )
        if not vectors:
            return
        arr = np.array(vectors[0], dtype=np.float32)
        normalized = _normalize(arr)
        db.add(AssistantSummaryEmbedding(
            session_id=session_id,
            turn_range=turn_range,
            summary_text=summary_text,
            embedding=normalized.tobytes(),
            model=model_name,
            dimension=int(normalized.shape[0]),
        ))
    except Exception:
        logger.exception("Failed to store summary embedding for session %s range %s", session_id, turn_range)


async def retrieve_similar_summaries(
    db: AsyncSession,
    session_id: str,
    query_vector: list[float],
    top_k: int,
) -> list[dict]:
    """返回与当前输入最相似的 Top-K 条历史摘要。"""
    if top_k <= 0:
        return []

    try:
        res = await db.execute(
            select(AssistantSummaryEmbedding)
            .where(AssistantSummaryEmbedding.session_id == session_id)
        )
        rows = res.scalars().all()
        if not rows:
            return []

        query = _normalize(np.array(query_vector, dtype=np.float32))
        scored: list[tuple[float, AssistantSummaryEmbedding]] = []
        for row in rows:
            arr = np.frombuffer(row.embedding, dtype=np.float32)
            if arr.shape[0] != query.shape[0]:
                continue
            arr = _normalize(arr)
            score = float(np.dot(arr, query))
            scored.append((score, row))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {
                "turn_range": row.turn_range,
                "summary_text": row.summary_text,
            }
            for _, row in scored[:top_k]
        ]
    except Exception:
        logger.exception("Failed to retrieve similar summaries")
        return []

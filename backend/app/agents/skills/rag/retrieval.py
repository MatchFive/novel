"""RAG retrieval for skill chunks using SQLite + numpy brute force."""
from __future__ import annotations

import logging

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.skills.models import SkillQueryResult
from app.core.llm_factory import get_embedding_client
from app.models import SkillRagEmbedding

logger = logging.getLogger(__name__)


def _normalize(v: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(v)
    if norm == 0:
        return v
    return v / norm


async def retrieve_skill_chunks(
    db: AsyncSession,
    skill_names: list[str],
    query: str,
    top_k: int = 3,
) -> list[SkillQueryResult]:
    if not skill_names or not query.strip():
        return []

    embedding_client, dimension = await get_embedding_client(db)
    query_vectors = await embedding_client.embed(
        [query],
        model=embedding_client.model,
        dimensions=dimension if dimension > 0 else None,
    )
    query_vec = _normalize(np.array(query_vectors[0], dtype=np.float32))

    stmt = select(SkillRagEmbedding).where(
        SkillRagEmbedding.skill_name.in_(skill_names)
    )
    res = await db.execute(stmt)
    rows = res.scalars().all()

    scored: list[tuple[SkillRagEmbedding, float]] = []
    for row in rows:
        try:
            emb = np.frombuffer(row.embedding, dtype=np.float32)
            if emb.shape[0] != query_vec.shape[0]:
                continue
            emb = _normalize(emb)
            score = float(np.dot(query_vec, emb))
            scored.append((row, score))
        except Exception:
            logger.exception("Failed to compute similarity for %s", row.chunk_path)
            continue

    scored.sort(key=lambda x: x[1], reverse=True)
    return [
        SkillQueryResult(
            skill_name=row.skill_name,
            chunk_path=row.chunk_path,
            chunk_text=row.chunk_text,
            score=score,
        )
        for row, score in scored[:top_k]
    ]

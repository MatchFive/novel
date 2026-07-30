"""Retrieve relevant ProjectExperience rows for a project."""
from __future__ import annotations

import logging

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ProjectExperience

logger = logging.getLogger(__name__)


def _normalize(v: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(v))
    if norm == 0:
        return v
    return v / norm


async def retrieve_project_experiences(
    db: AsyncSession,
    project_id: str,
    query_vector: list[float],
    top_k: int = 3,
    threshold: float = 0.72,
) -> list[dict]:
    """Return top-k ProjectExperience rows by cosine similarity, filtered by threshold."""
    if top_k <= 0:
        return []
    try:
        res = await db.execute(
            select(ProjectExperience)
            .where(ProjectExperience.project_id == project_id)
        )
        rows = res.scalars().all()
        if not rows:
            return []

        query = _normalize(np.array(query_vector, dtype=np.float32))
        scored: list[tuple[float, ProjectExperience]] = []
        for row in rows:
            if not row.embedding:
                continue
            emb = np.frombuffer(row.embedding, dtype=np.float32)
            if emb.shape[0] != query.shape[0]:
                continue
            emb = _normalize(emb)
            score = float(np.dot(query, emb))
            if score >= threshold:
                scored.append((score, row))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {
                "reflection_text": row.reflection_text,
                "rules": row.rules or [],
                "experience_type": row.experience_type,
                "score": score,
            }
            for score, row in scored[:top_k]
        ]
    except Exception:
        logger.exception("Failed to retrieve project experiences")
        return []

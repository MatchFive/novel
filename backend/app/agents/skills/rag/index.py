"""Build RAG index for skill chunks."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.skills.models import SkillConfig
from app.core.llm_factory import get_embedding_client
from app.database import AsyncSessionLocal
from app.models import SkillRagEmbedding

logger = logging.getLogger(__name__)

SKILLS_DIR = Path(__file__).parent.parent
CONFIG_DIR = SKILLS_DIR / "configs"
CHUNKS_DIR = SKILLS_DIR / "rag" / "chunks"


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from markdown text."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except Exception:
        meta = {}
    return meta, parts[2].strip()


async def _index_skill(db: AsyncSession, cfg: SkillConfig) -> int:
    chunks_path = cfg.chunks_path(SKILLS_DIR)
    if not chunks_path or not chunks_path.exists():
        return 0

    embedding_client, dimension = await get_embedding_client(db)

    # Remove existing embeddings for this skill
    await db.execute(
        delete(SkillRagEmbedding).where(
            SkillRagEmbedding.skill_name == cfg.skill_name
        )
    )
    await db.commit()

    indexed = 0
    chunk_files = sorted(chunks_path.rglob("*.md"))
    texts: list[str] = []
    metas: list[dict[str, Any]] = []
    paths: list[str] = []

    for path in chunk_files:
        text = path.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(text)
        if not body.strip():
            continue
        rel_path = str(path.relative_to(SKILLS_DIR))
        texts.append(body)
        metas.append(meta)
        paths.append(rel_path)

    if not texts:
        return 0

    # Embed in batches to avoid huge payloads
    batch_size = 16
    embeddings: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        vecs = await embedding_client.embed(
            batch,
            model=embedding_client.model,
            dimensions=dimension if dimension > 0 else None,
        )
        embeddings.extend(vecs)

    for meta, body, rel_path, vec in zip(metas, texts, paths, embeddings):
        arr = np.array(vec, dtype=np.float32)
        norm = np.linalg.norm(arr)
        if norm > 0:
            arr = arr / norm
        db.add(
            SkillRagEmbedding(
                skill_name=cfg.skill_name,
                chunk_path=rel_path,
                chunk_text=body[:4000],
                embedding=arr.tobytes(),
                model=embedding_client.model,
                dimension=dimension,
            )
        )
        indexed += 1

    await db.commit()
    return indexed


async def build_rag_index() -> dict[str, int]:
    results: dict[str, int] = {}
    async with AsyncSessionLocal() as db:
        for path in sorted(CONFIG_DIR.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                cfg = SkillConfig(**data)
                if cfg.type != "rag":
                    continue
                count = await _index_skill(db, cfg)
                results[cfg.skill_name] = count
                logger.info("Indexed %d chunks for skill %s", count, cfg.skill_name)
            except Exception:
                logger.exception("Failed to index skill config %s", path.name)
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    counts = asyncio.run(build_rag_index())
    for name, count in counts.items():
        print(f"{name}: {count} chunks")

"""SkillManager: load skill configs, inject inline skills, query RAG chunks."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.skills.models import SkillConfig, SkillQueryResult

logger = logging.getLogger(__name__)

SKILLS_DIR = Path(__file__).parent
CONFIG_DIR = SKILLS_DIR / "configs"


class SkillManager:
    _instance: "SkillManager | None" = None

    def __new__(cls) -> "SkillManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._skills: dict[str, SkillConfig] = {}
        self._inline_cache: dict[str, str] = {}
        self._initialized = True
        self._load_all()

    def _load_all(self) -> None:
        if not CONFIG_DIR.exists():
            return
        for path in sorted(CONFIG_DIR.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                config = SkillConfig(**data)
                self._skills[config.skill_name] = config
                if config.type == "inline" and config.content_path(SKILLS_DIR):
                    content_path = config.content_path(SKILLS_DIR)
                    if content_path and content_path.exists():
                        self._inline_cache[config.skill_name] = content_path.read_text(
                            encoding="utf-8"
                        )
            except Exception:
                logger.exception("Failed to load skill config %s", path.name)

    def list_skills(self) -> list[SkillConfig]:
        return list(self._skills.values())

    def get_skill(self, skill_name: str) -> SkillConfig | None:
        return self._skills.get(skill_name)

    def get_skills_for_worker(
        self,
        worker_name: str,
        worker_skills: list[str] | None = None,
        task_goal: str = "",
    ) -> list[tuple[SkillConfig, str]]:
        """Return (config, content) for inline skills applicable to a worker."""
        selected: set[str] = set(worker_skills or [])
        if not selected:
            for cfg in self._skills.values():
                if cfg.type == "inline" and worker_name in cfg.triggers:
                    selected.add(cfg.skill_name)

        results: list[tuple[SkillConfig, str]] = []
        for name in selected:
            cfg = self._skills.get(name)
            if not cfg or cfg.type != "inline":
                continue
            content = self._inline_cache.get(name)
            if content:
                results.append((cfg, content))

        results.sort(key=lambda item: (item[0].priority, item[0].skill_name))
        return results

    async def query_rag_skills(
        self,
        db: AsyncSession,
        worker_name: str,
        rag_skill_names: list[str] | None = None,
        query: str = "",
        top_k: int | None = None,
    ) -> list[SkillQueryResult]:
        """Retrieve top-k RAG chunks for the configured RAG skills."""
        selected: set[str] = set(rag_skill_names or [])
        if not selected:
            for cfg in self._skills.values():
                if cfg.type == "rag" and worker_name in cfg.triggers:
                    selected.add(cfg.skill_name)

        if not selected or not query.strip():
            return []

        all_results: list[SkillQueryResult] = []
        for name in selected:
            cfg = self._skills.get(name)
            if not cfg or cfg.type != "rag":
                continue
            k = top_k if top_k is not None else cfg.top_k
            try:
                from app.agents.skills.rag.retrieval import retrieve_skill_chunks
                chunks = await retrieve_skill_chunks(
                    db,
                    skill_names=[name],
                    query=query,
                    top_k=k,
                )
                all_results.extend(chunks)
            except Exception:
                logger.exception("RAG retrieval failed for skill %s", name)

        all_results.sort(key=lambda r: r.score, reverse=True)
        # Return top_k overall if a single top_k was requested
        if top_k is not None:
            return all_results[:top_k]
        return all_results


def get_skill_manager() -> SkillManager:
    return SkillManager()

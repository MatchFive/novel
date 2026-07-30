"""Skills package."""
from __future__ import annotations

from app.agents.skills.models import SkillConfig, SkillQueryResult
from app.agents.skills.skill_manager import SkillManager, get_skill_manager

__all__ = [
    "SkillConfig",
    "SkillManager",
    "SkillQueryResult",
    "get_skill_manager",
]

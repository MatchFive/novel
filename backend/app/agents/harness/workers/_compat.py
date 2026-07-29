from __future__ import annotations

from typing import Any


def task_goal(task) -> str:
    """Return the goal from a Task object or a plain string.

    Supports legacy callers that pass the goal string directly.
    """
    if hasattr(task, "goal"):
        return str(task.goal)
    return str(task)


def context_project_id(context) -> str | None:
    """Return project_id from a HarnessContext or a legacy dict."""
    if hasattr(context, "project_id"):
        return context.project_id
    if isinstance(context, dict):
        return context.get("project_id")
    return None


def context_project_summary(context) -> str:
    """Return project_summary from a HarnessContext or a legacy dict."""
    if hasattr(context, "project_summary"):
        return context.project_summary or ""
    if isinstance(context, dict):
        return context.get("project_summary") or ""
    return ""


def context_entity_list(context, entity_type: str) -> list[dict]:
    """Return an entity list from a HarnessContext or a legacy dict."""
    if hasattr(context, "entity_list"):
        return context.entity_list(entity_type)
    if isinstance(context, dict):
        return context.get(entity_type) or []
    return []


def context_session_get(context, key: str, default: Any = None) -> Any:
    """Return a value from session_context (HarnessContext) or a legacy dict."""
    if hasattr(context, "session_context"):
        return context.session_context.get(key, default)
    if isinstance(context, dict):
        return context.get(key, default)
    return default

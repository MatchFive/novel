from __future__ import annotations

import logging

from app.agents.workflows.executor import run_workflow
from app.agents.workflows.models import (
    WorkflowContext,
    WorkflowDefinition,
    WorkflowRunResult,
    WorkflowStep,
)
from app.agents.workflows.registry import (
    list_workflows,
    load_workflow_definition,
    register_step,
)

logger = logging.getLogger(__name__)

# Register built-in workflow step functions on import. These modules are added
# incrementally by later tasks; missing modules should not break import.
_BUILT_IN_STEP_MODULES = (
    "chapter_generation",
    "foreshadow_audit",
    "memory_update",
    "world_consistency",
)

for _module_name in _BUILT_IN_STEP_MODULES:
    try:
        __import__(f"app.agents.workflows.{_module_name}")
    except ImportError:
        logger.debug("Built-in workflow step module %r is not available yet", _module_name)

from __future__ import annotations

import logging

from app import repositories as repo
from app.agents.harness.models import HarnessContext
from app.agents.harness.worker_base import WorkerBase
from app.agents.harness.workers._compat import context_project_id, task_goal
from app.agents.harness.workers._chapter_utils import find_target_chapter
from app.agents.workflows.executor import run_workflow
from app.agents.workflows.models import WorkflowContext
from app.agents.workflows.registry import load_workflow_definition

logger = logging.getLogger(__name__)


class ChapterTextWorker(WorkerBase):
    worker_name = "chapter_text"

    async def run(self, task, context, history_context=None) -> dict:
        goal = task_goal(task)
        project_id = context_project_id(context)
        if not project_id:
            return {"changes": [], "stage": "chapter_text", "error": "缺少 project_id"}

        chapters = await repo.list_chapters(self.db, project_id)
        chapter = find_target_chapter(goal, context, chapters)
        if not chapter:
            return {"changes": [], "stage": "chapter_text", "error": "未找到目标章节"}

        async def llm_factory(level: str | None = None):
            return self.llm

        ctx = WorkflowContext(
            db=self.db,
            llm_factory=llm_factory,
            project_id=project_id,
            inputs={"chapter_id": chapter.get("id")},
        )
        result = await run_workflow(load_workflow_definition("chapter_generation"), ctx)

        if result.status == "failed":
            return {
                "changes": [],
                "stage": "chapter_text",
                "error": "; ".join(result.messages),
            }

        notes: list[str] = []
        for key in ("generate_segments", "consistency_review", "rating_check"):
            notes.extend(result.outputs.get(key, {}).get("notes", []))

        return self._normalize_result({
            "changes": result.change_records,
            "stage": "chapter_text",
            "notes": notes,
        })

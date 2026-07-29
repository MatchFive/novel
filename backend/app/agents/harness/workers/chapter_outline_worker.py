"""章节细纲生成 Worker。"""
from __future__ import annotations

import logging

from app import repositories as repo
from app.agents.harness.context_builder import ContextBuilder, build_entities_from_context
from app.agents.harness.prompts.chapter_generation import chapter_outline_prompt
from app.agents.harness.worker_base import WorkerBase
from app.agents.harness.workers._chapter_utils import (
    active_foreshadows,
    assigned_plot_nodes,
    broad_outline_text,
    generate_json,
    generation_settings,
    parse_chapter_numbers,
    previous_chapter_summary,
    user_prompt,
    volume_outline_text,
)
from app.agents.harness.workers._compat import (
    context_entity_list,
    context_project_id,
    context_session_get,
    task_goal,
)

logger = logging.getLogger(__name__)


class ChapterOutlineWorker(WorkerBase):
    worker_name = "chapter_outline"

    async def run(self, task, context, history_context=None) -> dict:
        goal = task_goal(task)
        project_id = context_project_id(context)
        if not project_id:
            return {"changes": [], "stage": "chapter_outline", "error": "缺少 project_id"}

        chapters = await repo.list_chapters(self.db, project_id)
        orders = parse_chapter_numbers(goal)

        # 如果没有解析出序号，但上下文携带了 entity_id，则取该章节的 order
        if orders is None and context_session_get(context, "entity_type") == "chapter":
            entity_id = context_session_get(context, "entity_id")
            target = next(
                (c for c in chapters if c.get("id") == entity_id),
                None,
            )
            if target is not None:
                orders = [target.get("order", 0) + 1]

        if not orders:
            return {
                "changes": [],
                "stage": "chapter_outline",
                "error": "无法识别目标章节，请使用“生成第 X 章细纲”或“生成前三章细纲”",
            }

        outlines = await repo.list_outlines(self.db, project_id)
        plot_nodes = await repo.list_plot(self.db, project_id)
        characters = context_entity_list(context, "characters")
        world = context_entity_list(context, "world")
        foreshadows = context_entity_list(context, "foreshadows")

        related = ""
        entities = build_entities_from_context(context.entities) if hasattr(context, "entities") else context
        builder = ContextBuilder(self.db, self.llm, entities=entities)
        try:
            related = await builder.build(goal, focus_entity_type="chapter")
        except Exception:
            logger.exception("ContextBuilder failed for chapter_outline")

        target_words, _rating = await generation_settings(self.db)

        all_changes: list[dict] = []
        previous_chapter: dict | None = None

        for chapter_num in sorted(orders):
            chapter_order = chapter_num - 1
            existing = next(
                (c for c in chapters if c.get("order") == chapter_order),
                None,
            )
            chapter_for_prompt = existing or {
                "id": None,
                "project_id": project_id,
                "order": chapter_order,
                "title": f"第 {chapter_num} 章",
                "content": "",
                "detailed_outline": "",
                "status": "draft",
            }

            volume_outline = volume_outline_text(outlines, chapter_order)
            prompt_context = {
                "chapter": chapter_for_prompt,
                "broad_outline": broad_outline_text(outlines),
                "volume_outline": volume_outline,
                "assigned_plot_nodes": assigned_plot_nodes(plot_nodes, chapter_for_prompt.get("id")),
                "characters": characters,
                "world": world,
                "previous_chapter_summary": previous_chapter_summary(previous_chapter),
                "active_foreshadows": active_foreshadows(foreshadows),
                "target_words": target_words,
            }
            system = chapter_outline_prompt(prompt_context)
            user_prompt_text = user_prompt(
                f"生成第 {chapter_num} 章细纲",
                related,
            )
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt_text},
            ]
            if history_context:
                messages = history_context + messages

            result = await generate_json(self.llm, messages)
            raw_changes: list[dict] = []
            if isinstance(result, dict):
                raw_changes = result.get("changes") or []
            elif isinstance(result, list):
                raw_changes = result

            for raw in raw_changes:
                fields = dict(raw.get("fields") or {})
                if not fields.get("detailed_outline"):
                    continue
                if not fields.get("title"):
                    fields["title"] = chapter_for_prompt.get("title") or f"第 {chapter_num} 章"
                else:
                    # 强制章节名控制在 10 个字以内
                    t = str(fields["title"])
                    if len(t) > 10:
                        fields["title"] = t[:10]
                fields["order"] = chapter_order
                fields["status"] = "reviewed"
                if existing:
                    all_changes.append({
                        "action": "update",
                        "entity_id": existing["id"],
                        "fields": fields,
                    })
                else:
                    # 新增章节：去掉 worker 可能带上的 id/project_id，由 change_apply 写入
                    fields.pop("id", None)
                    fields.pop("project_id", None)
                    all_changes.append({
                        "action": "add",
                        "entity_id": None,
                        "fields": fields,
                    })

            # 下一章的“前文”基于本章（按顺序）
            previous_chapter = {
                **chapter_for_prompt,
                "detailed_outline": raw_changes[0]["fields"]["detailed_outline"]
                if raw_changes and raw_changes[0].get("fields", {}).get("detailed_outline")
                else "",
            }

        return self._normalize_result({"changes": all_changes, "stage": "chapter_outline"})

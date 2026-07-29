"""Supervisor node: build ExecutionPlan from user input and worker metadata."""
from __future__ import annotations

import logging
import uuid

from app.agents.harness.models import ExecutionPlan, HarnessStage, HarnessState, Task
from app.agents.harness.worker_manager import WorkerManager
from app.core.llm_client import LLMClient

logger = logging.getLogger(__name__)


def _build_supervisor_prompt(worker_metadata: list, context_note: str = "") -> str:
    worker_descriptions = []
    for meta in worker_metadata:
        worker_descriptions.append(
            f"- {meta.worker_name}: {meta.description}\n"
            f"  可用工具: {', '.join(meta.tools)}\n"
            f"  输出格式: {meta.output_schema}"
        )

    return (
        "你是小说创作助手的调度器。根据用户指令与项目现有数据，判断需要派发哪些专精 Worker 来处理。\n\n"
        "可选 Worker 及其能力：\n" + "\n".join(worker_descriptions) + "\n\n"
        "请返回 JSON，格式如下：\n"
        '{"intent": "一句话意图", "tasks": [{"id": "t1", "worker": "character", "goal": "...", '
        '"deps": [], "input_artifacts": {}, "output_artifacts": []}, ...]}\n\n'
        "规则：\n"
        "1. 复合意图必须拆分为多个 task，每个 task 只派给一个对应 worker。\n"
        "2. 独立任务使用相同的 deps 列表（通常为 []），这样它们会被并行执行。\n"
        "3. 若下游 task 需要上游 task 的产物，用 output_artifacts/input_artifacts 显式引用。\n"
        "4. task 的 id 必须唯一。\n"
        "5. 若指令与长篇数据无关，返回 tasks 为空数组。\n"
        f"{context_note}\n\n"
        "只输出 JSON，不要 markdown 代码块，不要解释。"
    )


async def supervisor(state: HarnessState, llm: LLMClient, manager: WorkerManager) -> HarnessState:
    if not state.project_id:
        # Global chat: no plan
        state.plan = ExecutionPlan(intent="通用对话", tasks=[])
        state.stage = HarnessStage.EXECUTE
        return state

    metadata = manager.list_workers()
    context_note = ""
    if state.context.session_context:
        context_note = f"\n\n当前会话上下文：{state.context.session_context}"

    prompt = _build_supervisor_prompt(metadata, context_note)
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": state.user_input},
    ]

    plan = await _parse_plan(llm, messages)
    if not plan:
        # Fallback: single sequential task using outline worker
        plan = ExecutionPlan(
            intent=state.user_input[:50],
            tasks=[Task(id=_new_task_id(), worker="outline", goal=state.user_input)],
        )

    state.plan = plan
    state.stage = HarnessStage.EXECUTE
    return state


async def _parse_plan(llm: LLMClient, messages: list[dict]) -> ExecutionPlan | None:
    try:
        raw = await llm.parse_llm_json(messages)
        if not isinstance(raw, dict):
            return None
        tasks = raw.get("tasks") or []
        validated_tasks = []
        for t in tasks:
            if not isinstance(t, dict):
                continue
            validated_tasks.append(Task(
                id=t.get("id") or _new_task_id(),
                worker=t.get("worker", "outline"),
                goal=t.get("goal", ""),
                deps=t.get("deps") or [],
                input_artifacts=t.get("input_artifacts") or {},
                output_artifacts=t.get("output_artifacts") or [],
                meta=t.get("meta") or {},
            ))
        return ExecutionPlan(
            intent=raw.get("intent", ""),
            tasks=validated_tasks,
            global_context=raw.get("global_context", {}),
        )
    except Exception:
        logger.exception("Supervisor plan parsing failed")
    return None


def _new_task_id() -> str:
    return f"task_{uuid.uuid4().hex[:8]}"

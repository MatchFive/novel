"""Agent 助手接口：chat（分析→派发→变更）、confirm（应用）、reject、undo。"""
from __future__ import annotations

import json
import logging
import re

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, NotFoundError, ValidationError
from app.core.llm_factory import get_llm_client, get_embedding_client
from app.database import get_db
from app.models import AssistantSession, AssistantMessage, LongChangeRecord, Project, UserSetting
from app.agents.harness.history import (
    build_history_context,
    should_summarize,
    summarize_messages,
    append_summary,
)
from app.agents.harness.retrieval import (
    retrieve_similar_summaries,
    store_summary_embedding,
)
from app.agents.harness.nodes.supervisor import run_supervisor
from app.agents.harness.workers import (
    CharacterWorker, WorldWorker, OutlineWorker, PlotWorker, ForeshadowWorker,
    OutlineSplitWorker, BroadOutlineWorker, PlotNodesWorker, AssignmentWorker, ChapterOutlineWorker, ChapterTextWorker,
)
from app.agents.harness.worker_base import run_worker
from app.agents.harness.nodes.aggregator import aggregate, _WORKER_ENTITY
from app.agents.harness.nodes.responder import respond, GLOBAL_RESPONDER_PROMPT
from app.services.change_apply import _ENTITY_REPO, apply_change, confirm_session, reject_session

logger = logging.getLogger(__name__)
router = APIRouter(tags=["assistant"])

_WORKERS = {
    "character": CharacterWorker,
    "world": WorldWorker,
    "outline": OutlineWorker,
    "plot": PlotWorker,
    "foreshadow": ForeshadowWorker,
    "outline_split": OutlineSplitWorker,
    "broad_outline": BroadOutlineWorker,
    "plot_nodes": PlotNodesWorker,
    "assignment": AssignmentWorker,
    "chapter_outline": ChapterOutlineWorker,
    "chapter_text": ChapterTextWorker,
}


_WORKER_LEVEL = {
    "broad_outline": "high",
    "chapter_text": "high",
    "plot_nodes": "medium",
    "assignment": "medium",
    "chapter_outline": "medium",
}


async def _get_active_session(db, project_id) -> AssistantSession:
    res = await db.execute(
        select(AssistantSession)
        .where(AssistantSession.project_id == project_id, AssistantSession.is_active == True)  # noqa: E712
    )
    s = res.scalars().first()
    if not s:
        s = AssistantSession(
            project_id=project_id,
            title="对话 1",
            is_active=True,
            staged_changes=[],
            summaries=[],
            message_count=0,
        )
        db.add(s)
        await db.commit()
        await db.refresh(s)
    return s


async def _recursive_limit(db) -> int:
    res = await db.execute(select(UserSetting))
    s = res.scalars().first()
    return s.recursive_limit if s else 8


def _decode_project_id(project_id: str | None) -> str | None:
    """把前端 sentinel 'global' 转成 None，表示全局会话。"""
    if project_id is None or project_id == "global":
        return None
    return project_id


def _detect_compound_intent(user_input: str) -> dict | None:
    """对常见复合意图做规则化兜底，避免 supervisor LLM 漏拆任务。"""
    text = user_input.lower()
    has_character = any(kw in text for kw in ("角色", "人物", "主角", "配角", "龙套", "npc", "性格", "能力", "关系", "命运"))
    has_world = any(kw in text for kw in ("世界观", "设定", "规则", "体系", "境界", "力量", "信仰", "神格"))
    has_outline = any(kw in text for kw in ("大纲", "总纲", "章节结构", "主线", "支线", "起承转合"))
    has_plot = any(kw in text for kw in ("剧情", "桥段", "事件", "情节"))

    tasks: list[dict] = []
    if has_character:
        tasks.append({"worker": "character", "goal": "为项目新增或调整角色（姓名、性格、能力、关系、地位等），不要修改大纲"})
    if has_world:
        tasks.append({"worker": "world", "goal": "为项目新增或调整世界观设定"})
    if has_plot:
        tasks.append({"worker": "plot", "goal": "为项目编排剧情节点/桥段/事件"})
    if has_outline:
        tasks.append({"worker": "outline", "goal": "根据用户指令完善或更新现有大纲，只修改大纲内容"})

    if len(tasks) > 1:
        return {"intent": user_input, "tasks": tasks}
    return None


def _detect_chapter_generation_intent(user_input: str) -> dict | None:
    """对'生成第 X 章/前三章/第 X-Y 章 细纲/正文'类指令做精确路由，避免 supervisor 错派给 outline。"""
    text = user_input.lower()

    # 范围/批量：前三章、第1章到第3章
    range_match = re.search(r"第\s*(\d+)\s*章\s*(?:到|至)\s*第\s*(\d+)\s*章", user_input)
    prefix_match = re.search(r"前\s*(\d+)\s*章", user_input)
    single_match = re.search(r"第\s*(\d+)\s*章", user_input)

    chapter_nums: list[int] | None = None
    label = ""
    if range_match:
        start, end = int(range_match.group(1)), int(range_match.group(2))
        chapter_nums = list(range(start, end + 1))
        label = f"第 {start} 章到第 {end} 章"
    elif prefix_match:
        n = int(prefix_match.group(1))
        chapter_nums = list(range(1, n + 1))
        label = f"前 {n} 章"
    elif single_match:
        chapter_nums = [int(single_match.group(1))]
        label = f"第 {chapter_nums[0]} 章"

    if not chapter_nums:
        return None

    has_outline = "细纲" in text or "章节大纲" in text
    has_text = "正文" in text or "写" in text
    if has_outline or not has_text:
        goal = f"生成{label}细纲，严格遵循项目总纲、卷大纲与已有细纲；不存在的章节请先创建再写入细纲"
        return {"intent": goal, "tasks": [{"worker": "chapter_outline", "goal": goal}]}
    goal = f"生成{label}正文，严格遵循项目总纲、卷大纲与细纲"
    return {"intent": goal, "tasks": [{"worker": "chapter_text", "goal": goal}]}


# context 中实体键与 change entity_type 的映射
_CONTEXT_ENTITY_KEYS = {
    "character": "characters",
    "outline": "outlines",
    "plot": "plot",
    "foreshadow": "foreshadows",
    "world": "world",
    "chapter": "chapters",
}

_CHAPTER_AUTO_FIELDS = {"content", "detailed_outline", "status"}


def _is_chapter_auto_apply(record) -> bool:
    """章节正文/细纲的 update 变更直接落库，不进待确认列表。"""
    keys = set((record.after or {}).keys())
    return (
        record.entity_type == "chapter"
        and record.action == "update"
        and bool(record.entity_id)
        and keys <= _CHAPTER_AUTO_FIELDS
        and bool(keys & {"content", "detailed_outline"})
    )


def _apply_changes_to_context(context: dict, changes: list[dict], default_worker: str | None = None) -> None:
    """把 worker 产出的 changes 临时合并进 context，供后续顺序执行的 worker 参考。

    若 change 本身没有 entity_type，则按产出它的 worker 类型推断（与 aggregator 逻辑一致）。
    """
    for ch in changes:
        if not isinstance(ch, dict):
            continue
        action = ch.get("action")
        entity_type = ch.get("entity_type")
        if entity_type is None and default_worker:
            entity_type = _WORKER_ENTITY.get(default_worker, default_worker)
        entity_id = ch.get("entity_id")
        fields = ch.get("fields") or {}
        context_key = _CONTEXT_ENTITY_KEYS.get(entity_type)
        if not context_key:
            continue

        entities = context.get(context_key)
        if entities is None:
            context[context_key] = []
            entities = context[context_key]

        if action == "update" and entity_id:
            for e in entities:
                if e.get("id") == entity_id:
                    e.update(fields)
                    break
        elif action == "add":
            new_entity = dict(fields)
            if entity_id:
                new_entity["id"] = entity_id
            else:
                # 临时 id，避免后续 worker 引用时冲突
                new_entity["id"] = f"pending_{entity_type}_{len(entities)}"
            entities.append(new_entity)


@router.post("/chat")
async def chat(body: dict, db: AsyncSession = Depends(get_db)):
    project_id = body.get("project_id")
    user_input = body.get("message", "")
    context_payload = body.get("context") or {}

    if not user_input:
        raise ValidationError("message 必填")

    # Resolve project_id: explicit body value takes precedence, then context.project_id
    effective_project_id = project_id or context_payload.get("project_id")
    is_global = not effective_project_id

    if not is_global:
        proj = await db.get(Project, effective_project_id)
        if not proj:
            raise NotFoundError("项目不存在")

    sess = await _get_active_session(db, effective_project_id)
    # Merge context into session context
    sess.context = {**(sess.context or {}), **context_payload}

    try:
        user_msg = AssistantMessage(
            session_id=sess.id,
            role="user",
            content=user_input,
            metadata_={"context": context_payload},
        )
        db.add(user_msg)
        await db.flush()
    except Exception:
        logger.exception("Failed to persist user assistant message")
        await db.rollback()
        raise AppError(
            "无法保存用户消息，请稍后重试",
            code="PERSIST_FAILED",
            status_code=500,
        )

    recursive_limit = await _recursive_limit(db)

    settings_row = (await db.execute(select(UserSetting))).scalars().first()
    settings_obj = settings_row or UserSetting()

    # 加载当前会话全部消息 ID，切分「最近 N 条」与「更早消息」
    ids_res = await db.execute(
        select(AssistantMessage.id)
        .where(AssistantMessage.session_id == sess.id)
        .order_by(AssistantMessage.created_at.asc())
    )
    message_ids = [row[0] for row in ids_res.all()]
    current_idx = len(message_ids) - 1  # 即刚保存的用户消息
    recent_n = max(1, settings_obj.assistant_history_recent_messages or 20)
    top_k = max(0, settings_obj.assistant_history_top_k or 5)
    recent_ids = message_ids[max(0, current_idx - recent_n):current_idx]

    recent_res = await db.execute(
        select(AssistantMessage)
        .where(AssistantMessage.id.in_(recent_ids))
        .order_by(AssistantMessage.created_at.asc())
    )
    recent_messages = recent_res.scalars().all()

    # 检索与当前输入相似的历史摘要（基于摘要 embedding）；失败降级为仅用最近消息
    retrieved_summaries: list[dict] = []
    try:
        embedding_client, dimension = await get_embedding_client(db)
        query_vectors = await embedding_client.embed(
            [user_input],
            model=embedding_client.model,
            dimensions=dimension if dimension > 0 else None,
        )
        query_vector = query_vectors[0]
        if top_k > 0:
            retrieved_summaries = await retrieve_similar_summaries(
                db,
                sess.id,
                query_vector,
                top_k,
            )
    except Exception:
        logger.exception("Summary embedding retrieval failed, falling back to recent messages only")

    # 历史上下文（历史摘要 + 相似检索摘要 + 最近消息），不含 system 与当前用户输入
    history_context = build_history_context(sess, recent_messages, retrieved_summaries, settings_obj)

    if is_global:
        # 全局会话：不读取项目实体，supervisor 返回空 tasks，responder 直接回答
        context = {"project_id": None}
        plan = {"intent": "通用对话", "tasks": []}
        worker_results = []
    else:
        # 1. 前置取数
        from app import repositories as repo
        project = await db.get(Project, effective_project_id)
        project_summary = f"{project.title}\n{project.description}".strip() if project else ""
        context = {
            "project_id": effective_project_id,
            "project": project.to_dict() if project else None,
            "project_summary": project_summary,
            "outlines": await repo.list_outlines(db, effective_project_id),
            "characters": await repo.list_characters(db, effective_project_id),
            "foreshadows": await repo.list_foreshadows(db, effective_project_id),
            "world": await repo.list_world(db, effective_project_id),
            "plot": await repo.list_plot(db, effective_project_id),
            "chapters": await repo.list_chapters(db, effective_project_id),
            "entity_id": context_payload.get("entity_id"),
            "entity_type": context_payload.get("entity_type"),
        }

        context_note = ""
        if sess.context:
            context_note = f"\n\n当前会话上下文：{json.dumps(sess.context, ensure_ascii=False)}"

        # 2. 章节生成类指令做精确规则路由，避免 supervisor LLM  fallback 到 outline
        chapter_plan = _detect_chapter_generation_intent(user_input)
        if chapter_plan is not None:
            logger.warning("Assistant chapter generation rule plan: %s", chapter_plan)
            plan = chapter_plan
        else:
            # 2. Supervisor 拆分（使用不含当前用户输入的 messages，当前输入单独传入）
            supervisor_prompt = (
                "你是小说创作助手的调度器。根据用户指令与项目现有数据，判断需要派发哪些专精 Worker 来处理。"
                "可选 Worker：character（角色设计/调整）、world（世界观设定）、outline（大纲生成/调整）、"
                "plot（剧情节点编排）、foreshadow（伏笔埋设/回收）、outline_split（拆分已有大纲为时期/卷）、"
                "broad_outline（项目级总纲生成/更新）、plot_nodes（从总纲抽取关键剧情节点）、"
                "assignment（把剧情节点分配到已有/新建章节）、chapter_outline（生成单个章节细纲）、"
                "chapter_text（生成单个章节正文）。请返回 JSON："
                '{"intent": "一句话意图", "tasks": [{"worker": "character", "goal": "..."}, ...]}。\n\n'
                "Worker 选择规则（严格按内容归类，禁止把世界观/规则类指令派给 outline）：\n"
                "- world（世界观）：只要用户在新增/修改世界观、设定、力量体系、修炼境界、社会规则、"
                "历史背景、地理环境、种族、宗教、神话、盟约/契约/法则/禁令，或出现'世界观''设定''规则''体系'等词，"
                "就必须派给 world。此类指令绝不能派给 outline。\n"
                "- character：用户明确提到角色、人物、主角、配角、龙套、NPC、性格、能力、关系、命运。"
                "配角/NPC/龙套都必须由 character worker 创建，绝不能由 outline worker 创建。\n"
                "- outline：仅当用户明确提到传统大纲、章节结构、起承转合、主线/支线安排，或说'完善大纲/调整大纲/更新大纲'时才派给 outline。"
                "outline worker 只修改大纲，不能创建或修改角色、世界观。\n"
                "- outline_split：用户说“拆分大纲/把这条大纲拆成几卷/拆成时期”等，且上下文提供了 entity_id 时，派给 outline_split。\n"
                "- broad_outline：用户说“生成/重新生成总纲/项目大纲/整体大纲”时派给 broad_outline。\n"
                "- plot_nodes：用户说“生成剧情节点/桥段/关键事件”时派给 plot_nodes。\n"
                "- assignment：用户说“分配章节/把剧情节点分配到章节/把桥段分配到章节”时派给 assignment。\n"
                "- chapter_outline：用户说“生成细纲/章节大纲/写第 X 章细纲”时派给 chapter_outline。\n"
                "- chapter_text：用户说“生成正文/写第 X 章/写正文”时派给 chapter_text。\n"
                "- plot：用户提到具体剧情、事件、桥段、时间线节点但不涉及分配时。\n"
                "- foreshadow：用户提到伏笔、悬念、回收/呼应。\n\n"
                "复合意图处理（必须遵守）：若用户一条指令里同时涉及多个实体类型，必须拆分为多个 task，每个 task 只派给一个对应 worker。"
                "例如同时涉及'角色'和'大纲'，就要分别派发 character 和 outline，不能只用 outline 去创建角色。"
                "tasks 数组的顺序必须按依赖关系排列：先生成/修改前置实体（如角色），后基于这些实体完善下游内容（如大纲）。\n\n"
                "若指令涉及当前上下文中的 entity_type/entity_id，应优先派给对应 worker（如果 project_id 可用）。"
                f"{context_note}\n\n"
                "示例（只输出 JSON，不要解释）：\n"
                '用户：新增世界观设定：这是一个修仙世界，境界分为炼气、筑基。\n'
                '输出：{"intent": "新增修仙世界观", "tasks": [{"worker": "world", "goal": "新增修仙世界观设定，境界划分为炼气、筑基"}]}\n'
                '用户：生成前5章总纲。\n'
                '输出：{"intent": "生成前5章总纲", "tasks": [{"worker": "broad_outline", "goal": "生成前5章总纲"}]}\n'
                '用户：生成剧情节点。\n'
                '输出：{"intent": "生成剧情节点", "tasks": [{"worker": "plot_nodes", "goal": "生成剧情节点"}]}\n'
                '用户：把剧情节点分配到章节。\n'
                '输出：{"intent": "分配剧情节点到章节", "tasks": [{"worker": "assignment", "goal": "把剧情节点分配到章节"}]}\n'
                '用户：生成第1章细纲。\n'
                '输出：{"intent": "生成第1章细纲", "tasks": [{"worker": "chapter_outline", "goal": "生成第1章细纲"}]}\n'
                '用户：写第1章正文。\n'
                '输出：{"intent": "生成第1章正文", "tasks": [{"worker": "chapter_text", "goal": "生成第1章正文"}]}\n'
                '用户：主角性格应该更沉稳。\n'
                '输出：{"intent": "调整主角性格", "tasks": [{"worker": "character", "goal": "调整主角性格，使其更沉稳"}]}\n'
                '用户：把这条大纲拆成几卷。\n'
                '输出：{"intent": "拆分大纲为卷", "tasks": [{"worker": "outline_split", "goal": "把当前大纲条目拆成时期/卷结构"}]}\n'
                '用户：加上一些配角，并完善大纲。\n'
                '输出：{"intent": "新增配角并完善大纲", "tasks": [{"worker": "character", "goal": "为项目新增一批配角，包括姓名、年龄、身份、性格、与主角关系等"}, {"worker": "outline", "goal": "根据新增配角完善现有大纲"}]}\n'
                '用户：加上一些配角，完善大纲。\n'
                '输出：{"intent": "新增配角并完善大纲", "tasks": [{"worker": "character", "goal": "为项目新增一批配角"}, {"worker": "outline", "goal": "根据新增配角完善现有大纲"}]}\n\n'
                "若指令与长篇数据无关，返回 {\"intent\": \"...\", \"tasks\": []}。"
            )
            supervisor_msgs = [
                {"role": "system", "content": supervisor_prompt},
                *history_context,
                {"role": "user", "content": user_input},
            ]
            supervisor_llm = await get_llm_client(db, level="medium")
            plan = await run_supervisor(supervisor_llm, supervisor_msgs)

        # 规则化兜底：复合意图与 supervisor 计划做并集合并，
        # 保留 supervisor 已识别的 worker，只补追加规则命中但缺失的 worker
        compound_plan = _detect_compound_intent(user_input)
        if compound_plan is not None:
            supervisor_tasks = list(plan.get("tasks", []))
            if not supervisor_tasks:
                plan = compound_plan
            else:
                existing_workers = {t.get("worker") for t in supervisor_tasks}
                missing = [t for t in compound_plan["tasks"] if t.get("worker") not in existing_workers]
                if missing:
                    logger.warning(
                        "Supervisor missed compound intent workers %s, merging rule-based tasks into plan",
                        [t.get("worker") for t in missing],
                    )
                    for task in missing:
                        supervisor_tasks.append({
                            **task,
                            "goal": f"{task.get('goal', '')}。用户原始指令：{user_input}",
                        })
                    plan["tasks"] = supervisor_tasks

    logger.warning("Assistant supervisor plan: %s", plan)

    # 3. 派发 Worker（仅通过只读工具取数，产出结构化结果，不落库）
    # 按 supervisor 返回的顺序依次执行；前序 worker 的 changes 会回写 context，
    # 使后序 worker 能看到前面生成的实体（如先创建配角，再完善大纲）。
    worker_results = []
    for task in plan.get("tasks", []):
        wname = task.get("worker")
        wcls = _WORKERS.get(wname)
        if not wcls:
            continue
        goal = task.get("goal", user_input)
        level = _WORKER_LEVEL.get(wname, "medium")
        worker_llm = await get_llm_client(db, level=level)
        result = await run_worker(
            wcls, db, worker_llm, recursive_limit, goal, context,
            history_context=history_context,
        )
        result["worker"] = wname
        logger.warning("Worker %s result: %s", wname, result)
        worker_results.append(result)

        # 把本次产出的 changes 临时合并到 context，供后续 worker 使用
        changes = result.get("changes") or []
        if isinstance(changes, list):
            _apply_changes_to_context(context, changes, default_worker=wname)

    # 4. aggregator -> ChangeRecord[]
    records = aggregate(effective_project_id, worker_results)
    logger.warning("Aggregated change records: %s", [r.model_dump() for r in records])

    # 5. 章节正文/细纲变更直接应用（source="auto"），其余进 staged_changes
    notes_by_stage = {
        res.get("stage"): res.get("notes")
        for res in worker_results
        if res.get("notes")
    }
    auto_applied: list[dict] = []
    staged_records = []
    for r in records:
        if not is_global and _is_chapter_auto_apply(r):
            try:
                before_row = await repo.get_chapter(db, r.entity_id)
                if isinstance(before_row, dict):
                    before = dict(before_row)
                elif before_row is not None:
                    before = {c.name: getattr(before_row, c.name) for c in before_row.__table__.columns}
                else:
                    before = None
                await apply_change(db, effective_project_id, r.model_dump())
            except Exception:
                logger.exception("自动应用章节变更失败，降级为待确认")
                await db.rollback()
                staged_records.append(r)
                continue
            try:
                db.add(LongChangeRecord(
                    project_id=effective_project_id,
                    entity_type="chapter",
                    entity_id=r.entity_id,
                    before=before,
                    after=r.after,
                    status="applied",
                    source="auto",
                ))
                await db.commit()
            except Exception:
                logger.exception("自动应用审计记录写入失败（变更已应用）")
                await db.rollback()
            auto_applied.append({
                "change_id": r.id,
                "entity_id": r.entity_id,
                "entity_type": "chapter",
                "fields": list((r.after or {}).keys()),
                "notes": notes_by_stage.get(r.stage) or [],
            })
        else:
            staged_records.append(r)

    staged = list(sess.staged_changes or [])
    staged.extend([r.model_dump() for r in staged_records])
    sess.staged_changes = staged
    await db.commit()

    # 6. responder 摘要
    responder_llm = await get_llm_client(db, level="low")
    if is_global:
        summary = await respond(
            responder_llm, staged_records, user_input=user_input, history_context=history_context,
            system_prompt=GLOBAL_RESPONDER_PROMPT,
        )
    else:
        summary = await respond(
            responder_llm, staged_records, user_input=user_input, history_context=history_context,
            context=context, worker_results=worker_results,
        )

    if auto_applied:
        chapter_titles = {c.get("id"): c.get("title") for c in (context.get("chapters") or [])} if not is_global else {}
        field_labels = {"content": "正文", "detailed_outline": "细纲", "status": "状态"}
        lines = ["", "---", "**已直接写入：**"]
        for a in auto_applied:
            label = "、".join(field_labels.get(f, f) for f in a["fields"] if f != "status")
            title = chapter_titles.get(a["entity_id"]) or a["entity_id"]
            lines.append(f"- 章节《{title}》的{label}已保存（可撤销）")
            for n in a.get("notes", []):
                lines.append(f"  - {n}")
        summary += "\n".join(lines)

    records_data = [r.model_dump() for r in staged_records]
    assistant_msg_id = None
    try:
        assistant_msg = AssistantMessage(
            session_id=sess.id,
            role="assistant",
            content=summary,
            metadata_={
                "intent": plan.get("intent"),
                "change_record_ids": [r.get("id") for r in records_data],
                "context": context_payload,
                "auto_applied": auto_applied,
            },
        )
        db.add(assistant_msg)
        await db.commit()
        await db.refresh(assistant_msg)
        assistant_msg_id = assistant_msg.id

        # 重新加载完整历史（含刚刚保存的助手回复），用于后续压缩
        hist_res = await db.execute(
            select(AssistantMessage)
            .where(AssistantMessage.session_id == sess.id)
            .order_by(AssistantMessage.created_at.asc())
        )
        reload_messages = hist_res.scalars().all()

        # 7. 触发历史摘要压缩
        sess.message_count += 2
        await db.commit()
        await db.refresh(sess)
        if should_summarize(sess, settings_obj):
            threshold = settings_obj.assistant_summary_threshold or 20
            recent = reload_messages[-(threshold * 2):]
            summary_llm = await get_llm_client(db, level="low")
            summary_text = await summarize_messages(recent, settings_obj, summary_llm)
            append_summary(sess, recent, summary_text, settings_obj)
            await db.commit()
            await db.refresh(sess)

            # 为新生成的摘要生成并持久化 embedding，供后续相似检索
            try:
                embedding_client, dimension = await get_embedding_client(db)
                latest_summary = (sess.summaries or [])[-1]
                await store_summary_embedding(
                    db,
                    sess.id,
                    latest_summary.get("turn_range", ""),
                    latest_summary.get("summary", ""),
                    embedding_client,
                    embedding_client.model,
                    dimension,
                )
                await db.commit()
            except Exception:
                logger.exception("Failed to store summary embedding")
    except Exception:
        logger.exception("Failed to persist assistant message")
        await db.rollback()

    return {
        "ok": True,
        "session_id": sess.id,
        "message_id": assistant_msg_id,
        "intent": plan.get("intent"),
        "change_records": records_data,
        "auto_applied": auto_applied,
        "summary": summary,
    }


@router.get("/session/{project_id}")
async def get_session(project_id: str, db: AsyncSession = Depends(get_db)):
    pid = _decode_project_id(project_id)
    sess = await _get_active_session(db, pid)
    return sess.to_dict()


@router.post("/session/{project_id}")
async def create_session(project_id: str, db: AsyncSession = Depends(get_db)):
    pid = _decode_project_id(project_id)
    if pid is not None:
        proj = await db.get(Project, pid)
        if not proj:
            raise NotFoundError("项目不存在")

    # 旧 session 全部置 inactive
    await db.execute(
        update(AssistantSession)
        .where(AssistantSession.project_id == pid)
        .values(is_active=False)
    )

    count_res = await db.execute(
        select(func.count()).where(AssistantSession.project_id == pid)
    )
    count = count_res.scalar() or 0
    new_session = AssistantSession(
        project_id=pid,
        title=f"对话 {count + 1}",
        is_active=True,
        staged_changes=[],
        summaries=[],
        message_count=0,
    )
    db.add(new_session)
    await db.commit()
    await db.refresh(new_session)
    return {"ok": True, "session": new_session.to_dict()}


@router.get("/sessions/{project_id}")
async def list_sessions(project_id: str, db: AsyncSession = Depends(get_db)):
    pid = _decode_project_id(project_id)
    res = await db.execute(
        select(AssistantSession)
        .where(AssistantSession.project_id == pid)
        .order_by(AssistantSession.updated_at.desc())
    )
    sessions = [s.to_dict() for s in res.scalars().all()]
    return {"ok": True, "sessions": sessions}


@router.post("/session/{session_id}/switch")
async def switch_session(session_id: str, db: AsyncSession = Depends(get_db)):
    sess = await db.get(AssistantSession, session_id)
    if not sess:
        raise NotFoundError("会话不存在")
    await db.execute(
        update(AssistantSession)
        .where(AssistantSession.project_id == sess.project_id)
        .values(is_active=False)
    )
    sess.is_active = True
    await db.commit()
    await db.refresh(sess)
    return {"ok": True, "session": sess.to_dict()}


@router.get("/session/{project_id}/history")
async def get_session_history(project_id: str, db: AsyncSession = Depends(get_db)):
    pid = _decode_project_id(project_id)
    sess = await _get_active_session(db, pid)
    res = await db.execute(
        select(AssistantMessage)
        .where(AssistantMessage.session_id == sess.id)
        .order_by(AssistantMessage.created_at.asc())
    )
    messages = [
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "metadata": m.metadata_ or {},
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in res.scalars().all()
    ]
    return {"ok": True, "session_id": sess.id, "messages": messages, "staged_changes": sess.staged_changes or []}


@router.post("/stage")
async def stage_change(body: dict, db: AsyncSession = Depends(get_db)):
    session_id = body.get("session_id")
    record = body.get("change_record")
    if not session_id or not record:
        raise ValidationError("session_id 与 change_record 必填")
    if not isinstance(record, dict):
        raise ValidationError("change_record 必须是对象")
    required = ("id", "action", "entity_type")
    for key in required:
        value = record.get(key)
        if not value or not isinstance(value, str):
            raise ValidationError(f"change_record.{key} 不能为空字符串")
    after = record.get("after")
    if not after or not isinstance(after, dict):
        raise ValidationError("change_record.after 必须是非空对象")
    if not isinstance(record.get("requires_confirmation"), bool):
        raise ValidationError("change_record.requires_confirmation 必须是布尔值")
    sess = await db.get(AssistantSession, session_id)
    if not sess:
        raise NotFoundError("会话不存在")
    if sess.project_id is None:
        raise ValidationError("全局会话不支持暂存变更")
    record["project_id"] = sess.project_id
    entity_type = record["entity_type"]
    if entity_type not in _ENTITY_REPO:
        raise ValidationError(f"不支持的实体类型：{entity_type}")
    staged = list(sess.staged_changes or [])
    staged.append(record)
    sess.staged_changes = staged
    await db.commit()
    return {"ok": True, "staged_changes": staged}


async def _mark_latest_assistant_message(db, session_id: str, status: str, count: int = 0, error_count: int = 0):
    res = await db.execute(
        select(AssistantMessage)
        .where(AssistantMessage.session_id == session_id, AssistantMessage.role == "assistant")
        .order_by(AssistantMessage.created_at.desc())
        .limit(1)
    )
    msg = res.scalars().first()
    if msg:
        meta = dict(msg.metadata_ or {})
        meta["status"] = status
        if status == "partial":
            meta["applied_count"] = count
            meta["error_count"] = error_count
        else:
            meta[f"{status}_count"] = count
        msg.metadata_ = meta
        await db.commit()


@router.post("/confirm")
async def confirm(body: dict, db: AsyncSession = Depends(get_db)):
    session_id = body.get("session_id")
    change_ids = body.get("change_ids")
    if not session_id:
        raise ValidationError("session_id 必填")
    if change_ids is not None and not isinstance(change_ids, list):
        raise ValidationError("change_ids 必须是数组")
    sess = await db.get(AssistantSession, session_id)
    if not sess:
        raise NotFoundError("会话不存在")
    if sess.project_id is None:
        return {"ok": False, "errors": [{"code": "GLOBAL_SESSION", "message": "全局会话不支持确认变更"}]}
    result = await confirm_session(db, session_id, change_ids=change_ids)
    try:
        if result.get("ok"):
            await _mark_latest_assistant_message(
                db, session_id, "applied", len(result.get("applied", []))
            )
        else:
            await _mark_latest_assistant_message(
                db, session_id, "partial",
                len(result.get("applied", [])),
                len(result.get("errors", []))
            )
    except Exception:
        logger.exception("Failed to mark latest assistant message status")
    return result


@router.post("/reject")
async def reject(body: dict, db: AsyncSession = Depends(get_db)):
    session_id = body.get("session_id")
    change_ids = body.get("change_ids")
    if not session_id:
        raise ValidationError("session_id 必填")
    if change_ids is not None and not isinstance(change_ids, list):
        raise ValidationError("change_ids 必须是数组")
    result = await reject_session(db, session_id, change_ids=change_ids)
    try:
        await _mark_latest_assistant_message(
            db, session_id, "rejected", result.get("rejected_count", 0)
        )
    except Exception:
        logger.exception("Failed to mark latest assistant message status")
    return result


@router.post("/undo")
async def undo(body: dict, db: AsyncSession = Depends(get_db)):
    project_id = body.get("project_id")
    entity_type = body.get("entity_type")
    entity_id = body.get("entity_id")
    if not (project_id and entity_type and entity_id):
        raise ValidationError("project_id、entity_type、entity_id 必填")
    if entity_type != "chapter":
        return {"ok": False, "message": "仅支持撤销章节自动生成"}
    res = await db.execute(
        select(LongChangeRecord)
        .where(
            LongChangeRecord.project_id == project_id,
            LongChangeRecord.entity_type == entity_type,
            LongChangeRecord.entity_id == entity_id,
            LongChangeRecord.source == "auto",
            LongChangeRecord.status == "applied",
        )
        .order_by(LongChangeRecord.created_at.desc())
        .limit(1)
    )
    rec = res.scalars().first()
    if not rec or not rec.before:
        return {"ok": False, "message": "没有可撤销的自动生成"}

    from app import repositories as repo
    current_row = await repo.get_chapter(db, entity_id)
    if isinstance(current_row, dict):
        current = dict(current_row)
    elif current_row is not None:
        current = {c.name: getattr(current_row, c.name) for c in current_row.__table__.columns}
    else:
        current = None
    try:
        await apply_change(db, project_id, {
            "entity_type": entity_type,
            "action": "update",
            "entity_id": entity_id,
            "after": rec.before,
        })
    except Exception as e:
        logger.exception("撤销自动生成失败")
        await db.rollback()
        return {"ok": False, "message": f"撤销失败：{e}"}
    rec.source = "auto_undone"
    db.add(LongChangeRecord(
        project_id=project_id,
        entity_type=entity_type,
        entity_id=entity_id,
        before=current,
        after=rec.before,
        status="applied",
        source="undo",
    ))
    await db.commit()
    return {"ok": True}


@router.get("/undoable/{chapter_id}")
async def undoable(chapter_id: str, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(LongChangeRecord.id)
        .where(
            LongChangeRecord.entity_type == "chapter",
            LongChangeRecord.entity_id == chapter_id,
            LongChangeRecord.source == "auto",
            LongChangeRecord.status == "applied",
        )
        .limit(1)
    )
    return {"undoable": res.scalars().first() is not None}

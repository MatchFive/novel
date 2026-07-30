"""Analyze node: gather project context and historical summaries."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo
from app.agents.harness.history import build_history_context
from app.agents.harness.models import HarnessContext, HarnessStage
from app.agents.harness.retrieval import retrieve_similar_summaries
from app.agents.harness.state import HarnessState
from app.core.llm_factory import get_embedding_client
from app.models import AssistantMessage, AssistantSession, Project, UserSetting


def _build_history_context_manual(
    recent_messages: list[AssistantMessage],
    retrieved_summaries: list[dict],
) -> list[dict[str, str]]:
    """Fallback history builder when no AssistantSession is available."""
    out: list[dict[str, str]] = []

    if retrieved_summaries:
        out.append({
            "role": "user",
            "content": "[以下是与当前问题相关的历史摘要]",
        })
        for s in retrieved_summaries:
            out.append({
                "role": "user",
                "content": f"（{s.get('turn_range', '未知范围')}）\n{s.get('summary_text', '')}",
            })

    for m in recent_messages:
        out.append({"role": m.role, "content": m.content or ""})

    return out


async def analyze(
    state: HarnessState,
    db: AsyncSession,
    settings: UserSetting,
    recent_messages: list[AssistantMessage],
) -> HarnessState:
    project_id = state.project_id
    entities: dict[str, list[dict]] = {}
    project_summary = ""

    if project_id:
        project = await db.get(Project, project_id)
        if project:
            project_summary = f"{project.title}\n{project.description}".strip()
        entities = {
            "outlines": await repo.list_outlines(db, project_id),
            "characters": await repo.list_characters(db, project_id),
            "foreshadows": await repo.list_foreshadows(db, project_id),
            "world": await repo.list_world(db, project_id),
            "plot": await repo.list_plot(db, project_id),
            "chapters": await repo.list_chapters(db, project_id),
        }

    # Retrieve similar summaries (best-effort)
    retrieved_summaries: list[dict] = []
    try:
        embedding_client, dimension = await get_embedding_client(db)
        query_vectors = await embedding_client.embed(
            [state.user_input],
            model=embedding_client.model,
            dimensions=dimension if dimension > 0 else None,
        )
        top_k = max(0, settings.assistant_history_top_k or 5)
        if top_k > 0:
            retrieved_summaries = await retrieve_similar_summaries(
                db, state.session_id, query_vectors[0], top_k
            )
    except Exception:
        pass

    # Build history context, preferring the session-aware helper when possible.
    session: AssistantSession | None = None
    if state.session_id:
        session = await db.get(AssistantSession, state.session_id)

    if session is not None:
        history_context = build_history_context(
            session, recent_messages, retrieved_summaries, settings
        )
    else:
        history_context = _build_history_context_manual(recent_messages, retrieved_summaries)

    session_context = {**(state.context.session_context or {}), "history_context": history_context}
    state.context = HarnessContext(
        project_id=project_id,
        user_input=state.user_input,
        project_summary=project_summary,
        entities=entities,
        session_context=session_context,
    )
    state.stage = HarnessStage.PLAN
    return state

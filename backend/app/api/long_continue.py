"""长篇上下文感知续写（SSE 流式）。注入大纲/角色/伏笔/前文约束。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.core.llm_factory import get_default_llm_client
from app.database import get_db
from app.models import Project
from app import repositories as repo

router = APIRouter(prefix="/continue", tags=["long-continue"])


async def assemble_context(db: AsyncSession, project_id: str) -> str:
    outlines = await repo.list_outlines(db, project_id)
    characters = await repo.list_characters(db, project_id)
    foreshadows = await repo.list_foreshadows(db, project_id)
    chapters = await repo.list_chapters(db, project_id)

    lines = []
    lines.append("【总纲】")
    broads = [o for o in outlines if o.get("type") == "broad"]
    for o in broads:
        lines.append(f"- {o.get('title', '')}: {o.get('content', '')[:200]}")

    last_chapter = sorted(chapters, key=lambda c: c.get('order', 0))[-1] if chapters else None
    if last_chapter:
        from app.agents.harness.workers.chapter_workers import _volume_outline_text
        volume_outline = _volume_outline_text(outlines, last_chapter.get('order', 0))
        lines.append("\n【当前卷大纲】")
        lines.append(volume_outline)

    lines.append("\n【其他时期/卷标题】")
    for o in outlines:
        if o.get("type") in ("period", "volume"):
            lines.append(f"- {o.get('title', '')}")

    lines.append("\n【角色】")
    lines.extend(f"- {c.get('name', '')}（{c.get('status', '')}）: {c.get('traits', '')}" for c in characters[:20])
    lines.append("\n【待回收伏笔】")
    lines.extend(f"- {f.get('title', '')}: {f.get('content', '')[:120]}" for f in foreshadows if f.get('state') == 'pending')
    lines.append("\n【已有章节（末段衔接）】")
    if last_chapter:
        lines.append(f"上一章《{last_chapter.get('title', '')}》末尾：{(last_chapter.get('content', '') or '')[-300:]}")
    return "\n".join(lines)


@router.post("/{project_id}/continue")
async def continue_writing(project_id: str, body: dict, db: AsyncSession = Depends(get_db)):
    proj = await db.get(Project, project_id)
    if not proj:
        raise NotFoundError("项目不存在")
    instruction = body.get("instruction", "继续推进剧情")

    context = await assemble_context(db, project_id)
    prompt = (
        "你是长篇小说的续写引擎。请严格基于以下项目上下文进行续写，"
        "保持人物性格一致、回收已埋伏笔、呼应大纲。只输出新增正文。\n\n"
        f"=== 上下文 ===\n{context}\n=== 续写要求 ===\n{instruction}"
    )
    llm = await get_default_llm_client(db)
    messages = [{"role": "user", "content": prompt}]

    async def event_gen():
        try:
            async for chunk in llm.chat_stream(messages):
                yield f"data: {json.dumps({'delta': chunk}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'done': True}, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")

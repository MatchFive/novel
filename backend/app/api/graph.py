"""知识图谱视图接口：优先 Neo4j，无则降级用 SQLite 关系查询。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.database import get_db
from app.models import Project, LongCharacter, LongForeshadow
from app.graph.client import get_graph

router = APIRouter(tags=["graph"])


@router.get("/{project_id}")
async def graph_view(project_id: str, db: AsyncSession = Depends(get_db)):
    proj = await db.get(Project, project_id)
    if not proj:
        raise NotFoundError("项目不存在")

    # 优先 Neo4j
    g = get_graph()
    if g and g.enabled:
        chars = await g.query_nodes("Character")
        fores = await g.query_nodes("Foreshadow")
        nodes = [{"id": n.get("id"), "label": n.get("name") or n.get("title"), "type": "character"} for n in chars + fores]
        return {"source": "neo4j", "nodes": nodes, "edges": []}

    # 降级：SQLite
    res = await db.execute(select(LongCharacter).where(LongCharacter.project_id == project_id))
    chars = res.scalars().all()
    nodes = [{"id": c.id, "label": c.name, "type": "character"} for c in chars]
    edges = []
    for c in chars:
        for rel in (c.relations or []):
            if isinstance(rel, dict) and rel.get("target"):
                edges.append({"from": c.id, "to": rel["target"], "label": rel.get("relation", "")})
    # 伏笔作为节点
    res = await db.execute(select(LongForeshadow).where(LongForeshadow.project_id == project_id))
    for f in res.scalars().all():
        nodes.append({"id": f.id, "label": f.title, "type": "foreshadow", "state": f.state})
    return {"source": "sqlite", "nodes": nodes, "edges": edges}

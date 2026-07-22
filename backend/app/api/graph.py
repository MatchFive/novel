"""知识图谱视图接口：优先 Neo4j，无则降级用 SQLite + NetworkX 自动布局。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.database import get_db
from app.models import Project
from app.graph.client import get_graph
from app.services.graph_builder import build_project_graph

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
        nodes = [
            {"id": n.get("id"), "label": n.get("name") or n.get("title"), "type": "character"}
            for n in chars + fores
        ]
        return {"source": "neo4j", "width": 960, "height": 640, "nodes": nodes, "edges": []}

    # 降级：SQLite + NetworkX 自动布局
    return await build_project_graph(db, project_id)

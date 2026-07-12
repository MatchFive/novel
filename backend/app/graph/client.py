"""Neo4j 客户端（可选镜像）。无配置时返回 None，功能降级。"""
from __future__ import annotations

from typing import Any, Optional

from app.config import settings as app_settings


class GraphClient:
    def __init__(self):
        self.enabled = bool(app_settings.neo4j_enabled and app_settings.neo4j_uri)
        self._driver = None
        if self.enabled:
            try:
                from neo4j import AsyncGraphDatabase
                self._driver = AsyncGraphDatabase.driver(
                    app_settings.neo4j_uri,
                    auth=(app_settings.neo4j_user, app_settings.neo4j_password),
                )
            except Exception:
                self._driver = None
                self.enabled = False

    async def sync_entity(self, entity_type: str, entity_id: str, props: dict) -> None:
        if not self._driver:
            return
        label = {
            "character": "Character", "outline": "Outline",
            "foreshadow": "Foreshadow", "world": "World", "plot": "PlotNode",
        }.get(entity_type, "Entity")
        cypher = (
            f"MERGE (n:{label} {{id: $id}}) "
            "SET n += $props"
        )
        props = {k: (str(v) if not isinstance(v, (str, int, float, bool, list)) else v)
                 for k, v in (props or {}).items()}
        async with self._driver.session() as s:
            await s.run(cypher, id=entity_id, props=props)

    async def sync_relation(self, from_id: str, to_id: str, rel: str) -> None:
        if not self._driver:
            return
        cypher = (
            "MATCH (a {id:$from_id}), (b {id:$to_id}) "
            f"MERGE (a)-[:{rel.upper()}]->(b)"
        )
        async with self._driver.session() as s:
            await s.run(cypher, from_id=from_id, to_id=to_id)

    async def query_nodes(self, label: str) -> list[dict]:
        if not self._driver:
            return []
        cypher = f"MATCH (n:{label}) RETURN n.id AS id, n.name AS name, n.title AS title LIMIT 200"
        async with self._driver.session() as s:
            result = await s.run(cypher)
            return [dict(r) async for r in result]

    async def close(self) -> None:
        if self._driver:
            await self._driver.close()


_client: Optional[GraphClient] = None


def get_graph() -> Optional[GraphClient]:
    global _client
    if _client is None:
        _client = GraphClient()
    return _client

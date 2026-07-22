"""基于 SQLite + NetworkX 的本地知识图谱构建与自动布局。"""
from __future__ import annotations

from typing import Any

from app import repositories as repo


_ENTITY_CONFIG = {
    "character": {"repo": repo.list_characters, "label_field": "name", "type_label": "角色"},
    "outline": {"repo": repo.list_outlines, "label_field": "title", "type_label": "大纲"},
    "foreshadow": {"repo": repo.list_foreshadows, "label_field": "title", "type_label": "伏笔"},
    "world": {"repo": repo.list_world, "label_field": "category", "type_label": "世界观"},
    "plot": {"repo": repo.list_plot, "label_field": "title", "type_label": "剧情节点"},
    "chapter": {"repo": repo.list_chapters, "label_field": "title", "type_label": "章节"},
}


async def build_project_graph(
    db,
    project_id: str,
    width: int = 960,
    height: int = 640,
    seed: int = 42,
) -> dict[str, Any]:
    """从 SQLite 读取全部实体，构建 NetworkX 图并计算布局。"""
    try:
        import networkx as nx
    except ImportError:  # pragma: no cover
        # 未安装 networkx 时降级为旧逻辑
        return await _fallback_graph(db, project_id)

    G = nx.Graph()
    nodes_by_id: dict[str, dict] = {}
    name_to_character: dict[str, str] = {}

    # 1. 加载节点
    for entity_type, cfg in _ENTITY_CONFIG.items():
        entities = await cfg["repo"](db, project_id)
        for e in entities:
            eid = e.get("id")
            if not eid:
                continue
            label = e.get(cfg["label_field"]) or "未命名"
            node = {
                "id": eid,
                "label": label,
                "type": entity_type,
                "type_label": cfg["type_label"],
            }
            G.add_node(eid)
            nodes_by_id[eid] = node
            if entity_type == "character" and label:
                name_to_character[label] = eid

    # 2. 加载边
    # 角色关系：优先匹配 id，再按名字匹配
    characters = await repo.list_characters(db, project_id)
    for c in characters:
        cid = c.get("id")
        for rel in c.get("relations") or []:
            if not isinstance(rel, dict):
                continue
            target = rel.get("target") or rel.get("target_name") or rel.get("name")
            rel_label = rel.get("relation") or rel.get("type") or "关联"
            if target and target in nodes_by_id:
                G.add_edge(cid, target, label=rel_label)
            elif target and target in name_to_character:
                G.add_edge(cid, name_to_character[target], label=rel_label)

    # 大纲父子
    outlines = await repo.list_outlines(db, project_id)
    for o in outlines:
        oid = o.get("id")
        parent_id = o.get("parent_id")
        if parent_id and parent_id in nodes_by_id:
            G.add_edge(parent_id, oid, label="包含")

    # 伏笔 -> 剧情节点
    foreshadows = await repo.list_foreshadows(db, project_id)
    for f in foreshadows:
        fid = f.get("id")
        subplot_id = f.get("subplot_id")
        if subplot_id and subplot_id in nodes_by_id:
            G.add_edge(fid, subplot_id, label="属于")

    # 3. 计算布局
    positions = _compute_layout(G, width, height, seed)

    nodes = []
    for eid, node in nodes_by_id.items():
        x, y = positions.get(eid, (width / 2, height / 2))
        node["x"] = round(x, 2)
        node["y"] = round(y, 2)
        nodes.append(node)

    edges = [
        {"from": u, "to": v, "label": data.get("label", "")}
        for u, v, data in G.edges(data=True)
    ]

    return {
        "source": "sqlite+networkx",
        "width": width,
        "height": height,
        "nodes": nodes,
        "edges": edges,
    }


def _compute_layout(G, width: int, height: int, seed: int) -> dict[str, tuple[float, float]]:
    """使用 NetworkX 布局算法计算节点坐标。"""
    import networkx as nx

    node_count = G.number_of_nodes()
    if node_count == 0:
        return {}

    margin = 48
    usable_w = width - 2 * margin
    usable_h = height - 2 * margin

    if G.number_of_edges() == 0:
        # 没有关系时按网格排列，避免重叠
        cols = max(1, int((node_count ** 0.5) + 0.5))
        positions: dict[str, tuple[float, float]] = {}
        for i, node in enumerate(G.nodes()):
            col = i % cols
            row = i // cols
            x = margin + (col / max(1, cols - 1)) * usable_w if cols > 1 else width / 2
            y = margin + (row / max(1, int(node_count / cols))) * usable_h
            positions[node] = (x, y)
        return positions

    # spring_layout：自动把有关系的节点拉近、无关系的推开
    k = max(0.5, 2.0 / (node_count ** 0.5))
    pos = nx.spring_layout(
        G,
        seed=seed,
        k=k,
        iterations=100,
        scale=1.0,
    )

    # 归一化到画布尺寸并留边距
    xs = [p[0] for p in pos.values()]
    ys = [p[1] for p in pos.values()]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)

    range_x = max_x - min_x or 1.0
    range_y = max_y - min_y or 1.0

    positions = {}
    for node, (px, py) in pos.items():
        nx_norm = (px - min_x) / range_x
        ny_norm = (py - min_y) / range_y
        x = margin + nx_norm * usable_w
        # SVG 坐标系 y 向下；spring 布局 y 轴向上更好看，这里翻转一下让密集部分居中
        y = margin + (1 - ny_norm) * usable_h
        positions[node] = (x, y)

    return positions


async def _fallback_graph(db, project_id: str) -> dict[str, Any]:
    """NetworkX 未安装时的降级返回（仅保留旧逻辑）。"""
    from sqlalchemy import select
    from app.models import LongCharacter, LongForeshadow

    nodes = []
    edges = []
    res = await db.execute(select(LongCharacter).where(LongCharacter.project_id == project_id))
    for c in res.scalars().all():
        nodes.append({"id": c.id, "label": c.name, "type": "character"})
        for rel in (c.relations or []):
            if isinstance(rel, dict) and rel.get("target"):
                edges.append({"from": c.id, "to": rel["target"], "label": rel.get("relation", "")})
    res = await db.execute(select(LongForeshadow).where(LongForeshadow.project_id == project_id))
    for f in res.scalars().all():
        nodes.append({"id": f.id, "label": f.title, "type": "foreshadow", "state": f.state})
    return {"source": "sqlite", "nodes": nodes, "edges": edges}

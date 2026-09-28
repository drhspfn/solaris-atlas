from collections import deque

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.db.models.graph import Edge
from wuwa_story.db.models.ontology import RelationType


async def bounded_traverse(
    session: AsyncSession,
    start_node_id: int,
    *,
    max_depth: int = 2,
    max_nodes: int = 500,
    relation_keys: set[str] | None = None,
) -> set[int]:
    if not 0 <= max_depth <= 8:
        raise ValueError("max_depth must be between 0 and 8")
    if not 1 <= max_nodes <= 5000:
        raise ValueError("max_nodes must be between 1 and 5000")
    visited = {start_node_id}
    frontier = deque([(start_node_id, 0)])
    while frontier and len(visited) < max_nodes:
        current, depth = frontier.popleft()
        if depth >= max_depth:
            continue
        statement = select(Edge.to_node_id).where(Edge.from_node_id == current)
        if relation_keys:
            statement = statement.join(
                RelationType, Edge.relation_type_id == RelationType.id
            ).where(RelationType.key.in_(relation_keys))
        for target in await session.scalars(statement.limit(max_nodes - len(visited))):
            if target not in visited:
                visited.add(target)
                frontier.append((target, depth + 1))
    return visited

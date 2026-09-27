from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.db.models.graph import Edge, Node
from wuwa_story.db.models.ontology import RelationType


async def get_edges(
    session: AsyncSession,
    node_id: int,
    *,
    direction: str = "both",
    relation_keys: set[str] | None = None,
    limit: int = 200,
) -> list[Edge]:
    statement = select(Edge)
    if direction == "out":
        statement = statement.where(Edge.from_node_id == node_id)
    elif direction == "in":
        statement = statement.where(Edge.to_node_id == node_id)
    elif direction == "both":
        statement = statement.where((Edge.from_node_id == node_id) | (Edge.to_node_id == node_id))
    else:
        raise ValueError("direction must be one of: in, out, both")
    if relation_keys:
        statement = statement.join(RelationType, Edge.relation_type_id == RelationType.id).where(
            RelationType.key.in_(relation_keys)
        )
    result = await session.scalars(statement.order_by(Edge.id).limit(limit))
    return list(result)


async def find_node_for_edge(session: AsyncSession, node_id: int) -> Node | None:
    return await session.get(Node, node_id)

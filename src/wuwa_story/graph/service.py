from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.db.models.graph import Edge, Node
from wuwa_story.db.repositories.edges import get_edges
from wuwa_story.db.repositories.nodes import get_node


class GraphService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_node(self, canonical_key: str) -> Node | None:
        return await get_node(self.session, canonical_key)

    async def get_edges(
        self,
        node_id: int,
        *,
        direction: str = "both",
        relation_keys: set[str] | None = None,
        limit: int = 200,
    ) -> list[Edge]:
        return await get_edges(
            self.session, node_id, direction=direction, relation_keys=relation_keys, limit=limit
        )

    async def get_neighbors(
        self,
        node_id: int,
        *,
        direction: str = "out",
        relation_keys: set[str] | None = None,
        limit: int = 200,
    ) -> list[Node]:
        edges = await self.get_edges(
            node_id, direction=direction, relation_keys=relation_keys, limit=limit
        )
        neighbor_ids = {
            edge.to_node_id if edge.from_node_id == node_id else edge.from_node_id for edge in edges
        }
        if not neighbor_ids:
            return []
        result = await self.session.scalars(select(Node).where(Node.id.in_(neighbor_ids)))
        return list(result)

    async def find_direct_connections(
        self, from_key: str, to_key: str, relation_keys: set[str] | None = None
    ) -> list[Edge]:
        nodes = await self.session.scalars(
            select(Node).where(Node.canonical_key.in_((from_key, to_key)))
        )
        by_key = {node.canonical_key: node.id for node in nodes}
        if from_key not in by_key or to_key not in by_key:
            return []
        return [
            edge
            for edge in await self.get_edges(
                by_key[from_key], direction="out", relation_keys=relation_keys
            )
            if edge.to_node_id == by_key[to_key]
        ]

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.db.models.graph import Edge, EdgeEvidence, Node


async def validate_graph(session: AsyncSession) -> dict[str, int]:
    dangling = await session.scalar(
        select(func.count())
        .select_from(Edge)
        .outerjoin(Node, Edge.from_node_id == Node.id)
        .where(Node.id.is_(None))
    )
    source_edges_without_evidence = await session.scalar(
        select(func.count())
        .select_from(Edge)
        .outerjoin(EdgeEvidence, Edge.id == EdgeEvidence.edge_id)
        .where(Edge.layer == "source", EdgeEvidence.id.is_(None))
    )
    return {
        "dangling_edges": int(dangling or 0),
        "source_edges_without_evidence": int(source_edges_without_evidence or 0),
    }

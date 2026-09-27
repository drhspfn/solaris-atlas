from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.db.models.graph import Node


async def get_node(session: AsyncSession, canonical_key: str) -> Node | None:
    return await session.scalar(select(Node).where(Node.canonical_key == canonical_key))


async def upsert_node(
    session: AsyncSession,
    *,
    canonical_key: str,
    type_id: int,
    slug: str | None = None,
    release_id: int | None = None,
) -> Node:
    node = await get_node(session, canonical_key)
    if node is not None:
        if node.type_id != type_id:
            raise ValueError(f"Canonical key {canonical_key!r} already has a different node type")
        if node.slug is None and slug is not None:
            node.slug = slug
        return node
    node = Node(
        canonical_key=canonical_key, type_id=type_id, slug=slug, created_release_id=release_id
    )
    session.add(node)
    await session.flush()
    return node

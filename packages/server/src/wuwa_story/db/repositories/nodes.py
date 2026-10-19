from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.db.models.core import Quest
from wuwa_story.db.models.graph import Node, NodeType


async def get_node(session: AsyncSession, canonical_key: str, types: tuple[str, ...] = ()) -> Node | None:
    # Match the complete stored slug, never just its numeric prefix.
    statement = select(Node).where(
        Node.canonical_key == canonical_key if ":" in canonical_key else Node.slug == canonical_key)
    if types:
        statement = statement.join(NodeType).where(NodeType.key.in_(types))
    if ":" in canonical_key:
        return await session.scalar(statement)
    matches = list(await session.scalars(statement.limit(2)))
    return matches[0] if len(matches) == 1 else None


async def get_quest(session: AsyncSession, key: str | int) -> Quest | None:
    value = str(key)
    if value.isdigit():
        return await session.scalar(select(Quest).where(Quest.game_quest_id == int(value)))
    node = await get_node(session, value, ("quest",))
    return await session.get(Quest, node.id) if node else None


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

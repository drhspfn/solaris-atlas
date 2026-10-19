from sqlalchemy import case, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.db.models.core import Quest
from wuwa_story.db.models.graph import Node, NodeType
from wuwa_story.urls import numeric_id


async def get_node(session: AsyncSession, canonical_key: str, types: tuple[str, ...] = ()) -> Node | None:
    # Match the complete stored slug, never just its numeric prefix.
    statement = select(Node).where(or_(Node.canonical_key == canonical_key, Node.slug == canonical_key))
    if types:
        statement = statement.join(NodeType).where(NodeType.key.in_(types))
    matches = list(await session.scalars(statement.order_by(
        case((Node.canonical_key == canonical_key, 0), else_=1)).limit(2)))
    if matches and matches[0].canonical_key == canonical_key:
        return matches[0]
    return matches[0] if len(matches) == 1 else None


async def get_quest(session: AsyncSession, key: str | int) -> Quest | None:
    value = str(key)
    identifier = numeric_id(value)
    if identifier is not None:
        return await session.scalar(select(Quest).where(Quest.game_quest_id == identifier))
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

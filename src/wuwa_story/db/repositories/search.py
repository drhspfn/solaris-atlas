from typing import Any

from sqlalchemy import asc, desc, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.db.models.graph import Node, NodeType
from wuwa_story.db.models.search import EntityAlias, SearchDocument


def _unique_entities(
    rows: list[dict[str, Any]], limit: int, offset: int = 0
) -> list[dict[str, Any]]:
    """Collapse matching localized documents/aliases to one result per graph node."""
    unique: dict[int, dict[str, Any]] = {}
    for row in rows:
        unique.setdefault(row["id"], row)
    return list(unique.values())[offset : offset + limit]


async def lexical_search(
    session: AsyncSession,
    query: str,
    *,
    limit: int = 20,
    category: str | None = None,
    categories: list[str] | None = None,
    locale_id: int | None = None,
    sort_by: str = "relevance",
    sort_order: str = "desc",
    offset: int = 0,
) -> list[dict[str, Any]]:
    if sort_by not in {"relevance", "name"}:
        raise ValueError("sort_by must be 'relevance' or 'name'")
    if sort_order not in {"asc", "desc"}:
        raise ValueError("sort_order must be 'asc' or 'desc'")
    normalized = " ".join(query.casefold().split())
    selected_categories = set(categories or ())
    if category:
        selected_categories.add(category)
    sort = asc if sort_order == "asc" else desc
    canonical = await session.execute(
        select(
            Node.id,
            Node.canonical_key,
            Node.slug,
            NodeType.key.label("node_type"),
            Node.canonical_key.label("alias"),
            literal(2.0).label("score"),
        )
        .join(NodeType, Node.type_id == NodeType.id)
        .where(Node.canonical_key == query)
        .limit(limit)
    )
    canonical_rows = [dict(row._mapping) for row in canonical]
    if canonical_rows and (
        not selected_categories or canonical_rows[0]["node_type"] in selected_categories
    ):
        return canonical_rows[offset : offset + limit]
    exact_statement = (
        select(
            Node.id,
            Node.canonical_key,
            Node.slug,
            NodeType.key.label("node_type"),
            EntityAlias.alias,
            literal(1.0).label("score"),
        )
        .join(EntityAlias, EntityAlias.node_id == Node.id)
        .join(NodeType, Node.type_id == NodeType.id)
        .where(EntityAlias.normalized_alias == normalized)
    )
    if locale_id:
        exact_statement = exact_statement.where(EntityAlias.locale_id == locale_id)
    if selected_categories:
        exact_statement = exact_statement.where(NodeType.key.in_(selected_categories))
    if sort_by == "name":
        exact_statement = exact_statement.order_by(sort(EntityAlias.alias), Node.id)
    exact = await session.execute(exact_statement.limit(min(max((limit + offset) * 20, 200), 4000)))
    exact_rows = _unique_entities(
        [dict(row._mapping) for row in exact], min(max((limit + offset) * 20, 200), 4000)
    )
    if exact_rows:
        return exact_rows[offset : offset + limit]
    score = func.similarity(SearchDocument.title, query)
    statement = (
        select(
            Node.id,
            Node.canonical_key,
            Node.slug,
            NodeType.key.label("node_type"),
            SearchDocument.title.label("alias"),
            score.label("score"),
        )
        .join(SearchDocument, SearchDocument.target_node_id == Node.id)
        .join(NodeType, Node.type_id == NodeType.id)
        .where(or_(SearchDocument.title.op("%")(query), SearchDocument.body.op("%")(query)))
    )
    if selected_categories:
        statement = statement.where(NodeType.key.in_(selected_categories))
    if locale_id:
        statement = statement.where(SearchDocument.locale_id == locale_id)
    statement = statement.order_by(
        sort(SearchDocument.title) if sort_by == "name" else sort(score), Node.id
    ).limit(min(max((limit + offset) * 20, 200), 4000))
    rows = _unique_entities(
        [dict(row._mapping) for row in await session.execute(statement)], limit, offset
    )
    if rows:
        return rows
    fts = (
        select(
            Node.id,
            Node.canonical_key,
            Node.slug,
            NodeType.key.label("node_type"),
            SearchDocument.title.label("alias"),
            func.ts_rank(SearchDocument.search_vector, func.plainto_tsquery("simple", query)).label(
                "score"
            ),
        )
        .join(SearchDocument, SearchDocument.target_node_id == Node.id)
        .join(NodeType, Node.type_id == NodeType.id)
        .where(SearchDocument.search_vector.op("@@")(func.plainto_tsquery("simple", query)))
    )
    if selected_categories:
        fts = fts.where(NodeType.key.in_(selected_categories))
    if locale_id:
        fts = fts.where(SearchDocument.locale_id == locale_id)
    rank = func.ts_rank(SearchDocument.search_vector, func.plainto_tsquery("simple", query))
    fts = fts.order_by(
        sort(SearchDocument.title) if sort_by == "name" else sort(rank), Node.id
    ).limit(min(max((limit + offset) * 20, 200), 4000))
    return _unique_entities(
        [dict(row._mapping) for row in await session.execute(fts)], limit, offset
    )

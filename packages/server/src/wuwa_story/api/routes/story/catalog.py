"""Catalog and category browse endpoints."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.api.routes.story.shared import _display_label, _localized, _release_id
from wuwa_story.db.models.core import Character, DialogueLine, Item, Location, Quest, Speaker
from wuwa_story.db.models.graph import Node, NodeType
from wuwa_story.db.models.i18n import Locale, LocalizationValue
from wuwa_story.db.models.search import SearchDocument
from wuwa_story.db.session import get_session

router = APIRouter(tags=["story browsing"])

@router.get("/catalog")
async def browse_catalog(
    category: str = Query(pattern="^(character|item|location|quest|speaker)$"),
    locale: str = "en",
    limit: int = Query(24, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Return a stable, localized catalog page for the frontend browse views."""
    locale_row = await session.scalar(select(Locale).where(Locale.code == locale))
    if locale_row is None:
        raise HTTPException(status_code=400, detail=f"unknown locale: {locale}")
    release_id = await _release_id(session, None)
    node_types = {"location": ["area", "location"], "quest": ["quest"]}.get(category, [category])
    name_key_id = func.coalesce(
        Character.name_key_id,
        Item.name_key_id,
        Location.name_key_id,
        Quest.name_key_id,
        Speaker.name_key_id,
    )
    canonical_name = func.coalesce(
        Character.canonical_name,
        Item.canonical_name,
        Location.canonical_name,
    )
    alternate_title = (
        select(SearchDocument.title)
        .where(
            SearchDocument.target_node_id == Node.id,
            SearchDocument.locale_id == locale_row.id,
            SearchDocument.category == f"{category}_nickname",
        )
        .order_by(SearchDocument.id)
        .limit(1)
        .correlate(Node)
        .scalar_subquery()
    )
    statement = (
        select(
            Node,
            NodeType.key,
            SearchDocument.title,
            SearchDocument.aliases,
            LocalizationValue.content,
            name_key_id,
            canonical_name,
            alternate_title,
        )
        .join(NodeType, NodeType.id == Node.type_id)
        .outerjoin(
            SearchDocument,
            and_(
                SearchDocument.target_node_id == Node.id,
                SearchDocument.locale_id == locale_row.id,
                SearchDocument.category.in_(node_types),
            ),
        )
        .outerjoin(Character, Character.node_id == Node.id)
        .outerjoin(Item, Item.node_id == Node.id)
        .outerjoin(Location, Location.node_id == Node.id)
        .outerjoin(Quest, Quest.node_id == Node.id)
        .outerjoin(Speaker, Speaker.node_id == Node.id)
        .outerjoin(
            LocalizationValue,
            and_(
                LocalizationValue.key_id == name_key_id,
                LocalizationValue.locale_id == locale_row.id,
                LocalizationValue.release_id == release_id,
            ),
        )
        .where(NodeType.key.in_(node_types), Node.status == "active")
        .order_by(func.lower(func.coalesce(SearchDocument.title, Node.canonical_key)), Node.id)
    )
    if category == "character":
        role_type = Character.metadata_json["role_type"].as_integer()
        # RoleInfo.RoleType=2 rows are alternate/battle role configs, not the
        # primary Resonator roster. Keep unknown future values visible.
        statement = statement.where(or_(role_type == 1, role_type.is_(None)))
    total = (
        await session.scalar(
            select(func.count(Node.id))
            .join(NodeType, NodeType.id == Node.type_id)
            .outerjoin(Character, Character.node_id == Node.id)
            .where(NodeType.key.in_(node_types), Node.status == "active")
        )
        or 0
    )
    if category == "character":
        role_type = Character.metadata_json["role_type"].as_integer()
        total = (
            await session.scalar(
                select(func.count(Node.id))
                .join(NodeType, NodeType.id == Node.type_id)
                .join(Character, Character.node_id == Node.id)
                .where(
                    NodeType.key == "character",
                    Node.status == "active",
                    or_(role_type == 1, role_type.is_(None)),
                )
            )
            or 0
        )
    rows = (await session.execute(statement.offset(offset).limit(limit))).all()
    results = []
    fallback_labels = {
        "character": "Unnamed character",
        "item": "Unnamed item",
        "area": "Unnamed location",
        "location": "Unnamed location",
        "quest": "Quest",
        "speaker": "Unnamed speaker",
    }
    for (
        node,
        node_type,
        search_title,
        aliases,
        localized_title,
        entity_name_key_id,
        canonical_title,
        alias_title,
    ) in rows:
        title = (
            _display_label(search_title)
            or _display_label(localized_title)
            or _display_label(canonical_title)
            or _display_label(alias_title)
            or _display_label(node.slug)
        )
        if not title and entity_name_key_id is not None:
            localized = await _localized(session, entity_name_key_id, locale, release_id)
            title = _display_label(localized.get("content")) if localized else None
        if not title:
            raw_id = node.canonical_key.partition(":")[2]
            fallback = "Area" if node_type == "area" else fallback_labels.get(node_type, "Unnamed entry")
            title = f"{fallback} · {raw_id}"
        results.append(
            {
                "id": node.id,
                "canonical_key": node.canonical_key,
                "node_type": node_type,
                "category": category,
                "title": title,
                "aliases": aliases or [],
                "slug": node.slug,
            }
        )
    return {
        "category": category,
        "locale": locale,
        "total": total,
        "limit": limit,
        "offset": offset,
        "results": results,
    }

@router.get("/categories")
async def browse_categories(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(NodeType.key, func.count(Node.id))
            .outerjoin(Node, Node.type_id == NodeType.id)
            .group_by(NodeType.key)
            .order_by(NodeType.key)
        )
    ).all()
    counts = dict(rows)
    dialogue_count = await session.scalar(select(func.count()).select_from(DialogueLine)) or 0
    browse_categories = [
        {"key": "character", "count": counts.get("character", 0), "node_types": ["character"]},
        {"key": "item", "count": counts.get("item", 0), "node_types": ["item"]},
        {
            "key": "location",
            "count": counts.get("area", 0) + counts.get("location", 0),
            "node_types": ["area", "location"],
        },
        {"key": "quest", "count": counts.get("quest", 0), "node_types": ["quest"]},
        {"key": "speaker", "count": counts.get("speaker", 0), "node_types": ["speaker"]},
        {
            "key": "dialogue",
            "count": dialogue_count,
            "node_types": ["dialogue_line", "talk_item"],
        },
    ]
    return {
        "categories": [{"key": key, "count": count} for key, count in rows],
        "browse_categories": browse_categories,
    }

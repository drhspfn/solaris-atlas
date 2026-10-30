"""Catalog and category browse endpoints."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.api import read_projections
from wuwa_story.api.routes.story.shared import _display_label, _localized, _release_id
from wuwa_story.db.models.core import DialogueLine
from wuwa_story.db.models.graph import Node, NodeType
from wuwa_story.db.models.i18n import Locale
from wuwa_story.db.repositories.catalog import read_catalog
from wuwa_story.db.session import get_session
from wuwa_story.storage.entity_media import entity_image_urls
from wuwa_story.story.service import prefetch_localizations
from wuwa_story.urls import node_url

router = APIRouter(tags=["story browsing"])

@router.get("/catalog")
async def browse_catalog(
    category: str = Query(pattern="^(character|item|location|quest|speaker)$"),
    quest_type_id: int | None = Query(None, ge=0),
    locale: str = "en",
    limit: int = Query(24, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Return a stable, localized catalog page for the frontend browse views."""
    if read_projections.should_route_to_projection(category, "current", locale=locale):
        return await read_projections.catalog_page(
            session, category, locale, quest_type_id=quest_type_id, limit=limit, offset=offset,
        )
    locale_row = await session.scalar(select(Locale).where(Locale.code == locale))
    if locale_row is None:
        raise HTTPException(status_code=400, detail=f"unknown locale: {locale}")
    if quest_type_id is not None and category != "quest":
        raise HTTPException(status_code=400, detail="quest_type_id requires category=quest")
    release_id = await _release_id(session, None)
    total, rows = await read_catalog(
        session, category, locale_row.id, release_id, limit, offset, quest_type_id,
    )
    fallback_keys = {
        row[5] for row in rows
        if row[5] is not None
        and not any(_display_label(row[index]) for index in (2, 4, 6, 7))
    }
    await prefetch_localizations(session, fallback_keys, locale, release_id)
    images = await entity_image_urls(session, [row[0].id for row in rows])
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
                "href": node_url(node, node_type),
                "image_url": images.get(node.id),
            }
        )
    return {
        "category": category,
        "quest_type_id": quest_type_id,
        "locale": locale,
        "total": total,
        "limit": limit,
        "offset": offset,
        "results": results,
    }

@router.get("/categories")
async def browse_categories(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    if read_projections.should_route_to_projection(read_projections.ENTITY_PROJECTION_KINDS, "current"):
        return await read_projections.category_counts(session)
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

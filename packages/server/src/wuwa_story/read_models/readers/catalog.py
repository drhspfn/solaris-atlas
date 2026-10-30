"""Lightweight catalog cards and counts, independent of full public pages."""

from fastapi import HTTPException
from sqlalchemy import func, literal, or_, select, union_all

from wuwa_story.db.repositories.read_models import ENTITY_READ_MODELS, card_columns, card_rows
from wuwa_story.read_models.readers import entities


async def category_counts(session, locale="en", snapshot_key="current"):
    locale_id = await entities._locale_id(session, locale)
    queries = []
    for kind, model in ENTITY_READ_MODELS.items():
        if kind == "story_map":
            continue
        await entities._require_scope(session, kind, snapshot_key)
        node_type = model.card["node_type"].as_string()
        queries.append(
            select(literal(kind), node_type, func.count())
            .where(
                model.locale_id == locale_id,
                model.snapshot_key == snapshot_key,
            )
            .group_by(node_type)
        )
    rows = (await session.execute(union_all(*queries))).all()
    counts = {}
    for _, node_type, count in rows:
        counts[node_type] = counts.get(node_type, 0) + count
    browse = []
    for kind in ("character", "item", "location", "quest", "speaker", "dialogue"):
        types = (
            ["area", "location"]
            if kind == "location"
            else ["dialogue_line", "talk_item", "narration", "player_choice"]
            if kind == "dialogue"
            else [kind]
        )
        browse.append(
            {"key": kind, "count": sum(counts.get(t, 0) for t in types), "node_types": types}
        )
    return {
        "categories": [{"key": key, "count": count} for key, count in sorted(counts.items())],
        "browse_categories": browse,
    }


def catalog_entry(entry, category):
    """Project a published card into the existing /catalog results contract."""
    entities._ready_card(entry)
    category = entities._kind(category)
    card = entry.card
    if (
        not isinstance(card, dict)
        or not card.get("node_type")
        or not card.get("href")
        or not isinstance(card.get("aliases"), list)
        or not entry.title
    ):
        raise entities._unready()
    return {
        "id": entry.entity_id,
        "canonical_key": entry.canonical_key,
        "node_type": card["node_type"],
        "category": category,
        "title": entry.title,
        "aliases": card["aliases"],
        "slug": entry.slug,
        "href": card["href"],
        "image_url": entry.primary_image_url,
    }


async def catalog_page(
    session,
    category,
    locale="en",
    snapshot_key="current",
    *,
    quest_type_id=None,
    limit=24,
    offset=0,
):
    """Read cards and counts from projections; an unpublished scope is a 503, not an empty catalog."""
    category = entities._kind(category)
    if category == "story_map":
        raise HTTPException(400, "Story maps are not entity catalogs")
    if not 1 <= limit <= 100 or offset < 0:
        raise HTTPException(400, "Invalid catalog pagination")
    if quest_type_id is not None and (category != "quest" or quest_type_id < 0):
        raise HTTPException(400, "quest_type_id requires category=quest and a nonnegative value")
    await entities._require_scope(session, category, snapshot_key)
    model = ENTITY_READ_MODELS[category]
    locale_id = await entities._locale_id(session, locale)
    scope = (model.locale_id == locale_id, model.snapshot_key == snapshot_key)
    filters = list(scope)
    if category in entities._NODE_KINDS:
        filters.append(model.card["status"].as_string() == "active")
    if category == "character":
        role_type = model.role_type
        filters.append(or_(role_type == 1, role_type.is_(None)))
    if quest_type_id is not None:
        filters.append(model.quest_type == str(quest_type_id))
    total = await session.scalar(select(func.count()).select_from(model).where(*filters)) or 0
    entries = await card_rows(
        session,
        select(*card_columns(model))
        .where(*filters)
        .order_by(model.sort_title, model.entity_id)
        .offset(offset)
        .limit(limit),
    )
    return {
        "category": category,
        "quest_type_id": quest_type_id,
        "locale": locale,
        "total": total,
        "limit": limit,
        "offset": offset,
        "results": [catalog_entry(entry, category) for entry in entries],
    }

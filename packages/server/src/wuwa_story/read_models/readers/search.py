"""Exact-snapshot full-text search over physically separated public read tables."""

from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import case, func, literal, or_, select, union_all

from wuwa_story.db.repositories.read_models import ENTITY_READ_MODELS, card_columns
from wuwa_story.read_models.readers import catalog, entities


def _compact_line(line):
    return {
        "id": line["id"],
        "canonical_key": line["id"],
        "node_type": "dialogue_line",
        "category": "dialogue",
        "speaker": line["speaker"],
        "text": line["text"],
        "game_ids": line["game_ids"],
        "source_type": line["source_type"],
        "source_index": line["source_index"],
        "flow_state": line["flow_state"],
        "action": {key: line["action"][key] for key in ("id", "name", "index")}
        if line.get("action")
        else None,
        "source": {
            "file": line["provenance"]["source_file"],
            "row": line["provenance"]["source_row"],
        }
        if line.get("provenance")
        else None,
    }


async def search_page(
    session,
    q,
    category=None,
    scope="entities",
    locale="en",
    game_version=None,
    locale_id=None,
    sort_by="relevance",
    sort_order="desc",
    limit=20,
    offset=0,
    character=None,
    quest_id=None,
    *,
    _full_dialogue=False,
):
    snapshot = game_version or "current"
    if scope == "dialogue" and sort_by != "relevance":
        raise HTTPException(422, "Dialogue search uses authored order.")
    if sort_by not in {"name", "relevance"} or sort_order not in {"asc", "desc"}:
        raise HTTPException(422, "Invalid search ordering")
    if locale_id is None:
        locale_id = await entities._locale_id(session, locale)
    kinds = []
    selected_categories = (
        ["dialogue"]
        if scope == "dialogue"
        else category
        or [kind for kind in ENTITY_READ_MODELS if kind not in {"dialogue", "story_map"}]
    )
    if game_version and not category and scope != "dialogue":
        selected_categories = [
            kind
            for kind in selected_categories
            if kind not in {"location", "speaker", "game_notice"}
        ]
    for value in selected_categories:
        kind = entities._NODE_KIND_ALIASES.get(value, entities.KIND_ALIASES.get(value, value))
        if kind not in ENTITY_READ_MODELS or kind == "story_map":
            raise HTTPException(422, f"Unknown categories: {[value]}")
        if kind not in kinds:
            kinds.append(kind)
    for kind in kinds:
        await entities._require_scope(session, kind, snapshot)
    character_key = None
    if character:
        character_entry = await entities.projection_entry(
            session, "character", character, locale, snapshot
        )
        character_key = character_entry.canonical_key
        if not character_key:
            raise entities._unready()
    queries = []
    normalized = " ".join(q.casefold().split())
    tsquery = func.plainto_tsquery("simple", q)
    for kind in kinds:
        model = ENTITY_READ_MODELS[kind]
        match = or_(
            model.search_vector.op("@@")(tsquery),
            model.title.icontains(q.strip(), autoescape=True),
            model.searchable_text.icontains(q.strip(), autoescape=True),
            model.canonical_key == q,
        )
        filters = [model.locale_id == locale_id, model.snapshot_key == snapshot, match]
        if character_key:
            filters.append(model.card["character_keys"].contains([character_key]))
        if quest_id is not None:
            filters.append(model.card["quest_ids"].contains([quest_id]))
        if scope == "dialogue":
            # Page identities using only small columns, then hydrate selected lines.
            queries.append(
                select(model.entity_id, literal(kind).label("kind"))
                .where(*filters)
                .order_by(
                    model.card["flow_state"].as_string(),
                    model.card["action_index"].as_integer(),
                    model.card["source_index"].as_integer(),
                    model.entity_id,
                )
                .offset(offset)
                .limit(limit)
            )
        else:
            score = case(
                (model.canonical_key == q, 2.0),
                (func.lower(model.title) == normalized, 1.0),
                (model.card["alias_keys"].contains([normalized]), 1.0),
                else_=func.ts_rank(model.search_vector, tsquery),
            )
            queries.append(
                select(
                    literal(kind).label("kind"),
                    *card_columns(model),
                    model.title.label("alias"),
                    score.label("score"),
                ).where(*filters)
            )
    if scope == "dialogue":
        rows = (await session.execute(queries[0])).mappings().all()
        model = ENTITY_READ_MODELS["dialogue"]
        identities = [row["entity_id"] for row in rows]
        lines = (
            (
                await session.execute(
                    select(model.entity_id, model.payload["dialogue"].label("line")).where(
                        model.entity_id.in_(identities),
                        model.locale_id == locale_id,
                        model.snapshot_key == snapshot,
                    )
                )
            )
            .mappings()
            .all()
            if identities
            else []
        )
        by_id = {row["entity_id"]: row["line"] for row in lines}
        if any(not isinstance(by_id.get(identity), dict) for identity in identities):
            raise entities._unready()
        results = [by_id[identity] for identity in identities]
        if _full_dialogue:
            from wuwa_story.read_models.readers import provenance

            results = await provenance.hydrate_lines(session, results, locale_id, snapshot)
            return {
                "query": q,
                "locale": locale,
                "game_version": game_version,
                "total_returned": len(results),
                "offset": offset,
                "limit": limit,
                "results": results,
            }
        return {
            "query": q,
            "scope": "dialogue",
            "snapshot_version": game_version,
            "locale": locale,
            "filters": {"character": character, "quest_id": quest_id},
            "sort": {"by": "authored_order", "order": "asc"},
            "limit": limit,
            "offset": offset,
            "has_more": len(results) == limit,
            "results": [_compact_line(line) for line in results],
        }
    matches = union_all(*queries).subquery("search_matches")
    key = matches.c.sort_title if sort_by == "name" else matches.c.score
    order = key.asc() if sort_order == "asc" else key.desc()
    rows = (
        (
            await session.execute(
                select(matches)
                .order_by(order, matches.c.kind, matches.c.entity_id)
                .offset(offset)
                .limit(limit)
            )
        )
        .mappings()
        .all()
    )
    entries = [SimpleNamespace(**row) for row in rows]
    results = [
        {**catalog.catalog_entry(entry, entry.kind), "alias": entry.alias, "score": entry.score}
        for entry in entries
    ]
    return {
        "query": q,
        "scope": "entities",
        "mode": "lexical",
        "semantic_available": False,
        "snapshot_version": game_version,
        "filters": {"categories": category or [], "locale": locale},
        "sort": {"by": sort_by, "order": sort_order},
        "limit": limit,
        "offset": offset,
        "has_more": len(results) == limit,
        "results": results,
    }


async def dialogue_search_page(
    session, q, character=None, quest_id=None, locale="en", game_version=None, limit=50, offset=0
):
    return entities.ProjectedPayload(
        await search_page(
            session,
            q,
            ["dialogue"],
            "dialogue",
            locale,
            game_version,
            limit=limit,
            offset=offset,
            character=character,
            quest_id=quest_id,
            _full_dialogue=True,
        )
    )

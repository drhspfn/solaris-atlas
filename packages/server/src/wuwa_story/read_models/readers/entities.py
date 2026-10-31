"""Exact projection lookup, coverage and stored entity/page-section reads."""

from collections import Counter
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import case, literal, or_, select, union_all

from wuwa_story.db.models.i18n import Locale
from wuwa_story.db.repositories.read_models import ENTITY_READ_MODELS, card_columns, card_rows
from wuwa_story.urls import numeric_id

KIND_ALIASES = {
    "area": "location",
    "event": "game_event",
    "notice": "game_notice",
    "phone_message": "dialogue",
}


_NODE_KIND_ALIASES = {
    "phone_message": "dialogue",
    "area": "location",
    "talk_item": "dialogue",
    "dialogue_line": "dialogue",
    "narration": "dialogue",
    "player_choice": "dialogue",
}


_NODE_KINDS = ("character", "item", "location", "quest", "speaker", "dialogue", "cutscene")


def supports_node_projection(identity):
    """Route by explicit namespace, never by a failed lookup or missing projection.

    Unqualified identifiers and technical namespaces remain NodeExplorer
    inspection, which has its existing bounded source/provenance access.
    """
    prefix, separator, _ = str(identity).partition(":")
    return bool(separator and (prefix in _NODE_KINDS or prefix in _NODE_KIND_ALIASES))


class ProjectedPayload(dict):
    """Already-addressed public data; decorators must not hydrate the source graph."""


async def profile_page(session, kind, identity, locale="en", game_version=None):
    if kind == "quest" and numeric_id(identity) is not None:
        identity = f"quest:{identity}"
    return ProjectedPayload(
        await page_payload(
            session,
            kind,
            identity,
            locale,
            game_version or "current",
            selector="profile",
        )
    )


async def archive_page(session, identity, locale="en", game_version=None):
    return ProjectedPayload(
        await page_payload(
            session,
            "character",
            identity,
            locale,
            game_version or "current",
            selector="archive",
        )
    )


def _kind(kind):
    kind = KIND_ALIASES.get(kind, kind)
    if kind not in ENTITY_READ_MODELS:
        raise HTTPException(400, "Unknown projection kind")
    return kind


def _unready():
    return HTTPException(
        503,
        "Public read projection is not ready for this selection",
        headers={"Retry-After": "2", "Cache-Control": "no-store"},
    )


async def _coverage(session, kind, snapshot_key):
    from wuwa_story.db.models.read_refresh import ReadProjectionScope

    return await session.get(ReadProjectionScope, (kind, snapshot_key))


async def _require_scope(session, kind, snapshot_key):
    coverage = await _coverage(session, kind, snapshot_key)
    if coverage is None or getattr(coverage, "published_generation", 0) < 1:
        raise _unready()
    return coverage


def _missing(coverage):
    if coverage.status == "ready":
        raise HTTPException(404, "Entry not found in the published projection")
    raise _unready()


async def _locale_id(session, locale):
    identity = await session.scalar(select(Locale.id).where(Locale.code == locale))
    if identity is None:
        raise HTTPException(400, f"unknown locale: {locale}")
    return identity


def _ready(entry):
    if (
        entry is None
        or entry.generation < 1
        or not isinstance(entry.payload, dict)
        or not entry.payload
    ):
        raise _unready()
    return entry


def _ready_card(entry):
    if entry is None or entry.generation < 1 or not isinstance(entry.card, dict) or not entry.card:
        raise _unready()
    return entry


def _identity_statement(model, identity, locale_id, snapshot_key, columns):
    value = str(identity)
    identifier = numeric_id(value)
    statement = select(*columns).where(
        model.locale_id == locale_id,
        model.snapshot_key == snapshot_key,
    )
    if identifier is not None:
        return statement.where(model.entity_id == identifier).limit(1)
    return (
        statement.where(or_(model.canonical_key == value, model.slug == value))
        .order_by(
            case((model.canonical_key == value, 0), else_=1),
            model.entity_id,
        )
        .limit(2)
    )


def _resolve(entries, identity):
    entry = (
        entries[0]
        if entries and entries[0].canonical_key == str(identity)
        else entries[0]
        if len(entries) == 1
        else None
    )
    return _ready_card(entry) if entry is not None and hasattr(entry, "card") else _ready(entry)


async def projection_entry(session, kind, identity, locale="en", snapshot_key="current"):
    """Resolve identity and card metadata without fetching the full page payload."""
    kind = _kind(kind)
    coverage = await _require_scope(session, kind, snapshot_key)
    model = ENTITY_READ_MODELS[kind]
    statement = _identity_statement(
        model, identity, await _locale_id(session, locale), snapshot_key, card_columns(model)
    )
    entries = await card_rows(session, statement)
    if not entries:
        _missing(coverage)
    return _resolve(entries, identity)


async def page_payload(
    session, kind, identity, locale="en", snapshot_key="current", *, selector="profile"
):
    """Select one JSON section in PostgreSQL; selector=None explicitly requests the whole payload."""
    if selector not in {
        None,
        "profile",
        "archive",
        "node",
        "related",
        "source_record",
        "edges",
        "dialogue",
        "transcript",
        "continuity",
        "media",
    }:
        raise HTTPException(400, "Unknown projection payload selector")
    kind = _kind(kind)
    coverage = await _require_scope(session, kind, snapshot_key)
    model = ENTITY_READ_MODELS[kind]
    content = model.payload if selector is None else model.payload[selector]
    statement = _identity_statement(
        model,
        identity,
        await _locale_id(session, locale),
        snapshot_key,
        (model.entity_id, model.canonical_key, model.generation, content.label("payload")),
    )
    rows = (await session.execute(statement)).mappings().all()
    if not rows:
        _missing(coverage)
    if selector == "edges":
        entries = [SimpleNamespace(**{**row, "payload": {"edges": row["payload"]}}) for row in rows]
        entry = _resolve(entries, identity)
        edges = entry.payload["edges"]
        if not isinstance(edges, list):
            raise _unready()
        return edges
    entry = _resolve([SimpleNamespace(**row) for row in rows], identity)
    return entry.payload


async def _scope_entries(
    session, kind, locale="en", snapshot_key="current", selector=None, *, card=False
):
    kind = _kind(kind)
    await _require_scope(session, kind, snapshot_key)
    model = ENTITY_READ_MODELS[kind]
    locale_id = await _locale_id(session, locale)
    source = model.card if card else model.payload
    content = source if selector is None else source[selector]
    statement = select(model.entity_id, model.generation, content.label("payload")).where(
        model.locale_id == locale_id,
        model.snapshot_key == snapshot_key,
    )
    rows = (await session.execute(statement)).mappings().all()
    return [_ready(SimpleNamespace(**row)) for row in rows]


def _public(payload):
    internal = {"card", "catalog", "node", "related", "edges", "source_record"}
    return {key: value for key, value in payload.items() if key not in internal}


async def node_projection(session, identity, locale="en", snapshot_key="current"):
    value = str(identity)
    prefix = value.partition(":")[0]
    kind = _NODE_KIND_ALIASES.get(prefix, prefix)
    if kind in _NODE_KINDS:
        entry = await projection_entry(session, kind, value, locale, snapshot_key)
    else:
        locale_id = await _locale_id(session, locale)
        queries = []
        for candidate in _NODE_KINDS:
            model = ENTITY_READ_MODELS[candidate]
            match = (
                model.entity_id == numeric_id(value)
                if numeric_id(value) is not None
                else or_(
                    model.canonical_key == value,
                    model.slug == value,
                )
            )
            queries.append(
                select(literal(candidate).label("kind"), model.entity_id).where(
                    model.locale_id == locale_id,
                    model.snapshot_key == snapshot_key,
                    match,
                )
            )
        rows = (await session.execute(select(union_all(*queries).subquery()).limit(2))).all()
        if len(rows) != 1:
            if not rows:
                scopes = [
                    await _coverage(session, candidate, snapshot_key) for candidate in _NODE_KINDS
                ]
                if all(
                    scope and scope.status == "ready" and scope.published_generation > 0
                    for scope in scopes
                ):
                    raise HTTPException(404, "Node not found in the published projections")
            raise _unready()
        kind, entity_id = rows[0]
        entry = await projection_entry(session, kind, entity_id, locale, snapshot_key)
    entry.kind = kind
    return entry


async def node_section(
    session, identity, selector, locale="en", game_version=None, *, direction="both", limit=100
):
    snapshot = game_version or "current"
    entry = await node_projection(session, identity, locale, snapshot)
    if selector == "narrative":
        if entry.kind != "dialogue":
            raise _unready()
        return _public(
            await page_payload(
                session, entry.kind, entry.entity_id, locale, snapshot, selector=None
            )
        )
    if selector == "edges":
        edges = await page_payload(
            session, entry.kind, entry.entity_id, locale, snapshot, selector="edges"
        )
        return [
            edge
            for edge in edges
            if direction == "both"
            or (
                edge["from_node_id"] == entry.entity_id
                if direction == "out"
                else edge["to_node_id"] == entry.entity_id
            )
        ][:limit]
    if selector not in {"node", "source_record"}:
        # Raw edge IDs and relation-type IDs cannot be fabricated from display links.
        raise _unready()
    data = await page_payload(
        session, entry.kind, entry.entity_id, locale, snapshot, selector=selector
    )
    if selector == "source_record":
        from wuwa_story.read_models.readers import provenance

        return await provenance.source_record(session, data)
    return data


async def node_related_page(
    session,
    identity,
    direction="both",
    relation=None,
    category=None,
    source_file=None,
    include_evidence=False,
    locale="en",
    limit=100,
    offset=0,
):
    if direction not in {"in", "out", "both"}:
        raise HTTPException(422, "Invalid relation direction")
    entry = await node_projection(session, identity, locale)
    data = await page_payload(session, entry.kind, entry.entity_id, locale, selector="related")
    results = data.get("results")
    if not isinstance(results, list):
        raise _unready()
    evidence = include_evidence or category == "source_reference" or source_file is not None
    selected = []
    evidence_rows = []
    for item in results:
        node = item.get("node", {})
        if (
            direction != "both"
            and item.get("direction") != {"in": "incoming", "out": "outgoing"}[direction]
            and node.get("id") != entry.entity_id
        ):
            continue
        if relation and item.get("relation") != relation:
            continue
        if node.get("type") == "source_reference":
            evidence_rows.append(item)
        if category and node.get("type") != category:
            continue
        if not evidence and node.get("type") == "source_reference":
            continue
        if source_file:
            actual_file = item.get(
                "_source_file", (item.get("provenance") or {}).get("source_file")
            )
            if actual_file is None and "_source_file" not in item:
                raise _unready()
            if actual_file != source_file:
                continue
        selected.append(item)
    source_evidence = data.get("source_evidence", [])
    if all("_source_file" in item for item in evidence_rows):
        from wuwa_story.api.source_labels import source_family_label

        counts = Counter((item["_source_file"], item["relation"]) for item in evidence_rows)
        source_evidence = [
            {
                "label": source_family_label(file),
                "source_file": file,
                "relation": key,
                "record_count": count,
            }
            for (file, key), count in sorted(
                counts.items(),
                key=lambda pair: (-pair[1], pair[0][0] is None, pair[0][0] or "", pair[0][1]),
            )
        ]
    visible = selected[offset : offset + limit]
    return {
        **data,
        "direction": direction,
        "relation_filter": relation,
        "category_filter": category,
        "source_file_filter": source_file,
        "include_evidence": evidence,
        "locale": locale,
        "limit": limit,
        "offset": offset,
        "summary": (
            f"Found {len(visible)} linked entities."
            if visible
            else "No linked entity neighbors were found. Source records below are raw game configuration references, not inferred character or story relationships."
        ),
        "source_evidence": source_evidence,
        "results": [
            {key: value for key, value in item.items() if key != "_source_file"} for item in visible
        ],
    }


async def story_map_page(session, locale="en", game_version=None, quest_type_id=0):
    if type(quest_type_id) is not int or not 0 <= quest_type_id < 9223372036854775807:
        raise HTTPException(400, "quest_type_id must be a nonnegative integer")
    return ProjectedPayload(
        _public(
            await page_payload(
                session,
                "story_map",
                quest_type_id + 1,
                locale,
                game_version or "current",
                selector=None,
            )
        )
    )


async def quest_section(session, identity, locale="en", game_version=None, selector="media"):
    if numeric_id(identity) is not None:
        identity = f"quest:{identity}"
    return ProjectedPayload(
        await page_payload(
            session, "quest", identity, locale, game_version or "current", selector=selector
        )
    )


async def transcript_page(
    session,
    identity,
    character=None,
    q=None,
    locale="en",
    game_version=None,
    limit=500,
    offset=0,
    focus_line=None,
    include_raw=True,
):
    data = await quest_section(session, identity, locale, game_version, "transcript")
    lines = data.get("lines")
    if not isinstance(lines, list):
        raise _unready()
    if character:
        snapshot = game_version or "current"
        owner = await projection_entry(session, "character", character, locale, snapshot)
        model = ENTITY_READ_MODELS["dialogue"]
        await _require_scope(session, "dialogue", snapshot)
        locale_id = await _locale_id(session, locale)
        identities = [line["id"] for line in lines]
        accepted = set()
        for start in range(0, len(identities), 500):
            accepted.update(
                await session.scalars(
                    select(model.canonical_key).where(
                        model.locale_id == locale_id,
                        model.snapshot_key == snapshot,
                        model.canonical_key.in_(identities[start : start + 500]),
                        model.card["character_keys"].contains([owner.canonical_key]),
                    )
                )
            )
        lines = [line for line in lines if line["id"] in accepted]
    if q:
        lines = [
            line
            for line in lines
            if q.casefold()
            in (
                line.get("text", {}).get("content") or line.get("text", {}).get("inline_text") or ""
            ).casefold()
        ]
    if focus_line:
        positions = [index for index, line in enumerate(lines) if line.get("id") == focus_line]
        if not positions:
            raise HTTPException(404, "Cited passage not found in this quest")
        offset = max(0, positions[0] - limit // 2)
    visible = lines[offset : offset + limit]
    if include_raw:
        from wuwa_story.read_models.readers import provenance

        visible = await provenance.hydrate_lines(
            session, visible, await _locale_id(session, locale), game_version or "current"
        )
    else:
        visible = [
            {key: value for key, value in line.items() if key != "raw_refs"} for line in visible
        ]
    return ProjectedPayload(
        {
            **data,
            "lines": visible,
            "lines_returned": len(visible),
            "offset": offset,
            "limit": limit,
            "filters": {"character": character, "text": q, "locale": locale},
        }
    )

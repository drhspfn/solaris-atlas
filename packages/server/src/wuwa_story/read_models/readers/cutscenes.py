"""Cutscene playback and authored-order archive projection reads."""

from fastapi import HTTPException
from sqlalchemy import func, select, tuple_

from wuwa_story.db.repositories.read_models import ENTITY_READ_MODELS
from wuwa_story.read_models.readers import entities


async def cutscene_detail(session, name, game_version=None, locale="en"):
    identity = name if name.startswith("cutscene:") else f"cutscene:{name}"
    return entities._public(
        await entities.page_payload(
            session, "cutscene", identity, locale, game_version or "current", selector=None
        )
    )


async def cutscene_catalog(
    session, game_version=None, locale="en", q="", playable=True, limit=24, offset=0, cursor=None
):
    from wuwa_story.api.pagination import cursor_page, cursor_scope, decode_cursor

    model = ENTITY_READ_MODELS["cutscene"]
    snapshot = game_version or "current"
    await entities._require_scope(session, "cutscene", snapshot)
    locale_id = await entities._locale_id(session, locale)
    scope_filters = (model.locale_id == locale_id, model.snapshot_key == snapshot)
    catalog = model.card["catalog"]
    values = catalog["cursor_values"]
    order = [values[index].as_integer() for index in range(5)] + [
        values[5].as_string(),
        values[6].as_integer(),
    ]
    # Read distinct compact scope metadata, never playback contexts or page bodies.
    metadata = (
        (
            await session.execute(
                select(
                    order[0].label("revision"),
                    catalog["release_id"].label("release_id"),
                    catalog["imported_game_versions"].label("versions"),
                    (model.generation < 1).label("invalid"),
                )
                .where(*scope_filters)
                .distinct()
            )
        )
        .mappings()
        .all()
    )
    if any(
        row["revision"] is None or not isinstance(row["versions"], list) or row["invalid"]
        for row in metadata
    ):
        raise entities._unready()
    revisions = {row["revision"] for row in metadata}
    releases = {row["release_id"] for row in metadata}
    if len(revisions) > 1 or len(releases) > 1:
        raise entities._unready()
    scope = cursor_scope(
        "cutscenes", next(iter(releases), None) if game_version else None, q.strip(), playable
    )
    try:
        position = (
            decode_cursor(cursor, scope, (int, int, int, int, int, str, int)) if cursor else None
        )
        if position and revisions and position[0] != next(iter(revisions)):
            raise HTTPException(
                409, {"code": "cursor_expired", "message": "Scene order changed; reload the list"}
            )
        if position and offset:
            raise ValueError("Cursor and offset cannot be combined")
    except ValueError as error:
        raise HTTPException(400, "Invalid cursor for these filters") from error
    filters = list(scope_filters)
    if playable:
        filters.append(catalog["playable"].as_boolean().is_(True))
    if q.strip():
        text = (
            func.coalesce(catalog["item"]["title"].as_string(), "")
            + " "
            + func.coalesce(catalog["item"]["name"].as_string(), "")
        )
        filters.append(text.icontains(q.strip(), autoescape=True))
    total = await session.scalar(select(func.count()).select_from(model).where(*filters))
    if position:
        filters.append(tuple_(*order[1:]) < tuple_(*position[1:]))
    statement = (
        select(model.generation, catalog["item"].label("item"), values.label("cursor_values"))
        .where(*filters)
        .order_by(*(column.desc() for column in order[1:]))
        .offset(offset)
        .limit(limit + 1)
    )
    rows = (await session.execute(statement)).mappings().all()
    if any(
        row["generation"] < 1
        or not isinstance(row["item"], dict)
        or not isinstance(row["cursor_values"], list)
        or len(row["cursor_values"]) != 7
        for row in rows
    ):
        raise entities._unready()
    selected, next_cursor = cursor_page(rows, limit, scope, lambda row: row["cursor_values"])
    versions = {version for row in metadata for version in row["versions"]}
    from wuwa_story.story.domain import version_key

    return {
        "game_version": game_version,
        "imported_game_versions": sorted(versions, key=version_key, reverse=True),
        "total": total,
        "limit": limit,
        "offset": offset,
        "next_cursor": next_cursor,
        "items": [row["item"] for row in selected],
    }

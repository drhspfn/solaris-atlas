"""Projection-only public reads; missing snapshots never fall back to source data."""

from sqlalchemy import func, select

from wuwa_story.db.models.browse import (
    CharacterRead,
    CutsceneRead,
    DialogueRead,
    EntityReadRelation,
    GameEventRead,
    GameNoticeRead,
    ItemRead,
    LocationRead,
    QuestRead,
    SpeakerRead,
)

ENTITY_READ_MODELS = {
    "character": CharacterRead,
    "item": ItemRead,
    "location": LocationRead,
    "quest": QuestRead,
    "game_event": GameEventRead,
    "game_notice": GameNoticeRead,
    "speaker": SpeakerRead,
    "dialogue": DialogueRead,
    "cutscene": CutsceneRead,
}


def _model(kind):
    try:
        return ENTITY_READ_MODELS[kind]
    except KeyError:
        raise ValueError(f"Unknown read-model kind: {kind}") from None


async def read_entry(session, kind, entity_id, locale_id, snapshot_key="current"):
    """Return the exact projection row, or None when it has not been published."""
    model = _model(kind)
    return await session.scalar(
        select(model).where(
            model.entity_id == entity_id,
            model.locale_id == locale_id,
            model.snapshot_key == snapshot_key,
        )
    )


async def browse_entries(session, kind, locale_id, snapshot_key="current", *, limit=24, offset=0):
    """Return (total, rows) within one kind, locale and published snapshot."""
    model = _model(kind)
    if not 1 <= limit <= 100 or offset < 0:
        raise ValueError("limit must be between 1 and 100 and offset must be nonnegative")
    filters = (model.locale_id == locale_id, model.snapshot_key == snapshot_key)
    total = await session.scalar(select(func.count()).select_from(model).where(*filters)) or 0
    entries = list(
        await session.scalars(
            select(model)
            .where(*filters)
            .order_by(model.sort_title, model.entity_id)
            .offset(offset)
            .limit(limit)
        )
    )
    return total, entries


async def read_relations(
    session,
    owner_kind,
    owner_id,
    snapshot_key="current",
    *,
    related_kind=None,
    relation_type=None,
    limit=100,
    offset=0,
):
    _model(owner_kind)
    if related_kind is not None:
        _model(related_kind)
    if not 1 <= limit <= 100 or offset < 0:
        raise ValueError("limit must be between 1 and 100 and offset must be nonnegative")
    relation = EntityReadRelation
    statement = select(relation).where(
        relation.owner_kind == owner_kind,
        relation.owner_id == owner_id,
        relation.snapshot_key == snapshot_key,
    )
    if related_kind is not None:
        statement = statement.where(relation.related_kind == related_kind)
    if relation_type is not None:
        statement = statement.where(relation.relation_type == relation_type)
    return list(
        await session.scalars(
            statement.order_by(relation.related_kind, relation.related_id, relation.relation_type)
            .offset(offset)
            .limit(limit)
        )
    )

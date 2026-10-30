"""Atomic publication of prepared public entries and their source-backed links."""

from sqlalchemy import delete, func
from sqlalchemy.dialects.postgresql import insert

from wuwa_story.db.models.browse import EntityReadRelation
from wuwa_story.db.repositories.read_models import ENTITY_READ_MODELS


async def publish_entry(session, kind, entry, relations=()):
    """The caller owns the transaction; an older rebuild cannot replace newer data."""
    if kind not in ENTITY_READ_MODELS:
        raise ValueError("Unknown owner entity kind")
    model = ENTITY_READ_MODELS[kind]
    required = {"entity_id", "locale_id", "snapshot_key", "generation", "title", "sort_title", "payload"}
    if not required <= entry.keys():
        raise ValueError("Incomplete public read projection")
    if entry["generation"] < 1:
        raise ValueError("Projection generation must be positive")
    merged = {}
    for relation in relations:
        if not {"related_kind", "related_id", "relation_type"} <= relation.keys():
            raise ValueError("Incomplete projected relationship")
        if relation["related_kind"] not in ENTITY_READ_MODELS:
            raise ValueError("Unknown related entity kind")
        key = (relation["related_kind"], relation["related_id"], relation["relation_type"])
        if key in merged and merged[key] != relation:
            raise ValueError("Conflicting evidence for one projected relationship")
        merged[key] = relation
    identity = {"entity_id", "locale_id", "snapshot_key"}
    statement = insert(model).values(**entry)
    statement = statement.on_conflict_do_update(
        index_elements=[model.entity_id, model.locale_id, model.snapshot_key],
        set_={**{key: getattr(statement.excluded, key) for key in entry if key not in identity},
              "updated_at": func.now()},
        where=model.generation <= statement.excluded.generation,
    ).returning(model.entity_id)
    accepted = await session.scalar(statement)
    if accepted is None:
        return False
    owner = {"owner_kind": kind, "owner_id": entry["entity_id"], "snapshot_key": entry["snapshot_key"]}
    await session.execute(delete(EntityReadRelation).where(
        EntityReadRelation.owner_kind == kind,
        EntityReadRelation.owner_id == entry["entity_id"],
        EntityReadRelation.snapshot_key == entry["snapshot_key"],
    ))
    if merged:
        await session.execute(insert(EntityReadRelation), [{**relation, **owner} for relation in merged.values()])
    return True

"""Atomic publication of prepared public entries and their source-backed links."""

from sqlalchemy import delete, func
from sqlalchemy.dialects.postgresql import insert

from wuwa_story.db.models.browse import EntityReadRelation
from wuwa_story.db.repositories.read_models import ENTITY_READ_MODELS


async def publish_entries(session, kind, entries, relations=()):
    """Atomically upsert all prepared locale entries and optional relations in one round-trip."""
    if kind not in ENTITY_READ_MODELS:
        raise ValueError("Unknown owner entity kind")
    entries = list(entries)
    if not entries:
        return True
    model = ENTITY_READ_MODELS[kind]
    required = {"entity_id", "locale_id", "snapshot_key", "generation", "title", "sort_title", "card", "searchable_text", "payload"}
    for entry in entries:
        if not required <= entry.keys():
            raise ValueError("Incomplete public read projection")
        if entry["generation"] < 1:
            raise ValueError("Projection generation must be positive")
    merged = {}
    if relations is not None:
        for relation in relations or ():
            if not {"related_kind", "related_id", "relation_type"} <= relation.keys():
                raise ValueError("Incomplete projected relationship")
            if relation["related_kind"] not in ENTITY_READ_MODELS:
                raise ValueError("Unknown related entity kind")
            key = (relation["related_kind"], relation["related_id"], relation["relation_type"])
            if key in merged and merged[key] != relation:
                raise ValueError("Conflicting evidence for one projected relationship")
            merged[key] = relation
    identity = {"entity_id", "locale_id", "snapshot_key"}
    update_keys = [key for key in entries[0] if key not in identity]
    statement = insert(model).values(**entries[0]) if len(entries) == 1 else insert(model).values(entries)
    statement = statement.on_conflict_do_update(
        index_elements=[model.entity_id, model.locale_id, model.snapshot_key],
        set_={**{key: getattr(statement.excluded, key) for key in update_keys},
              "updated_at": func.now()},
        where=model.generation <= statement.excluded.generation,
    ).returning(model.entity_id)
    if len(entries) == 1:
        accepted_one = await session.scalar(statement)
        if accepted_one is None:
            return False
    else:
        accepted = list(await session.scalars(statement))
        if len(accepted) != len(entries):
            return False
    if relations is None:
        return True
    owner = {"owner_kind": kind, "owner_id": entries[0]["entity_id"], "snapshot_key": entries[0]["snapshot_key"]}
    await session.execute(delete(EntityReadRelation).where(
        EntityReadRelation.owner_kind == kind,
        EntityReadRelation.owner_id == owner["owner_id"],
        EntityReadRelation.snapshot_key == owner["snapshot_key"],
    ))
    if merged:
        await session.execute(insert(EntityReadRelation), [{**relation, **owner} for relation in merged.values()])
    return True


async def publish_entry(session, kind, entry, relations=()):
    """The caller owns the transaction; an older rebuild cannot replace newer data."""
    return await publish_entries(session, kind, [entry], relations=relations)

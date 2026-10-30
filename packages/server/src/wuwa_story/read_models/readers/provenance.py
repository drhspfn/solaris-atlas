"""Explicit raw inspection only: deduplicated primary-key resources, no graph joins."""

from sqlalchemy import select

from wuwa_story.db.models.core import QuestAction
from wuwa_story.db.models.raw import SourceRecord
from wuwa_story.db.repositories.read_models import ENTITY_READ_MODELS
from wuwa_story.read_models.readers import entities


async def source_record(session, payload):
    record = payload.get("source_record")
    if record is None:
        return payload
    identity = record.get("id")
    if type(identity) is not int or identity < 1:
        raise entities._unready()
    raw = await session.scalar(select(SourceRecord.data).where(SourceRecord.id == identity))
    if raw is None:
        raise entities._unready()
    return {**payload, "source_record": {**record, "raw_record": raw}}


async def _resources(session, identities, identity_column, data_column):
    result = {}
    identities = sorted(identities)
    for start in range(0, len(identities), 500):
        result.update(
            (
                await session.execute(
                    select(identity_column, data_column).where(
                        identity_column.in_(identities[start : start + 500]),
                    )
                )
            ).all()
        )
    if set(identities) - result.keys():
        raise entities._unready()
    return result


async def hydrate_lines(session, lines, locale_id, snapshot_key):
    """Reconstruct legacy include_raw responses after paging compact projections."""
    if not lines:
        return []
    model = ENTITY_READ_MODELS["dialogue"]
    identities = [line["id"] for line in lines]
    await entities._require_scope(session, "dialogue", snapshot_key)
    refs = {
        line["id"]: line["raw_refs"] for line in lines if isinstance(line.get("raw_refs"), dict)
    }
    missing = set(identities) - refs.keys()
    if missing:
        refs.update(
            (
                await session.execute(
                    select(model.canonical_key, model.card["raw_refs"]).where(
                        model.canonical_key.in_(missing),
                        model.locale_id == locale_id,
                        model.snapshot_key == snapshot_key,
                    )
                )
            ).all()
        )
    if any(not isinstance(refs.get(identity), dict) for identity in identities):
        raise entities._unready()
    source_ids, action_ids = set(), set()
    for value in refs.values():
        if value.get("source_record_id") is not None:
            source_ids.add(value["source_record_id"])
        if value.get("action_node_id") is not None:
            action_ids.add(value["action_node_id"])
        source_ids.update(value.get("choice_source_record_ids", {}).values())
    if any(type(identity) is not int or identity < 1 for identity in source_ids | action_ids):
        raise entities._unready()
    records = await _resources(session, source_ids, SourceRecord.id, SourceRecord.data)
    actions = await _resources(session, action_ids, QuestAction.node_id, QuestAction.params)

    def raw_provenance(value, identity):
        if value is None:
            return None
        if identity not in records:
            raise entities._unready()
        return {**value, "raw_record": records[identity]}

    hydrated = []
    for line in lines:
        reference = refs[line["id"]]
        action = line.get("action")
        if action is not None:
            if reference.get("action_node_id") not in actions:
                raise entities._unready()
            action = {**action, "params": actions[reference["action_node_id"]]}
        choices = []
        for choice in line.get("player_choices", []):
            choices.append(
                {
                    **choice,
                    "provenance": raw_provenance(
                        choice.get("provenance"),
                        reference.get("choice_source_record_ids", {}).get(choice["id"]),
                    ),
                }
            )
        hydrated.append(
            {
                **{key: value for key, value in line.items() if key != "raw_refs"},
                "action": action,
                "player_choices": choices,
                "provenance": raw_provenance(
                    line.get("provenance"), reference.get("source_record_id")
                ),
            }
        )
    return hydrated

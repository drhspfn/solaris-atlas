"""Generation guards and validation at the public projection publication boundary."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from wuwa_story.db.repositories.read_models import ENTITY_READ_MODELS
from wuwa_story.read_models.publication import publish_entry


def entry(**changes):
    return {
        "entity_id": 7,
        "locale_id": 1,
        "snapshot_key": "3.10",
        "generation": 12,
        "title": "Projected title",
        "sort_title": "projected title",
        "payload": {"complete": True},
        **changes,
    }


def relation(**changes):
    return {
        "related_kind": "quest",
        "related_id": 90,
        "relation_type": "appears_in",
        "evidence": {"source": "authored"},
        **changes,
    }


def session(accepted=7):
    return SimpleNamespace(scalar=AsyncMock(return_value=accepted), execute=AsyncMock())


@pytest.mark.asyncio
async def test_older_generation_skips_relation_deletion_and_insertion():
    database = session(accepted=None)
    assert await publish_entry(database, "character", entry(generation=11), [relation()]) is False
    database.scalar.assert_awaited_once()
    database.execute.assert_not_awaited()
    statement = database.scalar.call_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT (entity_id, locale_id, snapshot_key) DO UPDATE SET" in sql
    assert "WHERE core.character_read.generation <= excluded.generation" in sql
    assert "RETURNING core.character_read.entity_id" in sql


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", tuple(ENTITY_READ_MODELS))
async def test_accepted_publication_replaces_only_exact_owner_snapshot_and_deduplicates(kind):
    database = session()
    assert await publish_entry(database, kind, entry(), [relation(), relation()]) is True
    assert database.execute.await_count == 2
    statement = database.scalar.call_args.args[0]
    assert statement.table.fullname == ENTITY_READ_MODELS[kind].__table__.fullname
    compiled = statement.compile(dialect=postgresql.dialect())
    assert compiled.params["generation"] == 12
    assert compiled.params["payload"] == {"complete": True}
    deletion = database.execute.call_args_list[0].args[0].compile(dialect=postgresql.dialect())
    assert str(deletion).startswith("DELETE FROM core.entity_read_relation WHERE")
    assert set(deletion.params.values()) == {kind, 7, "3.10"}
    assert "owner_kind =" in str(deletion) and "owner_id =" in str(deletion)
    assert "snapshot_key =" in str(deletion)
    insertion, values = database.execute.call_args_list[1].args
    assert insertion.table.fullname == "core.entity_read_relation"
    assert values == [{**relation(), "owner_kind": kind, "owner_id": 7, "snapshot_key": "3.10"}]


@pytest.mark.asyncio
async def test_accepted_empty_relation_set_clears_previous_relations():
    database = session()
    assert await publish_entry(database, "item", entry()) is True
    database.execute.assert_awaited_once()
    assert str(database.execute.call_args.args[0]).startswith(
        "DELETE FROM core.entity_read_relation"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    [
        "entity_id",
        "locale_id",
        "snapshot_key",
        "generation",
        "title",
        "sort_title",
        "payload",
    ],
)
async def test_missing_entry_fields_are_rejected_before_any_write(field):
    database = session()
    incomplete = entry()
    del incomplete[field]
    with pytest.raises(ValueError, match="Incomplete"):
        await publish_entry(database, "character", incomplete)
    database.scalar.assert_not_awaited()
    database.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("generation", [0, -1])
async def test_nonpositive_generation_is_rejected_before_any_write(generation):
    database = session()
    with pytest.raises(ValueError, match="positive"):
        await publish_entry(database, "character", entry(generation=generation))
    database.scalar.assert_not_awaited()
    database.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_owner_kind_is_rejected_consistently_before_any_write():
    database = session()
    with pytest.raises(ValueError, match="[Uu]nknown"):
        await publish_entry(database, "unknown", entry())
    database.scalar.assert_not_awaited()
    database.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_relation",
    [
        relation(related_kind="unknown"),
        {key: value for key, value in relation().items() if key != "related_kind"},
        {key: value for key, value in relation().items() if key != "related_id"},
        {key: value for key, value in relation().items() if key != "relation_type"},
    ],
)
async def test_invalid_relation_is_rejected_before_upsert_or_deletion(invalid_relation):
    database = session()
    with pytest.raises(ValueError):
        await publish_entry(database, "character", entry(), [invalid_relation])
    database.scalar.assert_not_awaited()
    database.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_conflicting_relation_evidence_is_rejected_before_any_write():
    database = session()
    with pytest.raises(ValueError, match="Conflicting"):
        await publish_entry(
            database,
            "character",
            entry(),
            [
                relation(),
                relation(evidence={"source": "different"}),
            ],
        )
    database.scalar.assert_not_awaited()
    database.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_conflict_update_explicitly_refreshes_updated_at():
    database = session()
    assert await publish_entry(database, "character", entry()) is True
    compiled = database.scalar.call_args.args[0].compile(dialect=postgresql.dialect())
    update = str(compiled).split("DO UPDATE SET", 1)[1].split(" WHERE ", 1)[0]
    assert "updated_at =" in update

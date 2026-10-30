"""Projection relations are complete, release-evidenced source facts, not guesses."""

import json
from types import SimpleNamespace as Row
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from wuwa_story.db.models.game_events import GameEvent
from wuwa_story.db.models.graph import Node, NodeType
from wuwa_story.db.models.notices import GameNotice
from wuwa_story.read_models import builders
from wuwa_story.read_models import source_relations as relations
from wuwa_story.read_models import source_sections as sections
from wuwa_story.read_models.context import SOURCE_BUILD


def sql(statement):
    return str(
        statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )


@pytest.fixture
def context(monkeypatch):
    node = Row(id=7, type_id=1, canonical_key="character:70")
    node_type = Row(key="character")
    records = {(Node, 7): node, (NodeType, 1): node_type}
    session = Row(
        info={},
        get=AsyncMock(side_effect=lambda model, key: records.get((model, key))),
        execute=AsyncMock(return_value=[]),
        scalars=AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(sections.service, "_release_id", AsyncMock(return_value=12))
    monkeypatch.setattr(
        relations.shared, "_character_progression_materials", AsyncMock(return_value=[])
    )
    return Row(session=session, records=records, node=node, node_type=node_type)


@pytest.mark.asyncio
async def test_character_relations_merge_evidence_and_keep_dialogue_quest_item_provenance(context):
    edge = Row(id=20, from_node_id=4, to_node_id=7, basis="exact_join")
    target = Row(id=4, canonical_key="speaker:40")
    evidence = [
        Row(
            release_id=12,
            source_record_id=identity,
            source_file_path="speaker.json",
            source_raw_path=f"$[{identity}]",
            explanation="exact asset crosswalk",
        )
        for identity in (3, 5)
    ]
    context.session.execute.side_effect = [
        [(edge, target, "speaker", "references_character", proof) for proof in evidence],
        [(101, 4, 9, 33, 20), (102, 4, 9, 34, 20)],
        [(88, 9)],
        [],
    ]
    relations.shared._character_progression_materials.return_value = [
        {
            "item": {"id": 55},
            "item_id": 500,
            "kind": "character_ascension",
            "required_count": 4,
            "level_cap": 40,
            "source": {
                "file": "breach.json",
                "row": 2,
                "raw_path": "$[2].BreachConsume[0]",
                "basis": "exact group ID",
            },
        }
    ]
    rows = await builders.build_relations(context.session, "character", 7, "3.10")
    indexed = {
        (row["related_kind"], row["related_id"], row["relation_type"]): row["evidence"][
            "source_facts"
        ]
        for row in rows
    }
    incoming = indexed[("speaker", 4, "references_character")]
    assert len(incoming) == 2 and all(fact["direction"] == "incoming" for fact in incoming)
    assert incoming[0]["from_node_id"] == 4 and incoming[0]["to_node_id"] == 7
    assert {fact["source_record_id"] for fact in incoming} == {3, 5}
    assert indexed[("dialogue", 101, "has_dialogue")][0] == {
        "evidence_type": "exact_crosswalk_dialogue",
        "release_id": 12,
        "crosswalk_edge_id": 20,
        "speaker_node_id": 4,
        "state_node_id": 9,
        "source_record_id": 33,
    }
    quest = indexed[("quest", 88, "has_dialogue_in_quest")][0]
    assert quest["dialogue_node_ids"] == [101, 102] and quest["release_id"] == 12
    item = indexed[("item", 55, "uses_progression_item")][0]
    assert item["game_item_id"] == 500 and item["source"]["raw_path"] == "$[2].BreachConsume[0]"
    assert not any(row["related_kind"] == "character" for row in rows)
    json.dumps(rows)
    direct_sql, dialogue_sql, quest_sql, _co_presence_sql = [
        sql(call.args[0]) for call in context.session.execute.await_args_list
    ]
    assert "edge_evidence.release_id = 12" in direct_sql
    assert "'semantic'" not in direct_sql and "'inference'" not in direct_sql
    assert "references_character" in dialogue_sql and "edge.basis = 'exact_join'" in dialogue_sql
    assert "source_record.release_id = 12" in dialogue_sql
    assert "edge_evidence.release_id = 12" in quest_sql
    assert "LIMIT" not in direct_sql and "LIMIT" not in dialogue_sql


@pytest.mark.asyncio
async def test_unmapped_character_does_not_gain_relations_from_co_presence_or_names(context):
    context.session.execute.side_effect = [[], []]
    assert await builders.build_relations(context.session, "character", 7) == []
    relations.shared._character_progression_materials.assert_awaited_once_with(
        context.session, 70, "en", 12
    )


@pytest.mark.asyncio
async def test_all_direct_source_facts_are_kept_beyond_public_limits(context):
    context.node_type.key = "item"
    rows = []
    for identity in range(700):
        rows.append(
            (
                Row(
                    id=identity,
                    from_node_id=7,
                    to_node_id=1000 + identity,
                    basis="explicit_reference",
                ),
                Row(id=1000 + identity, canonical_key=f"quest:{identity}"),
                "quest",
                "references_quest",
                Row(
                    release_id=12,
                    source_record_id=identity,
                    source_file_path="items.json",
                    source_raw_path=f"$[{identity}]",
                    explanation=None,
                ),
            )
        )
    context.session.execute.return_value = rows
    result = await builders.build_relations(context.session, "item", 7)
    assert len(result) == 700
    assert "LIMIT" not in sql(context.session.execute.await_args.args[0])


@pytest.mark.asyncio
async def test_event_rewards_use_item_node_identity_and_exact_selected_runs(context):
    context.records[(GameEvent, 7)] = Row(source=sections.CLIENT_SOURCE, rewards=[{"id": 900}])
    context.session.scalars.return_value = [Row(id=42, rewards=[{"id": 500, "count": 3}])]
    context.session.execute.return_value = [(500, 55)]
    result = await builders.build_relations(context.session, "event", 7, "3.10")
    assert result[0]["related_id"] == 55
    assert result[0]["relation_type"] == "event_reward"
    fact = result[0]["evidence"]["source_facts"][0]
    assert fact["game_item_id"] == 500 and fact["occurrence_id"] == 42
    assert "game_version = '3.10'" in sql(context.session.scalars.await_args.args[0])
    assert 900 not in [item["evidence"]["source_facts"][0]["game_item_id"] for item in result]


@pytest.mark.asyncio
async def test_notice_records_import_association_without_claiming_authored_relationship(context):
    context.records[(GameNotice, 7)] = Row(related_event_ids=[4, 5], revision="recorded-hash")
    context.session.scalars.return_value = [4]
    result = await builders.build_relations(context.session, "notice", 7)
    assert result == [
        {
            "related_kind": "game_event",
            "related_id": 4,
            "relation_type": "notice_event_association",
            "evidence": {
                "source_facts": [
                    {
                        "evidence_type": "stored_import_association",
                        "notice_id": 7,
                        "notice_revision": "recorded-hash",
                        "field": "related_event_ids",
                    }
                ]
            },
        }
    ]


@pytest.mark.asyncio
async def test_missing_entity_returns_empty_complete_relation_set_for_atomic_deletion(context):
    context.records[(Node, 7)] = None
    assert await builders.build_relations(context.session, "character", 7) == []
    context.session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_relation_builder_enters_source_context_and_restores_caches_on_error(context):
    context.session.info["story_node_labels"] = {"stale": True}

    async def failure(*args):
        assert SOURCE_BUILD.get() is True
        assert "story_node_labels" not in context.session.info
        raise RuntimeError("source failure")

    context.session.execute.side_effect = failure
    with pytest.raises(RuntimeError, match="source failure"):
        await builders.build_relations(context.session, "character", 7)
    assert SOURCE_BUILD.get() is False
    assert context.session.info == {"story_node_labels": {"stale": True}}


@pytest.mark.asyncio
async def test_shared_state_is_projected_only_as_co_presence_with_both_line_sources(context):
    context.session.execute.side_effect = [
        [],
        [(101, 4, 9, 33, 20)],
        [],
        [(16, 9, 202, 44, 30)],
    ]
    rows = await builders.build_relations(context.session, "character", 7)
    shared = [row for row in rows if row["related_kind"] == "character"]
    assert len(shared) == 1 and shared[0]["related_id"] == 16
    assert shared[0]["relation_type"] == "co_present_in_flow_state"
    fact = shared[0]["evidence"]["source_facts"][0]
    assert fact["evidence_type"] == "co_presence_only"
    assert fact["owner_dialogue_node_ids"] == [101]
    assert fact["other_dialogue_node_ids"] == [202]
    assert fact["other_source_record_ids"] == [44]
    assert fact["other_crosswalk_edge_ids"] == [30]
    query = sql(context.session.execute.await_args.args[0])
    assert "source_record.release_id = 12" in query
    assert "edge_evidence.release_id = 12" in query
    assert "exact_join" in query

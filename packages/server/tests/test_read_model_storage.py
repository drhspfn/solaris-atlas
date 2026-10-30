"""Durable projections retain exact provenance pointers instead of source blobs."""

import json
from types import SimpleNamespace as Row
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects import postgresql

from wuwa_story.read_models import source_sections as sections


def test_shared_two_mib_source_record_is_not_repeated_in_projection_json():
    blob = {"TalkItems": [{"text": "x" * (2 * 1024 * 1024)}]}
    payload = {
        "source_record": {
            "source_record": {
                "id": 900,
                "source_file": "flow.json",
                "raw_path": "$[3]",
                "raw_record": blob,
            }
        },
        "node": {"metadata_json": {"data": blob, "source_file": "flow.json"}},
        "profile": {
            "source": {"source_record_id": 900, "raw_record": blob},
            "flow_states": [{"actions": [{"source_record_id": 900, "params": blob}]}],
        },
        "transcript": {
            "lines": [
                {
                    "id": "line:1",
                    "text": {"content": "Visible text"},
                    "raw_refs": {
                        "source_record_id": 900,
                        "action_node_id": 800,
                        "choice_source_record_ids": {"choice:1": 901},
                    },
                    "action": {"params": None},
                    "provenance": {"raw_record": blob, "source_file": "flow.json"},
                }
            ]
        },
    }
    compact = sections._compact_storage(payload)
    encoded = json.dumps(compact)
    assert len(encoded) < 1024
    assert len(json.dumps(payload)) > 8 * 1024 * 1024
    assert compact["source_record"]["source_record"]["id"] == 900
    assert compact["source_record"]["source_record"]["raw_path"] == "$[3]"
    assert compact["transcript"]["lines"][0]["raw_refs"]["choice_source_record_ids"] == {
        "choice:1": 901
    }
    assert compact["transcript"]["lines"][0]["action"]["params"] is None
    assert "transcript_raw" not in compact and "raw_record" not in encoded
    assert "raw_record" in payload["source_record"]["source_record"]


def test_compaction_preserves_interactive_branch_and_spine_parameters():
    blob = {"TalkItems": [{"text": "x" * (2 * 1024 * 1024)}]}
    blocks = [
        {
            "kind": "level_play",
            "params": {"condition": "GetItem", "count": 3},
            "branches": [{"type": "ParallelSelect", "params": {"branch": 2}}],
            "events": [
                {
                    "kind": "spine",
                    "controls": [
                        {
                            "action": "setAnimation",
                            "params": {"animation": "idle", "track": 0, "loop": True},
                        },
                        {"action": "setSkin", "params": {"skin": "winter"}},
                    ],
                    "metadata_json": {"data": {"public_control": "retain"}},
                }
            ],
        }
    ]
    playback = {"controls": [{"action": "setAnimation", "params": {"animation": "talk"}}]}
    authored_config = {
        "params": {"duration": 1},
        "raw": "authored mode",
        "raw_record": {"option": "public"},
    }
    payload = {
        "payload": {
            "transcript": {
                "story_blocks": blocks,
                "lines": [
                    {
                        "id": "line:1",
                        "raw_refs": {"source_record_id": 900, "action_node_id": 800},
                        "action": {"id": "show-talk", "index": 0, "params": blob},
                        "provenance": {"source_file": "flow.json", "raw_record": blob},
                        "illustration": {"playback": playback},
                    }
                ],
            },
            "profile": {"flow_states": [{"actions": [{"source_record_id": 900, "params": blob}]}]},
            "source_record": {"source_record": {"id": 900, "raw_path": "$[3]", "raw_record": blob}},
            "interactive_config": authored_config,
            "requirement": {"raw": blob, "source": {"source_record_id": 900, "raw_path": "$[3]"}},
        }
    }
    compact = sections._public_json(sections._compact_storage(payload))["payload"]
    assert compact["transcript"]["story_blocks"] == blocks
    assert compact["transcript"]["lines"][0]["illustration"]["playback"] == playback
    assert compact["interactive_config"] == authored_config
    assert "params" not in compact["transcript"]["lines"][0]["action"]
    assert compact["transcript"]["lines"][0]["raw_refs"]["action_node_id"] == 800
    assert compact["source_record"]["source_record"] == {"id": 900, "raw_path": "$[3]"}
    assert compact["profile"]["flow_states"][0]["actions"] == [{"source_record_id": 900}]
    assert compact["requirement"] == {"source": {"source_record_id": 900, "raw_path": "$[3]"}}
    assert len(json.dumps(compact)) < 2000


@pytest.mark.asyncio
async def test_raw_references_are_batched_exact_ids_and_cached_only_in_source_build():
    session = Row(
        info={"story_releases": {12: Row(id=12, game_version="3.10", sequence=20)}},
        execute=AsyncMock(
            side_effect=[
                [("line:1", 123, 900, 800)],
                [(123, "choice:1", 901)],
            ]
        ),
    )
    result = await sections._raw_refs(session, ["line:1", "line:1", "missing"], 12)
    assert result["line:1"] == {
        "source_record_id": 900,
        "action_node_id": 800,
        "choice_source_record_ids": {"choice:1": 901},
    }
    assert result["missing"] == {
        "source_record_id": None,
        "action_node_id": None,
        "choice_source_record_ids": {},
    }
    assert await sections._raw_refs(session, ["line:1"], 12) == {"line:1": result["line:1"]}
    assert session.execute.await_count == 2
    for call in session.execute.await_args_list:
        sql = str(
            call.args[0].compile(
                dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
            )
        )
        assert "source_record.release_id IN (12)" in sql
        assert "source_record.data" not in sql and "quest_action.params" not in sql


@pytest.mark.asyncio
async def test_retained_source_pointers_are_available_without_future_or_mismatched_action_fallback():
    # Execute the actual ID-only queries. Release IDs are deliberately not in
    # version order; canonical rows still own records from the earlier import.
    engine = create_engine("sqlite://")
    try:
        with engine.connect() as connection:
            for schema in ("graph", "core", "raw", "ontology"):
                connection.exec_driver_sql(f"ATTACH DATABASE ':memory:' AS {schema}")
            for statement in (
                "CREATE TABLE graph.node (id INTEGER PRIMARY KEY, canonical_key TEXT)",
                "CREATE TABLE raw.source_record (id INTEGER PRIMARY KEY, release_id INTEGER)",
                "CREATE TABLE core.dialogue_line (node_id INTEGER PRIMARY KEY, source_record_id INTEGER, action_node_id INTEGER)",
                "CREATE TABLE core.quest_action (node_id INTEGER PRIMARY KEY, source_record_id INTEGER)",
                "CREATE TABLE core.player_choice (node_id INTEGER PRIMARY KEY, source_record_id INTEGER)",
                "CREATE TABLE ontology.relation_type (id INTEGER PRIMARY KEY, key TEXT)",
                "CREATE TABLE graph.edge (id INTEGER PRIMARY KEY, from_node_id INTEGER, to_node_id INTEGER, relation_type_id INTEGER, layer TEXT, basis TEXT)",
                "CREATE TABLE graph.edge_evidence (id INTEGER PRIMARY KEY, edge_id INTEGER, release_id INTEGER)",
                "INSERT INTO graph.node VALUES (1,'retained'),(2,'future'),(3,'mismatch'),(4,'later_import'),(5,'choice:old'),(6,'choice:future'),(7,'choice:future_evidence')",
                "INSERT INTO raw.source_record VALUES (900,90),(901,90),(902,2),(903,13)",
                "INSERT INTO core.dialogue_line VALUES (1,900,800),(2,902,801),(3,900,802),(4,903,803)",
                "INSERT INTO core.quest_action VALUES (800,900),(801,902),(802,901),(803,903)",
                "INSERT INTO core.player_choice VALUES (5,901),(6,902),(7,901)",
                "INSERT INTO ontology.relation_type VALUES (1,'presents_choice')",
                "INSERT INTO graph.edge VALUES (1,1,5,1,'source','explicit_reference'),(2,1,6,1,'source','explicit_reference'),(3,1,7,1,'source','explicit_reference')",
                "INSERT INTO graph.edge_evidence VALUES (1,1,90),(2,2,2),(3,3,2)",
            ):
                connection.exec_driver_sql(statement)

            async def execute(statement):
                return connection.execute(statement).all()

            session = Row(
                info={
                    "story_releases": {
                        90: Row(id=90, game_version="3.9", sequence=99),
                        12: Row(id=12, game_version="3.10", sequence=20),
                        13: Row(id=13, game_version="3.10", sequence=21),
                        2: Row(id=2, game_version="3.11", sequence=1),
                    }
                },
                execute=execute,
            )
            result = await sections._raw_refs(
                session, ["retained", "future", "mismatch", "later_import"], 12
            )
            assert result["retained"] == {
                "source_record_id": 900,
                "action_node_id": 800,
                "choice_source_record_ids": {"choice:old": 901},
            }
            assert result["mismatch"]["source_record_id"] == 900
            assert result["mismatch"]["action_node_id"] is None
            assert result["future"] == sections._empty_raw_refs()
            assert result["later_import"] == sections._empty_raw_refs()
            # A historical build cannot reuse the retained pointer if its source
            # release is newer than that build's selected release.
            session.info["story_releases"][10] = Row(id=10, game_version="3.8", sequence=1)
            earlier = await sections._raw_refs(session, ["retained"], 10)
            assert earlier["retained"] == sections._empty_raw_refs()
    finally:
        engine.dispose()

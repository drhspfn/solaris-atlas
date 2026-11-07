from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select

from wuwa_story.api.routes.story import npc_profile, npc_scenes


def test_preview_sql_limits_each_selected_state_and_requires_release_evidence():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        for schema in ("graph", "core", "ontology"):
            connection.exec_driver_sql(f"ATTACH DATABASE ':memory:' AS {schema}")
        for sql in (
            "CREATE TABLE graph.node (id integer primary key, canonical_key text)",
            "CREATE TABLE graph.edge (id integer primary key, from_node_id integer, to_node_id integer, relation_type_id integer, layer text)",
            "CREATE TABLE graph.edge_evidence (id integer primary key, edge_id integer, release_id integer)",
            "CREATE TABLE ontology.relation_type (id integer primary key, key text)",
            "CREATE TABLE core.dialogue_line (node_id integer primary key, source_index integer)",
            "CREATE TABLE core.quest_action (node_id integer primary key, action_index integer)",
            "INSERT INTO ontology.relation_type VALUES (1,'contains_action'),(2,'presents_talk_item'),(3,'spoken_by')",
        ):
            connection.exec_driver_sql(sql)
        edge_id = 1
        for state in (101, 202):
            action = state + 1000
            connection.exec_driver_sql("INSERT INTO core.quest_action VALUES (?,?)", (action, 10))
            connection.exec_driver_sql(
                "INSERT INTO graph.edge VALUES (?,?,?,?,?)", (edge_id, state, action, 1, "source")
            )
            connection.exec_driver_sql(
                "INSERT INTO graph.edge_evidence VALUES (?,?,?)", (edge_id, edge_id, 7)
            )
            edge_id += 1
            for index in (0, 2, 10, 11, 12):
                line = state * 100 + index
                connection.exec_driver_sql(
                    "INSERT INTO graph.node VALUES (?,?)", (line, f"line:{state}:{index}")
                )
                connection.exec_driver_sql(
                    "INSERT INTO core.dialogue_line VALUES (?,?)", (line, index)
                )
                connection.exec_driver_sql(
                    "INSERT INTO graph.edge VALUES (?,?,?,?,?)",
                    (edge_id, action, line, 2, "source"),
                )
                connection.exec_driver_sql(
                    "INSERT INTO graph.edge_evidence VALUES (?,?,?)",
                    (edge_id, edge_id, 7 if index < 12 else 8),
                )
                edge_id += 1
                if state == 202 and index == 0:
                    connection.exec_driver_sql(
                        "INSERT INTO graph.edge VALUES (?,?,?,?,?)", (edge_id, line, 9, 3, "source")
                    )
                    connection.exec_driver_sql(
                        "INSERT INTO graph.edge_evidence VALUES (?,?,?)", (edge_id, edge_id, 7)
                    )
                    edge_id += 1
        connection.exec_driver_sql("INSERT INTO core.quest_action VALUES (?,?)", (2202, 2))
        connection.exec_driver_sql(
            "INSERT INTO graph.node VALUES (?,?)", (99999, "line:202:earlier")
        )
        connection.exec_driver_sql("INSERT INTO core.dialogue_line VALUES (?,?)", (99999, 10))
        for source, target, relation in ((202, 2202, 1), (2202, 99999, 2)):
            connection.exec_driver_sql(
                "INSERT INTO graph.edge VALUES (?,?,?,?,?)",
                (edge_id, source, target, relation, "source"),
            )
            connection.exec_driver_sql(
                "INSERT INTO graph.edge_evidence VALUES (?,?,?)", (edge_id, edge_id, 7)
            )
            edge_id += 1
        rows = connection.execute(select(npc_scenes.scene_preview_rows(7, [202]))).all()
        assert [(row.state_id, row.line_id, row.position) for row in rows] == [
            (202, 99999, 1),
            (202, 20200, 2),
            (202, 20202, 3),
        ]
        assert [row.speaker_id for row in rows] == [None, 9, None]
        assert [row.canonical_key for row in rows] == [
            "line:202:earlier",
            "line:202:0",
            "line:202:2",
        ]
        assert connection.execute(select(npc_scenes.scene_preview_rows(8, [202]))).all() == []
    engine.dispose()


@pytest.mark.asyncio
async def test_scenes_page_first_and_return_readable_quest_titles(monkeypatch):
    monkeypatch.setattr(
        npc_scenes, "npc_record", AsyncMock(return_value=(SimpleNamespace(id=1), None, {}, 7))
    )
    monkeypatch.setattr(
        npc_scenes, "npc_flow_state_ids", AsyncMock(return_value=list(range(1, 122)))
    )
    monkeypatch.setattr(npc_scenes, "prefetch_node_labels", AsyncMock())
    monkeypatch.setattr(npc_scenes, "prefetch_localizations", AsyncMock())
    monkeypatch.setattr(
        npc_scenes,
        "_node_label",
        AsyncMock(return_value={"canonical_key": "speaker:9", "label": "Ada"}),
    )
    monkeypatch.setattr(npc_scenes, "_localized", AsyncMock(return_value={"content": "Hello"}))
    monkeypatch.setattr(
        npc_scenes,
        "_quest_info",
        AsyncMock(return_value={"name": {"content": "A quest"}, "href": "/quests/1"}),
    )
    line = SimpleNamespace(localization_key_id=4, inline_text=None)
    query_results = [
        [(109, "internal-state"), (110, "other-state")],
        [(109, 9)],
        [(line, 109, 9, "dialogue:1")],
        [(42, 109)],
    ]
    session = SimpleNamespace(
        scalar=AsyncMock(return_value=1),
        execute=AsyncMock(
            side_effect=[SimpleNamespace(all=lambda: query_results[0]), *query_results[1:]]
        ),
        scalars=AsyncMock(return_value=[SimpleNamespace(node_id=42)]),
    )
    result = await npc_scenes.npc_scenes.__wrapped__(
        "character:npc:1", locale="en", limit=2, offset=108, session=session
    )
    assert result["total"] == 121 and [row["id"] for row in result["results"]] == [109, 110]
    assert result["results"][0]["title"] == "A quest"
    assert result["results"][0]["preview"] == [
        {
            "canonical_key": "dialogue:1",
            "text": "Hello",
            "speaker": {"canonical_key": "speaker:9", "label": "Ada"},
        }
    ]
    assert result["results"][0]["quests"][0]["href"] == "/quests/1"
    assert result["results"][1]["title"] == "Conversation"
    for call in session.execute.call_args_list:
        assert [109, 110] in call.args[0].compile().params.values()


@pytest.mark.asyncio
async def test_dialogue_rejects_state_outside_npc_source_set(monkeypatch):
    monkeypatch.setattr(
        npc_profile, "npc_record", AsyncMock(return_value=(SimpleNamespace(id=1), None, {}, 7))
    )
    monkeypatch.setattr(npc_profile, "npc_flow_state_ids", AsyncMock(return_value=[101, 202]))
    session = SimpleNamespace(scalar=AsyncMock(return_value=1), execute=AsyncMock())
    with pytest.raises(HTTPException) as error:
        await npc_profile.npc_dialogue.__wrapped__(
            "character:npc:1", locale="en", state_id=999, session=session
        )
    assert error.value.status_code == 404
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_dialogue_restricts_allowed_scene_before_selecting_lines(monkeypatch):
    monkeypatch.setattr(
        npc_profile, "npc_record", AsyncMock(return_value=(SimpleNamespace(id=1), None, {}, 7))
    )
    monkeypatch.setattr(npc_profile, "npc_flow_state_ids", AsyncMock(return_value=[101, 202]))
    monkeypatch.setattr(npc_profile, "prefetch_node_labels", AsyncMock())
    monkeypatch.setattr(npc_profile, "prefetch_localizations", AsyncMock())
    session = SimpleNamespace(
        scalar=AsyncMock(side_effect=[1, 0]), execute=AsyncMock(return_value=[])
    )
    result = await npc_profile.npc_dialogue.__wrapped__(
        "character:npc:1", locale="en", q="", limit=20, offset=0, state_id=202, session=session
    )
    assert result["total"] == 0
    statement = session.execute.await_args.args[0]
    assert [202] in statement.compile().params.values()
    sql = str(statement)
    assert "npc_actions.to_node_id AS action_id" in sql
    assert "core.quest_action.action_index ASC NULLS LAST" in sql
    assert "core.dialogue_line.source_index ASC NULLS LAST" in sql

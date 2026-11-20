"""Tests for server-side cutscene discovery, dynamic enqueueing, and quest transitions."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.engine.result import IteratorResult, SimpleResultMetaData

from wuwa_story.api.routes.story import media as story_media_route
from wuwa_story.ingestion import cutscene_discovery, release_media


@pytest.mark.parametrize(
    "engine_path,expected",
    [
        ("/Game/Aki/Sequence/LevelA_Seq/Main/03_8/M0394/DA_M0394.DA_M0394", "M0394"),
        ("asset:ue:/Game/Aki/Sequence/LevelA_Seq/Main/03_8/M0396/DA_M0396.DA_M0396", "M0396"),
        ("/Game/Aki/Sequence/LevelA_Seq/POI/03_6/P0063/DA_.DA_", None),
        ("/Game/Aki/Sequence/LevelB_Seq/Main/Section3_8/M3_14_12/DA_M3_14_12.DA_M3_14_12", None),
    ],
)
def test_server_levela_sequence_code(engine_path, expected):
    assert cutscene_discovery.levela_sequence_code(engine_path) == expected


@pytest.mark.asyncio
async def test_discover_bindata_cutscenes_dry_run_and_apply(monkeypatch):
    rows = [
        (10, 0, {"Id": 1, "VideoPath": ""}),
        (
            11,
            1,
            {
                "Id": 4,
                "VideoName": "RoleTowerLevel_VedioName_4",
                "VideoPath": "/Game/Aki/UI/UIResources/UiActivity/Mp4/Activity38/RoleTower/Charact_ChangLi.Charact_ChangLi",
            },
        ),
    ]
    session = NS(
        execute=AsyncMock(return_value=rows),
        get=AsyncMock(return_value=NS(id=22, game_version="3.8.0", upstream_name="up", upstream_commit="c")),
        scalars=AsyncMock(return_value=[]),
        commit=AsyncMock(),
    )
    dry = await cutscene_discovery.discover_bindata_cutscenes(session, 22, apply=False)
    assert dry["apply"] is False
    assert dry["cutscene_names"] == ["Charact_ChangLi"]
    assert dry["missing_nodes"] == 3
    assert dry["edge_candidates"] == 2
    assert dry["written_edges"] == 0

    register = AsyncMock(return_value={})
    monkeypatch.setattr(cutscene_discovery, "_register_records", register)
    session.execute = AsyncMock(return_value=rows)
    session.scalars = AsyncMock(return_value=[])
    applied = await cutscene_discovery.discover_bindata_cutscenes(session, 22, apply=True)
    assert applied["written_edges"] == 2
    register.assert_awaited_once()
    node_specs, edge_records = register.await_args.args[2:]
    assert [spec["canonical_key"] for spec in node_specs] == [
        "cutscene:Charact_ChangLi",
        "cutscene_variant:roletower:4:1",
        "asset:ue:/Game/Aki/UI/UIResources/UiActivity/Mp4/Activity38/RoleTower/Charact_ChangLi.Charact_ChangLi",
    ]
    assert [edge["type"] for edge in edge_records] == ["has_variant", "references_asset"]


@pytest.mark.asyncio
async def test_enqueue_discovered_cutscenes_matches_release_media_hash_and_is_idempotent(monkeypatch):
    parent = NS(id=50, processor_id=4, raw_output={"workspace_id": 10})
    existing = NS(id=51, status="completed", metadata_json={}, raw_output={})
    runs = []

    session = NS(
        get=AsyncMock(return_value=parent),
        scalar=AsyncMock(side_effect=[existing, None]),
        add=lambda run: runs.append(run),
        flush=AsyncMock(side_effect=lambda: setattr(runs[-1], "id", 52)),
        commit=AsyncMock(),
    )
    monkeypatch.setattr(release_media, "retain_workspace", AsyncMock())
    publish = AsyncMock()
    monkeypatch.setattr(release_media, "publish_media_job", publish)

    report = await release_media.enqueue_discovered_cutscenes(
        session,
        {"run_id": 50, "release_id": 22, "game_version": "3.8.0"},
        ["M0394", "M0396", "M0396"],
    )
    assert report == {"tasks": 2, "queued": 1}
    expected_request = {
        "release_id": 22,
        "game_version": "3.8.0",
        "kind": "cutscene",
        "targets": ["M0396"],
        "parent_id": 50,
        "cutscene_assembly_schema": release_media.CUTSCENE_ASSEMBLY_SCHEMA,
    }
    assert runs[0].input_hash == hashlib.sha256(json.dumps(expected_request, sort_keys=True).encode()).digest()
    publish.assert_awaited_once()
    assert publish.await_args.args[2] == release_media.CUTSCENE_ASSEMBLY_QUEUE


@pytest.mark.asyncio
async def test_quest_media_preserves_hybrid_sequence_to_movie_and_plot_mode_transitions(monkeypatch):
    state_a = NS(node_id=10, state_key="State_M0396")
    state_b = NS(node_id=11, state_key="State_M0398")
    action_hybrid = NS(node_id=101, action_index=2, action_name="ShowTalk", params={})
    action_m0398 = NS(node_id=102, action_index=1, action_name="ShowTalk", params={})
    action_mode = NS(node_id=103, action_index=2, action_name="SetPlotMode", params={"Mode": "LevelB"})
    action_levelb = NS(node_id=104, action_index=3, action_name="ShowTalk", params={})

    monkeypatch.setattr(story_media_route, "get_quest", AsyncMock(return_value=NS(node_id=1, game_quest_id=119000001)))
    monkeypatch.setattr(story_media_route, "_release_id", AsyncMock(return_value=22))
    monkeypatch.setattr(
        story_media_route,
        "authored_order",
        AsyncMock(
            return_value={
                "blocks": [],
                "state_ids": [10, 11],
                "positions": {"State_M0396": [1, 1], "State_M0398": [1, 2]},
                "mapped_flow_states": ["State_M0396", "State_M0398"],
            }
        ),
    )
    monkeypatch.setattr(story_media_route, "_quest_page_state_ids", AsyncMock(return_value=[10, 11]))

    async def fake_links(_session, ids, relations, _release_id):
        if "plays_cutscene" in relations:
            return {
                101: [
                    {
                        "node_id": 201,
                        "canonical_key": "asset:ue:/Game/Aki/Sequence/LevelA_Seq/Main/03_8/M0396/DA_M0396.DA_M0396",
                        "relation": "plays_sequence_asset",
                        "basis": "ShowTalk.Params.SequenceDataAsset",
                        "source": {"file": "flowstate.json", "raw_path": "$[0].Actions[2]"},
                    },
                    {
                        "node_id": 202,
                        "canonical_key": "cutscene:M0396",
                        "relation": "plays_cutscene",
                        "basis": "LevelA SequenceDataAsset -> embedded movie fragment",
                        "source": {"file": "flowstate.json", "raw_path": "$[0].Actions[2]"},
                    },
                ],
                102: [
                    {
                        "node_id": 203,
                        "canonical_key": "asset:ue:/Game/Aki/Sequence/LevelA_Seq/Main/03_8/M0398/DA_M0398.DA_M0398",
                        "relation": "plays_sequence_asset",
                        "basis": "ShowTalk.Params.SequenceDataAsset",
                        "source": {"file": "flowstate.json", "raw_path": "$[1].Actions[1]"},
                    },
                ],
                104: [
                    {
                        "node_id": 204,
                        "canonical_key": "asset:ue:/Game/Aki/Sequence/LevelB_Seq/Main/Section3_8/M3_14_30/DA_M3_14_30.DA_M3_14_30",
                        "relation": "plays_sequence_asset",
                        "basis": "ShowTalk.Params.SequenceDataAsset",
                        "source": {"file": "flowstate.json", "raw_path": "$[1].Actions[3]"},
                    },
                ],
            }
        return {}

    monkeypatch.setattr(story_media_route, "_links", fake_links)
    monkeypatch.setattr(story_media_route, "cutscene_flows", AsyncMock(return_value={202: {"asset_version": "3.8.0"}}))
    monkeypatch.setattr(story_media_route, "cutscene_videos", AsyncMock(return_value={}))
    monkeypatch.setattr(story_media_route, "caption_texts", AsyncMock(return_value={}))
    monkeypatch.setattr(story_media_route, "attach_public_descriptions", AsyncMock())
    monkeypatch.setattr(story_media_route, "scene_continuations", AsyncMock(return_value={}))
    monkeypatch.setattr(story_media_route, "attach_sequence_videos", AsyncMock(side_effect=lambda _s, m: m))
    monkeypatch.setattr(story_media_route.runtime_service, "sequence_playback", AsyncMock(return_value={}))

    action_rows = [
        (action_hybrid, state_a, "action:State_M0396:2"),
        (action_m0398, state_b, "action:State_M0398:1"),
        (action_mode, state_b, "action:State_M0398:2"),
        (action_levelb, state_b, "action:State_M0398:3"),
    ]
    session = NS(
        scalars=AsyncMock(return_value=[]),
        scalar=AsyncMock(return_value=None),
        execute=AsyncMock(
            side_effect=[
                NS(all=lambda: action_rows),
                [],
                IteratorResult(SimpleResultMetaData(["node_id", "data"]), iter([])),
            ]
        ),
        get=AsyncMock(return_value=NS(game_version="3.8.0")),
    )
    manifest = await story_media_route.quest_media("119000001", "3.8.0", "en", session)
    events = manifest["events"]
    assert [(e["kind"], e["reference"]) for e in events] == [
        ("sequence", "asset:ue:/Game/Aki/Sequence/LevelA_Seq/Main/03_8/M0396/DA_M0396.DA_M0396"),
        ("cutscene", "cutscene:M0396"),
        ("sequence", "asset:ue:/Game/Aki/Sequence/LevelA_Seq/Main/03_8/M0398/DA_M0398.DA_M0398"),
        ("sequence", "asset:ue:/Game/Aki/Sequence/LevelB_Seq/Main/Section3_8/M3_14_30/DA_M3_14_30.DA_M3_14_30"),
    ]
    assert events[0]["story_position"] == [1, 1, 2, -1]
    assert events[1]["story_position"] == [1, 1, 2, 0]
    assert events[1]["transition"] is True
    assert events[1]["sequence_transition"] == {
        "kind": "sequence_to_movie",
        "from_reference": "asset:ue:/Game/Aki/Sequence/LevelA_Seq/Main/03_8/M0396/DA_M0396.DA_M0396",
        "to_reference": "cutscene:M0396",
        "movie_scope": "embedded_fragment",
        "basis": "LevelA SequenceDataAsset -> embedded movie fragment",
    }
    assert events[3]["sequence_transition"] == {
        "kind": "plot_mode_transition",
        "from_reference": "asset:ue:/Game/Aki/Sequence/LevelA_Seq/Main/03_8/M0398/DA_M0398.DA_M0398",
        "to_reference": "asset:ue:/Game/Aki/Sequence/LevelB_Seq/Main/Section3_8/M3_14_30/DA_M3_14_30.DA_M3_14_30",
        "to_mode": "LevelB",
        "basis": "SetPlotMode",
    }

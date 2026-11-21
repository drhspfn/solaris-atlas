from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from wuwa_story.db.repositories import story_browsing
from wuwa_story.story import media as story_media


def _compile_pg(statement) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


@pytest.mark.asyncio
async def test_quest_transcript_rows_filters_by_node_revision_for_release():
    captured = []

    class Session:
        async def execute(self, statement):
            captured.append(_compile_pg(statement))
            return SimpleNamespace(all=lambda: [])

    await story_browsing.quest_transcript_rows(
        Session(), [101, 102], 22, None, None, None, None, 50, 0
    )
    assert len(captured) == 1
    sql = captured[0]
    assert "EXISTS (SELECT graph.node_revision.id" in sql
    assert "graph.node_revision.node_id = core.dialogue_line.node_id" in sql
    assert "graph.node_revision.node_id = core.quest_action.node_id" in sql
    assert "graph.node_revision.release_id = 22" in sql

    captured.clear()
    await story_browsing.quest_transcript_rows(
        Session(), [101], None, None, None, None, None, 50, 0
    )
    assert "graph.node_revision" not in captured[0]


@pytest.mark.asyncio
async def test_search_dialogue_rows_filters_by_node_revision_for_release():
    captured = []

    class Session:
        async def execute(self, statement):
            captured.append(_compile_pg(statement))
            return SimpleNamespace(all=lambda: [])

    await story_browsing.search_dialogue_rows(
        Session(), "rover", SimpleNamespace(id=1), 22, None, [101], 50, 0
    )
    sql = captured[0]
    assert "graph.node_revision.node_id = core.dialogue_line.node_id" in sql
    assert "graph.node_revision.node_id = core.quest_action.node_id" in sql
    assert "graph.node_revision.release_id = 22" in sql


@pytest.mark.asyncio
async def test_quest_actions_illustration_actions_and_choices_filter_by_node_revision():
    captured = []

    class Session:
        async def execute(self, statement):
            captured.append(_compile_pg(statement))
            return []

    await story_browsing.quest_actions(Session(), [101], release_id=22)
    await story_browsing.illustration_actions(Session(), [101], release_id=22)
    await story_browsing.dialogue_choices(Session(), [201], release_id=22)

    assert "graph.node_revision.node_id = core.quest_action.node_id" in captured[0]
    assert "graph.node_revision.release_id = 22" in captured[0]
    assert "graph.node_revision.node_id = core.quest_action.node_id" in captured[1]
    assert "graph.node_revision.node_id = core.player_choice.node_id" in captured[2]


@pytest.mark.asyncio
async def test_canonical_import_updates_quest_action_and_dialogue_line_on_conflict():
    from wuwa_story.ingestion import canonical_import

    executed_sql = []

    class Session:
        async def scalars(self, _statement):
            return []

        async def execute(self, statement):
            sql = str(statement.compile(dialect=postgresql.dialect()))
            executed_sql.append(sql)
            if "FROM i18n.localization_key" in sql:
                return SimpleNamespace(all=lambda: [("Tid_1", 400)])
            return SimpleNamespace(all=lambda: [])

    action_record = {
        "id": "action:state_1:0",
        "state_key": "state_1",
        "action_index": 0,
        "action_id": 10,
        "action_guid": "guid-1",
        "name": "ShowTalk",
        "source": {"file": "flowstate.json", "row": 1},
        "raw": {"Params": {"TalkItems": []}},
    }
    talk_record = {
        "id": "talk_item:state_1:0:0",
        "state_key": "state_1",
        "action_index": 0,
        "talk_index": 0,
        "talk_id": 1,
        "text_id": 2,
        "source_type": "Talk",
        "speaker_id": 9,
        "localization": {"key": "Tid_1", "resolution": "resolved_nonempty"},
        "source": {"file": "flowstate.json", "row": 1},
        "raw": {},
    }
    node_cache = {
        "flow_state:state_1": 50,
        "action:state_1:0": 100,
        "speaker:9": 300,
    }
    await canonical_import._insert_typed_entities(
        Session(),
        "action",
        [action_record],
        {"action:state_1:0": (100, 1)},
        {("flowstate.json", 1): 500},
        newer_release_ids=[],
        node_id_cache=node_cache,
    )
    await canonical_import._insert_typed_entities(
        Session(),
        "talk_item",
        [talk_record],
        {"talk_item:state_1:0:0": (200, 1)},
        {("flowstate.json", 1): 501},
        newer_release_ids=[],
        node_id_cache=node_cache,
    )
    action_sql = next(sql for sql in executed_sql if "INSERT INTO core.quest_action" in sql)
    line_sql = next(sql for sql in executed_sql if "INSERT INTO core.dialogue_line" in sql)
    assert "ON CONFLICT (node_id) DO UPDATE SET" in action_sql
    assert "action_name = excluded.action_name" in action_sql
    assert "params = excluded.params" in action_sql
    assert "ON CONFLICT (node_id) DO UPDATE SET" in line_sql
    assert "localization_key_id = excluded.localization_key_id" in line_sql
    assert "speaker_node_id = excluded.speaker_node_id" in line_sql


@pytest.mark.asyncio
async def test_dialogue_media_resolves_sequence_assets_and_sequence_audio_tracks(monkeypatch):
    async def link_loader(_session, from_ids, relations, _release_id):
        if "plays_sequence_asset" in relations:
            return {
                500: [
                    {
                        "node_id": 7303490,
                        "canonical_key": "asset:ue:/Game/Aki/Sequence/LevelA_Seq/Main/02_2/M0260/DA_M0260.DA_M0260",
                        "relation": "plays_sequence_asset",
                        "basis": "explicit_reference",
                        "source": {"file": "BinData/flowState/flowstate.json", "raw_path": "$.Params.SequenceDataAsset"},
                    }
                ]
            }
        return {}

    monkeypatch.setattr(
        story_media,
        "published_sequence_audio_tracks",
        AsyncMock(
            return_value=[
                SimpleNamespace(
                    owner_node_id=900,
                    language="ja",
                    rover=None,
                    asset_version="3.8.0",
                    event_reference="play_seq_all_sound_m0260",
                    source_name="play_seq_all_sound_m0260.ogg",
                    object_key="seq/ja_m0260.ogg",
                )
            ]
        ),
    )
    result = await story_media.dialogue_media(
        AsyncMock(),
        [10],
        22,
        action_ids_by_line={10: 500},
        state_ids_by_line={10: 900},
        link_loader=link_loader,
        settings_factory=lambda: SimpleNamespace(s3_bucket="sample"),
        storage_factory=lambda _: SimpleNamespace(public_url=lambda key: "https://cdn.example/" + key),
    )
    assert 10 in result
    assert result[10]["sequence_assets"] == [
        {
            "engine_path": "/Game/Aki/Sequence/LevelA_Seq/Main/02_2/M0260/DA_M0260.DA_M0260",
            "reference_kind": "sequence_data_asset",
            "expected_sound_bank": "play_seq_all_sound_m0260.bnk",
            "basis": "explicit_reference",
            "source": {"file": "BinData/flowState/flowstate.json", "raw_path": "$.Params.SequenceDataAsset"},
            "tracks": [
                {
                    "language": "ja",
                    "rover": None,
                    "url": "https://cdn.example/seq/ja_m0260.ogg",
                    "asset_version": "3.8.0",
                    "event_reference": "play_seq_all_sound_m0260",
                    "source_name": "play_seq_all_sound_m0260.ogg",
                }
            ],
        }
    ]


@pytest.mark.asyncio
async def test_dialogue_media_resolves_talk_ak_event_play_vo_tracks(monkeypatch):
    async def link_loader(_session, _from_ids, relations, _release_id):
        if "posts_audio_event" in relations:
            return {
                11: [
                    {
                        "node_id": 888,
                        "canonical_key": "asset:ue:/Game/Aki/WwiseAudio/Events/play_vo_monster_heti_onenote.play_vo_monster_heti_onenote",
                        "relation": "posts_audio_event",
                        "basis": "explicit_reference",
                        "source": {"file": "BinData/flowState/flowstate.json", "raw_path": "$.TalkAkEvent.AkEvent"},
                    }
                ]
            }
        return {}

    monkeypatch.setattr(
        story_browsing,
        "voice_references_by_file_names",
        AsyncMock(
            return_value=[
                SimpleNamespace(
                    node_id=777,
                    plot_audio_id="monster_heti_onenote",
                    file_name="vo_monster_heti_onenote",
                    media_asset_node_id=None,
                )
            ]
        ),
    )
    monkeypatch.setattr(
        story_media,
        "published_voice_tracks",
        AsyncMock(
            return_value=[
                SimpleNamespace(
                    owner_node_id=777,
                    language="en",
                    rover=None,
                    asset_version="3.8.0",
                    duration_seconds=1.5,
                    object_key="voices/en_vo_monster_heti_onenote.wav",
                )
            ]
        ),
    )
    monkeypatch.setattr(story_media, "published_audio_event_tracks", AsyncMock(return_value=[]))

    result = await story_media.dialogue_media(
        AsyncMock(),
        [11],
        22,
        link_loader=link_loader,
        settings_factory=lambda: SimpleNamespace(s3_bucket="sample"),
        storage_factory=lambda _: SimpleNamespace(public_url=lambda key: "https://cdn.example/" + key),
    )
    assert result[11]["audio_event_paths"] == [
        {
            "engine_path": "/Game/Aki/WwiseAudio/Events/play_vo_monster_heti_onenote.play_vo_monster_heti_onenote",
            "reference_kind": "unreal_asset_path",
            "basis": "explicit_reference",
            "source": {"file": "BinData/flowState/flowstate.json", "raw_path": "$.TalkAkEvent.AkEvent"},
            "tracks": [
                {
                    "rover": None,
                    "language": "en",
                    "url": "https://cdn.example/voices/en_vo_monster_heti_onenote.wav",
                    "asset_version": "3.8.0",
                    "duration_seconds": 1.5,
                }
            ],
        }
    ]

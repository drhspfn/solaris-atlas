from types import SimpleNamespace as Row
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from wuwa_story.api.routes.story import profiles


@pytest.mark.asyncio
@pytest.mark.parametrize("card_count", [1, 12])
async def test_character_quest_info_is_reused_across_scenes_and_quest_list(monkeypatch, card_count):
    state = Row(node_id=9, state_key="flow", source_record_id=99)
    quest = Row(node_id=7, game_quest_id=70)
    speaker = Row(node_id=4, game_speaker_id=40)
    link = Row(resolution_type="exact", source_record_id=3)
    character = Row(canonical_name="Name", name_key_id=None, description=None, playable=True)
    entities = list(range(20, 20 + card_count))
    speakers = list(range(100, 100 + card_count))
    session = Row(
        get=AsyncMock(return_value=character),
        execute=AsyncMock(side_effect=[
            [(link, speaker, Row(canonical_key="speaker:40")),
             (link, Row(node_id=5, game_speaker_id=50), Row(canonical_key="speaker:50"))],
            [(identity, 9) for identity in speakers], [(identity, 9) for identity in entities],
            list(zip(speakers, entities, strict=True)), [(99, 3, "flow.json")],
            Row(all=lambda: []), [],
        ]),
        scalars=AsyncMock(side_effect=[[9], [state], [quest], [7], [], [quest], []]),
        scalar=AsyncMock(return_value=None),
    )
    monkeypatch.setattr(profiles, "get_node", AsyncMock(return_value=Row(id=1, canonical_key="character:1", slug=None)))
    monkeypatch.setattr(profiles, "_release_id", AsyncMock(return_value=5))
    labels = AsyncMock()
    monkeypatch.setattr(profiles, "prefetch_node_labels", labels)
    monkeypatch.setattr(profiles, "prefetch_dialogue", AsyncMock())
    monkeypatch.setattr(profiles, "_node_label", AsyncMock(side_effect=lambda _s, identity, _l: {"id": identity}))
    monkeypatch.setattr(profiles, "_localized", AsyncMock(return_value=None))
    monkeypatch.setattr(profiles, "_latest_source_record", AsyncMock(return_value=None))
    monkeypatch.setattr(profiles, "_character_progression_materials", AsyncMock(return_value=[]))
    quest_info = AsyncMock(return_value={"game_quest_id": 70})
    monkeypatch.setattr(profiles, "_quest_info", quest_info)

    result = await profiles.character_profile.__wrapped__("character:1", session=session)
    assert result["quests_with_dialogue"] == [{"game_quest_id": 70}]
    scenes = [entry["shared_scenes"][0] for entry in result["co_present_characters"]]
    assert len(scenes) == card_count
    assert all(scene["quest_refs"] == [{"game_quest_id": 70}] for scene in scenes)
    assert all(scene["source"] == {"file": "flow.json", "row": 3, "record_id": 99} for scene in scenes)
    assert [card["character_entities"] for card in result["co_present_speakers"]] == [
        [{"id": identity}] for identity in entities
    ]
    assert all(card["flow_states"] == ["flow"] for card in result["co_present_speakers"])
    labels.assert_any_await(session, set(entities + speakers), "en")
    quest_info.assert_awaited_once_with(session, quest, "en", None)
    # More cards sharing the same state must not add state/mapping/source reads.
    assert session.execute.await_count == session.scalars.await_count == 7
    query = str(session.scalars.await_args_list[0].args[0].compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "SELECT DISTINCT" in query
    assert "speaker_node_id IN (4, 5)" in query

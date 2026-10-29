from types import SimpleNamespace as Row
from unittest.mock import AsyncMock

import pytest

from wuwa_story.api.routes.story import profiles


@pytest.mark.asyncio
async def test_character_quest_info_is_reused_across_scenes_and_quest_list(monkeypatch):
    state = Row(node_id=9, state_key="flow", source_record_id=None)
    quest = Row(node_id=7, game_quest_id=70)
    speaker = Row(node_id=4, game_speaker_id=40)
    link = Row(resolution_type="exact", source_record_id=3)
    character = Row(canonical_name="Name", name_key_id=None, description=None, playable=True)
    session = Row(
        get=AsyncMock(return_value=character),
        execute=AsyncMock(side_effect=[
            [(link, speaker, Row(canonical_key="speaker:40"))], [], [(2, 9), (3, 9)],
            Row(all=lambda: []), Row(first=lambda: None),
            Row(all=lambda: []), Row(first=lambda: None), [],
        ]),
        scalars=AsyncMock(side_effect=[[9], [state], [quest], [state], [quest], [7], [], [quest], []]),
        scalar=AsyncMock(return_value=None),
    )
    monkeypatch.setattr(profiles, "get_node", AsyncMock(return_value=Row(id=1, canonical_key="character:1", slug=None)))
    monkeypatch.setattr(profiles, "_release_id", AsyncMock(return_value=5))
    monkeypatch.setattr(profiles, "_node_label", AsyncMock(side_effect=lambda _s, identity, _l: {"id": identity}))
    monkeypatch.setattr(profiles, "_localized", AsyncMock(return_value=None))
    monkeypatch.setattr(profiles, "_latest_source_record", AsyncMock(return_value=None))
    monkeypatch.setattr(profiles, "_character_progression_materials", AsyncMock(return_value=[]))
    quest_info = AsyncMock(return_value={"game_quest_id": 70})
    monkeypatch.setattr(profiles, "_quest_info", quest_info)

    result = await profiles.character_profile.__wrapped__("character:1", session=session)
    assert result["quests_with_dialogue"] == [{"game_quest_id": 70}]
    assert [entry["shared_scenes"][0]["quest_refs"] for entry in result["co_present_characters"]] == [
        [{"game_quest_id": 70}], [{"game_quest_id": 70}],
    ]
    quest_info.assert_awaited_once_with(session, quest, "en", None)

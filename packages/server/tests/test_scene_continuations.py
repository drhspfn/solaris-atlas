from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.story import scene_continuations as module


@pytest.mark.asyncio
async def test_packaged_scene_is_explained_by_explicit_later_quest_not_as_current_position(monkeypatch):
    current = SimpleNamespace(node_id=10, game_quest_id=100, name_key_id=None)
    later = SimpleNamespace(node_id=20, game_quest_id=200, name_key_id=None)
    possible = SimpleNamespace(node_id=30, game_quest_id=300, name_key_id=None)
    monkeypatch.setattr(module.cutscenes, 'owners', AsyncMock(return_value=[
        (5, current, 'current', False), (5, later, 'later', False), (6, possible, 'possible', True)]))
    session = SimpleNamespace(execute=AsyncMock(
        return_value=[(20, 'QuestData', '$.PreQuest')]))
    result = await module.scene_continuations(session, [5, 6], 10, 21, 'en')
    assert result == {5: [{'game_quest_id': 200, 'title': None,
                           'basis': 'owning_quest_requires_current_quest',
                           'source': {'file': 'QuestData', 'raw_path': '$.PreQuest', 'release_id': 21}}]}
    assert 'story_position' not in result[5][0]


@pytest.mark.asyncio
async def test_unrelated_quest_owner_does_not_become_a_continuation(monkeypatch):
    other = SimpleNamespace(node_id=20, game_quest_id=200, name_key_id=None)
    monkeypatch.setattr(module.cutscenes, 'owners', AsyncMock(
        return_value=[(5, other, 'other', False)]))
    session = SimpleNamespace(execute=AsyncMock(return_value=[]))
    assert await module.scene_continuations(session, [5], 10, 21, 'en') == {}

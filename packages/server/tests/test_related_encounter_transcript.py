from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.story import usecases


@pytest.mark.asyncio
@pytest.mark.parametrize('related', [True, False])
async def test_related_states_are_included_without_claiming_global_transcript_order(monkeypatch, related):
    order = {'state_ids': [4648369], 'asset_version': '3.7.1', 'blocks': [],
             'positions': {}, 'mapped_flow_states': []}
    if related:
        order['ordering'] = 'related'
    monkeypatch.setattr(usecases, 'get_quest', AsyncMock(return_value=SimpleNamespace(node_id=4688653, game_quest_id=114000031)))
    monkeypatch.setattr(usecases, '_release_id', AsyncMock(return_value=15))
    monkeypatch.setattr(usecases, '_quest_page_state_ids', AsyncMock(return_value=[10]))
    monkeypatch.setattr(usecases, 'authored_order', AsyncMock(return_value=order))
    monkeypatch.setattr(usecases, '_quest_info', AsyncMock(return_value={}))
    for name, value in [('prefetch_dialogue', None), ('dialogue_media', {}),
                        ('dialogue_lines', []), ('illustration_payloads', {}),
                        ('present_blocks', [{'ordering': 'related'}])]:
        monkeypatch.setattr(usecases, name, AsyncMock(return_value=value))
    rows = AsyncMock(return_value=([], 0))
    monkeypatch.setattr(usecases.repository, 'quest_transcript_rows', rows)
    monkeypatch.setattr(usecases.repository, 'quest_scenes', AsyncMock(return_value=[]))
    result = await usecases.quest_transcript(SimpleNamespace(), 'quest:114000031')
    assert set(rows.await_args.args[1]) == {10, 4648369}
    assert rows.await_args.kwargs['ordered_states'] == (None if related else [4648369])
    assert result['tree_order_asset_version'] == (None if related else '3.7.1')
    assert ('positions in this quest are unknown' in result['ordering_semantics']) is related
    assert result['story_blocks'] == [{'ordering': 'related'}]

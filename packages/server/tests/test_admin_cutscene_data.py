import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from wuwa_story.admin import cutscenes


@pytest.mark.asyncio
async def test_admin_returns_all_reports_and_previews_for_the_playing_recording(monkeypatch):
    db = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(node_id=15542, cg_name='M0221')))
    request = Request({'type': 'http', 'method': 'GET', 'headers': [], 'query_string': b'locale=en',
                       'path_params': {'node_id': 15542}})
    request.state.session = db
    monkeypatch.setattr(cutscenes, 'release', AsyncMock(return_value=SimpleNamespace(id=21, game_version='3.7.0')))
    monkeypatch.setattr(cutscenes, 'detail', AsyncMock(return_value={
        'public_descriptions': [], 'event': {'playback': {'asset_version': '3.7.0',
                                                       'media': {'clip': {'asset_node_id': 10023}}}},
    }))
    images = [{'file_id': i, 'url': f'https://cdn.test/{i}.jpg'} for i in range(3)]
    previews = AsyncMock(return_value={15542: {'previews': images}})
    monkeypatch.setattr(cutscenes, 'previews', previews)
    events = [{'time': i, 'observation': f'Observation {i}'} for i in range(20)]
    monkeypatch.setattr(cutscenes, 'visual_reference', AsyncMock(return_value=SimpleNamespace(
        metadata_json={'asset_version': '3.7.0', 'title': 'A ruined shore', 'events': events})))
    response = await cutscenes.CutsceneData('Data', path='/data', name='data').render(request, None)
    data = json.loads(response.body)
    assert data['previews'] == images
    assert data['system_reports'][0]['events'] == events
    assert data['public_descriptions'] == []
    previews.assert_awaited_once_with(db, [15542], 21, limit=None, asset_version='3.7.0')

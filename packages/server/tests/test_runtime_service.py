from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from wuwa_story.db.repositories import sequence_playback as repository
from wuwa_story.story import runtime_service


def blocks():
    return [{'title': 'Objective', 'encounter': {'trees': [{'root': {
        'title': 'Activity', 'text_key': 'nested', 'children': [],
        'flow_states': [{'state_node_id': 7}],
    }}]}}]


@pytest.mark.asyncio
async def test_fragments_use_available_locations_not_unverified_metadata(monkeypatch):
    media = {'schema_version': 1, 'complete_video': True, 'sections': [
        {'kind': 'movie_fragment', 'status': 'published', 'file_id': 12, 'object_key': 'wrong'},
        {'kind': 'movie_fragment', 'status': 'published', 'file_id': 13},
        {'kind': 'subsequence', 'status': 'published', 'file_id': 12},
    ]}
    monkeypatch.setattr(repository, 'bundles', AsyncMock(return_value=[(7, {'sequence_media': media})]))
    monkeypatch.setattr(repository, 'available_movies', AsyncMock(return_value={12: 'verified'}))
    from wuwa_story.storage.s3 import S3Storage
    monkeypatch.setattr(S3Storage, 'public_url', lambda self, key: 'https://cdn/' + key)
    result = await runtime_service.sequence_playback(AsyncMock(), blocks(), 15)
    assert result[7]['complete_video'] is False
    assert len(result[7]['sections']) == 1
    assert result[7]['sections'][0]['url'] == 'https://cdn/verified'
    assert 'url' not in media['sections'][0]
    assert repository.bundles.await_args.args[1:] == ({7}, 15)


@pytest.mark.asyncio
async def test_nested_labels_localized_and_source_tree_unchanged(monkeypatch):
    source = blocks()
    before = deepcopy(source)
    monkeypatch.setattr(runtime_service.repository, 'named_localization_keys', AsyncMock(return_value={'nested': 1}))
    monkeypatch.setattr(runtime_service, 'prefetch_localizations', AsyncMock())
    monkeypatch.setattr(runtime_service, '_localized', AsyncMock(return_value={'content': 'Defeat the boss'}))
    monkeypatch.setattr(runtime_service, 'sequence_playback', AsyncMock(return_value={7: {'sections': []}}))
    result = await runtime_service.present_blocks(AsyncMock(), {'blocks': source}, 'en', 15)
    root = result[0]['encounter']['trees'][0]['root']
    assert root['title'] == 'Defeat the boss'
    assert root['flow_states'][0]['sequence_media'] == {'sections': []}
    assert source == before


@pytest.mark.asyncio
async def test_no_encounters_does_not_query_storage():
    session = AsyncMock()
    assert await runtime_service.sequence_playback(session, [], 15) == {}
    session.execute.assert_not_called()

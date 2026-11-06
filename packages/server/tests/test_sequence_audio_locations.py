from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from wuwa_story.db.repositories.sequence_playback import available_audio


@pytest.mark.asyncio
@pytest.mark.parametrize('mismatch', [None, 'bundle_file_id', 'event_reference', 'language', 'game_source'])
async def test_audio_url_requires_exact_bundle_event_language_and_provenance(mismatch):
    proof = {'asset_version': '3.7.1', 'download_id': 'client', 'sequence_metadata_revision': 3}
    selected = {7: {'file_id': 90, 'source_path': 'story_sequence:Flow28', 'media': {
        'game_source': proof, 'sections': [{'kind': 'audio_event', 'source': '/Game/Scene.407',
            'reference': '/Game/Event', 'tracks': [{'file_id': 91, 'status': 'published',
                'file': 'audio/event/en/shared.ogg', 'language': 'en', 'rover': None}]}]}}}
    metadata = {'asset_version': '3.7.1', 'download_id': 'client', 'bundle_file_id': 90,
                'game_source': deepcopy(proof), 'section_source': '/Game/Scene.407',
                'event_reference': '/Game/Event', 'archive_entry': 'audio/event/en/shared.ogg',
                'language': 'en', 'rover': None}
    if mismatch:
        metadata[mismatch] = 'different'
    session = AsyncMock()
    session.execute.return_value = [(7, 91, 'story_sequence:Flow28', 'audio/event/en/shared.ogg', metadata, 'verified')]
    result = await available_audio(session, selected, 21, 'media')
    assert result == ({} if mismatch else {(7, 91, '/Game/Scene.407'): 'verified'})


@pytest.mark.asyncio
async def test_no_audio_tracks_adds_no_database_query():
    session = AsyncMock()
    assert await available_audio(session, {7: {'media': {'sections': []}}}, 21, 'media') == {}
    session.execute.assert_not_awaited()

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.db.repositories import sequence_playback as repository
from wuwa_story.story import runtime_service


def sample():
    proof = {'schema_version': 1, 'asset_version': '3.7.1', 'download_id': 'client',
             'presentation_sha256': 'digest', 'package_path': 'flow'}
    frame = {'kind': 'illustration', 'status': 'published', 'file_id': 91, 'file': 'composition/a.png',
             'source': 'shot', 'scene_source': 'scene', 'time_seconds': 1.5, 'url': 'untrusted'}
    media = {'schema_version': 1, 'game_source': proof, 'sections': [frame],
             'composition': {'schema_version': 1, 'state': 'ready', 'reasons': []}}
    metadata = {'asset_version': '3.7.1', 'download_id': 'client', 'bundle_file_id': 90,
                'game_source': proof, 'archive_entry': frame['file'],
                'frames': [{'source': 'shot', 'scene_source': 'scene', 'time_seconds': 1.5}]}
    return proof, frame, media, metadata


@pytest.mark.asyncio
@pytest.mark.parametrize('mismatch', [None, 'bundle_file_id', 'game_source', 'archive_entry', 'frames', 'path'])
async def test_images_require_exact_bundle_frame_and_provenance(mismatch):
    _, frame, media, metadata = sample()
    path = 'flow'
    if mismatch == 'path':
        path = 'different'
    elif mismatch:
        metadata[mismatch] = 'different'
    session = AsyncMock()
    session.execute.return_value = [
        (7, 91, path, frame['file'], metadata, 'verified')]
    selected = {7: {'file_id': 90, 'source_path': 'flow', 'media': media}}
    result = await repository.available_illustrations(session, selected, 21, 'media')
    assert result == ({} if mismatch else {
                      (7, 91, 'shot', 'scene', 1.5): 'verified'})
    sql = session.execute.call_args.args[0].compile()
    assert 'file_reference.release_id' in str(sql)
    assert 'story_sequence_illustration' in sql.params.values()


@pytest.mark.asyncio
async def test_regular_flow_hash_resolves_only_trusted_image_and_preserves_status(monkeypatch):
    proof, _, media, _ = sample()
    row = (7, 90, 'flow', {'asset_version': '3.7.1', 'download_id': 'client',
                           'game_source': proof, 'sequence_media': media})
    monkeypatch.setattr(repository, 'bundles', AsyncMock(return_value=[row]))
    monkeypatch.setattr(repository, 'available_movies',
                        AsyncMock(return_value={}))
    monkeypatch.setattr(repository, 'available_illustrations', AsyncMock(
        return_value={(7, 91, 'shot', 'scene', 1.5): 'verified'}))
    from wuwa_story.storage.s3 import S3Storage
    monkeypatch.setattr(S3Storage, 'public_url',
                        lambda self, key: 'https://cdn/' + key)
    result = await runtime_service.sequence_playback(AsyncMock(), [], 21, {7: 'digest'})
    assert result[7]['sections'][0]['url'] == 'https://cdn/verified'
    assert result[7]['composition'] == media['composition']
    assert await runtime_service.sequence_playback(AsyncMock(), [], 21, {7: 'different'}) == {}
    repository.available_illustrations.return_value = {}
    result = await runtime_service.sequence_playback(AsyncMock(), [], 21, {7: 'digest'})
    assert result[7]['sections'] == []


@pytest.mark.asyncio
@pytest.mark.parametrize('matching_asset', [True, False])
@pytest.mark.parametrize('legacy', [False, True])
async def test_quest_media_attaches_only_the_authored_flow_sequence(monkeypatch, matching_asset, legacy):
    from wuwa_story.api.routes.story import media as route
    from wuwa_story.ingestion.spine_presentation import flow_presentation

    raw = {'Actions': [{'Name': 'PlaySequenceData',
                        'Params': {'Path': '/Game/Fox.Fox'}}]}
    action = SimpleNamespace(node_id=10, action_index=0)
    state = SimpleNamespace(node_id=7, state_key='Fox_1_1')
    reference = '/Game/Fox.Fox' if matching_asset else '/Game/Other.Other'

    async def links(_session, nodes, relations, _release):
        if 10 in nodes and 'plays_sequence_asset' in relations:
            return {10: [{'relation': 'plays_sequence_asset', 'canonical_key': 'asset:ue:' + reference,
                          'node_id': 12, 'basis': 'authored', 'source': {}}]}
        return {}
    monkeypatch.setattr(route, 'get_quest', AsyncMock(
        return_value=SimpleNamespace(node_id=1, game_quest_id=1)))
    monkeypatch.setattr(route, '_release_id', AsyncMock(return_value=21))
    monkeypatch.setattr(route, 'authored_order', AsyncMock(return_value=None))
    monkeypatch.setattr(route, '_quest_page_state_ids',
                        AsyncMock(return_value=[7]))
    monkeypatch.setattr(route, '_links', links)
    monkeypatch.setattr(route, 'cutscene_flows', AsyncMock(return_value={}))
    monkeypatch.setattr(route, 'cutscene_videos', AsyncMock(return_value={}))
    monkeypatch.setattr(route, 'caption_texts', AsyncMock(return_value={}))
    playback = AsyncMock(return_value={7: {'composition': {'state': 'ready'},
        'sections': [{'scene_source': 'fox-scene', 'kind': 'illustration', 'url': 'fox'}],
        'scenes': [{'source': 'fox-scene'}],
        'sequence_assets': [] if legacy else [{'asset': '/Game/Fox.Fox', 'scenes': ['fox-scene']}]}})
    monkeypatch.setattr(runtime_service, 'sequence_playback', playback)
    session = SimpleNamespace(scalars=AsyncMock(return_value=[]), scalar=AsyncMock(return_value=None),
                              execute=AsyncMock(side_effect=[SimpleNamespace(
                                  all=lambda: [(action, state, 'action:1')]), [(7, raw)]]),
                              get=AsyncMock(return_value=SimpleNamespace(game_version='3.7.1')))
    result = await route.quest_media('1', '3.7.1', 'en', session)
    event, = result['events']
    assert ('sequence_media' in event) is matching_asset
    if matching_asset:
        assert event['sequence_media']['sections'][0]['url'] == 'fox'
    assert playback.await_args.args[3] == {7: flow_presentation(raw)['sha256']}


@pytest.mark.asyncio
@pytest.mark.parametrize('binding', [True, False])
async def test_multi_sequence_event_does_not_receive_neighbor_frames(monkeypatch, binding):
    from wuwa_story.api.routes.story import media as route
    from wuwa_story.ingestion.spine_presentation import flow_presentation

    raw = {'Actions': [{'Name': 'PlaySequenceData', 'Params': {'Path': '/Game/A.A'}},
                       {'Name': 'PlaySequenceData', 'Params': {'Path': '/Game/B.B'}}]}
    state = SimpleNamespace(node_id=7, state_key='Flow')
    actions = [(SimpleNamespace(node_id=10 + i, action_index=i),
                state, f'action:{i}') for i in range(2)]

    async def links(_session, nodes, relations, _release):
        if 'plays_sequence_asset' in relations:
            return {10 + i: [{'relation': 'plays_sequence_asset', 'canonical_key': 'asset:ue:/Game/' + name + '.' + name,
                             'node_id': 20 + i, 'basis': 'authored', 'source': {}}] for i, name in enumerate(('A', 'B'))}
        return {}
    monkeypatch.setattr(route, 'get_quest', AsyncMock(
        return_value=SimpleNamespace(node_id=1, game_quest_id=1)))
    monkeypatch.setattr(route, '_release_id', AsyncMock(return_value=21))
    monkeypatch.setattr(route, 'authored_order', AsyncMock(return_value=None))
    monkeypatch.setattr(route, '_quest_page_state_ids',
                        AsyncMock(return_value=[7]))
    monkeypatch.setattr(route, '_links', links)
    monkeypatch.setattr(route, 'cutscene_flows', AsyncMock(return_value={}))
    monkeypatch.setattr(route, 'cutscene_videos', AsyncMock(return_value={}))
    monkeypatch.setattr(route, 'caption_texts', AsyncMock(return_value={}))
    media = {'composition': {'schema_version': 1, 'state': 'ready', 'reasons': []},
             'game_source': {'asset_version': '3.7.1', 'download_id': 'client'},
             'sections': [{'kind': 'illustration', 'scene_source': 'scene-a', 'url': 'a'},
                          {'kind': 'illustration',
                              'scene_source': 'scene-b', 'url': 'b'},
                          {'kind': 'audio_event', 'scope': 'flow', 'reconstruction_kind': 'levelplay_fixed_start',
                           'source_proof': {'flow_owner': 7, 'presentation_sha256': flow_presentation(raw)['sha256'],
                                            'release_id': 21, 'asset_version': '3.7.1', 'download_id': 'client'},
                           'tracks': [{'url': 'music'}]},
                          {'kind': 'audio_event', 'scope': 'flow', 'reconstruction_kind': 'levelplay_fixed_start',
                      'scene_source': 'scene-b',
                           'source_proof': {'flow_owner': 8, 'presentation_sha256': flow_presentation(raw)['sha256'],
                                            'release_id': 21, 'asset_version': '3.7.1', 'download_id': 'client'},
                           'tracks': [{'url': 'wrong'}]}],
             'scenes': [{'source': 'scene-a'}, {'source': 'scene-b'}],
             'sequence_assets': ([{'asset': '/Game/A.A', 'scenes': ['scene-a']}] if binding else []) +
             [{'asset': '/Game/B.B', 'scenes': ['scene-b']}]}
    monkeypatch.setattr(runtime_service, 'sequence_playback',
                        AsyncMock(return_value={7: media}))
    session = SimpleNamespace(scalars=AsyncMock(return_value=[]), scalar=AsyncMock(return_value=None),
                              execute=AsyncMock(side_effect=[SimpleNamespace(
                                  all=lambda: actions), [(7, raw)]]),
                              get=AsyncMock(return_value=SimpleNamespace(game_version='3.7.1')))
    result = await route.quest_media('1', '3.7.1', 'en', session)
    first, second = (event['sequence_media'] for event in result['events'])
    assert [section['url'] for section in first['sections']
            if section['kind'] == 'illustration'] == (['a'] if binding else [])
    assert [section['url'] for section in second['sections']
            if section['kind'] == 'illustration'] == ['b']
    assert [section['tracks'][0]['url'] for section in second['sections']
            if section['kind'] == 'audio_event'] == ['music']
    if binding:
        assert [section['tracks'][0]['url'] for section in first['sections']
                if section['kind'] == 'audio_event'] == ['music']
    if not binding:
        assert [section['tracks'][0]['url'] for section in first['sections']
                if section['kind'] == 'audio_event'] == ['music']
        assert first['composition']['state'] == 'unsupported'
        assert first['composition']['reasons'][-1]['code'] == 'scene_binding_unavailable'


@pytest.mark.asyncio
async def test_runtime_quest_sequence_without_explicit_flow_owner_is_unavailable(monkeypatch):
    from wuwa_story.api.routes.story import media as route

    block = {'id': 'quest-node', 'owner': 'quest_node:1:1', 'branches': [],
             'source': {'file': 'BinData/QuestNodeData/questnodedata.json', 'key': '1_1'},
             'media': [{'kind': 'sequence', 'reference': 'asset:ue:/Game/A.A',
                        'raw_path': '$.Data.EnterActions[0].Params.Path', 'basis': 'authored'}]}
    monkeypatch.setattr(route, 'get_quest', AsyncMock(return_value=SimpleNamespace(node_id=1, game_quest_id=1)))
    monkeypatch.setattr(route, '_release_id', AsyncMock(return_value=21))
    monkeypatch.setattr(route, 'authored_order', AsyncMock(return_value={'blocks': [block], 'state_ids': [],
                                                                      'positions': {}, 'mapped_flow_states': []}))
    monkeypatch.setattr(route, '_quest_page_state_ids', AsyncMock(return_value=[]))
    for name in ('_links', 'cutscene_flows', 'cutscene_videos', 'caption_texts'):
        monkeypatch.setattr(route, name, AsyncMock(return_value={}))
    playback = AsyncMock()
    monkeypatch.setattr(runtime_service, 'sequence_playback', playback)
    session = SimpleNamespace(scalars=AsyncMock(return_value=[]), scalar=AsyncMock(return_value=None),
        execute=AsyncMock(side_effect=[SimpleNamespace(all=lambda: []), SimpleNamespace(all=lambda: [])]),
        get=AsyncMock(return_value=SimpleNamespace(game_version='3.7.1')))
    result = await route.quest_media('1', '3.7.1', 'en', session)
    event, = result['events']
    assert event['sequence_media']['sections'] == []
    assert event['sequence_media']['composition']['reasons'] == [{'code': 'flow_owner_unavailable'}]
    playback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('match', [True, False])
async def test_runtime_flow_cue_requires_exact_state_source_and_cue_path(monkeypatch, match):
    from wuwa_story.api.routes.story import media as route

    path = '$.Actions[0].Params.Path'
    block = {'id': 'block', 'owner': 'flow_state:Flow', 'branches': [],
             'source': {'file': 'BinData/flowState/flowstate.json', 'key': 'Flow'},
             'media': [{'kind': 'sequence', 'reference': 'asset:ue:/Game/A.A',
                        'raw_path': path if match else '$.Actions[1].Params.Path', 'basis': 'authored'}]}
    raw = {'Actions': [{'Name': 'PlaySequenceData', 'Params': {'Path': '/Game/A.A'}}]}
    state = SimpleNamespace(node_id=7, state_key='Flow')
    action = SimpleNamespace(node_id=10, action_index=0)
    monkeypatch.setattr(route, 'get_quest', AsyncMock(return_value=SimpleNamespace(node_id=1, game_quest_id=1)))
    monkeypatch.setattr(route, '_release_id', AsyncMock(return_value=21))
    monkeypatch.setattr(route, 'authored_order', AsyncMock(return_value={'blocks': [block], 'state_ids': [7],
                                                                      'positions': {}, 'mapped_flow_states': []}))
    monkeypatch.setattr(route, '_quest_page_state_ids', AsyncMock(return_value=[7]))
    for name in ('_links', 'cutscene_flows', 'cutscene_videos', 'caption_texts'):
        monkeypatch.setattr(route, name, AsyncMock(return_value={}))
    monkeypatch.setattr(runtime_service, 'sequence_playback', AsyncMock(return_value={7: {
        'composition': {'schema_version': 1, 'state': 'ready', 'reasons': []},
        'sections': [{'kind': 'illustration', 'scene_source': 'scene', 'url': 'fox'}],
        'scenes': [{'source': 'scene'}]}}))
    session = SimpleNamespace(scalars=AsyncMock(return_value=[]), scalar=AsyncMock(return_value=None),
        execute=AsyncMock(side_effect=[SimpleNamespace(all=lambda: [(action, state, 'action:1')]),
                                      SimpleNamespace(all=lambda: []), [(7, raw)]]),
        get=AsyncMock(return_value=SimpleNamespace(game_version='3.7.1')))
    result = await route.quest_media('1', '3.7.1', 'en', session)
    event, = result['events']
    if match:
        assert event['sequence_media']['sections'][0]['url'] == 'fox'
    else:
        assert event['sequence_media']['sections'] == []
        assert event['sequence_media']['composition']['state'] == 'unsupported'

import json
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from sqlalchemy.engine.result import IteratorResult, SimpleResultMetaData
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from wuwa_story.config.settings import get_settings
from wuwa_story.db.repositories import sequence_playback as repository
from wuwa_story.story import runtime_service


def blocks():
    return [{'title': 'Objective', 'encounter': {'release_id': 15,
                                                 'client': {'asset_version': '3.7.1', 'download_id': 'client'}, 'trees': [{'root': {
                                                     'title': 'Activity', 'text_key': 'nested', 'children': [],
                                                     'flow_states': [{'state_node_id': 7, 'presentation_sha256': 'presentation'}],
                                                 }}]}}]


def bundle():
    proof = {'schema_version': 1, 'asset_version': '3.7.1', 'download_id': 'client',
             'presentation_sha256': 'presentation', 'package_path': 'story_sequence:flow7'}
    section = {'kind': 'movie_fragment', 'status': 'published', 'file_id': 12,
               'source': '/Game/Scene.4', 'file': 'media/Client/Content/Aki/Movies/a.mp4',
               'archive_path': 'Client/Content/Aki/Movies/a.mp4', 'object_key': 'wrong'}
    media = {'schema_version': 1, 'complete_video': True, 'game_source': proof,
             'sections': [section]}
    metadata = {'asset_version': '3.7.1', 'download_id': 'client',
                'game_source': proof, 'sequence_media': media}
    return (7, 90, 'story_sequence:flow7', metadata)


@pytest.mark.asyncio
async def test_fragments_use_available_locations_not_unverified_metadata(monkeypatch):
    row = bundle()
    media = row[3]['sequence_media']
    media['sections'].extend([{**media['sections'][0], 'file_id': 13},
                              {'kind': 'subsequence', 'status': 'published', 'file_id': 12}, None])
    monkeypatch.setattr(repository, 'bundles', AsyncMock(return_value=[row]))
    monkeypatch.setattr(repository, 'available_movies', AsyncMock(
        return_value={(7, 12, '/Game/Scene.4'): 'verified'}))
    from wuwa_story.storage.s3 import S3Storage
    monkeypatch.setattr(S3Storage, 'public_url',
                        lambda self, key: 'https://cdn/' + key)
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
    monkeypatch.setattr(runtime_service.repository, 'named_localization_keys', AsyncMock(
        return_value={'nested': 1}))
    monkeypatch.setattr(runtime_service, 'prefetch_localizations', AsyncMock())
    monkeypatch.setattr(runtime_service, '_localized', AsyncMock(
        return_value={'content': 'Defeat the boss'}))
    media = {**bundle()[3]['sequence_media'], 'sections': []}
    monkeypatch.setattr(runtime_service, 'sequence_playback',
                        AsyncMock(return_value={7: media}))
    result = await runtime_service.present_blocks(AsyncMock(), {'blocks': source}, 'en', 15)
    root = result[0]['encounter']['trees'][0]['root']
    assert root['title'] == 'Defeat the boss'
    attached = root['flow_states'][0]['sequence_media']
    assert {key: value for key, value in attached.items() if key !=
            'flow_context'} == media
    assert attached['flow_context']['state_node_id'] == 7
    assert source == before


@pytest.mark.asyncio
async def test_engine_spans_remain_visible_without_movie_file_ids_or_movie_locations(monkeypatch):
    row = bundle()
    camera = {'kind': 'camera', 'status': 'authored', 'source': '/Game/Shot.3',
              'scene_source': '/Game/Shot.0', 'clock': {'numerator': 30, 'denominator': 1},
              'range': {'Value': {'LowerBound': {'Type': 1, 'Value': {'Value': 0}}}},
              'url': 'must-not-be-trusted'}
    row[3]['sequence_media']['sections'].append(camera)
    monkeypatch.setattr(repository, 'bundles', AsyncMock(return_value=[row]))
    monkeypatch.setattr(repository, 'available_movies',
                        AsyncMock(return_value={}))
    result = await runtime_service.sequence_playback(AsyncMock(), blocks(), 15)
    assert result[7]['sections'] == [
        {k: v for k, v in camera.items() if k != 'url'}]
    assert result[7]['owner_node_id'] == 7 and result[7]['bundle_file_id'] == 90
    assert result[7]['schema_version'] == 1


@pytest.mark.asyncio
async def test_async_battle_flow_context_retains_condition_and_action_boundaries(monkeypatch):
    source = blocks()
    node = source[0]['encounter']['trees'][0]['root']
    node.update(owner='level_play_node:1:2', condition={
                'Type': 'WaitBattleCondition', 'ConditionId': 5})
    flow = node['flow_states'][0]
    flow.update(phase='EnterActions', timing='during', **{'async': True},
                cinematics=[{'start': {'action_index': 0, 'anchor': 'before_action'},
                             'end': {'action_index': 0, 'anchor': 'after_action'}}])
    monkeypatch.setattr(runtime_service.repository,
                        'named_localization_keys', AsyncMock(return_value={}))
    monkeypatch.setattr(runtime_service, 'prefetch_localizations', AsyncMock())
    monkeypatch.setattr(runtime_service, 'sequence_playback', AsyncMock(
        return_value={7: bundle()[3]['sequence_media']}))
    result = await runtime_service.present_blocks(AsyncMock(), {'blocks': source}, 'en', 15)
    context = result[0]['encounter']['trees'][0]['root']['flow_states'][0]['sequence_media']['flow_context']
    assert context['node_condition'] == node['condition'] and context['async'] is True
    assert context['phase'] == 'EnterActions' and context['boundaries'] == flow['cinematics']
    assert context['global_clock'] is None
    assert [(m['kind'], m['action_index'], m['anchor'], m['clock']) for m in context['markers']] == [
        ('cinematic_entry', 0, 'before_action', None), ('cinematic_exit', 0, 'after_action', None)]


@pytest.mark.asyncio
async def test_selected_owner_is_not_attached_to_another_occurrence_without_matching_client(monkeypatch):
    source = blocks()
    source.append(deepcopy(source[0]))
    del source[1]['encounter']['client']
    monkeypatch.setattr(runtime_service.repository,
                        'named_localization_keys', AsyncMock(return_value={}))
    monkeypatch.setattr(runtime_service, 'prefetch_localizations', AsyncMock())
    monkeypatch.setattr(runtime_service, 'sequence_playback', AsyncMock(
        return_value={7: bundle()[3]['sequence_media']}))
    presented = await runtime_service.present_blocks(AsyncMock(), {'blocks': source}, 'en', 15)
    assert 'sequence_media' in presented[0]['encounter']['trees'][0]['root']['flow_states'][0]
    assert 'sequence_media' not in presented[1]['encounter']['trees'][0]['root']['flow_states'][0]


@pytest.mark.asyncio
async def test_no_encounters_does_not_query_storage():
    session = AsyncMock()
    assert await runtime_service.sequence_playback(session, [], 15) == {}
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_available_movie_result_is_consumed_as_rows():
    session = AsyncMock()
    owner, file_id, path, metadata = bundle()
    media = metadata['sequence_media']
    section = media['sections'][0]
    proof = {'asset_version': '3.7.1', 'download_id': 'client', 'bundle_file_id': 90,
             'game_source': media['game_source'], 'archive_entry': section['file'],
             'archive_path': section['archive_path'], 'sections': [section['source']]}
    session.execute.return_value = IteratorResult(SimpleResultMetaData(
        ['owner', 'id', 'path', 'name', 'metadata', 'object_key']),
        iter([(7, 12, path, section['file'], proof, 'verified')]))
    selected = {owner: {'file_id': file_id,
                        'source_path': path, 'media': media}}
    assert await repository.available_movies(session, selected, 15, 'media') == {(7, 12, '/Game/Scene.4'): 'verified'}
    sql = session.execute.call_args.args[0].compile()
    assert 'file_reference.release_id' in str(sql)
    assert 'file_reference.owner_node_id' in str(sql)
    assert 'story_sequence_fragment' in sql.params.values()


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', ['asset', 'download', 'presentation', 'inner_proof', 'outer_proof',
                                 'schema0', 'package', 'wrong_owner'])
@pytest.mark.parametrize('valid_candidate', [False, True])
async def test_wrong_newer_bundle_cannot_mask_valid_exact_bundle(monkeypatch, bad, valid_candidate):
    wrong = deepcopy(bundle())
    owner, file_id, path, metadata = wrong
    proof = metadata['game_source']
    if bad in ('asset', 'download', 'presentation'):
        proof[{'asset': 'asset_version', 'download': 'download_id',
               'presentation': 'presentation_sha256'}[bad]] = 'wrong'
    elif bad == 'inner_proof':
        metadata['sequence_media']['game_source'] = {
            **proof, 'download_id': 'wrong'}
    elif bad == 'outer_proof':
        metadata['download_id'] = 'wrong'
    elif bad == 'schema0':
        metadata['sequence_media']['schema_version'] = 0
    elif bad == 'package':
        proof['package_path'] = 'other'
    else:
        owner = 8
    candidates = [(owner, file_id + 1, path, metadata)] + \
        ([bundle()] if valid_candidate else [])
    monkeypatch.setattr(repository, 'bundles',
                        AsyncMock(return_value=candidates))
    monkeypatch.setattr(repository, 'available_movies',
                        AsyncMock(return_value={}))
    result = await runtime_service.sequence_playback(AsyncMock(), blocks(), 15)
    if not valid_candidate:
        assert result == {}
        repository.available_movies.assert_not_awaited()
        return
    selected = repository.available_movies.await_args.args[1]
    assert selected[7]['file_id'] == 90


@pytest.mark.asyncio
@pytest.mark.parametrize('defect', ['missing_client', 'release', 'missing_presentation', 'conflict'])
async def test_missing_or_conflicting_encounter_proof_never_queries_media(defect):
    source = blocks()
    if defect == 'missing_client':
        del source[0]['encounter']['client']
    elif defect == 'release':
        source[0]['encounter']['release_id'] = 14
    elif defect == 'missing_presentation':
        del source[0]['encounter']['trees'][0]['root']['flow_states'][0]['presentation_sha256']
    else:
        other = deepcopy(source[0])
        other['encounter']['client']['download_id'] = 'other'
        source.append(other)
    session = AsyncMock()
    assert await runtime_service.sequence_playback(session, source, 15) == {}
    session.execute.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('defect', ['bundle', 'game_source', 'asset', 'download', 'path',
                                    'entry', 'archive', 'sections'])
async def test_fragment_requires_exact_owner_bundle_provenance_and_section(defect):
    owner, bundle_id, path, metadata = bundle()
    media = metadata['sequence_media']
    section = media['sections'][0]
    proof = {'asset_version': '3.7.1', 'download_id': 'client', 'bundle_file_id': bundle_id,
             'game_source': media['game_source'], 'archive_entry': section['file'],
             'archive_path': section['archive_path'], 'sections': [section['source']]}
    field = {'bundle': 'bundle_file_id', 'game_source': 'game_source', 'asset': 'asset_version',
             'download': 'download_id', 'entry': 'archive_entry', 'archive': 'archive_path',
             'sections': 'sections'}.get(defect)
    if field:
        proof[field] = 'wrong'
    else:
        path = 'other-owner-path'
    session = AsyncMock()
    session.execute.return_value = IteratorResult(SimpleResultMetaData(
        ['owner', 'id', 'path', 'name', 'metadata', 'object_key']),
        iter([(owner, 12, path, section['file'], proof, 'wrong')]))
    selected = {owner: {'file_id': bundle_id,
                        'source_path': 'story_sequence:flow7', 'media': media}}
    assert await repository.available_movies(session, selected, 15, 'media') == {}


@pytest.mark.asyncio
async def test_selected_bundle_never_falls_back_to_another_bundles_available_files(monkeypatch):
    older = bundle()
    newer = (older[0], 91, older[2], deepcopy(older[3]))
    monkeypatch.setattr(repository, 'bundles',
                        AsyncMock(return_value=[newer, older]))
    monkeypatch.setattr(repository, 'available_movies',
                        AsyncMock(return_value={}))
    result = await runtime_service.sequence_playback(AsyncMock(), blocks(), 15)
    assert repository.available_movies.await_args.args[1][7]['file_id'] == 91
    assert result[7]['sections'] == []


@pytest.mark.asyncio
async def test_authored_audio_event_survives_api_without_claiming_published_audio(monkeypatch):
    row = bundle()
    audio = {'kind': 'audio_event', 'status': 'referenced', 'source': '/Game/M0334_All.407',
             'scene_source': '/Game/M0334_All.76', 'reference': '/Game/Aki/WwiseAudio/Events/play_all_sound_m0334',
             'clock': {'numerator': 30, 'denominator': 1},
             'range': {'Value': {'LowerBound': {'Type': 1, 'Value': {'Value': 1}},
                                 'UpperBound': {'Type': 0, 'Value': {'Value': 2035}}}},
             'parameters': {'bSyncAudio': True, 'FadeDuration': 8000}}
    row[3]['sequence_media']['sections'].append(audio)
    monkeypatch.setattr(repository, 'bundles', AsyncMock(return_value=[row]))
    monkeypatch.setattr(repository, 'available_movies', AsyncMock(return_value={}))
    result = await runtime_service.sequence_playback(AsyncMock(), blocks(), 15)
    assert result[7]['schema_version'] == 1 and result[7]['complete_video'] is False
    assert result[7]['sections'] == [audio]
    assert 'url' not in result[7]['sections'][0] and 'file_id' not in result[7]['sections'][0]


def audio_bundle():
    row = bundle()
    row[3]['sequence_media']['sections'] = [{
        'kind': 'audio_event', 'source': '/Game/M0334_All.407', 'scene_source': '/Game/M0334_All.76',
        'reference': '/Game/Aki/WwiseAudio/Events/play_all_sound_m0334', 'status': 'referenced',
        'clock': {'numerator': 30, 'denominator': 1}, 'range': {'authored': 'unchanged'},
        'parameters': {'bSyncAudio': True, 'FadeDuration': 8000}, 'url': 'https://unverified/section',
        'tracks': [{'file_id': 20, 'file': 'audio/zh/male.ogg', 'status': 'published',
                    'language': 'zh', 'rover': 'male', 'url': 'https://unverified/track'},
                   {'file_id': 21, 'file': 'audio/ja/female.ogg', 'status': 'published',
                    'language': 'ja', 'rover': 'female'}]}]
    return row


@pytest.mark.asyncio
async def test_audio_track_url_uses_verified_location_and_retains_authored_metadata(monkeypatch):
    row = audio_bundle()
    original = deepcopy(row)
    monkeypatch.setattr(repository, 'bundles', AsyncMock(return_value=[row]))
    monkeypatch.setattr(repository, 'available_movies', AsyncMock(return_value={}))
    lookup = AsyncMock(return_value={(7, 20, '/Game/M0334_All.407'): 'verified-audio'})
    monkeypatch.setattr(repository, 'available_audio', lookup)
    from wuwa_story.storage.s3 import S3Storage

    monkeypatch.setattr(S3Storage, 'public_url', lambda self, key: 'https://cdn/' + key)
    result = await runtime_service.sequence_playback(AsyncMock(), blocks(), 15)
    audio, = result[7]['sections']
    track, = audio['tracks']
    assert track == {**row[3]['sequence_media']['sections'][0]['tracks'][0], 'url': 'https://cdn/verified-audio'}
    assert audio['parameters'] == {'bSyncAudio': True, 'FadeDuration': 8000}
    assert audio['range'] == {'authored': 'unchanged'} and audio['reference'].endswith('play_all_sound_m0334')
    assert 'url' not in audio and row == original
    assert lookup.await_args.args[1][7]['file_id'] == 90
    assert lookup.await_args.args[2:] == (15, get_settings().s3_bucket)


@pytest.mark.asyncio
async def test_unavailable_audio_tracks_do_not_leak_stored_urls_or_remove_event_metadata(monkeypatch):
    row = audio_bundle()
    monkeypatch.setattr(repository, 'bundles', AsyncMock(return_value=[row]))
    monkeypatch.setattr(repository, 'available_movies', AsyncMock(return_value={}))
    monkeypatch.setattr(repository, 'available_audio', AsyncMock(return_value={}))
    result = await runtime_service.sequence_playback(AsyncMock(), blocks(), 15)
    audio, = result[7]['sections']
    assert audio['tracks'] == [] and 'url' not in audio
    assert audio['kind'] == 'audio_event' and audio['source'] == '/Game/M0334_All.407'
    assert audio['clock'] == {'numerator': 30, 'denominator': 1}
    assert audio['parameters']['FadeDuration'] == 8000


@pytest.mark.asyncio
async def test_audio_bundle_proof_mismatch_never_reaches_track_location_lookup(monkeypatch):
    row = audio_bundle()
    row[3]['sequence_media']['game_source'] = {**row[3]['game_source'], 'download_id': 'other-client'}
    monkeypatch.setattr(repository, 'bundles', AsyncMock(return_value=[row]))
    lookup = AsyncMock(return_value={(7, 20, '/Game/M0334_All.407'): 'must-not-use'})
    monkeypatch.setattr(repository, 'available_audio', lookup)
    assert await runtime_service.sequence_playback(AsyncMock(), blocks(), 15) == {}
    lookup.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('tracks', [None, [], 'invalid', [{'file_id': 20, 'status': 'missing'}],
                                   [{'file_id': True, 'file': 'audio.ogg', 'status': 'published'}]])
async def test_no_published_audio_identities_skips_audio_lookup(monkeypatch, tracks):
    row = audio_bundle()
    row[3]['sequence_media']['sections'][0]['tracks'] = tracks
    monkeypatch.setattr(repository, 'bundles', AsyncMock(return_value=[row]))
    monkeypatch.setattr(repository, 'available_movies', AsyncMock(return_value={}))
    lookup = AsyncMock()
    monkeypatch.setattr(repository, 'available_audio', lookup)
    result = await runtime_service.sequence_playback(AsyncMock(), blocks(), 15)
    assert result[7]['sections'][0]['tracks'] == []
    lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_real_fragment_query_excludes_foreign_owners_releases_and_unavailable_files():
    engine = create_async_engine('sqlite+aiosqlite://')
    try:
        async with engine.begin() as connection:
            await connection.execute(text("ATTACH DATABASE ':memory:' AS storage"))
            await connection.execute(text('CREATE TABLE storage.file_object (id INTEGER, mime_type TEXT)'))
            await connection.execute(text('CREATE TABLE storage.file_location (file_id INTEGER, object_key TEXT, backend TEXT, bucket TEXT, available BOOLEAN, is_primary BOOLEAN)'))
            await connection.execute(text('CREATE TABLE storage.file_reference (id INTEGER, owner_node_id INTEGER, release_id INTEGER, file_id INTEGER, source_path TEXT, source_name TEXT, reference_type TEXT, metadata JSON)'))
            await connection.execute(text("INSERT INTO storage.file_object VALUES (12, 'video/mp4')"))
            await connection.execute(text("INSERT INTO storage.file_location VALUES (12, 'verified', 's3', 'media', 1, 1)"))
        owner, bundle_id, path, metadata = bundle()
        media = metadata['sequence_media']
        section = media['sections'][0]
        proof = {'asset_version': '3.7.1', 'download_id': 'client', 'bundle_file_id': bundle_id,
                 'game_source': media['game_source'], 'archive_entry': section['file'],
                 'archive_path': section['archive_path'], 'sections': [section['source']]}
        selected = {owner: {'file_id': bundle_id,
                            'source_path': path, 'media': media}}
        async with AsyncSession(engine) as session:
            for index, (ref_owner, ref_release) in enumerate([(8, 15), (7, 14)]):
                await session.execute(text('INSERT INTO storage.file_reference VALUES (:id, :owner, :release, 12, :path, :name, :kind, :metadata)'),
                                      {'id': index, 'owner': ref_owner, 'release': ref_release, 'path': path,
                                       'name': section['file'], 'kind': 'story_sequence_fragment', 'metadata': json.dumps(proof)})
            assert await repository.available_movies(session, selected, 15, 'media') == {}
            await session.execute(text('INSERT INTO storage.file_reference VALUES (3, 7, 15, 12, :path, :name, :kind, :metadata)'),
                                  {'path': path, 'name': section['file'], 'kind': 'story_sequence_fragment', 'metadata': json.dumps(proof)})
            assert await repository.available_movies(session, selected, 15, 'media') == {(7, 12, '/Game/Scene.4'): 'verified'}
            await session.execute(text('UPDATE storage.file_location SET available=0'))
            assert await repository.available_movies(session, selected, 15, 'media') == {}
            for index, (ref_owner, ref_release) in enumerate([(8, 15), (7, 14), (7, 15)], 4):
                await session.execute(text('INSERT INTO storage.file_reference VALUES (:id, :owner, :release, 90, :path, NULL, :kind, :metadata)'),
                                      {'id': index, 'owner': ref_owner, 'release': ref_release, 'path': path,
                                       'kind': 'story_presentation', 'metadata': json.dumps(metadata)})
            bundles = await repository.bundles(session, {7}, 15)
            assert len(bundles) == 1
            assert tuple(bundles[0]) == (7, 90, path, metadata)
    finally:
        await engine.dispose()

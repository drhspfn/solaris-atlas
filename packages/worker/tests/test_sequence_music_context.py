import hashlib
import json
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from wuwa_story_worker import sequence_music_context as music


@pytest.fixture
def timelines(monkeypatch):
    values = {
        'state': {'actions': [{'kind': 'SetState', 'time_ms': 0, 'state_group_id': 10, 'state_id': 20}]},
        'play': {'actions': [{'kind': 'Stop', 'time_ms': 0, 'target_id': 3},
                             {'kind': 'Play', 'time_ms': 10, 'target_id': 30}]},
        'stop': {'actions': [{'kind': 'Stop', 'time_ms': 0, 'target_id': 30}]},
    }
    monkeypatch.setattr(music, 'event_timeline', lambda path, _: deepcopy(values[path]))
    calls = []
    monkeypatch.setattr(music, 'validate_event_timeline', lambda timeline, path: calls.append((timeline, path)))
    return values, calls


def binding():
    return music.levelplay_music_binding('state', 'play', 'stop', 'stage4', 'start', 'finish')


def flow_target():
    from wuwa_story.ingestion.spine_presentation import flow_presentation

    presentation = flow_presentation({'StateKey': 'Story_1_29', 'Actions': [
        {'Name': 'PostAkEvent', 'Params': {'EventConfig': {'Type': 'Global', 'AkEvent': '/Game/Aki/WwiseAudio/Events/start.start'}}},
        {'Name': 'ShowTalk', 'Params': {'TalkItems': [
            {'TidTalk': 'first', 'BackgroundConfig': {'Type': 'SpineImage', 'Id': 850007}},
            {'TidTalk': 'second'}]}},
        {'Name': 'PostAkEvent', 'Params': {'EventConfig': {'Type': 'Global', 'AkEvent': '/Game/Aki/WwiseAudio/Events/stop.stop'}}},
    ]})
    return {'owner': 7, 'release_id': 21, 'presentation': presentation, 'presentation_sha256': presentation['sha256']}


def test_flow_music_candidate_is_bound_to_exact_talk_extent():
    target = flow_target()
    result = music.authored_flow_music_context(target, {'asset_version': '3.7.1', 'download_id': 'client'})
    assert result['kind'] == 'flow_authored_music'
    assert result['source_proof']['talk_text_keys'] == ['first', 'second']
    assert (result['source_proof']['play_path'], result['source_proof']['talk_path'], result['source_proof']['stop_path']) == (
        '$.Actions[0]', '$.Actions[1]', '$.Actions[2]')
    assert result['source_proof']['global_clock'] is None


def test_flow_candidate_rejects_tampered_presentation():
    target = flow_target()
    target['presentation']['source']['StateKey'] = 'other'
    with pytest.raises(ValueError, match='presentation proof'):
        music.authored_flow_music_context(target, {'asset_version': '3.7.1', 'download_id': 'client'})


def test_only_whole_file_native_loops_are_exposed():
    assert music.whole_file_loop(
        'wem/303.wem #i #b 30.0\ngroup = -S1 #p 0.01 #@loop')
    assert not music.whole_file_loop('wem/303.wem #i #b 30.0')
    with pytest.raises(ValueError, match='whole-file'):
        music.whole_file_loop('wem/100.wem\nwem/303.wem #@loop\ngroup = -S2')


def test_levelplay_binding_checks_the_consuming_play_and_keeps_source_timelines(timelines):
    _, calls = timelines
    result = binding()
    assert (result['state_group'], result['state'], result['play_target']) == (10, 20, 30)
    assert result['timing_basis'] == 'fixed_start_reconstruction'
    assert calls[0][0]['actions'][0]['kind'] == 'SetState'
    assert calls[0][1] == 'play'
    assert result['bank_timelines']['stop']['actions'][0]['target_id'] == 30


@pytest.mark.parametrize('node,actions', [
    ('state', [{'kind': 'SetState', 'time_ms': 1}]),
    ('play', [{'kind': 'Play', 'time_ms': 0, 'target_id': 30},
              {'kind': 'Play', 'time_ms': 0, 'target_id': 31}]),
    ('play', [{'kind': 'Stop', 'time_ms': 5, 'target_id': 30},
              {'kind': 'Play', 'time_ms': 10, 'target_id': 30}]),
    ('play', [{'kind': 'Play', 'time_ms': 0, 'target_id': 30},
              {'kind': 'Stop', 'time_ms': 0, 'target_id': 30}]),
    ('stop', [{'kind': 'Stop', 'time_ms': 0, 'target_id': 31}]),
])
def test_ambiguous_or_incompatible_bank_controls_are_rejected(timelines, node, actions):
    timelines[0][node]['actions'] = actions
    with pytest.raises(ValueError):
        binding()


@pytest.mark.asyncio
@pytest.mark.parametrize('context', [None, {'kind': 'levelplay_fixed_start', 'source_proof': {
    'asset_version': '3.7.1', 'download_id': 'id', 'release_id': 21, 'flow_owner': 7,
    'presentation_sha256': 'hash'}}])
async def test_native_context_is_resolved_before_reuse_without_mutating_payload(monkeypatch, context):
    from wuwa_story_worker import entity_media, sequence_levelplay_context

    resolver = AsyncMock(return_value=context)
    monkeypatch.setattr(sequence_levelplay_context,
                        'resolve_levelplay_context', resolver)
    monkeypatch.setattr(music, 'authored_flow_music_context', lambda *_: None)
    original = {'kind': 'spine', 'sequence_metadata_revision': 5, 'presentation': {'source': {}},
                'owner': 7, 'release_id': 21, 'presentation_sha256': 'hash'}
    targets = await entity_media.resolve_soundtrack_targets('root', [original, {'kind': 'image'}], 'fmodel',
                                                            {'download_id': 'id', 'asset_version': '3.7.1'})
    digest = hashlib.sha256(json.dumps(context, ensure_ascii=False, sort_keys=True,
                                       separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    assert targets[0]['soundtrack_context'] == context
    assert targets[0]['soundtrack_context_sha256'] == digest
    assert 'soundtrack_context' not in original and 'soundtrack_context' not in targets[1]
    resolver.assert_awaited_once()


@pytest.mark.asyncio
async def test_native_resolver_error_does_not_become_a_missing_context(monkeypatch):
    from wuwa_story_worker import entity_media, sequence_levelplay_context

    monkeypatch.setattr(music, 'authored_flow_music_context', lambda *_: None)
    monkeypatch.setattr(sequence_levelplay_context, 'resolve_levelplay_context', AsyncMock(
        side_effect=RuntimeError('unavailable')))
    with pytest.raises(RuntimeError, match='unavailable'):
        await entity_media.resolve_soundtrack_targets('root', [{'kind': 'spine', 'sequence_metadata_revision': 5,
                                                                'presentation': {'source': {}}}], 'fmodel', {})

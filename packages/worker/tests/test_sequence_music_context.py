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


def test_denia_pair_is_explicitly_reconstructed_and_keeps_authored_state_path():
    from wuwa_story.ingestion.spine_presentation import flow_presentation
    target = flow_target()
    source = target['presentation']['source']
    source['Actions'][0]['Params']['EventConfig']['AkEvent'] = (
        '/Game/Aki/WwiseAudio/Events/play_story_music_daniya_jinzhen_play.play_story_music_daniya_jinzhen_play')
    target['presentation'] = flow_presentation(source)
    target['presentation_sha256'] = target['presentation']['sha256']
    context = music.authored_flow_music_context(target, {'asset_version': '3.7.1', 'download_id': 'client'})
    assert context['kind'] == 'state_consumer_fixed_start'
    assert context['source_proof']['runtime_start_confirmed'] is False
    assert context['source_proof']['state_event'] == context['state_event']
    assert context['source_proof']['consumer_event'] == context['play_event']


@pytest.mark.parametrize('index', [0, 1])
def test_reconstructed_startup_rejects_late_override(index):
    from wuwa_story_worker.wwise_banks import reconstructed_startup_timeline
    state = {'kind': 'SetState', 'time_ms': 1, 'state_group_id': 10, 'state_id': 1}
    actions = [{'kind': 'Play', 'time_ms': 0}]
    actions.insert(index, state)
    with pytest.raises(ValueError, match='after zero time'):
        reconstructed_startup_timeline({'actions': actions}, {10: 2})


def test_reconstructed_startup_preserves_original_and_selected_state():
    from wuwa_story_worker.wwise_banks import reconstructed_startup_timeline
    original = [{'kind': 'SetState', 'time_ms': 0, 'state_group_id': 10, 'state_id': 1},
                {'kind': 'Play', 'time_ms': 0}]
    result = reconstructed_startup_timeline({'actions': original}, {10: 2})
    assert result['source_actions'] == original
    assert result['actions'][0]['state_id'] == 2 and result['actions'][1] == original[1]


def test_reconstructed_startup_accepts_zero_time_state_after_play():
    from wuwa_story_worker.wwise_banks import reconstructed_startup_timeline
    original = [{'kind': 'Play', 'time_ms': 0},
                {'kind': 'SetState', 'time_ms': 0, 'state_group_id': 10, 'state_id': 1}]
    result = reconstructed_startup_timeline({'actions': original}, {10: 2})
    assert result['source_actions'] == original
    assert result['actions'][0]['state_id'] == 2 and result['actions'][1] == original[0]


def test_state_consumer_binding_requires_native_selected_branch_and_matching_stop(monkeypatch):
    timelines = {
        'state': {'actions': [{'kind': 'SetState', 'time_ms': 0, 'state_group_id': 10, 'state_id': 20}]},
        'play': {'actions': [{'kind': 'Stop', 'time_ms': 0, 'target_id': 3},
                             {'kind': 'SetState', 'time_ms': 0, 'state_group_id': 10, 'state_id': 1},
                             {'kind': 'Play', 'time_ms': 0, 'target_id': 9}]},
        'stop': {'actions': [{'kind': 'Stop', 'time_ms': 0, 'target_id': 3}]},
    }
    monkeypatch.setattr(music, 'event_timeline', lambda path, _: deepcopy(timelines[path]))
    validate = []
    monkeypatch.setattr(music, 'validate_event_timeline', lambda timeline, path: validate.append((timeline, path)))
    binding = music.state_consumer_binding('state', 'play', 'stop', 'state', 'play', 'stop')
    assert binding['runtime_start_confirmed'] is False and binding['state'] == 20
    assert validate[0][0]['actions'][0]['state_id'] == 20
    assert binding['bank_timelines']['play']['actions'][1]['state_id'] == 1
    timelines['stop']['actions'][0]['target_id'] = 4
    with pytest.raises(ValueError, match='stop does not match'):
        music.state_consumer_binding('state', 'play', 'stop', 'state', 'play', 'stop')


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

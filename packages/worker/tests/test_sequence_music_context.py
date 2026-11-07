from copy import deepcopy

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

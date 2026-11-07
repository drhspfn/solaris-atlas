"""Validate Wwise controls before reconstructing a LevelPlay music phase.

The caller must establish the LevelPlay route and exact event references from
the pinned client. This module checks the banks, not quest execution order.
"""

from pathlib import Path

from wuwa_story_worker.wwise_banks import event_id, event_timeline, validate_event_timeline


def levelplay_music_binding(state_xml: Path, play_xml: Path, stop_xml: Path,
                            state_event: str, play_event: str, stop_event: str) -> dict:
    """Require one startup state, a consuming Play, and its matching Stop."""
    state = event_timeline(state_xml, event_id(state_event))
    play = event_timeline(play_xml, event_id(play_event))
    stop = event_timeline(stop_xml, event_id(stop_event))
    if (len(state['actions']) != 1 or state['actions'][0]['kind'] != 'SetState'
            or state['actions'][0]['time_ms'] != 0):
        raise ValueError('LevelPlay state event is not a single immediate SetState')
    plays = [action for action in play['actions'] if action['kind'] == 'Play']
    if (len(plays) != 1 or any(action['kind'] not in {'Play', 'Stop'} for action in play['actions'])
            or any(action['time_ms'] != 0 or index > play['actions'].index(plays[0])
                   for index, action in enumerate(play['actions']) if action['kind'] == 'Stop')):
        raise ValueError('LevelPlay music event is not a single supported Play')
    stops = stop['actions']
    if (len(stops) != 1 or stops[0]['kind'] != 'Stop' or stops[0]['time_ms'] != 0
            or stops[0]['target_id'] != plays[0]['target_id']):
        raise ValueError('LevelPlay stop event does not stop the selected music')
    binding = state['actions'][0]
    validate_event_timeline({'actions': [binding, *play['actions']]}, play_xml)
    return {'state_group': binding['state_group_id'], 'state': binding['state_id'],
            'play_target': plays[0]['target_id'],
            'state_event': state_event, 'play_event': play_event, 'stop_event': stop_event,
            'timing_basis': 'fixed_start_reconstruction',
            'bank_timelines': {'state': state, 'play': play, 'stop': stop}}

from copy import deepcopy

from wuwa_story.story.cutscene_occurrences import group_cutscene_occurrences


def event(state, position, reference='cutscene:movie'):
    return dict(kind='cutscene', reference=reference, flow_state=state,
                action=state + ':1', action_index=1, story_position=position,
                source={'file': 'flowstate', 'raw_path': state},
                playback={'entry': 'gender', 'nodes': [
                    {'id': 'gender', 'kind': 'choice', 'options': [
                        {'rover': 'male', 'next': 'm'}, {'rover': 'female', 'next': 'f'}]}]},
                transcript_states=[state])


def test_shared_movie_has_one_player_and_preserves_every_entry_and_branch():
    original = {'events': [event('mp4', [2]), event('engine', [1]),
                           event('other', [3], 'cutscene:other')]}
    before = deepcopy(original)
    result = group_cutscene_occurrences(original)
    assert len(result['events']) == 2
    movie = result['events'][0]
    assert movie['flow_state'] == 'engine'
    assert [p['flow_state']
            for p in movie['entry_points']] == ['engine', 'mp4']
    assert movie['transcript_states'] == ['engine', 'mp4']
    assert movie['playback'] == original['events'][0]['playback']
    assert original == before
    assert group_cutscene_occurrences(result) == result


def test_single_movie_and_non_movie_events_remain_available():
    source = {'events': [
        dict(kind='sequence', reference='asset:sequence'), event('one', [1])]}
    result = group_cutscene_occurrences(source)
    assert len(result['events']) == 2
    assert result['events'][0] == source['events'][0]
    assert 'entry_points' not in result['events'][1]


def test_separate_calls_retain_positions_conditions_and_sources():
    first, second = event('first', [1]), event('second', [9])
    second['branches'] = [{'id': 'quest:choice', 'type': 'Select'}]
    result = group_cutscene_occurrences(
        {'events': [first, second]})['events'][0]
    assert result['entry_points'][1]['story_position'] == [9]
    assert result['entry_points'][1]['branches'] == second['branches']
    assert result['entry_points'][1]['source'] == second['source']

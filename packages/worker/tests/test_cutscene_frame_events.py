import copy
import hashlib
import json
import sqlite3
from pathlib import Path

import flatbuffers
import pytest

from wuwa_story_worker.cutscene_frame_events import (
    discover_launcher_frame_audio,
    discover_movie_audio,
    movie_frame_bindings,
)

FIXTURES = Path(__file__).parent / 'fixtures'


@pytest.fixture
def actual():
    return json.loads((FIXTURES / 'cutscene_frame_events.json').read_text())


def test_real_m0374_event_is_not_suppressed_by_caption_voice_flags(actual):
    source = {'file': 'BinData/flowState/flowstate.json', 'source_record_id': 64656466,
              'raw_path': '$[15358].Actions'}
    raw = json.dumps([actual['m0374']])
    result = discover_movie_audio(raw, video_name='M0374', source=source)
    event, = result['events']
    assert event['reference'] == '/Game/Aki/WwiseAudio/Events/play_all_sound_m0374.play_all_sound_m0374'
    assert event['start_seconds'] == 0 and event['end_seconds'] is None
    assert event['mode'] == 'MusicSubtitle' and event['operation'] == 'post'
    assert event['source']['raw_path'] == '$[15358].Actions<json>[0].Params.Mp4FrameEvents[0].EventActions[0]'
    assert event['source']['source_record_id'] == 64656466
    assert event['resolved'] and not result['unresolved']
    assert json.loads(raw) == [actual['m0374']]


def test_real_shared_clock_seconds_and_equal_time_js_order(actual):
    movie = next(v['action'] for v in actual['other_actual_actions'] if v['action']['Params']['VideoName'] == 'M3_2_13')
    result = discover_movie_audio([movie])
    assert [e['start_seconds'] for e in result['events']] == [0, 101]
    assert [e['mode'] for e in result['events']] == ['MusicSubtitle', 'Global']
    assert [e['raw_action']['Name'] for e in result['frame_actions']] == [
        'PostAkEvent', 'OpenSystemBoard', 'PostAkEvent', 'FadeInScreen']
    assert not result['unresolved']


def test_actual_visual_frames_remain_evidence_not_fake_audio(actual):
    movie = next(v['action'] for v in actual['other_actual_actions'] if v['action']['Params']['VideoName'] == 'M0361')
    result = discover_movie_audio([movie])
    assert not result['events'] and not result['unresolved']
    assert result['frame_actions'][0]['classification'] == 'non_audio'


@pytest.mark.parametrize('second', [True, -1, float('nan'), float('inf'), '0', None])
def test_unverified_frame_clock_is_explicitly_unresolved(actual, second):
    action = copy.deepcopy(actual['m0374'])
    action['Params']['Mp4FrameEvents'][0]['Second'] = second
    result = discover_movie_audio([action])
    assert not result['events']
    assert result['unresolved'][0]['code'] == 'invalid_frame_seconds'


def test_unknown_modes_stop_flags_and_conditional_commands_not_silent(actual):
    action = copy.deepcopy(actual['m0374'])
    frame = action['Params']['Mp4FrameEvents'][0]
    command = frame['EventActions'][0]
    command['Params']['EventConfig'].update(Type='NewClientMode', EndMoment=3)
    frame['EventActions'].append({'Name': 'Branch', 'Params': {'Actions': [actual['m0374']]}})
    result = discover_movie_audio([action])
    assert len(result['events']) == 1 and not result['events'][0]['resolved']
    assert result['events'][0]['end_seconds'] is None
    assert {u['code'] for u in result['unresolved']} == {
        'unknown_audio_event_mode', 'uncompiled_audio_flags', 'uncompiled_frame_action'}
    assert result['events'][0]['event_config']['EndMoment'] == 3


def test_unverified_native_fields_are_not_assumed_aliases(actual):
    action = copy.deepcopy(actual['m0374'])
    action['Params']['MovieEvent'] = action['Params'].pop('Mp4FrameEvents')
    result = discover_movie_audio([action])
    assert not result['events']
    assert result['unresolved'][0]['code'] == 'unverified_native_movie_field'


def test_native_launcher_list_and_json_frames_match_verified_raw(actual):
    frames = actual['m0374']['Params']['Mp4FrameEvents']
    result = discover_launcher_frame_audio(json.dumps(frames), video_name='M0374')
    assert result['events'][0]['start_seconds'] == 0
    assert result['events'][0]['resolved']


def native(actions):
    builder = flatbuffers.Builder(256)
    value = builder.CreateString(json.dumps(actions))
    builder.StartObject(7)
    builder.PrependUOffsetTRelativeSlot(6, value, 0)
    root = builder.EndObject()
    builder.Finish(root)
    return bytes(builder.Output())


def test_native_real_blob_and_separate_same_movie_contexts(tmp_path, actual):
    blob = (FIXTURES / 'm0374_frame_flowstate.bin').read_bytes()
    assert hashlib.sha256(blob).hexdigest() == actual['native_record_sha256']
    other = copy.deepcopy(actual['m0374'])
    other['Params']['Mp4FrameEvents'][0]['Second'] = 12
    database = tmp_path / 'db_flowState.db'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE flowstate(StateKey TEXT, BinData BLOB)')
        db.executemany('INSERT INTO flowstate VALUES (?, ?)', [
            (actual['state_key'], blob), ('Other_context', native([other]))])
    before = database.read_bytes()
    bindings = movie_frame_bindings(tmp_path, 'M0374')
    assert len(bindings) == 2
    assert sorted(b['events'][0]['start_seconds'] for b in bindings) == [0, 12]
    original = next(b for b in bindings if b['source']['state_key'] == actual['state_key'])
    assert original['action_index'] == 2
    assert '.Actions<json>[2].Params.' in original['events'][0]['source']['raw_path']
    assert all(b['complete'] for b in bindings)
    assert database.read_bytes() == before


def test_missing_config_is_not_created_or_treated_as_silence(tmp_path):
    with pytest.raises(FileNotFoundError):
        movie_frame_bindings(tmp_path, 'M0374')
    assert not (tmp_path / 'db_flowState.db').exists()


def test_nested_movie_is_never_flattened_as_unconditional_audio(tmp_path, actual):
    branch = {'Name': 'If', 'Params': {'ThenActions': [actual['m0374']]}}
    parsed = discover_movie_audio([branch], video_name='M0374')
    assert not parsed['events']
    assert parsed['unresolved'][0]['code'] == 'nested_movie_requires_runtime_context'
    with sqlite3.connect(tmp_path / 'db_flowState.db') as db:
        db.execute('CREATE TABLE flowstate(StateKey, BinData)')
        db.execute('INSERT INTO flowstate VALUES (?,?)', ('conditional', native([branch])))
    binding, = movie_frame_bindings(tmp_path, 'M0374')
    assert not binding['complete'] and not binding['events']


def test_json_encoded_frame_list_inside_native_actions(tmp_path, actual):
    action = copy.deepcopy(actual['m0374'])
    action['Params']['Mp4FrameEvents'] = json.dumps(action['Params']['Mp4FrameEvents'])
    with sqlite3.connect(tmp_path / 'db_flowState.db') as db:
        db.execute('CREATE TABLE flowstate(StateKey, BinData)')
        db.execute('INSERT INTO flowstate VALUES (?,?)', ('encoded', native([action])))
    binding, = movie_frame_bindings(tmp_path, 'M0374')
    assert binding['complete'] and len(binding['events']) == 1
    assert '.Mp4FrameEvents<json>[0].EventActions[0]' in binding['events'][0]['source']['raw_path']


def test_target_and_unknown_flags_retain_unresolved_source(actual):
    action = copy.deepcopy(actual['m0374'])
    params = action['Params']['Mp4FrameEvents'][0]['EventActions'][0]['Params']
    params['EventConfig'].update(Type='Target', EntityId=123)
    params['PersistWhenExitDungeon'] = True
    result = discover_movie_audio([action])
    assert not result['events'][0]['resolved']
    assert result['events'][0]['raw_action']['Params']['PersistWhenExitDungeon'] is True
    assert {'target_audio_requires_entity_context', 'uncompiled_audio_flags'} <= {
        item['code'] for item in result['unresolved']}


def test_corrupt_native_matching_record_is_incomplete(tmp_path):
    with sqlite3.connect(tmp_path / 'db_flowState.db') as db:
        db.execute('CREATE TABLE flowstate(StateKey, BinData)')
        db.execute('INSERT INTO flowstate VALUES (?,?)', ('broken', b'M0374'))
    binding, = movie_frame_bindings(tmp_path, 'M0374')
    assert not binding['complete']
    assert binding['unresolved'][0]['code'] == 'invalid_native_actions'

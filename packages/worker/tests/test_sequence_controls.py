import json

import pytest

from wuwa_story_worker.sequence_controls import opened_ui_resources, sequence_control_score

PACKAGE = '/Game/Story/Scene'
LIBRARY = '/Game/Aki/TypeScript/Game/Module/Sequence/PlotBlueprintFunctionLibrary'


def fixture(tmp_path, *, rate=None, library=LIBRARY):
    def ref(kind, name, index):
        return {'ObjectName': f"{kind}'{name}'", 'ObjectPath': f'files/Client/Content/Story/Scene.{index}'}
    def const(value):
        return {'Token': 'EX_StringConst', 'Value': value}
    exports = [
        {'Type': 'Function', 'Name': 'Entry', 'Outer': ref('BlueprintGeneratedClass', 'Director_C', 4),
         'ScriptBytecode': [
             {'Token': 'EX_Context', 'StatementIndex': 0,
              'ObjectExpression': {'Token': 'EX_ObjectConst', 'Value': {'ObjectName': "Library_C'Default'", 'ObjectPath': library}},
              'ContextExpression': {'Token': 'EX_LocalVirtualFunction', 'Function': 'OpenUiView',
                                    'Parameters': [const('key'), const('key'), const('start'), const('start'), {'Token': 'EX_False'}]}},
             {'Token': 'EX_Return', 'StatementIndex': 100, 'Expression': {'Token': 'EX_Nothing'}}]},
        {'Type': 'MovieScene', 'Properties': {'TickResolution': rate or {'Numerator': 30}}},
        {'Type': 'MovieSceneEventTrack', 'Outer': ref('MovieScene', 'Scene', 1)},
        {'Type': 'MovieSceneEventTriggerSection', 'Outer': ref('MovieSceneEventTrack', 'Track', 2),
         'Properties': {'EventChannel': {'KeyTimes': [{'Value': 90}],
                                        'KeyValues': [{'Ptrs': {'Function': ref('Function', 'Director_C:Entry', 0)}}]}}},
        {'Type': 'BlueprintGeneratedClass', 'Name': 'Director_C'},
        {'Type': 'MovieSceneDialogueSection', 'Outer': ref('MovieSceneEventTrack', 'Track', 2),
         'Properties': {'DialogueInfo': {'SourceString': 'story.line'}, 'GuardTime': 120}},
    ]
    decoded = tmp_path / 'decoded'
    decoded.mkdir()
    (decoded / 'Scene.json').write_text(json.dumps(exports), encoding='utf-8')
    return {PACKAGE: {'decoded': decoded}}, exports


def test_event_frame_and_opening_are_resolved_using_owning_scene_clock(tmp_path):
    assets, _ = fixture(tmp_path)
    score = sequence_control_score(assets)
    event, = score['events']
    assert event['frame'] == 90 and event['time_seconds'] == 3
    assert event['scene_source'] == PACKAGE + '.1'
    assert event['calls'][0]['function'] == 'OpenUiView'
    assert score['runtime_traversal'] is False
    openings = opened_ui_resources(score)
    assert [entry['rover'] for entry in openings] == ['male', 'female']
    assert all(entry['animation'] == 'start' and entry['loop'] is False for entry in openings)


def test_same_function_name_on_an_unrelated_receiver_is_not_a_spine_binding(tmp_path):
    assets, _ = fixture(tmp_path, library='/Game/Unrelated/Library')
    assert opened_ui_resources(sequence_control_score(assets)) == []


@pytest.mark.parametrize('rate,seconds', [({'Numerator': 60000, 'Denominator': 1001}, 1.5015),
                                        ({'Numerator': 0}, None), ({'Denominator': 1}, None)])
def test_invalid_or_fractional_clock_is_never_replaced_with_assumed_30fps(tmp_path, rate, seconds):
    assets, _ = fixture(tmp_path, rate=rate)
    assert sequence_control_score(assets)['events'][0]['time_seconds'] == seconds


def test_dialogue_links_use_exact_localization_identity_and_keep_audio_commands(tmp_path):
    assets, _ = fixture(tmp_path)
    audio = {'kind': 'audio_action', 'source_path': '$.Actions[0]', 'command': {'Name': 'PostAkEvent'}}
    presentation = {'cues': [{'kind': 'dialogue', 'text_key': 'story.line', 'source_path': '$.TalkItems[4]'},
                             {'kind': 'dialogue', 'text_key': 'different', 'source_path': '$.TalkItems[5]'}, audio]}
    score = sequence_control_score(assets, presentation)
    assert score['dialogues'][0]['talk_sources'] == ['$.TalkItems[4]']
    assert score['dialogues'][0]['command']['GuardTime'] == 120  # Original units preserved.
    assert score['flow_audio_actions'] == [audio]


def test_mismatched_channel_is_reported_without_pairing_unrelated_keys(tmp_path):
    assets, exports = fixture(tmp_path)
    exports[3]['Properties']['EventChannel']['KeyValues'] = []
    (tmp_path / 'decoded/Scene.json').write_text(json.dumps(exports), encoding='utf-8')
    score = sequence_control_score(assets)
    assert score['events'] == [] and score['unresolved'][0]['code'] == 'mismatched_event_channel'


def test_preload_keys_are_kept_separate_from_confirmed_open_commands(tmp_path):
    assets, exports = fixture(tmp_path)
    exports.append({'Type': 'BP_SequenceData_Generated_C', 'Properties': {'PreloadUiArray': ['other_key']}})
    (tmp_path / 'decoded/Scene.json').write_text(json.dumps(exports), encoding='utf-8')
    score = sequence_control_score(assets)
    assert score['preloads'][0]['prefab_key'] == 'other_key'
    assert score['preloads'][0]['usage'] == 'preload_only'
    assert {entry['prefab_key'] for entry in opened_ui_resources(score)} == {'key'}


def test_array_open_keeps_authored_clip_pairs_and_retains_unknown_arrays_without_guessing():
    def score(clips):
        return {'events': [{'source': 'scene.3', 'frame': 8, 'calls': [{
            'function': 'OpenUiViewInArray', 'receiver': {'reference': {'ObjectPath': LIBRARY}},
            'arguments': ['key', 'key', clips, clips]}]}]}
    entries = opened_ui_resources(score([{'Name': 'pose', 'NeedLoop': True}, {'Name': 'move', 'NeedLoop': False}]))
    assert [entry['animation'] for entry in entries] == ['pose', 'move', 'pose', 'move']
    entries = opened_ui_resources(score({'symbol': 'dynamic'}))
    assert len(entries) == 2 and all(entry['animation'] is None for entry in entries)

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.ingestion.entity_media import flow_background_paths, flow_spine_ids
from wuwa_story.ingestion.spine_presentation import flow_presentation


def test_score_keeps_branches_audio_and_external_cues_without_inventing_timing():
    talk = {'Id': 8, 'TidTalk': 'story.line', 'WhoId': 5, 'PlayVoice': True,
            'AudioDelay': 0.3, 'GuardTime': 2,
            'BackgroundConfig': {'Type': 'SpineImage', 'Id': 7, 'IsLoop': True},
            'TalkAkEvent': {'AkEvent': '/Game/Music.Play'},
            'Actions': [{'Name': 'PlaySpine', 'Params': {
                'Config': {'Type': 'Play', 'Name': '1to2', 'IsLoop': False}}}]}
    raw = {'StateKey': 'state', 'Actions': json.dumps([
        {'Name': 'ShowTalk', 'Params': {'TalkItems': [talk],
                                        'SequenceTransitions': {'0': [{'NextSequenceIndex': 2}]}}},
        {'Name': 'PlaySequenceData', 'Params': {'Path': '/Game/Story.Sequence'}},
        {'Name': 'PostAkEvent', 'Params': {'EventConfig': {'AkEvent': '/Game/Music.Event'}}}])}
    result = flow_presentation(raw)
    assert result['resource_ids'] == [7]
    assert result['timing_status'] == 'unresolved'
    assert result['ordering'] == 'authored_structure'
    assert result['source']['Actions'][0]['Params']['SequenceTransitions']
    dialogue, = [cue for cue in result['cues'] if cue['kind'] == 'dialogue']
    assert dialogue['command'] == talk
    animation, = [cue for cue in result['cues']
                  if cue['kind'] == 'play_animation']
    assert animation['source_path'] == '$.Actions[0].Params.TalkItems[0].Actions[0].Params.Config'
    assert animation['command']['Name'] == '1to2'
    assert 'duration' not in animation and 'start_time' not in animation
    assert any(cue['kind'] == 'audio_action' for cue in result['cues'])
    assert result['unresolved'][0]['asset'] == '/Game/Story.Sequence'
    assert result == flow_presentation(raw)
    assert raw['Actions'].startswith('[')  # Source is not mutated.
    assert result == flow_presentation(json.loads(json.dumps(raw, sort_keys=True)))


def test_nested_backgrounds_are_discovered_and_unrelated_numeric_ids_are_not():
    raw = {'Actions': [{'Name': 'Branch', 'Params': {'SuccessActions': [
        {'Name': 'ShowTalk', 'Params': {'TalkItems': [
            {'BackgroundConfig': {'Type': 'SpineImage', 'Id': 7}},
            {'BackgroundConfig': {'Type': 'Image', 'ImageAsset': '/Game/Still.Still'}}]}}],
        'ActorMontageArray': [{'MontageId': 900005}], 'FaceExpressionId': 900005}}]}
    assert flow_spine_ids(raw) == {7}
    assert flow_background_paths(raw) == {'/Game/Still.Still'}
    assert flow_presentation({'Actions': None})['cues'] == []
    assert flow_presentation({'Actions': '[bad json'})['resource_ids'] == []


def test_pose_and_frame_events_are_kept_for_the_external_clock():
    result = flow_presentation({'FrameEvents': [{'EventKey': 'cue', 'EventActions': [
        {'Name': 'PlaySpine', 'Params': {'Config': {'Type': 'Play', 'Name': '2', 'IsLoop': False}}}]}]})
    assert result['unresolved'][0]['code'] == 'frame_event_clock_not_decoded'
    assert len(result['cues']) == 2
    assert result['cues'][1]['command']['Name'] == '2'
    changed = flow_presentation({'FrameEvents': []})
    assert result['sha256'] != changed['sha256']


def test_sequence_showtalk_keeps_talk_event_bindings_and_exact_controller_reference():
    event = {'Position': {'TalkItemId': 8}, 'FrameEvent': {
        'EventKey': 'cue', 'EventActions': [{'Name': 'PlaySpine', 'Params': {
            'Config': {'Type': 'Play', 'Name': 'close', 'IsLoop': False}}}]}}
    result = flow_presentation({'Actions': [{'Name': 'ShowTalk', 'Params': {
        'SequenceDataAsset': '/Game/Story/Data.Data', 'TalkFrameEvents': [event]}}]})
    sequence, = [cue for cue in result['cues'] if cue['kind'] == 'sequence']
    assert sequence['source_path'] == '$.Actions[0].Params.SequenceDataAsset'
    frames, = [cue for cue in result['cues'] if cue['kind'] == 'frame_events']
    assert frames['command'] == [event]
    assert 'time' not in frames


@pytest.mark.asyncio
async def test_sequence_only_flow_is_not_dropped_when_it_has_no_spine_background_id():
    from wuwa_story.ingestion.entity_media import MediaRequest, entity_media_targets
    raw = {'StateKey': 'Story_1_2', 'Actions': [{'Name': 'ShowTalk', 'Params': {
        'SequenceDataAsset': '/Game/Story/Data.Data', 'TalkItems': [{'TidTalk': 'line'}]}}]}
    node = SimpleNamespace(id=5, canonical_key='flow_state:Story_1_2')
    session = SimpleNamespace(scalar=AsyncMock(side_effect=[7, node, raw]))
    request = MediaRequest(download_id='a' * 64, asset_version='3.7.0', game_version='3.7.0',
                           entities=[node.canonical_key], kinds=['image'])
    target, = await entity_media_targets(session, request)
    assert target['owner'] == 5 and target['reference_type'] == 'story_presentation'
    assert target['resource_id'] is None and target['spine_recipe'] == 3
    assert target['presentation']['cues'][0]['kind'] == 'sequence'

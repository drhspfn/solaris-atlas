import pytest
from wuwa_story_worker.model_montage import parse_montage, segment_samples, package_path


def sequence():
    return {'file': 'Client/Content/part.ueanim', 'sha256': '123', 'duration': 5, 'matches_skeleton': True}


def row():
    seg = {'AnimReference': {'ObjectPath': '/Game/part'}, 'StartPos': 0, 'AnimStartTime': 1,
           'AnimEndTime': 3, 'AnimPlayRate': 2, 'LoopingCount': 2}
    return {'Type':'AnimMontage', 'Name':'Example', 'Properties':{'SequenceLength':2, 'SlotAnimTracks':[
        {'SlotName':'DefaultSlot', 'AnimTrack':{'AnimSegments':[seg]}}], 'CompositeSections':[
        {'SectionName':'Loop','NextSectionName':'Loop','LinkValue':0}]}}


def test_retimes_trims_and_preserves_loop_section():
    plan = parse_montage(row(), {package_path('/Game/part'): sequence()})
    assert plan['sections'] == [{'name':'Loop','next':'Loop','time':0}]
    assert segment_samples(plan['segments'][0], [0, 2, 4]) == [(0,1),(.5,2),(1,3),(1,1),(1.5,2),(2,3)]


def test_reverse_rate():
    montage = row()
    montage['Properties']['SlotAnimTracks'][0]['AnimTrack']['AnimSegments'][0]['AnimPlayRate'] = -2
    segment = parse_montage(montage, {package_path('/Game/part'): sequence()})['segments'][0]
    assert segment_samples(segment, [2]) == [(0,3),(.5,2),(1,1),(1,3),(1.5,2),(2,1)]


def test_missing_or_incompatible_sequences_are_errors():
    with pytest.raises(ValueError, match='Missing or incompatible'):
        parse_montage(row(), {})
    with pytest.raises(ValueError, match='Missing or incompatible'):
        parse_montage(row(), {package_path('/Game/part'): {**sequence(), 'matches_skeleton': False}})


def test_rejects_unsupported_overlap():
    montage = row();montage['Properties']['SlotAnimTracks'][0]['AnimTrack']['AnimSegments'][0]['StartPos'] = .5
    with pytest.raises(ValueError, match='gap or overlap'):
        parse_montage(montage, {package_path('/Game/part'): sequence()})


def test_rejects_path_traversal_and_unresolved_sections():
    with pytest.raises(ValueError, match='Unsafe'):
        package_path('/Game/../bad')
    montage = row();montage['Properties']['CompositeSections'][0]['NextSectionName']='missing'
    assert parse_montage(montage, {package_path('/Game/part'): sequence()})['unresolved_sections'] == ['missing']


def test_non_absolute_section_is_not_misinterpreted():
    montage = row()
    montage['Properties']['CompositeSections'][0]['LinkMethod'] = 'EAnimLinkMethod::Relative'
    with pytest.raises(ValueError, match='Non-absolute'):
        parse_montage(montage, {package_path('/Game/part'): sequence()})


def test_retains_authored_blend_and_notifies():
    montage = row()
    montage['Properties']['BlendIn'] = {'BlendTime': 0.2}
    montage['Properties']['Notifies'] = [{'Name': 'footstep'}]
    plan = parse_montage(montage, {package_path('/Game/part'): sequence()})
    assert plan['blend_in'] == {'BlendTime': 0.2}
    assert plan['notifies'] == [{'Name': 'footstep'}]

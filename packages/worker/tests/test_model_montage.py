import pytest

from wuwa_story_worker.model_montage import package_path, parse_montage, segment_samples


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
    montage = row()
    montage['Properties']['SlotAnimTracks'][0]['AnimTrack']['AnimSegments'][0]['StartPos'] = .5
    with pytest.raises(ValueError, match='gap or overlap'):
        parse_montage(montage, {package_path('/Game/part'): sequence()})


def test_rejects_path_traversal_and_unresolved_sections():
    with pytest.raises(ValueError, match='Unsafe'):
        package_path('/Game/../bad')
    montage = row()
    montage['Properties']['CompositeSections'][0]['NextSectionName']='missing'
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


def test_plan_keeps_montage_and_sequence_identity(tmp_path):
    import json

    from wuwa_story_worker.model_montage import create_montage_clips, montage_references

    montage = row()
    (tmp_path / 'montage.json').write_text(json.dumps([montage]), encoding='utf8')
    source = {'source': 'montage.json', 'name': 'Example', 'type': 'AnimMontage', 'matches_skeleton': True}
    sources = {'client/content/example': source}
    definitions, references = montage_references(sources, tmp_path)
    assert references == {'client/content/part'}
    seen = set()
    clips = create_montage_clips(definitions, sources, seen, {'client/content/part': sequence()}, set())
    assert len(clips) == 1
    assert clips[0]['montage']['sections'][0]['next'] == 'Loop'
    assert clips[0]['montage']['preview_sampling_fps'] == 30
    assert seen == {'client/content/example'}


def test_glb_metadata_preserves_segments_and_rejects_missing(tmp_path):
    import json
    import struct

    from wuwa_story_worker.model_montage import annotate_montage_glb

    path = tmp_path / 'animation.glb'
    body = json.dumps({'asset': {'version': '2.0'}, 'animations': [{'name': 'Example'}]}).encode()
    body += b' ' * (-len(body) % 4)
    tail = struct.pack('<I4s', 4, b'BIN\0') + b'data'
    path.write_bytes(struct.pack('<4sII', b'glTF', 2, 20 + len(body) + len(tail)) +
                     struct.pack('<I4s', len(body), b'JSON') + body + tail)
    clips = [{'name': 'Example', 'montage': {'sections': [{'name': 'Loop', 'next': 'Loop'}]}}]
    annotate_montage_glb(path, clips)
    data = path.read_bytes()
    size, kind = struct.unpack('<I4s', data[12:20])
    assert kind == b'JSON'
    exported = json.loads(data[20:20 + size])
    assert exported['animations'][0]['extras']['solaris_montage'] == clips[0]['montage']
    assert data.endswith(tail)
    before = data
    with pytest.raises(ValueError, match='Missing exported'):
        annotate_montage_glb(path, [{'name': 'missing', 'montage': {'segments': []}}])
    assert path.read_bytes() == before


def test_worker_plan_and_batch_keep_montage_segments(tmp_path):
    import json
    from types import SimpleNamespace

    from wuwa_story_worker.model_bundle import animation_batch, build_animation_plan

    converted = tmp_path / 'converted'
    converted.mkdir()
    part = converted / 'part.ueanim'
    part.write_bytes(b'verified segment')
    model = tmp_path / 'model.uemodel'
    model.write_bytes(b'verified model')
    definition = row()
    (tmp_path / 'montage.json').write_text(json.dumps([definition]), encoding='utf8')
    sources = {
        'client/content/part': {'source': 'part.json', 'name': 'Part', 'type': 'AnimSequence',
                                'matches_skeleton': True, 'skeleton': '/Game/Rig'},
        'client/content/montage': {'source': 'montage.json', 'name': 'Example', 'type': 'AnimMontage',
                                   'matches_skeleton': True, 'skeleton': '/Game/Rig'},
    }
    dto = SimpleNamespace(metadata=SimpleNamespace(num_frames=150, frames_per_second=30, additive_anim_type=0),
                          tracks=[SimpleNamespace(name='root')], curves=[])

    def read(path, catalog):
        assert path == part and catalog is sources
        return SimpleNamespace(object_path='/Game/part', object_name='Part'), dto

    plan = build_animation_plan(model, converted, '/Game/Rig', sources, {'root'}, read, source_root=tmp_path)
    montage = next(clip for clip in plan['clips'] if clip.get('montage'))
    assert montage['name'] == 'Example'
    assert montage['montage']['segments'][0]['file'] == 'part.ueanim'
    assert plan['animation_coverage']['missing_exports'] == 0
    assert animation_batch(plan, model, converted, '/Game/Rig', 1, 1) == [montage]
    part.write_bytes(b'changed')
    with pytest.raises(ValueError, match='hash mismatch'):
        animation_batch(plan, model, converted, '/Game/Rig', 1, 1)


def test_cache_identity_changes_for_any_segment_or_timing():
    from copy import deepcopy

    from wuwa_story_worker.model_animation_packs import clip_identity

    clip = {'source': '/Game/Montage.json', 'file': 'first.ueanim', 'sha256': 'first',
            'montage': {'segments': [{'sha256': 'first'}, {'sha256': 'second'}],
                        'sections': [{'name': 'Loop', 'next': 'Loop'}]}}
    original = clip_identity(clip)
    changed = deepcopy(clip)
    changed['montage']['segments'][1]['sha256'] = 'new'
    assert clip_identity(changed) != original
    changed = deepcopy(clip)
    changed['montage']['sections'][0]['next'] = 'End'
    assert clip_identity(changed) != original


def test_batch_detects_changed_nonfirst_montage_segment(tmp_path):
    from wuwa_story_worker.model_bundle import animation_batch, file_sha256, package_path

    converted = tmp_path / 'converted'
    converted.mkdir()
    first, second = converted / 'first.ueanim', converted / 'second.ueanim'
    first.write_bytes(b'first')
    second.write_bytes(b'second')
    model = tmp_path / 'model.uemodel'
    model.write_bytes(b'model')
    plan = {'schema_version': 1, 'skeleton': package_path('/Game/Rig'), 'model_sha256': file_sha256(model),
            'clips': [{'name': 'montage', 'file': 'first.ueanim', 'sha256': file_sha256(first),
                       'montage': {'segments': [{'file': 'first.ueanim', 'sha256': file_sha256(first)},
                                                {'file': 'second.ueanim', 'sha256': file_sha256(second)}]}}]}
    assert len(animation_batch(plan, model, converted, '/Game/Rig', 0, 1)) == 1
    second.write_bytes(b'new second')
    with pytest.raises(ValueError, match='Montage segment hash mismatch'):
        animation_batch(plan, model, converted, '/Game/Rig', 0, 1)

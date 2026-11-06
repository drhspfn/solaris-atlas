import pytest

from wuwa_story_worker.sequence_media import movie_archive_path, sequence_media_score


def test_movie_fragment_keeps_own_clock_offset_and_authored_range():
    package = '/Game/Scene'
    exports = [
        {'Type': 'MovieScene', 'Properties': {
            'TickResolution': {'Numerator': 60000, 'Denominator': 1001}}},
        {'Type': 'MovieSceneMediaTrack', 'Outer': {'ObjectPath': package + '.0'}},
        {'Type': 'MovieSceneMediaSection', 'Outer': {'ObjectPath': package + '.1'},
         'Properties': {'MediaSource': {'ObjectPath': '/Game/Clip'},
                        'SectionRange': {'Value': {'LowerBound': {'Type': 1, 'Value': {'Value': 402}}}},
                        'StartFrameOffset': {'Value': 102}}},
    ]
    exports = [{**entry, 'Package': package, '_export_index': index}
               for index, entry in enumerate(exports)]
    exports.append({'Type': 'StreamMediaSource', 'Package': '/Game/Clip', '_export_index': 0,
                    'Properties': {'StreamUrl': 'filepath://./Aki/Movies/LevelA_Seq/03_1/M0334_Cam003d.mp4'}})
    result = sequence_media_score(exports)
    section, = result['sections']
    assert section['archive_path'] == 'Client/Content/Aki/Movies/LevelA_Seq/03_1/M0334_Cam003d.mp4'
    assert section['clock'] == {'numerator': 60000, 'denominator': 1001}
    assert section['parameters'] == {'StartFrameOffset': {'Value': 102}}
    assert section['range'] == exports[2]['Properties']['SectionRange']
    assert result['complete_video'] is False
    assert result['runtime_traversal'] is False


@pytest.mark.parametrize('url', [None, 'https://example.com/a.mp4',
                                 'filepath://./Aki/Movies/../../secret.mp4', 'filepath://./Aki/Movies/%2e%2e/a.mp4',
                                 'filepath://./Aki/Movies/a.mp4?x=1', 'filepath://./Aki/Movies/a\\b.mp4'])
def test_movie_paths_reject_remote_or_escaping_sources(url):
    assert movie_archive_path(url) is None


def test_missing_source_and_cyclic_owner_remain_explicitly_unresolved():
    section = {'Type': 'MovieSceneMediaSection', 'Package': '/Game/Scene', '_export_index': 0,
               'Outer': {'ObjectPath': '/Game/Scene.0'},
               'Properties': {'MediaSource': {'ObjectPath': '/Game/Absent'}}}
    result, = sequence_media_score([section])['sections']
    assert result['clock'] is None
    assert result['status'] == 'unresolved_media_source'
    assert result['archive_path'] is None


def test_nested_sequence_is_not_claimed_as_video_or_flattened():
    result, = sequence_media_score([{'Type': 'MovieSceneCinematicShotSection',
                                     'Package': '/Game/Scene', '_export_index': 1,
                                     'Properties': {'SubSequence': {'ObjectPath': 'Client/Content/Story/Shot.7'},
                                                    'Parameters': {'TimeScale': 2}}}])['sections']
    assert result['kind'] == 'subsequence'
    assert result['reference'] == '/Game/Story/Shot.7'
    assert result['parameters'] == {'Parameters': {'TimeScale': 2}}
    assert 'archive_path' not in result


def test_engine_camera_and_skeletal_spans_keep_scene_binding_and_animation_offsets():
    package = '/Game/Shot'
    raw_range = {'Value': {'LowerBound': {'Type': 1, 'Value': {'Value': 0}},
                           'UpperBound': {'Type': 0, 'Value': {'Value': 45}}}}
    raw = [
        {'Type': 'MovieScene', 'Properties': {'TickResolution': {'Numerator': 30},
         'PlaybackRange': raw_range,
                                              'Spawnables': [{'Guid': 'camera', 'ObjectTemplate': {'ObjectName': "CineCameraActor'Camera'", 'ObjectPath': package + '.1'}}],
                                              'ObjectBindings': [{'ObjectGuid': 'camera', 'Tracks': [{'ObjectPath': package + '.2'}]}]}},
        {'Type': 'CineCameraActor', 'Name': 'Camera'},
        {'Type': 'MovieSceneSpawnTrack', 'Outer': {'ObjectPath': package + '.0'}},
        {'Type': 'MovieSceneSpawnSection', 'Outer': {'ObjectPath': package + '.2'},
         'Properties': {'SectionRange': raw_range}},
        {'Type': 'MovieSceneSkeletalAnimationTrack',
            'Outer': {'ObjectPath': package + '.0'}},
        {'Type': 'MovieSceneSkeletalAnimationSection', 'Outer': {'ObjectPath': package + '.4'},
         'Properties': {'SectionRange': raw_range, 'Params': {
             'Animation': {'ObjectPath': '/Game/Anim/Attack'}, 'StartFrameOffset': {'Value': 11}}}},
    ]
    result = sequence_media_score(
        [{**e, 'Package': package, '_export_index': i} for i, e in enumerate(raw)])
    camera, skeletal = result['sections']
    assert camera['kind'] == 'camera' and camera['coverage'] == 'spawn'
    assert camera['binding']['guid'] == 'camera'
    assert skeletal['kind'] == 'skeletal_animation' and skeletal['reference'] == '/Game/Anim/Attack'
    assert skeletal['parameters']['Params']['StartFrameOffset'] == {
        'Value': 11}
    assert all(s['scene_source'] == package + '.0' and s['range']
               == raw_range for s in result['sections'])
    assert result['scenes'][0]['range'] == raw_range
    assert not any('archive_path' in s for s in result['sections'])


def test_movie_role_requires_actual_texture_material_and_postprocess_binding():
    package = '/Game/Scene'
    texture = {'ObjectPath': '/Game/Texture'}
    raw = [
        {'Type': 'MovieScene', 'Properties': {'TickResolution': {'Numerator': 30}}},
        {'Type': 'MovieSceneMediaTrack', 'Outer': {'ObjectPath': package + '.0'},
         'Properties': {'TrackTags': ['Male']}},
        {'Type': 'MovieSceneMediaSection', 'Outer': {'ObjectPath': package + '.1'},
         'Properties': {'MediaTexture': texture, 'MediaSource': {'ObjectPath': '/Game/Feathers'}}},
        {'Type': 'KuroPostProcessVolume', 'Outer': {'ObjectPath': package + '.0'},
         'Properties': {'Settings': {'WeightedBlendables': {'Array': [
             {'Weight': 1, 'Object': {'ObjectPath': '/Game/Material'}}]}}}},
    ]
    exports = [{**e, 'Package': package, '_export_index': i}
               for i, e in enumerate(raw)]
    exports.append({'Type': 'Material', 'Package': '/Game/Material', '_export_index': 0,
                    'Properties': {'MaterialDomain': 'MD_PostProcess', 'BlendMode': 'BLEND_Translucent',
                                   'CachedExpressionData': {'ReferencedTextures': [texture]}}})
    section, = sequence_media_score(exports)['sections']
    assert section['media_texture'] == texture and section['rover'] == 'male'
    assert section['composition_role'] == 'post_process_layer'
    assert section['material_bindings'][0]['source'] == package + '.3'
    # Translucency does not prove an MP4 alpha channel.
    assert 'alpha_mode' not in section
    exports[-1]['Properties']['CachedExpressionData']['ReferencedTextures'] = [
        {'ObjectPath': '/Game/Other'}]
    section, = sequence_media_score(exports)['sections']
    assert 'composition_role' not in section


@pytest.mark.parametrize('parent_texture,child_texture,confirmed', [
    (None, None, True),  # A real MI without overrides still inherits the cache proof.
    ('/Game/VideoTexture', '/Game/OtherTexture', False),
    ('/Game/OtherTexture', '/Game/VideoTexture', True),
    (None, '/Game/OtherTexture', False),  # Unmapped parent cache cannot prove usage.
])
def test_material_texture_matching_respects_child_parameter_overrides(parent_texture, child_texture, confirmed):
    package = '/Game/Scene'
    texture = {'ObjectPath': '/Game/VideoTexture'}
    raw = [
        {'Type': 'MovieScene'},
        {'Type': 'MovieSceneMediaTrack', 'Outer': {'ObjectPath': package + '.0'}},
        {'Type': 'MovieSceneMediaSection', 'Outer': {'ObjectPath': package + '.1'},
         'Properties': {'MediaTexture': texture}},
        {'Type': 'KuroPostProcessVolume', 'Outer': {'ObjectPath': package + '.0'},
         'Properties': {'Settings': {'WeightedBlendables': {'Array': [
             {'Weight': 1, 'Object': {'ObjectPath': '/Game/Child'}}]}}}},
    ]
    exports = [{**entry, 'Package': package, '_export_index': index}
               for index, entry in enumerate(raw)]
    for name, parent, value in [('Child', 'Parent', child_texture), ('Parent', 'Material', parent_texture)]:
        props = {'Parent': {'ObjectPath': '/Game/' + parent}}
        if value is not None:
            props['TextureParameterValues'] = [{
                'ParameterInfo': {'Name': 'Video', 'Association': 'GlobalParameter', 'Index': -1},
                'ParameterValue': {'ObjectPath': value},
            }]
        exports.append({'Type': 'MaterialInstanceConstant', 'Package': '/Game/' + name,
                        '_export_index': 0, 'Properties': props})
    exports.append({'Type': 'Material', 'Package': '/Game/Material', '_export_index': 0,
                    'Properties': {'MaterialDomain': 'MD_PostProcess',
                                   'CachedExpressionData': {'ReferencedTextures': [texture]}}})
    section, = sequence_media_score(exports)['sections']
    assert (section.get('composition_role') == 'post_process_layer') is confirmed
    assert bool(section.get('material_bindings')) is confirmed


def audio_exports(audio_event=None, soft_audio_event=None, rate=None):
    package = '/Game/Aki/Sequence/LevelA_Seq/Main/03_1/M0334/M0334_All'
    section_range = {'Value': {'LowerBound': {'Type': 1, 'Value': {'Value': 1}},
                               'UpperBound': {'Type': 0, 'Value': {'Value': 2035}}}}
    return [
        {'Type': 'MovieScene', 'Package': package, '_export_index': 76,
         'Properties': {'TickResolution': rate or {'Numerator': 30},
                        'PlaybackRange': {'Value': {'UpperBound': {'Value': {'Value': 1806}}}}}},
        {'Type': 'MovieSceneKuroAudioEventTrack', 'Package': package, '_export_index': 408,
         'Outer': {'ObjectPath': package + '.76'}},
        {'Type': 'MovieSceneKuroAudioEventSection', 'Package': package, '_export_index': 407,
         'Outer': {'ObjectPath': package + '.408'},
         'Properties': {'AudioEvent': audio_event, 'SoftAudioEvent': soft_audio_event,
                        'bSyncAudio': True, 'FadeDuration': 8000, 'SectionRange': section_range}},
    ]


def test_m0334_audio_event_preserves_source_clock_range_sync_and_unconverted_fade():
    event = '/Game/Aki/WwiseAudio/Events/play_all_sound_m0334'
    hard = {'ObjectName': "AkAudioEvent'play_all_sound_m0334'", 'ObjectPath': event}
    soft = {'AssetPathName': event + '.play_all_sound_m0334', 'SubPathString': ''}
    exports = audio_exports(hard, soft)
    section, = sequence_media_score(exports)['sections']
    assert section['kind'] == 'audio_event' and section['reference'] == event
    assert section['source'].endswith('M0334_All.407')
    assert section['scene_source'].endswith('M0334_All.76')
    assert section['clock'] == {'numerator': 30, 'denominator': 1}
    assert section['range'] == exports[2]['Properties']['SectionRange']
    assert section['parameters'] == {'bSyncAudio': True, 'FadeDuration': 8000}
    assert section['audio_event'] == hard and section['soft_audio_event'] == soft
    assert section['reference_field'] == 'AudioEvent' and section['status'] == 'referenced'
    assert 'url' not in section and 'file_id' not in section


@pytest.mark.parametrize('hard', [None, {}, {'ObjectPath': 'None'}])
def test_soft_audio_event_fallback_uses_authored_asset_path_not_a_name_guess(hard):
    soft = {'AssetPathName': '/Game/Aki/WwiseAudio/Events/Exact.Exact', 'SubPathString': ''}
    section, = sequence_media_score(audio_exports(hard, soft))['sections']
    assert section['reference'] == '/Game/Aki/WwiseAudio/Events/Exact'
    assert section['reference_field'] == 'SoftAudioEvent'
    assert section['soft_audio_event'] == soft


def test_conflicting_hard_and_soft_audio_events_do_not_select_either_candidate():
    hard = {'ObjectPath': '/Game/Aki/WwiseAudio/Events/First'}
    soft = {'AssetPathName': '/Game/Aki/WwiseAudio/Events/Second.Second'}
    section, = sequence_media_score(audio_exports(hard, soft))['sections']
    assert section['reference'] is None and section['status'] == 'unresolved_audio_event'
    assert section['diagnostics'] == [{'code': 'conflicting_audio_event_references'}]
    assert section['audio_event'] == hard and section['soft_audio_event'] == soft
    assert section['parameters']['bSyncAudio'] is True


@pytest.mark.parametrize('path', ['https://example.com/Event', '/Game/../Secret',
                                  '/Game/Event.OtherObject', '/Game/Event?query=1', '/Game/Event\\Other'])
def test_invalid_audio_event_paths_remain_unresolved_without_dropping_section(path):
    section, = sequence_media_score(audio_exports(None, {'AssetPathName': path}))['sections']
    assert section['reference'] is None and section['status'] == 'unresolved_audio_event'
    assert section['range'] and section['parameters']['FadeDuration'] == 8000


def test_audio_event_retains_fractional_clock_and_does_not_invent_missing_parameters():
    exports = audio_exports({'ObjectPath': '/Game/Event'}, rate={'Numerator': 60000, 'Denominator': 1001})
    del exports[2]['Properties']['FadeDuration']
    exports[2]['Properties']['bSyncAudio'] = False
    section, = sequence_media_score(exports)['sections']
    assert section['clock'] == {'numerator': 60000, 'denominator': 1001}
    assert section['parameters'] == {'bSyncAudio': False}

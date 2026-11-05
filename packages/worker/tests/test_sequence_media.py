import pytest

from wuwa_story_worker.sequence_media import movie_archive_path, sequence_media_score


def test_movie_fragment_keeps_own_clock_offset_and_authored_range():
    package = '/Game/Scene'
    exports = [
        {'Type': 'MovieScene', 'Properties': {'TickResolution': {'Numerator': 60000, 'Denominator': 1001}}},
        {'Type': 'MovieSceneMediaTrack', 'Outer': {'ObjectPath': package + '.0'}},
        {'Type': 'MovieSceneMediaSection', 'Outer': {'ObjectPath': package + '.1'},
         'Properties': {'MediaSource': {'ObjectPath': '/Game/Clip'},
                        'SectionRange': {'Value': {'LowerBound': {'Type': 1, 'Value': {'Value': 402}}}},
                        'StartFrameOffset': {'Value': 102}}},
    ]
    exports = [{**entry, 'Package': package, '_export_index': index} for index, entry in enumerate(exports)]
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

from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from wuwa_story_worker import cutscene_import
from wuwa_story.ingestion.cutscenes import VideoInput


def test_music_context_is_explicit_and_does_not_change_legacy_recipe_identity():
    asset = 'asset:ue:/Game/Test.Test'
    assert 'music_context' not in VideoInput(asset=asset).model_dump()
    video = VideoInput(asset=asset, music_context={
        'bank': 'music.bnk', 'audio_directory': 'music', 'state_group': 10,
        'state': 20, 'source_event': 'set_phase', 'evidence': 'native state switch'})
    assert video.model_dump()['music_context']['state'] == 20
    with pytest.raises(ValueError):
        VideoInput(asset=asset, music_context={
                   **video.music_context.model_dump(), 'state': -1})


@pytest.mark.asyncio
async def test_reconstructed_music_loops_and_remains_an_independent_stem(tmp_path, monkeypatch):
    wav = tmp_path / 'music.wav'
    wav.write_bytes(b'rendered-source')
    evidence = tmp_path / 'music.bnk'
    evidence.write_bytes(b'native-bank')
    calls = []

    async def run(command, *_):
        calls.append(command)
        from pathlib import Path
        Path(command[-1]).write_bytes(b'mixed-stem')
    monkeypatch.setattr(cutscene_import, 'run_tool', run)
    video = VideoInput(asset='asset:ue:/Game/Test.Test', music_context={
        'bank': 'music.bnk', 'audio_directory': 'music', 'state_group': 10,
        'state': 20, 'source_event': 'set_phase', 'evidence': 'native state switch'})
    result = await cutscene_import.localized_audio_recipe(video, tmp_path/'movie.mp4',
                                                          tmp_path, tmp_path, tmp_path/'output', tmp_path/'decoder', tmp_path/'ffmpeg',
                                                          105.3, False, '3.7.1', frame_stems=[{'role': 'music', 'language': None, 'rover': None,
                                                                                               'path': wav, 'sources': [evidence], 'start_seconds': 0, 'end_seconds': None, 'fixed_start': True}])
    assert result.stems[0].role == 'music'
    assert 'reconstructed' in result.stems[0].evidence
    assert calls[0][calls[0].index('-stream_loop')+1] == '-1'
    assert '105.3' in calls[0]


def test_music_separation_preserves_native_sequence_and_discards_only_parallel_effects():
    from wuwa_story_worker.cutscene_music_context import music_recipe
    body = 'wem/1.wem #E #B 10.0\nwem/2.wem #i #b 100.35 #v -4.0dB #@loop\ngroup = -S2 ##loop\nwem/3.wem #e #v -6.0dB\ngroup = -L2 #@layer-v'
    roles = {1: {'role': 'music'}, 2: {
        'role': 'music'}, 3: {'role': 'effects'}}
    result = music_recipe(body, roles)
    assert result == '\n'.join(body.splitlines()[:3])
    with pytest.raises(ValueError, match='without altering native timing'):
        music_recipe('wem/1.wem\nwem/3.wem\ngroup = -S2', roles)

    with pytest.raises(ValueError, match='without altering native timing'):
        music_recipe(body.replace('-L2 #@layer-v',
                     '-L2 #v -6dB #@layer-v'), roles)


def test_initial_stop_only_clears_preexisting_music_not_the_new_event(tmp_path):
    from wuwa_story_worker.wwise_banks import initial_parameters
    xml = tmp_path/'bank.xml'
    xml.write_text('<root/>')
    stop = {'kind': 'Stop', 'time_ms': 0, 'target_id': 10}
    play = {'kind': 'Play', 'time_ms': 0, 'target_id': 20}
    timeline = {'actions': [stop, play]}
    assert initial_parameters(timeline, xml, xml) == []
    assert timeline['actions'] == [stop, play]
    for actions in ([play, stop], [{**stop, 'time_ms': 1000}, play], [stop]):
        with pytest.raises(ValueError, match='shared running control timeline'):
            initial_parameters({'actions': actions}, xml, xml)


@pytest.mark.parametrize('start,reconstructed_source,expected', [(0,False,1),(15,False,2),(0,True,1)])
def test_movie_song_supersedes_only_approximate_background(start,reconstructed_source,expected):
    from pathlib import Path
    from wuwa_story_worker.cutscene_music_context import select_music_stems
    song = {'role':'music','start_seconds':start,'sources':[Path('song.wem')],
            'source':{'reconstruction':'dialogue'} if reconstructed_source else {}}
    bed = {'role':'music','start_seconds':0,'sources':[Path('bed.xml'),Path('bed.wav')], 'fixed_start':True}
    result=select_music_stems([song],[bed])
    assert len(result)==expected
    if expected==1:
        assert result[0]['sources']==[Path('song.wem'),Path('bed.xml')]
        assert song['sources']==[Path('song.wem')]
    assert select_music_stems([],[bed])==[bed]

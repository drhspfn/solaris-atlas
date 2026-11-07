import json
from unittest.mock import AsyncMock, Mock

import pytest

from wuwa_story_worker import cutscene_frame_audio, cutscene_frame_catalog, release_media


def frame_binding():
    return {'video_name': 'M0374', 'raw_action': {'Name': 'PlayMovie', 'Params': {'VideoName': 'M0374'}},
            'complete': True, 'unresolved': [], 'source': {'file': 'db_flowState.db', 'raw_path': 'native.Actions[0]'},
            'events': [{'reference': '/Game/Aki/WwiseAudio/Events/play_all_sound_m0374.play_all_sound_m0374',
                        'start_seconds': 0, 'end_seconds': None, 'mode': 'MusicSubtitle', 'operation': 'post'}]}


@pytest.mark.asyncio
async def test_diagnostic_sentinel_does_not_erase_exact_verified_m0374_timeline(tmp_path, monkeypatch):
    binding = frame_binding()
    sentinel = {'events': [], 'unresolved': [{'code': 'native_source_unavailable', 'file': 'optional.db'}],
                'complete': False, 'source': {}}
    reader = Mock(return_value=[binding, sentinel])
    monkeypatch.setattr(cutscene_frame_catalog, 'catalog_movie_frame_bindings', reader)
    native = AsyncMock(return_value={'soundtracks': [{'bank': 'native.wav', 'audio_directory': '.',
        'start_seconds': 0, 'reference': binding['events'][0]['reference'], 'mode': 'MusicSubtitle',
        'source': binding['source']}], 'diagnostics': []})
    monkeypatch.setattr(cutscene_frame_audio, 'event_soundtracks', native)
    config = tmp_path / 'ConfigDB/db_cgVedio.db'
    inputs, report = await release_media.frame_audio_inputs(tmp_path, tmp_path, config, 'M0374',
        tmp_path / 'fmodel', tmp_path / 'work', voice_root=tmp_path / 'pinned')
    reader.assert_called_once_with(config.parent, 'M0374')
    assert inputs[0]['start_seconds'] == 0 and inputs[0]['source'] == binding['source']
    assert report['diagnostics'] == sentinel['unresolved']
    assert native.await_args.args[3] is binding
    assert native.await_args.kwargs['voice_root'] == tmp_path / 'pinned'


@pytest.mark.asyncio
async def test_conflicting_actual_complete_contexts_are_not_arbitrarily_selected(tmp_path, monkeypatch):
    first, second = frame_binding(), frame_binding()
    second['events'][0]['start_seconds'] = 1
    monkeypatch.setattr(cutscene_frame_catalog, 'catalog_movie_frame_bindings', lambda *_: [first, second])
    native = AsyncMock()
    monkeypatch.setattr(cutscene_frame_audio, 'event_soundtracks', native)
    inputs, report = await release_media.frame_audio_inputs(tmp_path, tmp_path, tmp_path / 'video.db',
                                                           'M0374', tmp_path / 'fmodel', tmp_path)
    assert not inputs
    assert report['diagnostics'][-1]['code'] == 'alternative_movie_audio_contexts_require_selection'
    native.assert_not_awaited()


@pytest.mark.asyncio
async def test_exact_launcher_without_events_is_not_missing_audio(tmp_path, monkeypatch):
    binding = frame_binding()
    binding['events'] = []
    monkeypatch.setattr(cutscene_frame_catalog, 'catalog_movie_frame_bindings', lambda *_: [binding])
    inputs, report = await release_media.frame_audio_inputs(tmp_path, tmp_path, tmp_path / 'video.db',
                                                           'M0374', tmp_path / 'fmodel', tmp_path)
    assert inputs == [] and report['diagnostics'] == []


@pytest.mark.asyncio
@pytest.mark.parametrize('diagnostics', [[], [{'code': 'native_source_unavailable'}]])
@pytest.mark.parametrize('failure', [None, 'missing', 'other'])
async def test_cutscene_leaf_wires_frame_audio_after_merge_with_pinned_voice_and_report(tmp_path, monkeypatch, diagnostics, failure):
    assets = tmp_path / 'release-media/files'
    movie = assets / 'Client/Content/Aki/Movies/M0374.mp4'
    movie.parent.mkdir(parents=True)
    movie.write_bytes(b'movie')
    monkeypatch.setattr(release_media, 'client_root', lambda _: tmp_path)
    monkeypatch.setattr(release_media, 'asset_workspace', lambda: tmp_path)
    monkeypatch.setattr(release_media, 'video_database', lambda _: assets / 'ConfigDB/video.db')
    monkeypatch.setattr(release_media, 'source_context', AsyncMock(return_value={}))
    monkeypatch.setattr(release_media, 'missing_cutscene_identities', AsyncMock(return_value={'cutscene:M0374'}))
    monkeypatch.setattr(release_media, 'cleanup_published_output', AsyncMock())
    monkeypatch.setattr(release_media, 'update_admin_run', AsyncMock())
    monkeypatch.setattr(release_media, 'tool_path', lambda key: tmp_path / key)
    monkeypatch.setattr(release_media.shutil, 'which', lambda _: str(tmp_path / 'ffmpeg'))
    pinned = tmp_path / 'pinned-voices'
    package = Mock(return_value=pinned)
    monkeypatch.setattr(release_media, 'package_root', package)
    order = []
    monkeypatch.setattr(release_media, 'extract_cutscene_audio', AsyncMock(return_value=tmp_path / 'audio'))
    monkeypatch.setattr(release_media, 'merge_exports', lambda *_: order.append('merge'))
    inputs = [{'bank': 'native.wav', 'start_seconds': 0}]
    report = {'contexts': [frame_binding()], 'diagnostics': diagnostics}

    async def frame(root, exported, config, name, fmodel, work, voice_root=None):
        assert order == ['merge'] and work.is_dir()
        assert voice_root == pinned and fmodel.name == 'WUWA_FMODEL_PATH'
        assert name == 'M0374'
        order.append('frame')
        return inputs, report

    monkeypatch.setattr(release_media, 'frame_audio_inputs', frame)
    plans = []

    def plan(*args, **kwargs):
        plans.append(kwargs)
        return {'videos': [{'asset': 'asset:ue:/Game/Aki/Movies/M0374.mp4',
                            'frame_audio': kwargs.get('frame_audio', [])}], 'flow': {'evidence': 'native CG'}}

    monkeypatch.setattr(release_media, 'plan_cutscene', plan)

    async def publish(path, *_args, **kwargs):
        saved = json.loads(path.read_text(encoding='utf-8'))
        assert saved['videos'][0]['frame_audio'] == inputs
        assert 'db_flowState.db' in saved['flow']['evidence']
        if failure:
            if failure == 'missing':
                kwargs['missing_assets'].append('event.bnk: unresolved audio')
            raise ValueError('publication guard')
        return {'videos': 1}

    monkeypatch.setattr(release_media, 'import_cutscene_recipe', publish)
    payload = {'targets': ['M0374'], 'run_id': 90, 'game_version': '3.7.0'}
    parent = {'asset_version': '3.7.1', 'workspace_id': 11, 'voice_plan_id': 'pinned'}
    if failure:
        with pytest.raises(ValueError, match='publication guard'):
            await release_media.cutscene(payload, parent)
        missing = (['frame_audio:M0374:native_source_unavailable'] if diagnostics else [])
        if failure == 'missing':
            missing.append('event.bnk: unresolved audio')
        if missing:
            persisted = release_media.update_admin_run.await_args.kwargs['raw_output']
            assert persisted['missing_assets'] == missing
            entries = persisted['media_report']['entries']
            assert [entry['expected'] for entry in entries if entry['status'] == 'not_resolved'] == missing
            assert persisted['media_report']['requested'] == len(entries)
            assert persisted['media_report']['missing'] == len(missing)
        else:
            assert release_media.update_admin_run.await_count == 1
        release_media.cleanup_published_output.assert_not_awaited()
        return
    result, status = await release_media.cutscene(payload, parent)
    assert plans[-1]['frame_audio'] == inputs and 'frame_audio' not in plans[0]
    assert result['media_report']['frame_audio'] == report
    assert status == ('partial' if diagnostics else 'completed')
    assert bool(result['missing_assets']) is bool(diagnostics)
    assert json.loads((tmp_path / 'release-media/90/frame-audio-report.json').read_text()) == report
    assert package.call_args.args[2] == 'pinned'

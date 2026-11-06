import hashlib
import json
import subprocess
import wave
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from wuwa_story_worker import entity_media
from wuwa_story_worker import sequence_audio_prepare as audio


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    root, voice = tmp_path / 'base', tmp_path / 'voice'
    for folder, identity in [(root, 'base-plan'), (voice, 'voice-plan')]:
        folder.mkdir()
        (folder / 'plan.json').write_text(json.dumps({'id': identity, 'version': '3.7.1'}))
        (folder / 'status.json').write_text(json.dumps({'id': identity, 'state': 'downloaded'}))
    event = '/Game/Aki/WwiseAudio/Events/play_all_sound_m0334'
    bank_path = 'Client/Content/Aki/WwiseAudio_Generated/Event/en/play_all_sound_m0334.bnk'
    init_path = 'Client/Content/Aki/WwiseAudio_Generated/Init.bnk'
    source = root / 'Client/Content/Aki/WwiseAudio/Events/play_all_sound_m0334.uasset'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'event source')
    init = root / init_path
    init.parent.mkdir(parents=True)
    init.write_bytes(b'init')
    media = {}
    for identity, folder, plan in [(1, root, 'base-plan'), (2, voice, 'voice-plan')]:
        package = f'Client/Content/Aki/WwiseAudio_Generated/Media/{identity}.wem'
        path = folder / package
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f'raw {identity}'.encode())
        media[identity] = (path, plan, package)
    cooked = tmp_path / 'event.json'
    cooked.write_text(json.dumps([{'Type': 'AkAudioEvent', 'EventCookedData': {'EventLanguageMap': [
        {'Key': {'LanguageName': 'en'}, 'Value': {'EventId': audio.event_id('play_all_sound_m0334'),
         'SoundBanks': [{'SoundBankPathName': 'Event/en/play_all_sound_m0334.bnk'}],
         'Media': [{'MediaId': i, 'MediaPathName': f'Media/{i}.wem'} for i in (1, 2)]}},
    ]}}]))
    controls = {'media': {'sections': [
        {'kind': 'movie_fragment', 'source': 'scene.2', 'file': 'media/movie.mp4'},
        {'kind': 'audio_event', 'source': 'scene.407', 'reference': event,
         'parameters': {'bSyncAudio': True, 'FadeDuration': 8000}, 'clock': {'numerator': 30, 'denominator': 1}},
    ]}}
    entries = {'controls.json': json.dumps(controls).encode(), 'media/movie.mp4': b'original movie',
               'decoded/scene.json': b'original retained JSON'}
    manifest = {'asset_version': '3.7.1', 'download_id': 'base-plan', 'sequence_metadata_revision': 3,
                'control_sources': {'missing': [{'code': 'existing_incomplete_source'}]},
                'files': {name: {'size_bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                          for name, data in entries.items()}}
    bundle = tmp_path / 'retained.zip'
    with zipfile.ZipFile(bundle, 'w') as archive:
        for name, data in {**entries, 'manifest.json': json.dumps(manifest).encode()}.items():
            archive.writestr(name, data)
    tools = {}
    for name in ('fmodel', 'converter', 'WUWA_VGMSTREAM_PATH', 'WUWA_WWISER_PATH'):
        path = tmp_path / name
        path.write_bytes(name.encode())
        tools[name] = path
    monkeypatch.setattr(audio, 'tool_path', lambda name: tools[name])
    monkeypatch.setattr(entity_media, 'client_voice_exports', lambda *_: {})
    monkeypatch.setattr(audio, 'prefetch_events', AsyncMock(return_value={event: (cooked, [source])}))
    monkeypatch.setattr(audio, 'prefetch_banks', AsyncMock(return_value={init_path: init}))
    monkeypatch.setattr(audio, 'prefetch_media', AsyncMock(return_value=media))
    monkeypatch.setattr(audio, 'ensure_archive_index', AsyncMock(side_effect=lambda archive, *_: archive / 'index'))
    base_only = [False]

    def paths(index, _names):
        return [bank_path, '1.wem', '2.wem'] if base_only[0] or index.parent == voice else ['1.wem']

    monkeypatch.setattr(audio, 'indexed_audio_paths', paths)

    async def tool(args, log, *_):
        log.write_text('exact export')
        assert args[-1] == bank_path
        path = Path(args[3]) / bank_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'localized bank')

    monkeypatch.setattr(entity_media, 'tool', tool)
    monkeypatch.setattr(audio, 'rover_parameters', lambda _xml, rover, **_: [rover])

    async def run(args, *_):
        if '-dn' in args:
            Path(args[args.index('-dn') + 1] + '.xml').write_text('<root/>')
        else:
            Path(args[-1]).write_bytes(b'OggS encoded')

    monkeypatch.setattr(audio, 'run_tool', run)

    async def render(bank, inputs, work, decoder, rover, runner, **kwargs):
        wav = work / (rover + '.wav')
        with wave.open(str(wav), 'wb') as stream:
            stream.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
            stream.writeframes(bytes(48000 * 4))
        return wav, [bank, inputs / '1.wem', inputs / '2.wem', work / 'bank.xml']

    monkeypatch.setattr(audio, 'render_bank', AsyncMock(side_effect=render))
    return root, voice, bundle, tools, entries, base_only


@pytest.mark.asyncio
@pytest.mark.parametrize('base_only', [False, True])
async def test_localized_prep_preserves_movies_packages_originals_and_hashes(prepared, base_only, monkeypatch):
    root, voice, bundle, tools, entries, base_flag = prepared
    base_flag[0] = base_only
    if base_only:
        monkeypatch.setattr(audio, '_voice_root', lambda *_: pytest.fail('Base closure needs no voice discovery'))
    output, originals = await audio.prepare_sequence_audio(root, bundle, tools['fmodel'], tools['converter'],
                                                           voice_root=None if base_only else voice)
    assert output != bundle
    assert audio.prefetch_media.await_args.args[1] == (root if base_only else voice)
    with zipfile.ZipFile(output) as archive:
        assert archive.read('media/movie.mp4') == entries['media/movie.mp4']
        assert archive.read('decoded/scene.json') == entries['decoded/scene.json']
        manifest = json.loads(archive.read('manifest.json'))
        for name, proof in manifest['files'].items():
            data = archive.read(name)
            assert len(data) == proof['size_bytes']
            assert hashlib.sha256(data).hexdigest() == proof['sha256']
        controls = json.loads(archive.read('controls.json'))
        section = controls['media']['sections'][1]
        assert section['parameters']['FadeDuration'] == 8000  # No guessed fade conversion.
        assert {t['rover'] for t in section['tracks']} == {'male', 'female'}
        for track in section['tracks']:
            assert track['language'] == 'en' and track['status'] == 'rendered' and track['duration_seconds'] == 1
            assert track['tools']['WUWA_WWISER_PATH'] == audio.sha256(tools['WUWA_WWISER_PATH'])
            assert track['voice_plan_id'] == (None if base_only else 'voice-plan')
            assert any(p['package_path'].endswith('/Event/en/play_all_sound_m0334.bnk') for p in track['source_media'])
        assert any(name.endswith('/play_all_sound_m0334.bnk') and name.startswith('source/') for name in archive.namelist())
        assert sum(name.endswith('.wem') and name.startswith('source/') for name in archive.namelist()) == 2
        assert manifest['control_sources']['missing'] == [{'code': 'existing_incomplete_source'}]
    assert {p.suffix for p in originals} == {'.wem', '.bnk', '.uasset'}


@pytest.mark.asyncio
async def test_section_failure_retains_video_and_marks_publication_partial(prepared, monkeypatch):
    root, voice, bundle, tools, entries, _ = prepared
    monkeypatch.setattr(audio, 'render_bank', AsyncMock(side_effect=ValueError('Unsupported temporal state')))
    output, _ = await audio.prepare_sequence_audio(root, bundle, tools['fmodel'], tools['converter'], voice)
    with zipfile.ZipFile(output) as archive:
        assert archive.read('media/movie.mp4') == entries['media/movie.mp4']
        manifest = json.loads(archive.read('manifest.json'))
        assert any(item.get('status') == 'unresolved_audio' for item in manifest['control_sources']['missing'])
        section = json.loads(archive.read('controls.json'))['media']['sections'][1]
        assert section['status'] == 'unresolved_audio_event' and section['tracks'] == []


@pytest.mark.asyncio
@pytest.mark.parametrize('error', [BlockingIOError('busy'), TimeoutError('retry'), subprocess.CalledProcessError(137, 'decoder')])
async def test_transient_audio_failures_propagate_to_existing_retry(prepared, monkeypatch, error):
    root, voice, bundle, tools, _, _ = prepared
    monkeypatch.setattr(audio, 'render_bank', AsyncMock(side_effect=error))
    with pytest.raises(type(error)):
        await audio.prepare_sequence_audio(root, bundle, tools['fmodel'], tools['converter'], voice)


def test_discovery_uses_only_unique_completed_matching_voice_plan(tmp_path, monkeypatch):
    monkeypatch.delenv('WUWA_VOICE_ROOT', raising=False)
    monkeypatch.setattr(audio, 'asset_workspace', lambda: tmp_path)
    for suffix in ('one', 'two'):
        path = tmp_path / 'voices' / ('3.7.1-' + suffix)
        path.mkdir(parents=True)
        (path / 'plan.json').write_text(json.dumps({'id': suffix, 'version': '3.7.1'}))
        (path / 'status.json').write_text(json.dumps({'id': suffix, 'state': 'downloaded'}))
        if suffix == 'one':
            assert audio._voice_root(None, '3.7.1') == path
    with pytest.raises(ValueError, match='unique completed matching'):
        audio._voice_root(None, '3.7.1')

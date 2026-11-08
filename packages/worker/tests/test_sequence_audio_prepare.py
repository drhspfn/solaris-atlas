import hashlib
import json
import subprocess
import wave
import zipfile
from pathlib import Path
from types import SimpleNamespace
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


@pytest.mark.asyncio
async def test_levelplay_music_is_packaged_as_one_fixed_start_audio_section(prepared, monkeypatch):
    from wuwa_story_worker import sequence_music_context
    from wuwa_story_worker.sequence_fragments import publish_sequence_fragments

    root, voice, bundle, tools, _, _ = prepared
    context = {'schema_version': 1, 'kind': 'levelplay_fixed_start',
               'play_event': '/Game/Aki/WwiseAudio/Events/play_music',
               'state_event': '/Game/Aki/WwiseAudio/Events/set_state',
               'stop_event': '/Game/Aki/WwiseAudio/Events/stop_music',
               'source_proof': {'asset_version': '3.7.1', 'download_id': 'base-plan'}}
    music = AsyncMock(return_value=(
        {'language': None, 'rover': None, 'status': 'rendered', 'loop': True},
        b'OggS reconstructed', {'state.xml': b'bank proof'}, [], {'play_target': 30, 'bank_timelines': {}}))
    monkeypatch.setattr(sequence_music_context,
                        'prepare_levelplay_music', music)
    output, _ = await audio.prepare_sequence_audio(root, bundle, tools['fmodel'], tools['converter'],
                                                   voice_root=voice, soundtrack_context=context)
    with zipfile.ZipFile(output) as archive:
        controls = json.loads(archive.read('controls.json'))
        section, = [s for s in controls['media']
                    ['sections'] if s.get('reconstruction_kind')]
        assert section['reference'] == context['play_event']
        assert section['scope'] == 'flow'
        assert section['source_proof'] == context['source_proof']
        assert section['clock'] is None and section['range'] is None
        assert section['loop'] and section['timing_basis'] == 'fixed_start_reconstruction'
        assert section['music_binding'] == {'play_target': 30}
        assert archive.read(section['tracks'][0]
                            ['file']) == b'OggS reconstructed'
        manifest = json.loads(archive.read('manifest.json'))
        assert manifest['files'][section['tracks'][0]['file']]['sha256'] == hashlib.sha256(
            b'OggS reconstructed').hexdigest()
        assert any(name.endswith('/state.xml') for name in archive.namelist())
    reference = SimpleNamespace()
    with zipfile.ZipFile(output) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    controls = json.loads(entries['controls.json'])
    controls['media']['sections'] = [s for s in controls['media']['sections'] if s.get('kind') != 'movie_fragment']
    entries['controls.json'] = json.dumps(controls).encode()
    manifest = json.loads(entries['manifest.json'])
    manifest['files']['controls.json'] = {'size_bytes': len(entries['controls.json']),
                                          'sha256': hashlib.sha256(entries['controls.json']).hexdigest()}
    entries['manifest.json'] = json.dumps(manifest).encode()
    with zipfile.ZipFile(output, 'w') as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    service = SimpleNamespace(register_file=AsyncMock(return_value=SimpleNamespace(id=91)),
                              register_variant=AsyncMock(), register_reference=AsyncMock(return_value=reference))
    session = SimpleNamespace(scalar=AsyncMock(return_value=None))
    published = await publish_sequence_fragments(session, output,
        {'owner': 7, 'release_id': 21, 'path': 'story_sequence:flow_state:Flow'},
        SimpleNamespace(id=90), service, 100, {'asset_version': '3.7.1', 'download_id': 'base-plan'})
    section, = [s for s in published['sections'] if s.get('reconstruction_kind')]
    assert section['tracks'][0]['status'] == 'published' and section['tracks'][0]['file_id'] == 91
    assert section['tracks'][0]['role'] == 'music'
    assert section['source_proof'] == context['source_proof'] and section['loop']


@pytest.mark.asyncio
async def test_unresolved_levelplay_music_does_not_discard_other_sequence_audio(prepared, monkeypatch):
    from wuwa_story_worker import sequence_music_context

    root, voice, bundle, tools, _, _ = prepared
    context = {'play_event': 'play', 'source_proof': {'source': 'levelplay'}}
    monkeypatch.setattr(sequence_music_context, 'prepare_levelplay_music', AsyncMock(
        side_effect=ValueError('ambiguous')))
    output, _ = await audio.prepare_sequence_audio(root, bundle, tools['fmodel'], tools['converter'],
                                                   voice_root=voice, soundtrack_context=context)
    with zipfile.ZipFile(output) as archive:
        controls = json.loads(archive.read('controls.json'))
        section, = [s for s in controls['media']
                    ['sections'] if s.get('reconstruction_kind')]
        assert section['status'] == 'unresolved_audio_event' and not section['tracks']
        assert any(s.get('tracks') for s in controls['media']['sections'] if not s.get(
            'reconstruction_kind'))
        assert any(item.get('status') == 'unresolved_audio' for item in json.loads(
            archive.read('manifest.json'))['control_sources']['missing'])


@pytest.mark.asyncio
async def test_successful_levelplay_retry_clears_only_its_own_previous_diagnostic(prepared, monkeypatch):
    from wuwa_story_worker import sequence_music_context

    root, voice, bundle, tools, _, _ = prepared
    context = {'play_event': 'play', 'state_event': 'state',
               'stop_event': 'stop', 'source_proof': {'source': 'levelplay'}}
    identity = hashlib.sha256(json.dumps(context, ensure_ascii=False, sort_keys=True,
                                         separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    stale = {'status': 'unresolved_audio', 'source': 'levelplay:' + identity,
             'code': 'sequence_audio_unavailable', 'message': 'offline'}
    other = {**stale, 'source': 'other'}
    with zipfile.ZipFile(bundle) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(entries['manifest.json'])
    manifest['control_sources']['missing'].extend([stale, other])
    entries['manifest.json'] = json.dumps(manifest).encode()
    with zipfile.ZipFile(bundle, 'w') as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    monkeypatch.setattr(sequence_music_context, 'prepare_levelplay_music', AsyncMock(return_value=(
        {'language': None, 'rover': None, 'status': 'rendered', 'loop': True},
        b'OggS', {}, [], {'play_target': 30})))
    output, _ = await audio.prepare_sequence_audio(root, bundle, tools['fmodel'], tools['converter'],
                                                   voice_root=voice, soundtrack_context=context)
    with zipfile.ZipFile(output) as archive:
        missing = json.loads(archive.read('manifest.json'))[
            'control_sources']['missing']
    assert stale not in missing and other in missing


@pytest.mark.asyncio
async def test_authored_event_retry_clears_recovered_audio_errors_only(prepared):
    root, voice, bundle, tools, _, _ = prepared
    with zipfile.ZipFile(bundle) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    controls = json.loads(entries['controls.json'])
    section = controls['media']['sections'][1]
    section['diagnostics'] = [{'code': 'sequence_audio_unavailable', 'message': 'offline'},
                              {'code': 'native_control_missing'}]
    stale = {'status': 'unresolved_audio', 'source': section['source'],
             'code': 'sequence_audio_unavailable', 'message': 'offline'}
    other = {**stale, 'source': 'other'}
    native = {'status': 'unresolved_control',
              'source': section['source'], 'code': 'native_control_missing'}
    manifest = json.loads(entries['manifest.json'])
    manifest['control_sources']['missing'].extend([stale, other, native])
    entries['controls.json'] = json.dumps(controls).encode()
    manifest['files']['controls.json'] = {'size_bytes': len(entries['controls.json']),
                                          'sha256': hashlib.sha256(entries['controls.json']).hexdigest()}
    entries['manifest.json'] = json.dumps(manifest).encode()
    with zipfile.ZipFile(bundle, 'w') as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    output, _ = await audio.prepare_sequence_audio(root, bundle, tools['fmodel'], tools['converter'], voice_root=voice)
    with zipfile.ZipFile(output) as archive:
        section = json.loads(archive.read('controls.json'))[
            'media']['sections'][1]
        missing = json.loads(archive.read('manifest.json'))[
            'control_sources']['missing']
    assert section['status'] == 'rendered'
    assert section['diagnostics'] == [{'code': 'native_control_missing'}]
    assert stale not in missing and other in missing and native in missing


@pytest.mark.asyncio
@pytest.mark.parametrize('state_only', [True, False])
@pytest.mark.parametrize('background', ['spine', 'image'])
async def test_legacy_flow_soundtrack_requires_verified_music_bus(prepared, monkeypatch, state_only, background):
    from wuwa_story.ingestion.spine_presentation import flow_presentation

    from wuwa_story_worker import sequence_music_context

    root, voice, bundle, tools, _, _ = prepared
    with zipfile.ZipFile(bundle) as archive:
        entries = {name: archive.read(name) for name in archive.namelist() if name != 'controls.json'}
    manifest = json.loads(entries['manifest.json'])
    manifest['files'].pop('controls.json')
    presentation = flow_presentation({'StateKey': 'Story_1_29', 'Actions': [
        {'Name': 'PostAkEvent', 'Params': {'EventConfig': {'Type': 'Global', 'AkEvent': '/Game/Aki/WwiseAudio/Events/play_all_sound_m0334'}}},
        {'Name': 'ShowTalk', 'Params': {'TalkItems': [{'TidTalk': 'first', 'BackgroundConfig': (
            {'Type': 'SpineImage', 'Id': 7} if background == 'spine'
            else {'Type': 'Image', 'ImageAsset': '/Game/Scene.Scene'})}]}},
        {'Name': 'PostAkEvent', 'Params': {'EventConfig': {'Type': 'Global', 'AkEvent': '/Game/Aki/WwiseAudio/Events/stop'}}},
    ]})
    entries['presentation.json'] = json.dumps(presentation).encode()
    manifest['files']['presentation.json'] = {'size_bytes': len(entries['presentation.json']),
                                               'sha256': hashlib.sha256(entries['presentation.json']).hexdigest()}
    entries['manifest.json'] = json.dumps(manifest).encode()
    with zipfile.ZipFile(bundle, 'w') as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    if state_only:
        monkeypatch.setattr(audio, 'render_bank', AsyncMock(side_effect=ValueError('Wwise control-only event')))
    else:
        monkeypatch.setattr(sequence_music_context, 'verify_authored_music', AsyncMock(return_value=(True, [], {})))
    context = sequence_music_context.authored_flow_music_context(
        {'owner': 7, 'release_id': 21, 'presentation': presentation, 'presentation_sha256': presentation['sha256']},
        {'asset_version': '3.7.1', 'download_id': 'base-plan'})
    output, _ = await audio.prepare_sequence_audio(root, bundle, tools['fmodel'], tools['converter'],
                                                   voice_root=voice, soundtrack_context=context)
    with zipfile.ZipFile(output) as archive:
        controls = json.loads(archive.read('controls.json'))
        section, = controls['media']['sections']
        assert section['scope'] == 'flow' and section['source_proof'] == context['source_proof']
        if state_only:
            assert not section['tracks'] and section['status'] == 'unresolved_audio_event'
            assert any(item.get('status') == 'unresolved_audio' for item in json.loads(archive.read('manifest.json'))['control_sources']['missing'])
        else:
            assert section['status'] == 'rendered'
            assert all(track['role'] == 'music' and track['loop'] is True for track in section['tracks'])


@pytest.mark.asyncio
async def test_legacy_levelplay_context_requires_and_preserves_fresh_presentation(prepared, monkeypatch):
    from wuwa_story.ingestion.spine_presentation import flow_presentation

    from wuwa_story_worker import sequence_music_context

    root, voice, bundle, tools, _, _ = prepared
    presentation = flow_presentation({'StateKey': 'Story_1_1', 'Actions': []})
    with zipfile.ZipFile(bundle) as archive:
        entries = {name: archive.read(name) for name in archive.namelist() if name != 'controls.json'}
    manifest = json.loads(entries['manifest.json'])
    manifest['files'].pop('controls.json')
    entries['presentation.json'] = json.dumps(presentation).encode()
    manifest['files']['presentation.json'] = {'size_bytes': len(entries['presentation.json']),
                                               'sha256': hashlib.sha256(entries['presentation.json']).hexdigest()}
    entries['manifest.json'] = json.dumps(manifest).encode()
    with zipfile.ZipFile(bundle, 'w') as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    context = {'kind': 'levelplay_fixed_start', 'play_event': 'play', 'state_event': 'state', 'stop_event': 'stop',
               'source_proof': {'asset_version': '3.7.1', 'download_id': 'base-plan',
                                'presentation_sha256': presentation['sha256']}}
    monkeypatch.setattr(sequence_music_context, 'prepare_levelplay_music', AsyncMock(return_value=(
        {'language': None, 'rover': None, 'role': 'music', 'status': 'rendered', 'loop': True},
        b'OggS', {}, [], {'play_target': 30})))
    output, _ = await audio.prepare_sequence_audio(root, bundle, tools['fmodel'], tools['converter'],
                                                   voice_root=voice, soundtrack_context=context)
    with zipfile.ZipFile(output) as archive:
        section, = json.loads(archive.read('controls.json'))['media']['sections']
    assert section['reconstruction_kind'] == 'levelplay_fixed_start'
    assert section['source_proof']['presentation_sha256'] == presentation['sha256']
    assert section['tracks'][0]['role'] == 'music'


@pytest.mark.asyncio
async def test_reconstructed_flow_prepares_music_with_authored_state_reference(prepared, monkeypatch):
    from wuwa_story.ingestion.spine_presentation import flow_presentation

    from wuwa_story_worker import sequence_music_context

    root, voice, bundle, tools, _, _ = prepared
    prefix = '/Game/Aki/WwiseAudio/Events/'
    state = prefix + 'play_story_music_daniya_jinzhen_play.play_story_music_daniya_jinzhen_play'
    presentation = flow_presentation({'StateKey': 'Story', 'Actions': [
        {'Name': 'PostAkEvent', 'Params': {'EventConfig': {'Type': 'Global', 'AkEvent': state}}},
        {'Name': 'ShowTalk', 'Params': {'TalkItems': [{'TidTalk': 'line', 'BackgroundConfig': {
            'Type': 'SpineImage', 'Id': 7}}]}},
        {'Name': 'PostAkEvent', 'Params': {'EventConfig': {'Type': 'Global', 'AkEvent': prefix + 'stop.stop'}}},
    ]})
    with zipfile.ZipFile(bundle) as archive:
        entries = {name: archive.read(name) for name in archive.namelist() if name != 'controls.json'}
    manifest = json.loads(entries['manifest.json'])
    manifest['files'].pop('controls.json')
    entries['presentation.json'] = json.dumps(presentation).encode()
    manifest['files']['presentation.json'] = {'size_bytes': len(entries['presentation.json']),
                                               'sha256': hashlib.sha256(entries['presentation.json']).hexdigest()}
    entries['manifest.json'] = json.dumps(manifest).encode()
    with zipfile.ZipFile(bundle, 'w') as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    context = sequence_music_context.authored_flow_music_context(
        {'owner': 7, 'release_id': 21, 'presentation': presentation, 'presentation_sha256': presentation['sha256']},
        {'asset_version': '3.7.1', 'download_id': 'base-plan'})
    monkeypatch.setattr(sequence_music_context, 'prepare_levelplay_music', AsyncMock(return_value=(
        {'status': 'rendered', 'loop': True}, b'OggS', {'timeline.json': b'{}'}, [], {'play_target': 30})))
    output, _ = await audio.prepare_sequence_audio(root, bundle, tools['fmodel'], tools['converter'],
                                                   voice_root=voice, soundtrack_context=context)
    with zipfile.ZipFile(output) as archive:
        section, = json.loads(archive.read('controls.json'))['media']['sections']
    assert section['reference'] == state and section['confidence'] == 'reconstructed'
    assert section['source_proof']['runtime_start_confirmed'] is False
    assert section['tracks'][0]['role'] == 'music' and section['status'] == 'rendered'

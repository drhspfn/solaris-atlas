import errno
import json
import subprocess
import wave
from unittest.mock import AsyncMock

import pytest

from wuwa_story_worker import broker, entity_media
from wuwa_story_worker import cutscene_frame_audio as audio


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    root, assets, voice = [tmp_path / name for name in ('base', 'assets', 'voice')]
    for path in (root, assets, voice):
        path.mkdir()
    (root / 'plan.json').write_text(json.dumps({'id': 'base-plan', 'version': '3.7.1'}))
    fmodel, converter = tmp_path / 'fmodel', tmp_path / 'converter'
    fmodel.write_bytes(b'fmodel')
    converter.write_bytes(b'converter')
    reference = '/Game/Aki/WwiseAudio/Events/example.example'
    event = {'reference': reference, 'video_name': 'Movie', 'start_seconds': 12.5,
             'end_seconds': None, 'mode': 'MusicSubtitle', 'operation': 'post',
             'resolved': True, 'source': {'raw_path': '$[2].Params.Mp4FrameEvents[0]'}}
    cooked = root / 'event.json'
    maps = [{'Key': {'LanguageName': lang}, 'Value': {'EventId': audio.event_id('example'),
             'SoundBanks': [{'SoundBankPathName': f'Event/{lang}/example.bnk'}],
             'Media': [{'MediaId': identity, 'MediaPathName': f'Media/{identity}.wem'}]}}
            for lang, identity in [('en', 1), ('ja', 2), ('ko', 3), ('zh', 4)]]
    cooked.write_text(json.dumps([{'Type': 'AkAudioEvent', 'EventCookedData': {'EventLanguageMap': maps}}]))
    original = root / 'Client/Content/Aki/WwiseAudio/Events/example.uasset'
    original.parent.mkdir(parents=True)
    original.write_bytes(b'original event')
    init = root / 'Init.bnk'
    init.write_bytes(b'init')
    banks = {'Client/Content/Aki/WwiseAudio_Generated/Init.bnk': (init, 'base-plan')}
    media = {}
    for language, identity in [('en', 1), ('ja', 2)]:
        folder = voice / language
        folder.mkdir()
        bank = folder / 'example.bnk'
        bank.write_bytes(language.encode())
        banks[f'Client/Content/Aki/WwiseAudio_Generated/Event/{language}/example.bnk'] = (bank, 'voice-plan')
        wem = folder / f'{identity}.wem'
        wem.write_bytes(b'media')
        media[identity] = (wem, 'voice-plan', f'Client/Content/Aki/WwiseAudio_Generated/Media/{language}/{identity}.wem')
    monkeypatch.setattr(audio, 'tool_path', lambda _: converter)
    monkeypatch.setattr(entity_media, 'client_voice_exports', lambda *_: {})
    monkeypatch.setattr(audio, 'prefetch_events', AsyncMock(return_value={reference: (cooked, [original])}))
    monkeypatch.setattr(audio, 'prefetch_banks', AsyncMock(return_value={}))
    monkeypatch.setattr(audio, 'ensure_archive_index', AsyncMock(return_value=root / 'index'))
    monkeypatch.setattr(audio, 'indexed_audio_paths', lambda *_: [])
    monkeypatch.setattr(audio, '_voice_root', lambda *_: voice)
    monkeypatch.setattr(audio, '_banks', AsyncMock(return_value=banks))
    monkeypatch.setattr(audio, 'prefetch_media', AsyncMock(return_value=media))
    return root, assets, event, fmodel, tmp_path / 'work'


@pytest.mark.asyncio
async def test_exact_localized_inputs_preserve_clock_proof_and_partial_languages(prepared):
    root, assets, event, fmodel, work = prepared
    result = await audio.event_soundtracks(root, assets, 'Movie',
        {'events': [event], 'unresolved': [{'code': 'existing_control_gap'}]}, fmodel, work)
    assert {t['language'] for t in result['soundtracks']} == {'en', 'ja'}
    assert len(result['diagnostics']) == 3
    assert {d.get('language') for d in result['diagnostics']} == {None, 'ko', 'zh'}
    for track in result['soundtracks']:
        assert (assets / track['bank']).read_bytes() == track['language'].encode()
        assert track['start_seconds'] == 12.5 and track['end_seconds'] is None
        assert track['stop_policy'] == 'not_inferred'
        assert track['source'] == event['source']
        assert len(track['source_media']) == 4
        assert track['source_media'][0]['package_path'].endswith('example.uasset')
        assert all(p['sha256'] and p['download_id'] for p in track['source_media'])
    assert audio.prefetch_media.await_args.args[2] == {1, 2, 3, 4}
    assert any(path.name == 'Init.bnk' for path in result['sources'])


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [
    {'start_seconds': float('nan')}, {'start_seconds': True}, {'start_seconds': -1},
    {'mode': 'Target'}, {'resolved': False}, {'reference': '/Game/Aki/WwiseAudio/Events/example.other'},
    {'end_seconds': float('nan')}, {'end_seconds': True}, {'end_seconds': 12.5},
])
async def test_unresolved_bindings_never_export_or_guess(prepared, changes):
    root, assets, event, fmodel, work = prepared
    result = await audio.event_soundtracks(root, assets, 'Movie', {'events': [{**event, **changes}]}, fmodel, work)
    assert result['soundtracks'] == []
    assert result['diagnostics'][0]['code'] == 'frame_audio_unavailable'
    audio.prefetch_events.assert_not_awaited()


@pytest.mark.asyncio
async def test_transient_export_failure_reaches_existing_retry(prepared):
    root, assets, event, fmodel, work = prepared
    audio.prefetch_events.side_effect = BlockingIOError('export lock busy')
    with pytest.raises(BlockingIOError):
        await audio.event_soundtracks(root, assets, 'Movie', {'events': [event]}, fmodel, work)


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['export', 'render'])
@pytest.mark.parametrize('error,retryable', [
    (OSError(errno.ENOSPC, 'disk full'), True),
    (OSError(errno.ENOMEM, 'memory pressure'), True),
    (subprocess.TimeoutExpired('renderer', 120), True),
    (subprocess.CalledProcessError(137, 'renderer'), True),
    (RuntimeError('required tool unavailable'), False),
    (broker.JobDeferred('voice dependency queued'), False),
])
async def test_operational_frame_failures_propagate_without_partial(prepared, monkeypatch, phase, error, retryable):
    root, assets, event, fmodel, work = prepared
    if phase == 'export':
        audio.prefetch_events.side_effect = error
        operation = audio.event_soundtracks(root, assets, 'Movie', {'events': [event]}, fmodel, work)
    else:
        monkeypatch.setattr(audio, 'run_tool', AsyncMock(side_effect=error))
        operation = audio.prepare_frame_audio(root, assets, 'Movie', {'events': [event]},
            fmodel, fmodel, fmodel, work)
    with pytest.raises(type(error)) as caught:
        await operation
    assert caught.value is error
    assert broker.transient_job_error(error) is retryable


def native_xml(tmp_path):
    bank = tmp_path / 'bank.xml'
    bank.write_text('''<root version="172"><object name="CAkSound"><field name="ulID" value="10"/>
      <field name="sourceID" value="101"/><field name="DirectParentID" value="11"/>
      <field name="OverrideBusId" value="0"/></object><object name="CAkActorMixer">
      <field name="ulID" value="11"/><field name="OverrideBusId" value="20"/></object></root>''')
    init = tmp_path / 'init.xml'
    init.write_text(f'''<root><object name="CAkBus"><field name="ulID" value="20"/>
      <field name="OverrideBusId" value="0"/><object name="RTPC">
      <field name="RTPCID" value="{audio.event_id('Volume_Music')}"/>
      <field name="rtpcType" value="0"/><field name="ParamID" value="0"/>
      </object></object></root>''')
    return bank, init


def test_role_is_native_inherited_volume_route_not_filename_or_language(tmp_path):
    bank, init = native_xml(tmp_path)
    proof = audio.audio_bus_roles(bank, init, {101})[101]
    assert proof['role'] == 'music'
    assert proof['routes'][0]['parent_route'] == [10, 11]
    assert proof['routes'][0]['bus_route'] == [20]
    assert proof['routes'][0]['volume_controls'][0]['parameter'] == 'Volume_Music'
    init.write_text(init.read_text().replace(str(audio.event_id('Volume_Music')), '999'))
    with pytest.raises(ValueError, match='Unknown or conflicting'):
        audio.audio_bus_roles(bank, init, {101})


@pytest.mark.parametrize('delay,after_play', [(1000, False), (0, True)])
def test_startup_state_never_accepts_delayed_or_running_context(tmp_path, delay, after_play):
    bank, init = native_xml(tmp_path)
    state = {'kind': 'SetState', 'time_ms': delay, 'state_group_id': 30, 'state_id': 31}
    play = {'kind': 'Play', 'time_ms': 0, 'target_id': 10}
    with pytest.raises(ValueError):
        audio._initial_parameters({'actions': [play, state] if after_play else [state, play]}, bank, init)


def test_unconsumed_initial_state_is_explicit_and_consuming_state_remains_guarded(tmp_path):
    bank, init = native_xml(tmp_path)
    timeline = {'actions': [{'kind': 'SetState', 'time_ms': 0, 'state_group_id': 30, 'state_id': 31},
                            {'kind': 'Play', 'time_ms': 0, 'target_id': 10}]}
    assert audio._initial_parameters(timeline, bank, init) == ['(30=31)']
    init.write_text(init.read_text().replace('<object name="RTPC">',
        '<field name="ulStateGroupID" value="30"/><object name="RTPC">'))
    with pytest.raises(ValueError, match='unsupported native audio consumer'):
        audio._initial_parameters(timeline, bank, init)


@pytest.mark.asyncio
@pytest.mark.parametrize('group', ['', 'group = -S1 #p 0.01\n'])
async def test_flat_layer_render_keeps_authored_txtp_commands(tmp_path, monkeypatch, group):
    bank_xml, init_xml = native_xml(tmp_path)
    bank = tmp_path / 'example.bnk'
    bank.write_bytes(b'bank')
    inputs = tmp_path / 'inputs'
    inputs.mkdir()
    (inputs / 'Init.bnk').write_bytes(b'init')
    (inputs / '101.wem').write_bytes(b'compressed media')
    renderer = tmp_path / 'wwiser'
    renderer.write_bytes(b'tool')
    monkeypatch.setattr(audio, 'tool_path', lambda _: renderer)
    monkeypatch.setattr(audio, 'event_timeline', lambda *_: {'actions': [{'kind': 'Play'}]})
    monkeypatch.setattr(audio, 'rover_parameters', lambda *_args, **_kw: [])
    commands = []

    async def run(args, *_):
        if '-dn' in args:
            target = audio.Path(args[args.index('-dn') + 1] + '.xml')
            target.write_bytes((init_xml if args[-1].endswith('Init.bnk') else bank_xml).read_bytes())
        elif '-go' in args:
            folder = audio.Path(args[args.index('-go') + 1])
            (folder / 'event.txtp').write_text('wem/101.wem #p 0.25 #v -6.0\n' + group)
        else:
            commands.append(audio.Path(args[-1]).read_text())
            with wave.open(args[args.index('-o') + 1], 'wb') as stream:
                stream.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
                stream.writeframes(bytes(48000 * 4))
        return None

    monkeypatch.setattr(audio, 'run_tool', run)
    stems = await audio.render_role_stems(bank, inputs, tmp_path / 'render', tmp_path / 'decoder')
    assert [stem['role'] for stem in stems] == ['music']
    assert stems[0]['duration_seconds'] == 1
    assert commands == ['wem/101.wem #p 0.25 #v -6.0\n' + group]
    assert stems[0]['media_ids'] == [101]


@pytest.mark.asyncio
async def test_movies_without_frame_audio_do_not_require_audio_tools(tmp_path, monkeypatch):
    monkeypatch.setattr(audio, 'tool_path', lambda *_: pytest.fail('No frame audio needs no tool lookup'))
    result = await audio.prepare_frame_audio(tmp_path, tmp_path, 'Movie', {'events': []},
        tmp_path / 'absent-fmodel', tmp_path / 'absent-decoder', tmp_path / 'absent-ffmpeg', tmp_path)
    assert result == {'soundtracks': [], 'sources': [], 'diagnostics': [], 'stems': []}

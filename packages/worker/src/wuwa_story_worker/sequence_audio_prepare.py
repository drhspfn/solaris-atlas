"""Prepare exact sequence Wwise soundtracks without flattening engine timelines."""

import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
import wave
import zipfile
from pathlib import Path, PurePosixPath

from wuwa_story_worker.archive_index import (
    ensure_archive_index,
    indexed_audio_paths,
    voice_filter_batches,
)
from wuwa_story_worker.broker import transient_job_error
from wuwa_story_worker.character_voice_assets import prefetch_banks, prefetch_events, prefetch_media
from wuwa_story_worker.cutscene_import import run_tool
from wuwa_story_worker.media_provenance import package_path, sha256
from wuwa_story_worker.tooling import asset_workspace, tool_path
from wuwa_story_worker.wwise_banks import event_id, render_bank, rover_parameters


def _ready_voice(path, version):
    plan = json.loads((path / 'plan.json').read_text(encoding='utf-8'))
    status = json.loads((path / 'status.json').read_text(encoding='utf-8'))
    if plan.get('version') != version or not plan.get('id') or status.get('id') != plan['id'] or status.get('state') != 'downloaded':
        raise ValueError(
            'Sequence audio requires a completed voice plan matching the pinned client version')
    return path


def _voice_root(explicit, version):
    if explicit is not None or os.getenv('WUWA_VOICE_ROOT'):
        return _ready_voice(Path(explicit or os.environ['WUWA_VOICE_ROOT']).resolve(), version)
    folder = asset_workspace() / 'voices'
    candidates = []
    for path in sorted(folder.glob(version + '-*')) if folder.is_dir() else []:
        try:
            candidates.append(_ready_voice(path, version))
        except (OSError, ValueError):
            continue
    if len(candidates) != 1:
        raise ValueError(
            'Localized sequence audio needs an explicit or unique completed matching voice_root')
    return candidates[0]


def _safe(path):
    relative = PurePosixPath(path)
    if not path or relative.is_absolute() or '..' in relative.parts or '\\' in path or ':' in path:
        raise ValueError('Unsafe sequence audio archive path')
    return relative


async def _banks(root, voice, expected, initial, work, fmodel, tool):
    """Use exact CookedData paths, including Event/en/... absent from prefetch_banks."""
    result = {path: (source, json.loads((root / 'plan.json').read_text())['id'])
              for path, source in initial.items()}
    for label, archive in [('main', root), ('voice', voice)]:
        if archive is None or expected.issubset(result):
            continue
        index = await ensure_archive_index(archive, fmodel, root)
        paths = await asyncio.to_thread(indexed_audio_paths, index,
                                        {PurePosixPath(path).name for path in expected - result.keys()})
        selected = sorted(set(paths) & (expected - result.keys()))
        output = work / 'banks' / label / 'files'
        output.mkdir(parents=True, exist_ok=True)
        plan = json.loads((archive / 'plan.json').read_text())
        for batch in voice_filter_batches(selected):
            log = output.parent / \
                (hashlib.sha256('|'.join(batch).encode()
                                ).hexdigest()[:16] + '.log')
            await tool([fmodel.resolve(), (archive / 'game').resolve(), '@' + str((root / 'keys.txt').resolve()),
                        output.resolve(), '|'.join(batch)], log, 7200)
            if any(word in log.read_text(errors='replace') for word in ('[Fail]', '[Error]')):
                raise ValueError(
                    'Sequence bank export failed; inspect the private tool log')
            for path in batch:
                source = output / _safe(path)
                if source.is_file():
                    result[path] = (source, plan['id'])
    return result


async def prepare_sequence_audio(root, bundle, fmodel, converter, voice_root=None, soundtrack_context=None):
    from wuwa_story_worker import bundle_io
    with bundle_io.spill_directory(Path(bundle).parent) as spill:
        return await _prepare_sequence_audio(root, bundle, fmodel, converter, voice_root, soundtrack_context, spill)


async def _prepare_sequence_audio(root, bundle, fmodel, converter, voice_root, soundtrack_context, spill):
    """Return a new verified bundle and audio originals; section failures remain partial."""
    from wuwa_story_worker import bundle_io
    from wuwa_story_worker.entity_media import client_voice_exports, tool, verified_voice_export

    root, bundle = Path(root), Path(bundle)
    with zipfile.ZipFile(bundle) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        entries = bundle_io.load_entries(archive, manifest['files'], spill)
    legacy_controls = 'controls.json' not in entries
    if legacy_controls:
        if not soundtrack_context or soundtrack_context.get('kind') not in {'flow_authored_music', 'levelplay_fixed_start', 'state_consumer_fixed_start'}:
            return bundle, []
        controls = {'format': 'solaris.sequence-controls.v1', 'media': {'complete_video': False, 'sections': []}}
        manifest['controls'] = {'file': 'controls.json'}
    else:
        controls = json.loads(entries['controls.json'])
    media_sections = controls.get('media', {}).get('sections', [])
    media_sections[:] = [section for section in media_sections
                         if section.get('reconstruction_kind') not in {'levelplay_fixed_start', 'flow_authored_music', 'state_consumer_fixed_start'}]
    flow_context = soundtrack_context if isinstance(soundtrack_context, dict) and soundtrack_context.get('kind') == 'flow_authored_music' else None
    if flow_context:
        identity = hashlib.sha256(json.dumps(flow_context, ensure_ascii=False, sort_keys=True,
                                              separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        media_sections.append({'kind': 'audio_event', 'source': 'flow:' + identity, 'scope': 'flow',
                               'reference': flow_context.get('play_event'), 'reconstruction_kind': 'flow_authored_music',
                               'source_proof': flow_context.get('source_proof'), 'clock': None, 'range': None,
                               'timing_basis': 'authored_action_extent'})
    sections = [section for section in controls.get('media', {}).get('sections', [])
                if section.get('kind') == 'audio_event']
    if not sections and soundtrack_context is None:
        return bundle, []
    plan = json.loads((root / 'plan.json').read_text(encoding='utf-8'))
    if manifest.get('asset_version') != plan['version'] or manifest.get('download_id') != plan['id']:
        raise ValueError(
            'Sequence audio bundle differs from the pinned base client')
    if flow_context or (soundtrack_context and soundtrack_context.get('kind') == 'state_consumer_fixed_start') or (legacy_controls and soundtrack_context):
        from wuwa_story.ingestion.spine_presentation import presentation_hash

        if 'presentation.json' not in entries:
            raise ValueError('Flow soundtrack requires the exact retained presentation')
        presentation = json.loads(entries['presentation.json'])
        proof = soundtrack_context.get('source_proof') or {}
        if (presentation.get('sha256') != proof.get('presentation_sha256')
                or presentation_hash({k: v for k, v in presentation.items() if k != 'sha256'}) != presentation.get('sha256')
                or proof.get('asset_version') != plan['version'] or proof.get('download_id') != plan['id']):
            raise ValueError('Flow soundtrack differs from the retained presentation or client')
        if flow_context or soundtrack_context.get('kind') == 'state_consumer_fixed_start':
            from wuwa_story_worker.sequence_music_context import authored_flow_music_context

            expected = authored_flow_music_context(
                {'owner': proof.get('flow_owner'), 'release_id': proof.get('release_id'),
                 'presentation': presentation, 'presentation_sha256': presentation['sha256']},
                {'asset_version': plan['version'], 'download_id': plan['id']})
            if expected != soundtrack_context:
                raise ValueError('Flow soundtrack does not match its authored action extent')
    work = root / 'sequence-audio' / sha256(bundle)[:20]
    work.mkdir(parents=True, exist_ok=True)
    context_identity = (hashlib.sha256(json.dumps(soundtrack_context, ensure_ascii=False, sort_keys=True,
                                                  separators=(',', ':'), allow_nan=False).encode()).hexdigest()
                        if soundtrack_context is not None else None)
    retried_sources = {section.get('source') for section in sections}
    if context_identity and not flow_context:
        retried_sources.add('levelplay:' + context_identity)
    missing = manifest.setdefault(
        'control_sources', {}).setdefault('missing', [])
    missing[:] = [item for item in missing if not (
        item.get('status') == 'unresolved_audio' and item.get(
            'source') in retried_sources
        and item.get('code') == 'sequence_audio_unavailable')]
    for section in sections:
        if isinstance(section.get('diagnostics'), list):
            section['diagnostics'] = [item for item in section['diagnostics']
                                      if item.get('code') != 'sequence_audio_unavailable']
    originals, prepared = set(), {}
    for section in sections:
        section.pop('tracks', None)
        diagnostics = section.setdefault('diagnostics', [])
        reference = section.get('reference')
        try:
            if not isinstance(reference, str) or not re.fullmatch(r'/Game/Aki/WwiseAudio/Events/[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)?', reference):
                raise ValueError(
                    'Missing or unsupported authored audio event reference')
            cache_key = (reference, section.get('reconstruction_kind') == 'flow_authored_music')
            if cache_key not in prepared:
                decoder, renderer = tool_path(
                    'WUWA_VGMSTREAM_PATH'), tool_path('WUWA_WWISER_PATH')
                tools = {'WUWA_VGMSTREAM_PATH': sha256(decoder), 'WUWA_WWISER_PATH': sha256(renderer),
                         'WUWA_FMODEL_PATH': sha256(fmodel), 'WUWA_TEXTURE_CONVERTER_PATH': sha256(converter)}
                cached = client_voice_exports(root, tools['WUWA_FMODEL_PATH'])
                events = await prefetch_events(root, [reference], cached, fmodel, converter, tool, verified_voice_export)
                if reference not in events:
                    raise ValueError(
                        'Exact authored Wwise event could not be decoded')
                cooked_path, event_originals = events[reference]
                cooked = json.loads(
                    cooked_path.read_text(encoding='utf-8-sig'))
                entries['audio-evidence/' + hashlib.sha256(reference.encode()).hexdigest()[
                    :16] + '/event.json'] = cooked_path.read_bytes()
                maps = [row for export in cooked if export.get('Type') == 'AkAudioEvent'
                        for row in export.get('EventCookedData', {}).get('EventLanguageMap', [])]
                if not maps:
                    raise ValueError(
                        'Authored Wwise event has no cooked language map')
                stem = reference.rsplit('/', 1)[-1].split('.')[0]
                expected_banks, identities = set(), set()
                for row in maps:
                    value = row['Value']
                    if value.get('EventId') != event_id(stem):
                        raise ValueError(
                            'Cooked Wwise event ID differs from authored event')
                    for bank in value.get('SoundBanks', []):
                        path = bank['SoundBankPathName']
                        _safe(path)
                        if not path.startswith('Event/') or PurePosixPath(path).name != stem + '.bnk':
                            raise ValueError(
                                'Unsupported cooked sequence bank path')
                        expected_banks.add(
                            'Client/Content/Aki/WwiseAudio_Generated/' + path)
                    for media in value.get('Media', []):
                        identity = media['MediaId']
                        if type(identity) is not int or identity <= 0 or PurePosixPath(media['MediaPathName']).name != f'{identity}.wem':
                            raise ValueError(
                                'Invalid cooked sequence media identity')
                        identities.add(identity)
                initial = await prefetch_banks(root, [reference], fmodel, verified_voice_export)
                base_index = await ensure_archive_index(root, fmodel, root)
                base_paths = await asyncio.to_thread(indexed_audio_paths, base_index,
                                                     {PurePosixPath(path).name for path in expected_banks} | {f'{identity}.wem' for identity in identities})
                base_ids = {int(PurePosixPath(path).stem)
                            for path in base_paths if PurePosixPath(path).suffix == '.wem'}
                voice = root if identities.issubset(base_ids) and expected_banks.issubset(
                    base_paths) else _voice_root(voice_root, plan['version'])
                voice_plan = json.loads((voice / 'plan.json').read_text())
                event_work = work / \
                    hashlib.sha256(reference.encode()).hexdigest()[:16]
                event_work.mkdir(exist_ok=True)
                banks = await _banks(root, voice, expected_banks, initial, event_work, fmodel, tool)
                init = banks.get(
                    'Client/Content/Aki/WwiseAudio_Generated/Init.bnk')
                if init is None:
                    raise ValueError(
                        'Exact Wwise Init bank is required for sequence bus gains')
                media = await prefetch_media(root, voice, identities, cached, fmodel, tool, verified_voice_export)
                originals.update(event_originals)
                originals.update(source for source,
                                 _download in banks.values())
                originals.update(source for source, _download,
                                 _package in media.values())
                audio = event_work / 'audio'
                audio.mkdir(exist_ok=True)
                shutil.copyfile(init[0], audio / 'Init.bnk')
                for identity, (source, _download, _package) in media.items():
                    shutil.copyfile(source, audio / f'{identity}.wem')
                tracks, errors = [], []
                for row in maps:
                    language = row['Key']['LanguageName']
                    if language == 'SFX':
                        language = None
                    try:
                        if language not in {None, 'en', 'ja', 'ko', 'zh'} or len(row['Value'].get('SoundBanks', [])) != 1:
                            raise ValueError(
                                'Unsupported cooked language or multi-bank sequence event')
                        bank_path = 'Client/Content/Aki/WwiseAudio_Generated/' + \
                            row['Value']['SoundBanks'][0]['SoundBankPathName']
                        if bank_path not in banks:
                            raise ValueError(
                                'Missing exact localized Wwise bank: ' + bank_path)
                        bank, bank_download = banks[bank_path]
                        branch = event_work / (language or 'shared')
                        branch.mkdir(exist_ok=True)
                        xml = branch / 'bank.xml'
                        await run_tool([sys.executable, str(renderer), '-d', 'xml', '-dn', str(branch / 'bank'), '--', str(bank)], 120)
                        male = rover_parameters(xml, 'male', language=language)
                        female = rover_parameters(
                            xml, 'female', language=language)
                        for rover in ('male', 'female') if male != female else (None,):
                            wav, evidence = await render_bank(bank, audio, branch, decoder, rover, run_tool, language=language)
                            flow_loop = None
                            if section.get('reconstruction_kind') == 'flow_authored_music':
                                from wuwa_story_worker.sequence_music_context import (
                                    verify_authored_music,
                                )
                                flow_loop, flow_originals, flow_evidence = await verify_authored_music(
                                    root, fmodel, converter, audio, branch, evidence, flow_context.get('stop_event'))
                                originals.update(flow_originals)
                                for proof_name, content in flow_evidence.items():
                                    entries['audio-evidence/' + hashlib.sha256(reference.encode()).hexdigest()[:16] + '/' + proof_name] = content
                            with wave.open(str(wav)) as stream:
                                duration = stream.getnframes() / stream.getframerate()
                            if duration <= 0:
                                raise ValueError(
                                    'Empty sequence audio rendition')
                            name = hashlib.sha256(reference.encode()).hexdigest()[
                                :16] + '-' + (language or 'shared') + '-' + (rover or 'shared')
                            ogg = branch / (name + '.ogg')
                            await run_tool(['ffmpeg', '-v', 'error', '-y', '-i', str(wav), '-map_metadata', '-1',
                                            '-c:a', 'libopus', '-b:a', '192k', str(ogg)])
                            entry = 'audio/' + name + '.ogg'
                            entries[entry] = ogg.read_bytes()
                            proof = [{'package_path': bank_path, 'download_id': bank_download, 'sha256': sha256(bank)},
                                     {'package_path': 'Client/Content/Aki/WwiseAudio_Generated/Init.bnk', 'download_id': init[1], 'sha256': sha256(init[0])}]
                            proof.extend({'package_path': package_path(source), 'download_id': plan['id'], 'sha256': sha256(source)}
                                         for source in event_originals)
                            if flow_loop is not None:
                                proof.extend({'package_path': package_path(source), 'download_id': plan['id'], 'sha256': sha256(source)}
                                             for source in flow_originals)
                            originals.update([*event_originals, bank, init[0]])
                            for source in evidence:
                                if source.suffix == '.wem':
                                    identity = int(source.stem)
                                    original, download, package = media[identity]
                                    proof.append(
                                        {'package_path': package, 'download_id': download, 'sha256': sha256(original)})
                                    originals.add(original)
                                elif source.suffix in {'.xml', '.txtp', '.json', '.wwiser-original'}:
                                    key = 'audio-evidence/' + name + '/' + source.name
                                    entries[key] = source.read_bytes()
                            tracks.append({'file': entry, 'language': language, 'rover': rover, 'status': 'rendered',
                                           'duration_seconds': duration, 'source_media': proof, 'tools': tools,
                                           'voice_plan_id': voice_plan['id'] if voice != root else None,
                                           'timing_basis': 'standalone_authored_event; section clock/range/fade remain source values',
                                           **({'role': 'music', 'loop': flow_loop} if flow_loop is not None else {})})
                    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                        if isinstance(error, BlockingIOError) or transient_job_error(error):
                            raise
                        errors.append({'code': 'sequence_audio_unavailable',
                                      'language': language, 'message': str(error)[:500]})
                prepared[cache_key] = (tracks, errors)
            tracks, errors = prepared[cache_key]
            section['tracks'] = tracks
            diagnostics.extend(
                error for error in errors if error not in diagnostics)
            section['status'] = 'rendered' if tracks else 'unresolved_audio_event'
        except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
            if isinstance(error, BlockingIOError) or transient_job_error(error):
                raise
            section['status'] = 'unresolved_audio_event'
            diagnostics.append(
                {'code': 'sequence_audio_unavailable', 'message': str(error)[:500]})
    if soundtrack_context is not None and not flow_context:
        from wuwa_story_worker.sequence_music_context import prepare_levelplay_music

        identity = context_identity
        reconstructed = soundtrack_context.get('kind') == 'state_consumer_fixed_start'
        section = {'kind': 'audio_event', 'source': 'levelplay:' + identity, 'scope': 'flow',
                   'reference': soundtrack_context.get('state_event' if reconstructed else 'play_event'), 'clock': None, 'range': None,
                   'reconstruction_kind': soundtrack_context.get('kind', 'levelplay_fixed_start'), 'timing_basis': 'fixed_start_reconstruction',
                   **({'confidence': 'reconstructed'} if reconstructed else {}),
                   'source_proof': soundtrack_context.get('source_proof'), 'status': 'unresolved_audio_event',
                   'tracks': [], 'diagnostics': []}
        media_sections.append(section)
        sections.append(section)
        try:
            track, data, evidence, audio_originals, binding = await prepare_levelplay_music(
                root, fmodel, converter, work / identity, soundtrack_context)
            entry = f'audio/levelplay-{identity}.ogg'
            entries[entry] = data
            for name, content in evidence.items():
                _safe(name)
                entries[f'audio-evidence/{identity}/{name}'] = content
            track.update(file=entry, role='music')
            section.update(status='rendered', tracks=[track], loop=track['loop'],
                           state_event=soundtrack_context['state_event'],
                           stop_event=soundtrack_context['stop_event'],
                           music_binding={key: value for key, value in binding.items() if key != 'bank_timelines'})
            originals.update(audio_originals)
        except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
            if isinstance(error, BlockingIOError) or transient_job_error(error):
                raise
            section['diagnostics'].append(
                {'code': 'sequence_audio_unavailable', 'message': str(error)[:500]})
    for section in sections:
        for diagnostic in section.get('diagnostics', []):
            value = {'status': 'unresolved_audio',
                     'source': section['source'], **diagnostic}
            if value not in missing:
                missing.append(value)
    for source in originals:
        name, data = 'source/' + package_path(source), Path(source)
        if name in entries and not bundle_io.same_content(entries[name], data):
            raise ValueError(
                'Sequence audio original conflicts with retained source bytes')
        entries[name] = data
    entries['controls.json'] = json.dumps(
        controls, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    manifest['files'] = {name: bundle_io.digest(data) for name, data in entries.items()}
    entries['manifest.json'] = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    # Different flow/release music proofs can share the same visual source ZIP.
    # Never overwrite an archive another worker may still be rendering/uploading.
    identity = hashlib.sha256(entries['manifest.json']).hexdigest()
    output = bundle.with_name(bundle.stem + '-audio-' + identity + '.zip')
    temporary = output.with_name(output.name + '.' + uuid.uuid4().hex + '.partial')
    try:
        bundle_io.write_archive(temporary, entries)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return output, sorted(originals)

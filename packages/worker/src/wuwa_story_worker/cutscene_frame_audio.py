"""Prepare source-authored movie-clock events through the shared Wwise exporters.

This module does not invent event endpoints or merge alternative wrapper contexts.
"""

import asyncio
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
import wave
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path, PurePosixPath

from wuwa_story_worker.archive_index import ensure_archive_index, indexed_audio_paths
from wuwa_story_worker.broker import transient_job_error
from wuwa_story_worker.character_voice_assets import prefetch_banks, prefetch_events, prefetch_media
from wuwa_story_worker.cutscene_import import run_tool
from wuwa_story_worker.media_provenance import package_path, sha256
from wuwa_story_worker.sequence_audio_prepare import _banks, _voice_root
from wuwa_story_worker.tooling import tool_path
from wuwa_story_worker.wwise_banks import (
    event_id,
    event_timeline,
    rover_parameters,
    validate_event_timeline,
)


def _initial_parameters(timeline, bank_xml, init_xml):
    """Retain startup side effects even when this closed audio graph does not consume them."""
    actions = timeline['actions']
    if any(a['kind'] not in {'SetState', 'Play', 'PlayEvent'} for a in actions):
        raise ValueError('Frame event requires a shared running control timeline')
    try:
        return validate_event_timeline(timeline, bank_xml)
    except ValueError:
        states = [a for a in actions if a['kind'] == 'SetState']
        plays = [i for i, a in enumerate(actions) if a['kind'] == 'Play']
        if (not states or not plays or any(a['time_ms'] != 0 for a in states)
                or any(i >= min(plays) for i, a in enumerate(actions) if a['kind'] == 'SetState')):
            raise
        bindings = {}
        for state in states:
            group, value = state['state_group_id'], state['state_id']
            if group in bindings and bindings[group] != value:
                raise ValueError('Conflicting initial frame audio states') from None
            # STMG defines global state identities; only HIRC audio/bus nodes
            # consume them. An unconsumed state may still affect other events.
            for path in (bank_xml, init_xml):
                for node in ET.parse(path).iter('object'):
                    if (node.find("field[@name='ulID']") is not None
                            and node.get('name', '').startswith('CAk')
                            and not node.get('name', '').startswith(('CAkAction', 'CAkEvent'))
                            and node.find(f".//field[@value='{group}']") is not None):
                        raise ValueError('Initial frame State has an unsupported native audio consumer') from None
            bindings[group] = value
        return sorted(f'({group}={value})' for group, value in bindings.items())


async def render_role_stems(bank, audio, work, decoder, *, language=None, rover=None):
    """Render independent native top-level layers, preserving each TXTP command.

    Nested groups or shared state timelines remain explicit unsupported cases.
    No whole-event render is repeated for each role, and no WEM is normalized.
    """
    bank, audio, work, decoder = map(Path, (bank, audio, work, decoder))
    renderer = tool_path('WUWA_WWISER_PATH')
    init = audio / 'Init.bnk'
    if not init.is_file():
        raise ValueError('Exact Init bank is required for role rendering')
    work.mkdir(parents=True, exist_ok=True)
    bank_xml, init_xml = work / 'bank.xml', work / 'init.xml'
    for source, output in ((bank, bank_xml), (init, init_xml)):
        await run_tool([sys.executable, str(renderer), '-d', 'xml', '-dn', str(output.with_suffix('')), '--', str(source)], 120)
    timeline = event_timeline(bank_xml, event_id(bank.stem))
    (work / 'event-timeline.json').write_text(json.dumps(timeline, indent=2))
    params = _initial_parameters(timeline, bank_xml, init_xml)
    requested = rover_parameters(bank_xml, rover, language=language)
    if any(p.split('=')[0] == q.split('=')[0] and p != q for p in params for q in requested):
        raise ValueError('Initial State conflicts with playback selection')
    generated = work / 'recipe'
    generated.mkdir(exist_ok=True)
    for path in generated.glob('*.txtp'):
        path.unlink()
    command = [sys.executable, str(renderer), '-d', 'none', '-g', '-gwd', '-gra',
               '-go', str(generated), '-gw', 'wem', '-gf', str(event_id(bank.stem))]
    if params or requested:
        command += ['-gp', *sorted(set(params + requested))]
    report = await run_tool([*command, '--', str(bank), str(init)], 120)
    message = b''.join(getattr(report, k, b'') or b'' for k in ('stdout', 'stderr')).decode(errors='replace')
    (work / 'renderer.log').write_text(message)
    if 'missing' in message.lower() or 'error' in message.lower():
        raise ValueError('Native frame recipe has unresolved dependencies; inspect renderer.log')
    recipes = list(generated.glob('*.txtp'))
    if len(recipes) != 1:
        raise ValueError('Frame audio needs an explicit random/switch selection')
    body = recipes[0].read_text(encoding='utf-8-sig').split('# AUTOGENERATED', 1)[0]
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    layers = [line for line in lines if re.match(r'^wem/[0-9]+\.wem(?:\s|$)', line)]
    groups = [line for line in lines if line.startswith('group')]
    if (not layers or len(lines) != len(layers) + len(groups)
            or (len(layers) > 1 and groups != [f'group = -L{len(layers)} #@layer-v'])
            or (len(layers) == 1 and groups)):
        raise ValueError('Role separation requires independent native top-level layers')
    identities = {int(re.match(r'wem/(\d+)\.wem', line)[1]) for line in layers}
    roles = audio_bus_roles(bank_xml, init_xml, identities)
    wem = generated / 'wem'
    wem.mkdir(exist_ok=True)
    for identity in identities:
        source = audio / f'{identity}.wem'
        if not source.is_file():
            raise ValueError(f'Frame recipe WEM is unavailable: {identity}')
        shutil.copyfile(source, wem / source.name)
    (work / 'role-proof.json').write_text(json.dumps(roles, indent=2))
    outputs = []
    for role in sorted({p['role'] for p in roles.values()}):
        selected = [line for line in layers if roles[int(re.match(r'wem/(\d+)\.wem', line)[1])]['role'] == role]
        recipe = generated / (role + '.txtp')
        recipe.write_text('\n'.join(selected + ([f'group = -L{len(selected)} #@layer-v'] if len(selected) > 1 else [])) + '\n')
        wav = work / (role + '.wav')
        await run_tool([str(decoder.resolve()), '-i', '-o', str(wav), str(recipe)], 300)
        with wave.open(str(wav)) as stream:
            duration = stream.getnframes() / stream.getframerate()
            if duration <= 0 or stream.getframerate() != 48000 or stream.getnchannels() not in {1, 2}:
                raise ValueError('Invalid native role WAV')
        selected_ids = {int(re.match(r'wem/(\d+)\.wem', line)[1]) for line in selected}
        outputs.append({'role': role, 'path': wav, 'duration_seconds': duration,
                        'recipe': recipe, 'media_ids': sorted(selected_ids),
                        'role_evidence': {i: roles[i] for i in selected_ids}})
    return outputs


def audio_bus_roles(bank_xml, init_xml, identities):
    """Classify selected media by inherited native output-bus volume controls.

    Labels below identify Wwise GameParameter hashes, never filenames, language,
    durations or an assumption that every unlocalized source is an effect.
    """
    controls = {event_id('Volume_' + name): (role, 'Volume_' + name)
                for role, name in [('music', 'Music'), ('voice', 'Voice'), ('effects', 'SFX')]}
    nodes = {}
    for path in (bank_xml, init_xml):
        for node in ET.parse(path).iter('object'):
            identity = node.find("field[@name='ulID']")
            if identity is None or node.get('name') not in {
                    'CAkSound', 'CAkActorMixer', 'CAkSwitchCntr', 'CAkRanSeqCntr',
                    'CAkLayerCntr', 'CAkMusicTrack', 'CAkMusicSegment',
                    'CAkMusicRanSeqCntr', 'CAkMusicSwitchCntr', 'CAkBus', 'CAkAuxBus'}:
                continue
            key = int(identity.get('value'))
            if key in nodes:
                raise ValueError('Ambiguous native audio node identity')
            nodes[key] = node

    def field(node, name):
        values = {int(f.get('value')) for f in node.findall(f".//field[@name='{name}']")}
        if len(values) > 1:
            raise ValueError('Ambiguous native audio routing field: ' + name)
        return next(iter(values), 0)

    result = {}
    for identity in identities:
        leaves = [key for key, node in nodes.items() if node.get('name') in {'CAkSound', 'CAkMusicTrack'}
                  and node.find(f".//field[@name='sourceID'][@value='{identity}']") is not None]
        if not leaves:
            raise ValueError(f'Selected WEM has no native source node: {identity}')
        proofs = []
        for leaf in leaves:
            route, bus, current = [], 0, leaf
            while current:
                if current in route or current not in nodes:
                    raise ValueError('Incomplete or cyclic native audio parent route')
                route.append(current)
                node = nodes[current]
                bus = field(node, 'OverrideBusId')
                if bus:
                    break
                current = field(node, 'DirectParentID')
            if not bus:
                raise ValueError('Selected source has no explicit inherited output bus')
            buses, matches = [], []
            while bus:
                if bus in buses or bus not in nodes or nodes[bus].get('name') not in {'CAkBus', 'CAkAuxBus'}:
                    raise ValueError('Incomplete or cyclic native output bus route')
                buses.append(bus)
                node = nodes[bus]
                for rtpc in node.findall(".//object[@name='RTPC']"):
                    key = field(rtpc, 'RTPCID')
                    if key in controls:
                        if field(rtpc, 'rtpcType') != 0 or field(rtpc, 'ParamID') not in {0, 5}:
                            raise ValueError('Role control does not target native volume')
                        role, label = controls[key]
                        matches.append({'role': role, 'parameter': label, 'rtpc_id': key, 'bus_id': bus})
                bus = field(node, 'OverrideBusId')
            roles = {item['role'] for item in matches}
            if len(roles) != 1:
                raise ValueError(f'Unknown or conflicting native role controls for WEM {identity}')
            proofs.append({'source_node_id': leaf, 'parent_route': route, 'bus_route': buses,
                           'volume_controls': matches, 'role': roles.pop()})
        roles = {proof['role'] for proof in proofs}
        if len(roles) != 1:
            raise ValueError('Same media has incompatible native role routes')
        result[identity] = {'role': roles.pop(), 'routes': proofs,
                            'bank_xml_sha256': sha256(Path(bank_xml)), 'init_xml_sha256': sha256(Path(init_xml))}
    return result


async def event_soundtracks(root, assets, movie_name, bindings, fmodel, work, voice_root=None):
    """Return exact bank inputs, originals and unresolved source diagnostics.

    ``bindings`` is ONE discover_movie_audio result, never a list of alternative
    wrappers. Bank paths are relative to ``assets``; endpoints remain authored.
    """
    from wuwa_story_worker.entity_media import client_voice_exports, tool, verified_voice_export

    root, assets, work, fmodel = map(Path, (root, assets, work, fmodel))
    result = {'soundtracks': [], 'sources': [], 'diagnostics': list(bindings.get('unresolved', []))}
    events = bindings.get('events', [])
    if not events:
        return result
    plan = json.loads((root / 'plan.json').read_text(encoding='utf-8'))
    converter = tool_path('WUWA_TEXTURE_CONVERTER_PATH')
    cached = client_voice_exports(root, sha256(fmodel))
    originals = set()
    for event in events:
        try:
            reference, start = event['reference'], event['start_seconds']
            if (not event.get('resolved') or event.get('mode') not in {'Global', 'MusicSubtitle'}
                    or event.get('operation') != 'post'
                    or event.get('video_name', movie_name) != movie_name
                    or not isinstance(reference, str)
                    or not re.fullmatch(r'/Game/Aki/WwiseAudio/Events/[A-Za-z0-9_]+\.[A-Za-z0-9_]+', reference)
                    or type(start) not in (int, float) or not math.isfinite(start) or start < 0):
                raise ValueError('Unresolved or invalid movie-clock event binding')
            stem = reference.rsplit('/', 1)[-1].split('.')[0]
            if reference.rsplit('.', 1)[-1] != stem:
                raise ValueError('Wwise package and event object names disagree')
            end = event.get('end_seconds')
            if end is not None and (type(end) not in (int, float) or not math.isfinite(end) or end <= start):
                raise ValueError('Invalid authored movie-clock event endpoint')
            cooked = await prefetch_events(root, [reference], cached, fmodel, converter, tool, verified_voice_export)
            if reference not in cooked:
                raise ValueError('Exact frame audio event is unavailable')
            cooked_path, event_originals = cooked[reference]
            maps = [row for export in json.loads(cooked_path.read_text(encoding='utf-8-sig'))
                    if export.get('Type') == 'AkAudioEvent'
                    for row in export.get('EventCookedData', {}).get('EventLanguageMap', [])]
            if not maps:
                raise ValueError('Frame event has no cooked language map')
            expected, identities = set(), set()
            for row in maps:
                language, value = row['Key']['LanguageName'], row['Value']
                if language not in {'SFX', 'en', 'ja', 'ko', 'zh'} or value['EventId'] != event_id(stem):
                    raise ValueError('Unsupported cooked language or event identity')
                if len(value.get('SoundBanks', [])) != 1:
                    raise ValueError('Frame event requires exactly one bank per language')
                bank = value['SoundBanks'][0]['SoundBankPathName']
                if bank not in {f'Event/{stem}.bnk', f'Event/{language}/{stem}.bnk'}:
                    raise ValueError('Cooked frame bank path differs from its authored identity')
                expected.add('Client/Content/Aki/WwiseAudio_Generated/' + bank)
                for media in value.get('Media', []):
                    identity, path = media['MediaId'], PurePosixPath(media['MediaPathName'])
                    if (type(identity) is not int or identity <= 0 or path.is_absolute()
                            or '..' in path.parts or '\\' in str(path) or ':' in str(path)
                            or path.name != f'{identity}.wem' or path.parts[0] != 'Media'):
                        raise ValueError('Invalid cooked frame media identity')
                    identities.add(identity)
            initial = await prefetch_banks(root, [reference], fmodel, verified_voice_export)
            index = await ensure_archive_index(root, fmodel, root)
            paths = await asyncio.to_thread(indexed_audio_paths, index,
                {PurePosixPath(path).name for path in expected} | {f'{i}.wem' for i in identities})
            base_ids = {int(PurePosixPath(p).stem) for p in paths if p.endswith('.wem')}
            voice = root if expected.issubset(paths) and identities.issubset(base_ids) else _voice_root(voice_root, plan['version'])
            banks = await _banks(root, voice, expected, initial, work, fmodel, tool)
            init = banks.get('Client/Content/Aki/WwiseAudio_Generated/Init.bnk')
            if init is None:
                raise ValueError('Exact Init bank is required for frame audio bus gains')
            media = await prefetch_media(root, voice, identities, cached, fmodel, tool, verified_voice_export)
            originals.update([*event_originals, cooked_path, init[0]])
            for row in maps:
                language, value = row['Key']['LanguageName'], row['Value']
                package = 'Client/Content/Aki/WwiseAudio_Generated/' + value['SoundBanks'][0]['SoundBankPathName']
                if package not in banks:
                    result['diagnostics'].append({'code': 'frame_audio_bank_unavailable',
                        'language': language, 'source': event.get('source'), 'package_path': package})
                    continue
                required = {m['MediaId'] for m in value.get('Media', [])}
                if not required.issubset(media):
                    result['diagnostics'].append({'code': 'frame_audio_media_unavailable',
                        'language': language, 'source': event.get('source'), 'media_ids': sorted(required - media.keys())})
                    continue
                bank, download = banks[package]
                destination = assets / 'frame-audio' / stem / language
                destination.mkdir(parents=True, exist_ok=True)
                source_media = [{'package_path': package_path(source), 'download_id': plan['id'],
                                 'sha256': sha256(source)} for source in event_originals]
                for source, identity, origin in [(bank, download, package),
                        (init[0], init[1], 'Client/Content/Aki/WwiseAudio_Generated/Init.bnk'),
                        *[media[i] for i in sorted(required)]]:
                    target = destination / source.name
                    if source.resolve() != target.resolve():
                        shutil.copyfile(source, target)
                    originals.add(source)
                    source_media.append({'package_path': origin, 'download_id': identity, 'sha256': sha256(source)})
                result['soundtracks'].append({'bank': (destination / bank.name).relative_to(assets).as_posix(),
                    'audio_directory': destination.relative_to(assets).as_posix(), 'language': None if language == 'SFX' else language,
                    'rover': None, 'start_seconds': start, 'end_seconds': event.get('end_seconds'),
                    'reference': reference, 'source': event.get('source'), 'mode': event['mode'],
                    'stop_policy': event.get('stop_policy', 'not_inferred'), 'source_media': source_media,
                    'event_json': str(cooked_path), 'event_json_sha256': sha256(cooked_path),
                    'tools': {'fmodel': sha256(fmodel), 'converter': sha256(converter)}})
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
            if isinstance(error, (OSError, RuntimeError)) or transient_job_error(error):
                raise
            result['diagnostics'].append({'code': 'frame_audio_unavailable',
                'source': event.get('source'), 'message': str(error)[:500]})
    result['sources'] = sorted(originals)
    return result


async def prepare_frame_audio(root, assets, movie_name, bindings, fmodel, decoder,
                              ffmpeg, work, voice_root=None):
    """Return role WAVs/Opus previews, original sources and partial diagnostics.

    WAVs preserve the event's natural duration. ``start_seconds`` and any verified
    ``end_seconds`` belong to the movie clock; the assembly owner applies them.
    Neither a music stem's length nor VideoSound cleanup establishes a frame
    event's stop policy.
    """
    assets, work = Path(assets), Path(work)
    result = await event_soundtracks(root, assets, movie_name, bindings, fmodel, work, voice_root)
    result['stems'] = []
    if not result['soundtracks']:
        return result
    if len(bindings.get('events', [])) > 1:
        result['diagnostics'].append({'code': 'frame_audio_requires_shared_timeline',
            'message': 'Multiple frame events cannot be flattened into independent event renditions'})
        return result
    renderer = tool_path('WUWA_WWISER_PATH')
    tools = {'wwiser': sha256(renderer), 'decoder': sha256(Path(decoder)),
             'ffmpeg': sha256(Path(ffmpeg)), 'frame_audio': sha256(Path(__file__))}
    shared = {}
    for track in result['soundtracks']:
        try:
            bank, audio = assets / track['bank'], assets / track['audio_directory']
            identity = hashlib.sha256(json.dumps([track, tools], sort_keys=True).encode()).hexdigest()[:20]
            output = assets / 'frame-audio' / 'rendered' / identity
            output.mkdir(parents=True, exist_ok=True)
            xml = output / 'selection.xml'
            await run_tool([sys.executable, str(renderer), '-d', 'xml', '-dn',
                            str(xml.with_suffix('')), '--', str(bank)], 120)
            variants = ('male', 'female') if rover_parameters(xml, 'male', language=track['language']) != rover_parameters(
                xml, 'female', language=track['language']) else (None,)
            for rover in variants:
                rendered = await render_role_stems(bank, audio, output / (rover or 'shared'),
                    decoder, language=track['language'], rover=rover)
                for stem in rendered:
                    role = stem['role']
                    language = track['language'] if role == 'voice' else None
                    # Only collapse identical shared PCM, never identify roles or
                    # alternatives through naming, localization or duration.
                    key, digest = (role, language, rover), sha256(stem['path'])
                    if key in shared:
                        if shared[key] != digest:
                            raise ValueError('Nonvoice rendition differs across language banks; explicit variant is required')
                        continue
                    if role == 'voice' and language is None:
                        raise ValueError('Native voice role has no authored playback language')
                    preview = stem['path'].with_suffix('.ogg')
                    await run_tool([str(ffmpeg), '-v', 'error', '-y', '-i', str(stem['path']),
                                    '-map_metadata', '-1', '-c:a', 'libopus', '-b:a', '192k', str(preview)], 300)
                    shared[key] = digest
                    result['stems'].append({'path': stem['path'].relative_to(assets).as_posix(),
                        'playable': preview.relative_to(assets).as_posix(), 'role': role,
                        'language': language, 'rover': rover, 'duration_seconds': stem['duration_seconds'],
                        'start_seconds': track['start_seconds'], 'end_seconds': track['end_seconds'],
                        'stop_policy': track['stop_policy'], 'source': track['source'],
                        'source_media': [p for p in track['source_media'] if not p['package_path'].endswith('.wem')
                                         or int(PurePosixPath(p['package_path']).stem) in stem['media_ids']],
                        'sources': [path.relative_to(assets).as_posix() for path in [bank, audio / 'Init.bnk',
                                    *[audio / f'{i}.wem' for i in stem['media_ids']], stem['recipe'],
                                    stem['path'].parent / 'role-proof.json', stem['path'].parent / 'event-timeline.json']],
                        'event_json_sha256': track['event_json_sha256'], 'media_ids': stem['media_ids'],
                        'tools': {**track['tools'], **tools},
                        'evidence': 'HIRC source parent/output-bus route to native Volume GameParameter; original TXTP layer commands retained',
                        'role_evidence': stem['role_evidence']})
            result['sources'].extend(p for p in output.rglob('*') if p.is_file() and p.suffix in {'.xml', '.txtp', '.json'})
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
            if isinstance(error, (OSError, RuntimeError)) or transient_job_error(error):
                raise
            result['diagnostics'].append({'code': 'frame_audio_render_unavailable',
                'source': track['source'], 'language': track['language'], 'message': str(error)[:500]})
    result['sources'] = sorted(set(result['sources']))
    if result['sources']:
        folder = assets / 'frame-audio' / 'evidence'
        folder.mkdir(parents=True, exist_ok=True)
        identity = hashlib.sha256(json.dumps([movie_name, bindings, tools], sort_keys=True).encode()).hexdigest()[:20]
        archive = folder / (identity + '.zip')
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as zipped:
            seen = {}
            for source in result['sources']:
                try:
                    name = 'source/' + package_path(source)
                except ValueError:
                    name = 'evidence/' + sha256(source)[:16] + '/' + source.name
                digest = sha256(source)
                if name in seen and seen[name] != digest:
                    raise ValueError('Conflicting frame audio original source bytes')
                if name not in seen:
                    zipped.write(source, name)
                    seen[name] = digest
            zipped.writestr('bindings.json', json.dumps(bindings, ensure_ascii=False, sort_keys=True))
        result['evidence_archive'] = archive.relative_to(assets).as_posix()
        for stem in result['stems']:
            stem['sources'].append(result['evidence_archive'])
    return result

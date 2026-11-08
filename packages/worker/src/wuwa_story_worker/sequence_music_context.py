"""Validate Wwise controls before reconstructing a LevelPlay music phase.

The caller must establish the LevelPlay route and exact event references from
the pinned client. This module checks the banks, not quest execution order.
"""

import json
import re
import shutil
import sys
import wave
from pathlib import Path

from wuwa_story_worker.wwise_banks import event_id, event_timeline, validate_event_timeline


def authored_flow_music_context(target, client):
    """Select only a unique top-level event enclosing one illustrated ShowTalk.

    This is a candidate, not evidence that the event renders music; the Wwise
    bank and its audio bus must still be verified before publishing a track.
    """
    from wuwa_story.ingestion.dungeon_context import walk
    from wuwa_story.ingestion.spine_presentation import presentation_hash

    presentation = target.get('presentation') or {}
    digest = presentation.get('sha256')
    if (not isinstance(digest, str) or digest != target.get('presentation_sha256')
            or presentation_hash({k: v for k, v in presentation.items() if k != 'sha256'}) != digest):
        raise ValueError('Flow soundtrack presentation proof mismatch')
    source = presentation.get('source') or {}
    actions = source.get('Actions')
    if not isinstance(actions, list) or not isinstance(source.get('StateKey'), str):
        return None
    events = [(i, action) for i, action in enumerate(actions)
              if isinstance(action, dict) and action.get('Name') == 'PostAkEvent']
    talks = [(i, action) for i, action in enumerate(actions)
             if isinstance(action, dict) and action.get('Name') == 'ShowTalk'
             and any(cue.get('kind') == 'background' and cue.get('background', {}).get('kind') in {'spine', 'image'}
                     and cue.get('source_path', '').startswith(f'$.Actions[{i}].Params.TalkItems[')
                     for cue in presentation.get('cues', []))]
    if len(events) != 2 or len(talks) != 1:
        return None
    (start, start_action), (end, end_action) = events
    talk, talk_action = talks[0]
    if not start < talk < end:
        return None
    if any(value.get('Name') in {'PostAkEvent', 'SetAudioState'}
           for _, value in walk(talk_action)):
        return None

    def event(action):
        config = (action.get('Params') or {}).get('EventConfig') or {}
        return config.get('AkEvent') if config.get('Type') == 'Global' else None

    play, stop = event(start_action), event(end_action)
    if not isinstance(play, str) or not isinstance(stop, str):
        return None
    items = (talk_action.get('Params') or {}).get('TalkItems') or []
    keys = [item.get('TidTalk') for item in items if isinstance(item, dict)]
    if not keys or any(not isinstance(key, str) or not key for key in keys) or len(set(keys)) != len(keys):
        return None
    return {'schema_version': 1, 'kind': 'flow_authored_music', 'play_event': play, 'stop_event': stop,
            'source_proof': {'asset_version': client['asset_version'], 'download_id': client['download_id'],
                             'release_id': target['release_id'], 'flow_owner': target['owner'],
                             'presentation_sha256': digest, 'flow_state': source['StateKey'],
                             'play_path': f'$.Actions[{start}]', 'talk_path': f'$.Actions[{talk}]',
                             'stop_path': f'$.Actions[{end}]', 'talk_text_keys': keys,
                             'timing_basis': 'authored_action_extent', 'global_clock': None}}


def levelplay_music_binding(state_xml: Path, play_xml: Path, stop_xml: Path,
                            state_event: str, play_event: str, stop_event: str) -> dict:
    """Require one startup state, a consuming Play, and its matching Stop."""
    state = event_timeline(state_xml, event_id(state_event))
    play = event_timeline(play_xml, event_id(play_event))
    stop = event_timeline(stop_xml, event_id(stop_event))
    if (len(state['actions']) != 1 or state['actions'][0]['kind'] != 'SetState'
            or state['actions'][0]['time_ms'] != 0):
        raise ValueError('LevelPlay state event is not a single immediate SetState')
    plays = [action for action in play['actions'] if action['kind'] == 'Play']
    if (len(plays) != 1 or any(action['kind'] not in {'Play', 'Stop'} for action in play['actions'])
            or any(action['time_ms'] != 0 or index > play['actions'].index(plays[0])
                   for index, action in enumerate(play['actions']) if action['kind'] == 'Stop')):
        raise ValueError('LevelPlay music event is not a single supported Play')
    stops = stop['actions']
    if (len(stops) != 1 or stops[0]['kind'] != 'Stop' or stops[0]['time_ms'] != 0
            or stops[0]['target_id'] != plays[0]['target_id']):
        raise ValueError('LevelPlay stop event does not stop the selected music')
    binding = state['actions'][0]
    validate_event_timeline({'actions': [binding, *play['actions']]}, play_xml)
    return {'state_group': binding['state_group_id'], 'state': binding['state_id'],
            'play_target': plays[0]['target_id'],
            'state_event': state_event, 'play_event': play_event, 'stop_event': stop_event,
            'timing_basis': 'fixed_start_reconstruction',
            'bank_timelines': {'state': state, 'play': play, 'stop': stop}}


def whole_file_loop(recipe: str) -> bool:
    """Only advertise browser looping when the whole rendered recipe loops."""
    lines = [line.strip() for line in recipe.split('# AUTOGENERATED', 1)[0].splitlines()
             if line.strip() and not line.lstrip().startswith('#')]
    loops = any('#@loop' in line or '##loop' in line for line in lines)
    if not loops:
        return False
    if (len(lines) != 2 or not re.match(r'^wem/\d+\.wem(?:\s|$)', lines[0])
            or not re.match(r'^group\s*=\s*-S1(?:\s|$)', lines[1])
            or not re.search(r'#@loop\b|##loop\b', lines[1])):
        raise ValueError(
            'Native music loop cannot be represented by whole-file playback')
    return True


async def verify_authored_music(root, fmodel, converter, audio, work, evidence, stop_reference):
    """Reject mixed/unknown buses and require the authored closing event to stop audio."""
    from wuwa_story_worker.character_voice_assets import prefetch_banks, prefetch_events
    from wuwa_story_worker.cutscene_frame_audio import audio_bus_roles
    from wuwa_story_worker.cutscene_import import run_tool
    from wuwa_story_worker.entity_media import client_voice_exports, tool, verified_voice_export
    from wuwa_story_worker.media_provenance import sha256
    from wuwa_story_worker.tooling import tool_path

    match = re.fullmatch(r'/Game/Aki/WwiseAudio/Events/([A-Za-z0-9_]+)(?:\.([A-Za-z0-9_]+))?', stop_reference or '')
    if not match or (match[2] and match[1] != match[2]):
        raise ValueError('Invalid authored flow stop event')
    cached = client_voice_exports(root, sha256(fmodel))
    events = await prefetch_events(root, [stop_reference], cached, fmodel, converter, tool, verified_voice_export)
    banks = await prefetch_banks(root, [stop_reference], fmodel, verified_voice_export)
    path = 'Client/Content/Aki/WwiseAudio_Generated/Event/' + match[1] + '.bnk'
    if stop_reference not in events or path not in banks:
        raise ValueError('Authored flow stop event is unavailable')
    stop_xml = work / 'flow-stop.xml'
    await run_tool([sys.executable, str(tool_path('WUWA_WWISER_PATH')), '-d', 'xml', '-dn',
                    str(stop_xml.with_suffix('')), '--', str(banks[path])], 120)
    timeline = event_timeline(stop_xml, event_id(match[1]))
    if not timeline['actions'] or any(action['kind'] != 'Stop' or action['time_ms'] != 0 for action in timeline['actions']):
        raise ValueError('Authored flow closing event is not an immediate Wwise Stop')
    bank_xml = next((Path(item) for item in evidence if Path(item).name == 'bank.xml'), None)
    recipes = [Path(item) for item in evidence if Path(item).suffix == '.txtp']
    media = {int(Path(item).stem) for item in evidence if Path(item).suffix == '.wem'}
    if bank_xml is None or len(recipes) != 1 or not media:
        raise ValueError('Authored flow music lacks unique bank or media evidence')
    init_xml = work / 'flow-init.xml'
    await run_tool([sys.executable, str(tool_path('WUWA_WWISER_PATH')), '-d', 'xml', '-dn',
                    str(init_xml.with_suffix('')), '--', str(audio / 'Init.bnk')], 120)
    roles = audio_bus_roles(bank_xml, init_xml, media)
    if set(roles) != media or any(value['role'] != 'music' for value in roles.values()):
        raise ValueError('Authored flow event is not exclusively verified music')
    loop = whole_file_loop(recipes[0].read_text(encoding='utf-8-sig'))
    cooked, event_sources = events[stop_reference]
    return loop, [banks[path], *event_sources], {
        'flow-stop.xml': stop_xml.read_bytes(), 'flow-init.xml': init_xml.read_bytes(),
        'flow-stop-event.json': Path(cooked).read_bytes(),
        'flow-stop-timeline.json': json.dumps(timeline, sort_keys=True).encode(),
        'flow-music-bus.json': json.dumps(roles, sort_keys=True).encode(),
    }


async def prepare_levelplay_music(root, fmodel, converter, work, context):
    """Render only the selected native music branch, retaining source evidence."""
    from wuwa_story.ingestion.cutscenes import MusicContext

    from wuwa_story_worker.character_voice_assets import (
        prefetch_banks,
        prefetch_events,
        prefetch_media,
    )
    from wuwa_story_worker.cutscene_import import run_tool
    from wuwa_story_worker.cutscene_music_context import render_music_context
    from wuwa_story_worker.entity_media import client_voice_exports, tool, verified_voice_export
    from wuwa_story_worker.media_provenance import package_path, sha256
    from wuwa_story_worker.tooling import tool_path
    from wuwa_story_worker.wwise_banks import bank_media_ids

    root, work = Path(root), Path(work)
    if (not isinstance(context, dict) or context.get('schema_version') != 1
            or context.get('kind') != 'levelplay_fixed_start'
            or not isinstance(context.get('source_proof'), dict) or not context['source_proof']):
        raise ValueError('Missing verified LevelPlay music context')
    references = [context.get(key) for key in (
        'state_event', 'play_event', 'stop_event')]
    pattern = re.compile(
        r'/Game/Aki/WwiseAudio/Events/([A-Za-z0-9_]+)(?:\.([A-Za-z0-9_]+))?')
    names = []
    for reference in references:
        match = pattern.fullmatch(reference) if isinstance(
            reference, str) else None
        if not match or (match[2] and match[1] != match[2]):
            raise ValueError('Invalid LevelPlay Wwise event reference')
        names.append(match[1])
    if len(set(names)) != 3:
        raise ValueError('LevelPlay music events must be distinct')
    plan = json.loads((root / 'plan.json').read_text(encoding='utf-8'))
    proof = context['source_proof']
    if proof.get('asset_version') != plan['version'] or proof.get('download_id') != plan['id']:
        raise ValueError(
            'LevelPlay music context differs from the pinned client')
    tools = {name: sha256(path) for name, path in {
        'WUWA_FMODEL_PATH': fmodel, 'WUWA_TEXTURE_CONVERTER_PATH': converter,
        'WUWA_VGMSTREAM_PATH': tool_path('WUWA_VGMSTREAM_PATH'),
        'WUWA_WWISER_PATH': tool_path('WUWA_WWISER_PATH')}.items()}
    cached = client_voice_exports(root, tools['WUWA_FMODEL_PATH'])
    events = await prefetch_events(root, references, cached, fmodel, converter, tool, verified_voice_export)
    if any(ref not in events for ref in references):
        raise ValueError('Missing exact LevelPlay Wwise event package')
    bank_files = await prefetch_banks(root, references, fmodel, verified_voice_export)
    prefix = 'Client/Content/Aki/WwiseAudio_Generated/'
    expected = [prefix + 'Event/' + name + '.bnk' for name in names]
    expected.append(prefix + 'Init.bnk')
    if any(path not in bank_files for path in expected):
        raise ValueError('Missing exact LevelPlay music bank')
    work.mkdir(parents=True, exist_ok=True)
    assets = work / 'assets'
    audio = assets / 'audio'
    audio.mkdir(parents=True, exist_ok=True)
    originals = set()
    evidence = {}
    for reference, name, path in zip(references, names, expected[:3], strict=True):
        cooked, sources = events[reference]
        decoded = json.loads(Path(cooked).read_text(encoding='utf-8-sig'))
        maps = [row for export in decoded if export.get('Type') == 'AkAudioEvent'
                for row in export.get('EventCookedData', {}).get('EventLanguageMap', [])]
        if (len(maps) != 1 or maps[0].get('Key', {}).get('LanguageName') != 'SFX'
                or maps[0].get('Value', {}).get('EventId') != event_id(name)):
            raise ValueError(
                'LevelPlay event is not a unique shared cooked event')
        cooked_banks = maps[0]['Value'].get('SoundBanks', [])
        if (len(cooked_banks) != 1 or cooked_banks[0].get('SoundBankPathName') != 'Event/' + name + '.bnk'):
            raise ValueError(
                'LevelPlay event does not reference its exact bank')
        originals.update(sources)
        evidence[f'events/{name}.json'] = Path(cooked).read_bytes()
        shutil.copyfile(bank_files[path], audio / (name + '.bnk'))
        originals.add(bank_files[path])
        await run_tool([sys.executable, str(tool_path('WUWA_WWISER_PATH')), '-d', 'xml', '-dn',
                        str(work / name), '--', str(audio / (name + '.bnk'))], 120)
        evidence[f'events/{name}.xml'] = (work / (name + '.xml')).read_bytes()
    shutil.copyfile(bank_files[expected[-1]], audio / 'Init.bnk')
    originals.add(bank_files[expected[-1]])
    binding = levelplay_music_binding(
        *(work / (name + '.xml') for name in names), *names)
    evidence['levelplay-binding.json'] = json.dumps(
        binding, sort_keys=True).encode()
    selection = work / 'selection'
    selection.mkdir(exist_ok=True)
    state_param = f"({binding['state_group']}={binding['state']})"
    result = await run_tool([sys.executable, str(tool_path('WUWA_WWISER_PATH')), '-d', 'none', '-g',
                             '-gwd', '-gra', '-go', str(
                                 selection), '-gw', 'wem', '-gf', str(event_id(names[1])),
                             '-gp', state_param, '--', str(audio / (names[1] + '.bnk')), str(audio / 'Init.bnk')], 120)
    report = (getattr(result, 'stderr', b'') or b'').decode(errors='replace')
    if 'missing' in report.casefold() or 'error' in report.casefold():
        raise ValueError(
            'LevelPlay music selection has unresolved Wwise dependencies')
    recipes = list(selection.glob('*.txtp'))
    if len(recipes) != 1:
        raise ValueError(
            'LevelPlay music requires one unambiguous selected rendition')
    recipe = recipes[0].read_text(
        encoding='utf-8-sig').split('# AUTOGENERATED', 1)[0]
    identities = {int(value) for value in re.findall(
        r'(?m)^\s*wem/(\d+)\.wem\b', recipe)}
    if not identities or len(identities) > 64 or not identities.issubset(
            set(bank_media_ids((audio / (names[1] + '.bnk')).read_bytes()))):
        raise ValueError('LevelPlay music selected invalid or excessive media')
    selected = await prefetch_media(root, root, identities, cached, fmodel, tool, verified_voice_export)
    if set(selected) != identities:
        raise ValueError('LevelPlay music selected media is missing')
    for identity, (path, _, _) in selected.items():
        shutil.copyfile(path, audio / f'{identity}.wem')
        originals.add(path)
    music = MusicContext(bank='audio/' + names[1] + '.bnk', audio_directory='audio',
                         state_group=binding['state_group'], state=binding['state'],
                         source_event=names[0], evidence='Verified LevelPlay fixed-start reconstruction')
    stem, = await render_music_context(music, assets, work / 'render', tool_path('WUWA_VGMSTREAM_PATH'))
    wav = stem['path']
    music_only = [Path(source) for source in stem['sources']
                  if Path(source).name == 'music-only.txtp']
    loop = whole_file_loop(music_only[0].read_text(
        encoding='utf-8-sig') if music_only else recipe)
    with wave.open(str(wav)) as stream:
        duration = stream.getnframes() / stream.getframerate()
    if duration <= 0:
        raise ValueError('Empty LevelPlay music rendition')
    ogg = work / 'music.ogg'
    await run_tool(['ffmpeg', '-v', 'error', '-y', '-i', str(wav), '-map_metadata', '-1',
                    '-c:a', 'libopus', '-b:a', '192k', str(ogg)])
    for source in stem['sources']:
        path = Path(source)
        if path.suffix in {'.xml', '.txtp', '.json', '.wwiser-original'}:
            evidence['render/' + path.name] = path.read_bytes()
    proof = [{'package_path': package_path(path), 'download_id': plan['id'], 'sha256': sha256(path)}
             for path in sorted(originals)]
    track = {'language': None, 'rover': None, 'role': 'music', 'status': 'rendered', 'duration_seconds': duration,
             'source_media': proof, 'tools': tools, 'timing_basis': 'fixed_start_reconstruction',
             'loop': loop}
    return track, ogg.read_bytes(), evidence, sorted(originals), binding

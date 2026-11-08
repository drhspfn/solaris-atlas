"""Source-verified fixed-start reconstructions of persistent boss music phases."""
import json
import shutil
from pathlib import Path

from wuwa_story_worker.asset_export import export_assets
from wuwa_story_worker.cutscene_import import run_tool
from wuwa_story_worker.tooling import tool_path
from wuwa_story_worker.wwise_banks import bank_media_ids, event_id, event_timeline, render_bank

# Explicit client event pair, verified through the state's native switch consumer.
MUSIC_EVENTS = {'M0357': ('set_state_boss_3_3_aliefuyi_m0357_stage3_loop',
                          'play_music_boss_3_3_aliefuyi'),
                'C0030': ('map_main_tinyuan', 'map_main_tinyuan'),
                'M0390': ('play_all_sound_m0390', 'play_story_music_3_7_boss_tianyansuxin')}


async def prepare_music_context(root, assets, movie, recipe):
    if movie not in MUSIC_EVENTS:
        return
    import sys
    state_event, music_event = MUSIC_EVENTS[movie]
    prefix = 'Client/Content/Aki/WwiseAudio_Generated/'
    from wuwa_story_worker.cutscene_import import confined
    state_bank = next((confined(assets, track['bank']) for video in recipe['videos']
                       for track in video.get('soundtrack', [])
                       if track.get('bank') and Path(track['bank']).stem == state_event), None)
    expected = [prefix + 'Init.bnk', prefix + 'Event/' + music_event + '.bnk']
    if state_bank is None:
        expected.append(prefix + 'Event/' + state_event + '.bnk')
    receipt = await export_assets(root, tool_path('WUWA_FMODEL_PATH'), '|'.join(expected))
    files = receipt.parent / 'files'
    folder = assets / 'fixed-music' / movie
    folder.mkdir(parents=True, exist_ok=True)
    for path in expected:
        source = files / path
        if not source.is_file():
            raise ValueError('Music context source is missing: ' + path)
        shutil.copyfile(source, folder / source.name)
    if state_bank is not None:
        shutil.copyfile(state_bank, folder / (state_event + '.bnk'))
    state_xml = folder / 'state.xml'
    await run_tool([sys.executable, str(tool_path('WUWA_WWISER_PATH')), '-d', 'xml', '-dn',
                    str(folder / 'state'), '--', str(folder / (state_event + '.bnk'))], 120)
    timeline = event_timeline(state_xml, event_id(state_event))
    states = [action for action in timeline['actions']
              if action['kind'] == 'SetState']
    if len(states) != 1 or states[0]['time_ms'] != 0:
        raise ValueError(
            'Music context is no longer a deterministic startup state')
    state = states[0]
    ids = bank_media_ids((folder / (music_event + '.bnk')).read_bytes())
    media_paths = [prefix + 'Media/' +
                   str(identity) + '.wem' for identity in ids]
    receipt = await export_assets(root, tool_path('WUWA_FMODEL_PATH'), '|'.join(media_paths))
    for path in media_paths:
        source = receipt.parent / 'files' / path
        if not source.is_file():
            raise ValueError('Music context media is missing: ' + path)
        shutil.copyfile(source, folder / source.name)
    context = {'bank': (folder / (music_event + '.bnk')).relative_to(assets).as_posix(),
               'audio_directory': folder.relative_to(assets).as_posix(),
               'state_group': state['state_group_id'], 'state': state['state_id'],
               'source_event': state_event,
               'evidence': 'Client state event selects native persistent music switch. Reconstructed from phase entry at movie time zero; previous combat crossfade is not reproduced. Fallback only: a verified movie-clock song takes precedence.'}
    (folder / 'reconstruction.json').write_text(json.dumps(context, indent=2))
    for video in recipe['videos']:
        video['music_context'] = context
    recipe['flow']['evidence'] += '; Fixed-start reconstructed music: ' + \
        context['evidence']


def select_music_stems(authored, reconstructed):
    """An explicitly posted song supersedes approximate background reconstruction."""
    primary = next((index for index, stem in enumerate(authored)
                    if stem['role'] == 'music' and stem['start_seconds'] == 0), None)
    if primary is None:
        return [*authored, *reconstructed]
    selected = [dict(stem) for stem in authored]
    # Keep state-consumer evidence used to validate the native voice/effect bank,
    # but do not play a second approximation of the movie's own song.
    evidence = [path for stem in reconstructed for path in stem['sources']
                if Path(path).suffix != '.wav']
    selected[primary]['sources'] = list(dict.fromkeys([*selected[primary]['sources'], *evidence]))
    return selected


def music_recipe(body, roles):
    """Retain intact music subtrees; only independent parallel effects can be removed."""
    import re
    stack = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        leaf = re.match(r'^wem/(\d+)\.wem(?:\s|$)', line)
        if leaf:
            identity = int(leaf[1])
            stack.append(([line], {roles[identity]['role']}, None))
            continue
        group = re.match(r'^group\s*=\s*-(L|S)(\d+)(?:\s|$)', line)
        if not group or not 0 < int(group[2]) <= len(stack):
            raise ValueError('Unsupported native music recipe structure')
        count = int(group[2])
        children = stack[-count:]
        del stack[-count:]
        stack.append(([item for child in children for item in child[0]] + [line],
                      set().union(*(child[1] for child in children)),
                      children if group[1] == 'L' else None))
    if len(stack) != 1:
        raise ValueError('Native music recipe has no single root')
    lines, root_roles, parallel = stack[0]
    if root_roles == {'music'}:
        return '\n'.join(lines)
    if (parallel is None or any(len(child[1]) != 1 for child in parallel)
            or not re.fullmatch(r'group\s*=\s*-L\d+\s+#@layer-v', lines[-1])):
        raise ValueError(
            'Music cannot be separated without altering native timing')
    selected = [child for child in parallel if child[1] == {'music'}]
    if not selected:
        raise ValueError('Native music context contains no music')
    return '\n'.join([line for child in selected for line in child[0]] +
                     ([f'group = -L{len(selected)} #@layer-v'] if len(selected) > 1 else []))


async def render_music_context(context, assets, work, decoder, *, override_startup_states=False):
    from wuwa_story_worker.cutscene_frame_audio import audio_bus_roles
    from wuwa_story_worker.cutscene_import import confined
    bank, audio = confined(assets, context.bank), confined(
        assets, context.audio_directory)
    output, sources = await render_bank(bank, audio, work, decoder, None,
                                        run_tool, startup_states={context.state_group: context.state},
                                        **({'override_startup_states': True} if override_startup_states else {}))
    xml = next(path for path in sources if path.name == 'bank.xml')
    import sys
    init_xml = xml.parent / 'init.xml'
    await run_tool([sys.executable, str(tool_path('WUWA_WWISER_PATH')), '-d', 'xml', '-dn',
                    str(init_xml.with_suffix('')), '--', str(audio / 'Init.bnk')], 120)
    identities = {int(path.stem) for path in sources if path.suffix == '.wem'}
    roles = audio_bus_roles(xml, init_xml, identities)
    if not roles:
        raise ValueError(
            'Reconstructed context contains no verified music sources')
    if any(item['role'] != 'music' for item in roles.values()):
        recipe = next(path for path in sources if path.suffix == '.txtp')
        body = recipe.read_text(
            encoding='utf-8-sig').split('# AUTOGENERATED', 1)[0]
        selected = recipe.parent / 'music-only.txtp'
        selected.write_text(music_recipe(body, roles), encoding='utf-8')
        output = recipe.parent / 'music-only.wav'
        await run_tool([str(decoder.resolve()), '-i', '-o', str(output), str(selected)], 300)
        proof = recipe.parent / 'music-role-proof.json'
        proof.write_text(json.dumps(roles, indent=2))
        sources.extend([selected, proof])
    return [{'role': 'music', 'language': None, 'rover': None, 'path': output,
             'start_seconds': 0, 'end_seconds': None, 'fixed_start': True,
             'sources': [*sources, init_xml, *audio.glob('*.json'), audio / (context.source_event + '.bnk')],
             'source': {'reconstruction': context.model_dump()}, 'reference': context.source_event}]

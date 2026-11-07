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
                          'play_music_boss_3_3_aliefuyi')}


async def prepare_music_context(root, assets, movie, recipe):
    if movie not in MUSIC_EVENTS:
        return
    import sys
    state_event, music_event = MUSIC_EVENTS[movie]
    prefix = 'Client/Content/Aki/WwiseAudio_Generated/'
    expected = [prefix + 'Init.bnk', *(prefix + 'Event/' + event + '.bnk'
                                      for event in (state_event, music_event))]
    receipt = await export_assets(root, tool_path('WUWA_FMODEL_PATH'), '|'.join(expected))
    files = receipt.parent / 'files'
    folder = assets / 'fixed-music' / movie
    folder.mkdir(parents=True, exist_ok=True)
    for path in expected:
        source = files / path
        if not source.is_file():
            raise ValueError('Music context source is missing: ' + path)
        shutil.copyfile(source, folder / source.name)
    state_xml = folder / 'state.xml'
    await run_tool([sys.executable, str(tool_path('WUWA_WWISER_PATH')), '-d', 'xml', '-dn',
                    str(folder / 'state'), '--', str(folder / (state_event + '.bnk'))], 120)
    timeline = event_timeline(state_xml, event_id(state_event))
    if len(timeline['actions']) != 1 or timeline['actions'][0]['kind'] != 'SetState' or timeline['actions'][0]['time_ms'] != 0:
        raise ValueError('Music context is no longer a deterministic startup state')
    state = timeline['actions'][0]
    ids = bank_media_ids((folder / (music_event + '.bnk')).read_bytes())
    media_paths = [prefix + 'Media/' + str(identity) + '.wem' for identity in ids]
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
               'evidence': 'Client state event selects native boss music switch. Reconstructed from phase entry at movie time zero; previous combat crossfade is not reproduced.'}
    (folder / 'reconstruction.json').write_text(json.dumps(context, indent=2))
    for video in recipe['videos']:
        video['music_context'] = context
    recipe['flow']['evidence'] += '; Fixed-start reconstructed music: ' + context['evidence']


async def render_music_context(context, assets, work, decoder):
    from wuwa_story_worker.cutscene_import import confined
    from wuwa_story_worker.cutscene_frame_audio import audio_bus_roles
    bank, audio = confined(assets, context.bank), confined(assets, context.audio_directory)
    output, sources = await render_bank(bank, audio, work, decoder, None,
        run_tool, startup_states={context.state_group: context.state})
    xml = next(path for path in sources if path.name == 'bank.xml')
    import sys
    init_xml = xml.parent / 'init.xml'
    await run_tool([sys.executable, str(tool_path('WUWA_WWISER_PATH')), '-d', 'xml', '-dn',
                    str(init_xml.with_suffix('')), '--', str(audio / 'Init.bnk')], 120)
    identities = {int(path.stem) for path in sources if path.suffix == '.wem'}
    roles = audio_bus_roles(xml, init_xml, identities)
    if not roles or any(item['role'] != 'music' for item in roles.values()):
        raise ValueError('Reconstructed context must route exclusively through the native music bus')
    return [{'role': 'music', 'language': None, 'rover': None, 'path': output,
             'start_seconds': 0, 'end_seconds': None, 'fixed_start': True,
             'sources': [*sources, init_xml, *audio.glob('*.json'), audio / (context.source_event + '.bnk')],
             'source': {'reconstruction': context.model_dump()}, 'reference': context.source_event}]

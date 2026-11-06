import hashlib
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from wuwa_story_worker import wwise_banks

FIXTURE = Path(__file__).parent / 'fixtures/cutscene-event-controls.zip'
ROUTES = [
    f'{locale}/{event}/bank.xml'
    for locale in ('en', 'ja')
    for event in ('play_all_sound_c0026', 'play_all_sound_c0032', 'play_all_sound_m0318',
                  'play_all_sound_m2_12_06', 'play_all_sound_m3_6_26')
] + ['main/play_all_sound_m3_1_11_1/bank.xml',
     'main/play_story_music_3_0_b_m3_1_11_c/bank.xml']


def source_xml(tmp_path, route):
    with zipfile.ZipFile(FIXTURE) as archive:
        data = archive.read(route)
        assert hashlib.sha256(data).hexdigest() == json.loads(archive.read('source.json'))['hashes'][route]
    xml = tmp_path / 'bank.xml'
    xml.write_bytes(data)
    return xml


@pytest.mark.parametrize('route', ROUTES)
def test_all_retained_event_routes_preserve_controls_without_inventing_phase(tmp_path, route):
    xml = source_xml(tmp_path, route)
    timeline = wwise_banks.event_timeline(xml, wwise_banks.event_id(route.split('/')[1]))
    actions = timeline['actions']
    assert timeline['xml_sha256'] == hashlib.sha256(xml.read_bytes()).hexdigest()
    if 'm2_12_06' in route:
        assert [(a['state_group_id'], a['state_id'], a['time_ms'])
                for a in actions if a['kind'] == 'SetState'] == [
                    (3533944365, 748895195, 0), (3533944365, 2673105805, 23000)]
        assert any(a['kind'] == 'Play' and a['target_id'] == 228457520
                   and a['time_ms'] == 0 for a in actions)
        stop = next(a for a in actions if a['kind'] == 'Stop')
        assert stop['target_id'] == 248398658
        assert not any(a['kind'] == 'Play' and a['target_id'] == 248398658 for a in actions)
        assert stop['properties'][59] == 3000
        assert sum(a['kind'] == 'Play' for a in actions) == 3  # Effects stay once.
        with pytest.raises(ValueError, match='temporal SetState.*23000ms'):
            wwise_banks.validate_event_timeline(timeline)
        assert wwise_banks.rover_parameters(xml, None, language=route.split('/')[0]) == []
    elif 'music' in route:
        assert len(actions) == 1
        assert actions[0]['state_group_id'] == 4135626798
        assert actions[0]['state_id'] == 84696444 and actions[0]['time_ms'] == 0
        with pytest.raises(ValueError, match='control-only.*elapsed playback time'):
            wwise_banks.validate_event_timeline(timeline)
    else:
        wwise_banks.validate_event_timeline(timeline)
        assert any(a['kind'] == 'Play' for a in actions)


@pytest.mark.parametrize('mutation, message', [
    ('cycle', 'PlayEvent cycle'), ('missing_event', 'Missing Wwise event'),
    ('missing_action', 'Missing or unsupported Wwise action'),
    ('random', 'Randomized Wwise action'), ('duplicate', 'Duplicate Wwise object'),
])
def test_event_closure_rejects_unknown_timing_and_references(tmp_path, mutation, message):
    import xml.etree.ElementTree as ET

    xml = source_xml(tmp_path, 'en/play_all_sound_m2_12_06/bank.xml')
    tree = ET.parse(xml)
    action = next(n for n in tree.iter('object')
                  if n.find("field[@name='ulID'][@value='555468902']") is not None)
    if mutation in ('cycle', 'missing_event'):
        action.find(".//field[@name='idExt']").set('value', '351254616' if mutation == 'cycle' else '42')
    elif mutation == 'missing_action':
        next(tree.iterfind(".//field[@name='ulActionID']")).set('value', '42')
    elif mutation == 'random':
        bundle = next(n for n in action.iter('object') if 'RANGED_MODIFIERS' in n.get('name', ''))
        bundle.find("field[@name='cProps']").set('value', '1')
    else:
        action.find("field[@name='ulID']").set('value', '351254616')
    tree.write(xml)
    with pytest.raises(ValueError, match=message):
        wwise_banks.event_timeline(xml, 351254616)


def test_nested_delays_accumulate_without_deduplicating_real_invocations(tmp_path):
    import xml.etree.ElementTree as ET

    xml = source_xml(tmp_path, 'en/play_all_sound_m2_12_06/bank.xml')
    tree = ET.parse(xml)
    action = next(n for n in tree.iter('object')
                  if n.find("field[@name='ulID'][@value='555468902']") is not None)
    action.find(".//field[@name='idExt']").set('value', '3294638973')
    nested = next(n for n in tree.iter('object')
                  if n.find("field[@name='ulID'][@value='319767475']") is not None)
    bundle = nested.find(".//list[@name='pProps']")
    bundle.set('count', '1')
    prop = ET.SubElement(bundle, 'object', name='AkPropBundle')
    ET.SubElement(prop, 'field', name='pID', value='58')
    ET.SubElement(prop, 'field', name='pValue', value='700')
    tree.write(xml)
    actions = wwise_banks.event_timeline(xml, 351254616)['actions']
    assert [(a['time_ms'], a['event_chain']) for a in actions if a['kind'] == 'SetState'] == [
        (700, [351254616, 3294638973]), (23700, [351254616, 3294638973])]


@pytest.mark.asyncio
@pytest.mark.parametrize('route', ['en/play_all_sound_m2_12_06/bank.xml',
                                  'main/play_story_music_3_0_b_m3_1_11_c/bank.xml'])
async def test_renderer_saves_controls_and_rejects_static_music_before_generation(tmp_path, monkeypatch, route):
    xml = source_xml(tmp_path, route)
    bank = tmp_path / f'{route.split("/")[1]}.bnk'
    bank.write_bytes(b'bank')
    renderer = tmp_path / 'wwiser.pyz'
    renderer.write_bytes(b'pinned')
    monkeypatch.setattr(wwise_banks, 'tool_path', lambda _: renderer)
    commands = []

    async def run(args, timeout):
        commands.append(args)
        assert '-dn' in args
        Path(args[args.index('-dn') + 1] + '.xml').write_bytes(xml.read_bytes())
        return SimpleNamespace(stderr=b'')

    with pytest.raises(ValueError, match='Wwise (temporal SetState|control-only)'):
        await wwise_banks.render_bank(bank, tmp_path, tmp_path / 'work', tmp_path / 'decoder', None, run)
    assert len(commands) == 1
    saved = list((tmp_path / 'work').rglob('event-timeline.json'))
    assert len(saved) == 1 and json.loads(saved[0].read_text())['actions']


@pytest.mark.parametrize('locale', ['en', 'ja'])
def test_authored_initial_music_event_binds_state_before_consuming_play(tmp_path, locale):
    xml = source_xml(tmp_path, f'{locale}/play_all_sound_m2_12_06/bank.xml')
    # This is the exact nested startup event, not a rendition of the full movie.
    timeline = wwise_banks.event_timeline(xml, 3294638973)
    assert [a['kind'] for a in timeline['actions']] == ['Stop', 'SetState', 'Play']
    assert wwise_banks.validate_event_timeline(timeline, xml) == ['(3533944365=748895195)']


@pytest.mark.parametrize('mutation, message', [
    ('after_play', 'follows consuming Play'), ('late', 'temporal SetState'),
    ('unmapped', 'initial music State mapping'), ('no_consumer', 'no consuming Play'),
])
def test_initial_state_requires_authored_mapping_and_startup_order(tmp_path, mutation, message):
    xml = source_xml(tmp_path, 'en/play_all_sound_m2_12_06/bank.xml')
    timeline = wwise_banks.event_timeline(xml, 3294638973)
    state = timeline['actions'][1]
    if mutation == 'after_play':
        timeline['actions'][1:] = reversed(timeline['actions'][1:])
    elif mutation == 'late':
        state['time_ms'] = 1
    elif mutation == 'unmapped':
        state['state_id'] = 42
    else:
        state['state_group_id'] = 42
    with pytest.raises(ValueError, match=message):
        wwise_banks.validate_event_timeline(timeline, xml)


@pytest.mark.asyncio
async def test_renderer_initial_state_is_passed_to_generator_and_init_changes_workspace(tmp_path, monkeypatch):
    import xml.etree.ElementTree as ET

    xml = source_xml(tmp_path, 'en/play_all_sound_m2_12_06/bank.xml')
    tree = ET.parse(xml)
    # Retain the authored initial event and make it the requested root for this harness.
    root = next(n for n in tree.iter('object')
                if n.find("field[@name='ulID'][@value='351254616']") is not None)
    refs = root.find(".//list[@name='actions']")
    refs.remove(refs[-1])
    refs.set('count', '3')
    root.find(".//field[@name='ulActionListSize']").set('value', '3')
    tree.write(xml)
    bank = tmp_path / 'play_all_sound_m2_12_06.bnk'
    bank.write_bytes(b'bank')
    init = tmp_path / 'Init.bnk'
    init.write_bytes(b'init-one')
    wem = tmp_path / '123.wem'
    wem.write_bytes(b'media')
    renderer = tmp_path / 'wwiser.pyz'
    renderer.write_bytes(b'pinned')
    monkeypatch.setattr(wwise_banks, 'tool_path', lambda _: renderer)
    from wuwa_story_worker import cutscene_import
    monkeypatch.setattr(cutscene_import, 'soundtrack_wem', lambda *_: wem)

    async def run(args, timeout):
        if '-dn' in args:
            Path(args[args.index('-dn') + 1] + '.xml').write_bytes(xml.read_bytes())
        elif '-go' in args:
            assert args[args.index('-gp') + 1] == '(3533944365=748895195)'
            (Path(args[args.index('-go') + 1]) / 'selected.txtp').write_text('wem/123.wem #i')
        else:
            Path(args[args.index('-o') + 1]).write_bytes(b'RIFF' + bytes(48))
        return SimpleNamespace(stderr=b'')

    async def render():
        return await wwise_banks.render_bank(bank, tmp_path, tmp_path / 'work',
                                             tmp_path / 'decoder', None, run)

    first, evidence = await render()
    repeated, _ = await render()  # Copied Init files must not become ambiguous inputs.
    assert first == repeated
    assert any(p.name == 'event-timeline.json' for p in evidence)
    init.write_bytes(b'init-two')
    changed, _ = await render()
    assert first != changed

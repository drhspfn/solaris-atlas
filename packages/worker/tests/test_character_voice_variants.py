import hashlib
import json
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from wuwa_story_worker import character_voice_variants as variants
from wuwa_story_worker.wwise_banks import bank_media_ids, event_id

FIXTURE = Path(__file__).parent / 'fixtures/character-voice-hirc-local.zip'


def sources():
    with zipfile.ZipFile(FIXTURE) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def parsed(data, name):
    return variants.alternatives(ET.fromstring(data['xml/' + name + '.xml']),
                                 ET.fromstring(data['xml/Init.xml']), name,
                                 bank_media_ids(data['bank/' + name + '.bnk']))


def test_real_seven_banks_prove_all_seventeen_pairs_without_filename_gender_guesses():
    data = sources()
    evidence = json.loads(data['source.json'])
    assert 'production bank identity not established' in evidence['scope']
    for name, digest in evidence['sha256'].items():
        assert hashlib.sha256(data[name]).hexdigest() == digest
    all_variants = []
    for path in sorted(name for name in data if name.startswith('bank/') and name != 'bank/Init.bnk'):
        name = Path(path).stem
        values = parsed(data, name)
        all_variants.extend(values)
        # Reverse every source-object and branch list. Stable IDs cannot depend
        # on serialization order, XML display names, or the random playlist index.
        xml = ET.fromstring(data['xml/' + name + '.xml'])
        for node in xml.iter('list'):
            node[:] = list(reversed(list(node)))
        reordered = variants.alternatives(xml, ET.fromstring(data['xml/Init.xml']), name,
                                          bank_media_ids(data[path]))
        assert [v['source_name'] for v in values] == [v['source_name'] for v in reordered]
    assert len(all_variants) == 34
    assert len({(v['event_id'], v['language']) for v in all_variants}) == 17
    gender = [v for v in all_variants if any(s['group_id'] == event_id('player_rover_gender')
              for b in v['selector_branches'] for s in b['states'])]
    random = [v for v in all_variants if v['random_children']]
    assert len(gender) == 10 and len(random) == 24
    assert all(v['media_id'] > 0 and v['sound_id'] > 0 for v in all_variants)
    assert len({v['source_name'] for v in all_variants}) == 34
    shou = parsed(data, 'play_favor_word_shouanren_sys_story_exp')
    female = next(v for v in shou if any(s['value_id'] == event_id('female')
                  for b in v['selector_branches'] for s in b['states']))
    assert any(s['value_id'] == event_id('none') and s['group_id'] == variants.GENDER
               for b in female['selector_branches'] for s in b['states'])


@pytest.mark.parametrize('mutation,reason', [
    ('eMode', 'random/sequence'), ('ulSwitchID', 'Unknown voice selector'),
    ('bIsContinuous', 'random/sequence'), ('ulPluginID', 'codec'),
])
def test_unsupported_hierarchy_is_not_published_as_valid_alternatives(mutation, reason):
    data = sources()
    name = 'play_favor_word_fuluoluo_burst_burst01or02_random_01'
    xml = ET.fromstring(data['xml/' + name + '.xml'])
    field = xml.find(f".//field[@name='{mutation}']")
    field.set('value', '999')
    with pytest.raises(ValueError, match=reason):
        variants.alternatives(xml, ET.fromstring(data['xml/Init.xml']), name,
                              bank_media_ids(data['bank/' + name + '.bnk']))


def test_exact_media_inventory_and_init_project_are_required():
    data = sources()
    name = 'play_favor_word_luosela_sys_birthcele'
    xml, init = ET.fromstring(data['xml/' + name + '.xml']), ET.fromstring(data['xml/Init.xml'])
    with pytest.raises(ValueError, match='inventory disagree'):
        variants.alternatives(xml, init, name, {1, 2})
    init.find(".//field[@name='dwProjectID']").set('value', '123')
    with pytest.raises(ValueError, match='Incompatible'):
        variants.alternatives(xml, init, name, bank_media_ids(data['bank/' + name + '.bnk']))


def test_actual_wwiser_renditions_are_independent_sources_not_concatenated():
    report = json.loads(sources()['renditions.json'])
    assert sum(len(b['renditions']) for b in report['banks']) == 45
    for bank in report['banks']:
        assert not bank['warnings']
        identities = {variants.rendition_media(row['body']) for row in bank['renditions']}
        assert len(identities) == len(bank['renditions'])
    for body in ('wem/1.wem #i\nwem/2.wem #i', 'wem/1.wem #v -3dB',
                 'wem/1.wem #i\ncommands = #v 2', 'wem/1.wem #i\ngroup = -S1'):
        with pytest.raises(ValueError, match='unsupported DSP'):
            variants.rendition_media(body)


@pytest.mark.asyncio
async def test_runtime_validates_actual_banks_init_and_every_rendition(tmp_path, monkeypatch):
    data = sources()
    name = 'play_favor_word_fuluoluo_burst_burst01or02_random_02'
    bank, init, renderer = tmp_path / (name + '.bnk'), tmp_path / 'Init.bnk', tmp_path / 'wwiser.pyz'
    bank.write_bytes(data['bank/' + name + '.bnk'])
    init.write_bytes(data['bank/Init.bnk'])
    renderer.write_bytes(b'pinned tool')
    monkeypatch.setattr(variants, 'tool_path', lambda _: renderer)
    report = next(b for b in json.loads(data['renditions.json'])['banks'] if b['event'] == name)
    omit = False

    async def run(args, log, timeout):
        log.write_text('')
        if '-dn' in args:
            path = Path(str(args[args.index('-dn') + 1]) + '.xml')
            path.write_bytes(data['xml/' + ('Init' if args[-1].stem == 'Init' else name) + '.xml'])
        else:
            assert args[-1].read_bytes() == init.read_bytes()
            for i, row in enumerate(report['renditions'][1:] if omit else report['renditions']):
                (args[args.index('-go') + 1] / f'{i}.txtp').write_text(row['body'])

    cooked = [{'EventCookedData': {'EventLanguageMap': [{'Value': {'Media': [
        {'MediaId': identity} for identity in bank_media_ids(bank.read_bytes())]}}]}}]
    result, proof = await variants.verified_alternatives(bank, init, tmp_path, cooked, run)
    assert len(result) == 8 and proof['schema'] == 1
    assert proof['init_sha256'] == hashlib.sha256(init.read_bytes()).hexdigest()
    assert all(v['renditions'] for v in result)
    omit = True
    with pytest.raises(ValueError, match='omitted authored'):
        await variants.verified_alternatives(bank, init, tmp_path, cooked, run)


@pytest.mark.asyncio
async def test_builder_retains_both_variants_with_pinned_evidence_and_partial_loss(tmp_path, monkeypatch):
    from wuwa_story_worker import entity_media

    data = sources()
    name = 'play_favor_word_shouanren_sys_story_exp'
    values = parsed(data, name)
    source = f'/Game/Aki/WwiseAudio/Events/{name}.{name}'
    root, voice = tmp_path / 'client', tmp_path / 'voice'
    for path, identity in ((root, 'client'), (voice, 'voice')):
        path.mkdir()
        (path / 'plan.json').write_text(json.dumps({'id': identity, 'version': '3.7.1'}))
        (path / 'status.json').write_text(json.dumps({'id': identity, 'state': 'downloaded'}))
    event = tmp_path / 'event.json'
    event.write_text(json.dumps([{'EventCookedData': {'EventLanguageMap': [{'Value': {'Media': [
        {'DebugName': f'en_vo_untrusted_{i}.wav', 'MediaId': v['media_id'],
         'MediaPathName': f"Media/{v['media_id']}.wem"} for i, v in enumerate(values)]}}]}}]))
    decoder = tmp_path / 'decoder'
    decoder.write_bytes(b'decoder')
    monkeypatch.setattr(entity_media, 'tool_path', lambda _: decoder)
    monkeypatch.setattr(entity_media, 'client_voice_exports', lambda *_: {})
    monkeypatch.setattr(entity_media, 'prefetch_events', AsyncMock(return_value={source: (event, [])}))
    bank, init = tmp_path / (name + '.bnk'), tmp_path / 'Init.bnk'
    bank.write_bytes(b'bank')
    init.write_bytes(b'init')
    monkeypatch.setattr(entity_media, 'prefetch_banks', AsyncMock(return_value={
        'Client/Content/Aki/WwiseAudio_Generated/Event/' + bank.name: bank,
        'Client/Content/Aki/WwiseAudio_Generated/Init.bnk': init}))
    monkeypatch.setattr(variants, 'verified_alternatives', AsyncMock(return_value=(values, {
        'schema': 1, 'bank_sha256': 'b'*64, 'init_sha256': 'c'*64,
        'wwiser_sha256': 'd'*64, 'event_id': event_id(name)})))
    media = {}
    for value in values:
        path = tmp_path / f"{value['media_id']}.wem"
        path.write_bytes(b'media')
        media[value['media_id']] = (path, 'voice', 'Media/' + path.name)
    monkeypatch.setattr(entity_media, 'prefetch_media', AsyncMock(return_value=media))

    async def decode(args, log, timeout):
        import wave
        with wave.open(str(args[3]), 'wb') as wav:
            wav.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
            wav.writeframes(b'\0\0' * 80)
    monkeypatch.setattr(entity_media, 'tool', decode)
    target = {'kind': 'voice', 'path': source, 'language': 'en'}
    proof, diagnostics = {}, []
    files, missing = await entity_media.build_entity_files(root, [target], decoder, decoder,
                            voice_root=voice, provenance=proof, voice_diagnostics=diagnostics)
    assert len(files) == 2 and not missing and not diagnostics
    assert {f[0]['source_name'] for f in files} == {v['source_name'] for v in values}
    assert all(bank in f[4] and init in f[4] for f in files)
    assert all(p['voice_variant']['expected_source_names'] == sorted(v['source_name'] for v in values)
               for p in proof.values())
    del media[values[0]['media_id']]
    files, missing = await entity_media.build_entity_files(root, [target], decoder, decoder,
                            voice_root=voice, provenance=proof, voice_diagnostics=diagnostics)
    assert len(files) == 1 and missing == [source]
    assert diagnostics[-1]['language'] == 'en' and 'Missing exact authored' in diagnostics[-1]['reason']


@pytest.mark.asyncio
async def test_bank_prefetch_exact_paths_share_one_verified_export_not_other_named_banks(tmp_path, monkeypatch):
    from wuwa_story_worker import character_voice_assets as assets
    from wuwa_story_worker.archive_index import index_voice_paths
    from wuwa_story_worker.entity_media import verified_voice_export

    data = sources()
    (tmp_path / 'plan.json').write_text(json.dumps({'id': 'pinned'}))
    fmodel = tmp_path / 'fmodel'
    fmodel.write_bytes(b'pinned extractor')
    names = [Path(path).stem for path in data if path.startswith('bank/') and path != 'bank/Init.bnk']
    expected = {'Client/Content/Aki/WwiseAudio_Generated/Event/' + name + '.bnk' for name in names}
    expected.add('Client/Content/Aki/WwiseAudio_Generated/Init.bnk')
    log = tmp_path / 'archive-index.log'
    log.write_text(''.join('[File] ' + path + '\n' for path in sorted(expected))
                   + '[File] Client/Content/Other/Init.bnk\n[Done] Listed 9 files\n')
    index_voice_paths(log)

    async def export(root, extractor, filters):
        assert set(filters.split('|')) == expected
        work = root / 'bank-export'
        entries = []
        for package in filters.split('|'):
            path = work / 'files' / package
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data['bank/' + Path(package).name])
            entries.append({'path': package, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        receipt = work / 'manifest.json'
        receipt.write_text(json.dumps({'job_id': 'pinned', 'extractor_sha256': hashlib.sha256(fmodel.read_bytes()).hexdigest(),
                                       'files': entries}))
        return receipt
    mock = AsyncMock(side_effect=export)
    monkeypatch.setattr(assets, 'export_assets', mock)
    result = await assets.prefetch_banks(tmp_path, [f'/Game/Aki/WwiseAudio/Events/{name}.{name}' for name in names],
                                        fmodel, verified_voice_export)
    assert set(result) == expected and mock.await_count == 1
    result[next(iter(expected))].write_bytes(b'changed')
    with pytest.raises(ValueError, match='changed'):
        verified_voice_export((result[next(iter(expected))], '0'*64))

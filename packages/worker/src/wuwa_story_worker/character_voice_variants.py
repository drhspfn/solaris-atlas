"""Bounded, source-backed alternatives for simple state/random voice events.

This is deliberately not a general Wwise interpreter. WWiser must independently
produce exactly these single-source renditions, with the pinned Init bank loaded.
Unknown layers, sequences, DSP or dependencies keep the event unresolved.
"""

import hashlib
import json
import re
import shutil
import sys
import xml.etree.ElementTree as ET

from wuwa_story_worker.client_assets import wait_workspace_lock
from wuwa_story_worker.media_provenance import sha256
from wuwa_story_worker.tooling import tool_path
from wuwa_story_worker.wwise_banks import bank_media_ids, event_id, has_authored_event

LANGUAGES = {event_id(language): language for language in ('en', 'ja', 'ko', 'zh')}
GENDER = event_id('player_rover_gender')
NONE = event_id('none')


def field(node, name):
    item = node.find(f".//field[@name='{name}']")
    if item is None:
        raise ValueError(f'Missing HIRC field {name}')
    return int(item.get('value'))


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def header(root):
    node = root.find("./object[@name='BankHeader']")
    require(node is not None, 'Missing Wwise bank header')
    return {key: field(node, key) for key in
            ('dwBankGeneratorVersion', 'dwSoundBankID', 'dwLanguageID', 'dwProjectID')}


def alternatives(xml, init_xml, name, media_ids):
    """IDs and OR selector branches, independent of names/order/default state."""
    bank, init = header(xml), header(init_xml)
    require(bank['dwBankGeneratorVersion'] == init['dwBankGeneratorVersion'] == 172
            and bank['dwProjectID'] == init['dwProjectID']
            and bank['dwSoundBankID'] == event_id(name)
            and init['dwSoundBankID'] == event_id('init'), 'Incompatible event/Init bank')
    objects = xml.findall("./object[@name='HircChunk']/list[@name='listLoadedItem']/object")
    require(0 < len(objects) <= 256, 'Unsupported voice hierarchy size')
    nodes = {field(node, 'ulID'): node for node in objects}
    require(len(nodes) == len(objects), 'Duplicate HIRC identity')
    event = nodes.get(event_id(name))
    require(event is not None and event.get('name') == 'CAkEvent', 'Missing exact authored event')
    actions = event.findall(".//field[@name='ulActionID']")
    require(len(actions) == 1, 'Layered voice actions are unsupported')
    action = nodes.get(int(actions[0].get('value')))
    require(action is not None and action.get('name') == 'CAkActionPlay'
            and field(action, 'ulActionType') == 0x0403, 'Unsupported voice action')
    leaves = []

    def walk(identity, states=(), random=(), ancestors=()):
        require(identity not in ancestors and len(ancestors) < 16 and identity in nodes,
                'Cyclic, missing or deep HIRC child')
        require(len(leaves) < 256, 'Too many voice alternatives')
        node = nodes[identity]
        kind = node.get('name')
        if kind == 'CAkSound':
            require(field(node, 'ulPluginID') == 0x00140001, 'Unsupported voice codec')
            languages = {LANGUAGES[s['value_id']] for s in states if s['value_id'] in LANGUAGES}
            require(len(languages) == 1, 'Voice has no unique authored language state')
            leaves.append({'language': next(iter(languages)), 'sound_id': identity,
                           'media_id': field(node, 'sourceID'), 'states': states, 'random': random})
        elif kind == 'CAkSwitchCntr':
            require(field(node, 'eGroupType') == 1 and field(node, 'bIsContinuousValidation') == 0,
                    'Unsupported switch or continuous state')
            group = field(node, 'ulGroupID')
            for branch in node.findall(".//list[@name='SwitchList']/object"):
                value = field(branch, 'ulSwitchID')
                require((group == GENDER and value in (NONE, event_id('male'), event_id('female')))
                        or (group != GENDER and value in (*LANGUAGES, NONE)), 'Unknown voice selector')
                children = [int(f.get('value')) for f in branch.findall(".//field[@name='NodeID']")]
                require(len(children) <= 1, 'Layered state children are unsupported')
                condition = {'container_id': identity, 'group_id': group, 'value_id': value,
                             'default_value_id': field(node, 'ulDefaultSwitch'),
                             'child_id': children[0] if children else None}
                if children:
                    walk(children[0], (*states, condition), random, (*ancestors, identity))
        elif kind == 'CAkRanSeqCntr':
            require(not random and field(node, 'eMode') == 0 and field(node, 'eRandomMode') == 0
                    and field(node, 'bIsContinuous') == 0 and field(node, 'sLoopCount') == 1
                    and field(node, 'eTransitionMode') == 0 and field(node, '_bIsUsingWeight') == 0,
                    'Unsupported random/sequence voice container')
            playlist = node.findall(".//object[@name='CAkPlayList']/list[@name='pItems']/object")
            children = [field(item, 'ulPlayID') for item in playlist]
            require(len(children) == len(set(children)) == 2
                    and all(field(item, 'weight') == 50000 for item in playlist),
                    'Unsupported random voice playlist')
            for child in children:
                walk(child, states, (*random, {'container_id': identity, 'child_id': child,
                     'mode': 'random', 'weight': 50000, 'avoid_repeat': field(node, 'wAvoidRepeatCount')}),
                     (*ancestors, identity))
        else:
            raise ValueError(f'Unsupported voice object {kind}')

    walk(field(action, 'idExt'))
    require({leaf['media_id'] for leaf in leaves} == set(media_ids),
            'Cooked media and authored HIRC inventory disagree')
    variants = {}
    for leaf in leaves:
        if len({item['media_id'] for item in leaves if item['language'] == leaf['language']}) < 2:
            continue
        identity = {'event_id': event_id(name), 'language': leaf['language'],
                    'sound_id': leaf['sound_id'], 'media_id': leaf['media_id'],
                    'state_children': sorted((s['container_id'], s['group_id'], s['child_id']) for s in leaf['states']),
                    'random_children': sorted((r['container_id'], r['child_id']) for r in leaf['random'])}
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
        variant = variants.setdefault(digest, {'id': digest, 'source_name': f"{leaf['language']}:hirc1:{digest}",
                                             **identity, 'selector_branches': []})
        branch = {'states': leaf['states'], 'random': leaf['random']}
        if branch not in variant['selector_branches']:
            variant['selector_branches'].append(branch)
    # WWiser may merge aliases, but different authored paths to the same WEM
    # require a more expressive renderer contract than this single-source slice.
    require(len({v['media_id'] for v in variants.values()}) == len(variants),
            'Repeated media across distinct authored paths is unsupported')
    return sorted(variants.values(), key=lambda v: v['source_name'])


def rendition_media(body):
    """Only the independently observed, unmodified single-source TXTP recipes."""
    lines = [line.strip() for line in body.split('# AUTOGENERATED', 1)[0].splitlines() if line.strip()]
    sources = []
    for line in lines:
        match = re.fullmatch(r'wem/(\d+)\.wem #i', line)
        if match:
            sources.append(int(match[1]))
        else:
            break
    if len(lines) == len(sources) == 1:
        return sources[0]
    if len(sources) == 2 and len(lines) == 3:
        choice = re.fullmatch(r'group = -R2>([12])', lines[-1])
        if choice:
            return sources[int(choice[1]) - 1]
    raise ValueError('Voice rendition requires unsupported DSP, layering or sequencing')


async def verified_alternatives(bank, init, work, cooked, run_tool):
    """Both banks come from verified pinned export receipts, never local proof data."""
    renderer = tool_path('WUWA_WWISER_PATH')
    data = bank.read_bytes()
    require(has_authored_event(data, bank.stem), 'Missing authored voice event')
    identities = {m['MediaId'] for e in cooked for row in e.get('EventCookedData', {}).get('EventLanguageMap', [])
                  for m in row.get('Value', {}).get('Media', [])}
    require(identities == set(bank_media_ids(data)), 'Cooked/bank media disagree')
    inputs = {'bank_sha256': sha256(bank), 'init_sha256': sha256(init), 'wwiser_sha256': sha256(renderer)}
    digest = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()[:24]
    root = work / 'voice-hirc' / digest
    async with wait_workspace_lock(root):
        root.mkdir(parents=True, exist_ok=True)
        local_bank, local_init = root / bank.name, root / 'Init.bnk'
        shutil.copyfile(bank, local_bank)
        shutil.copyfile(init, local_init)
        for path, name in ((local_bank, 'event'), (local_init, 'init')):
            await run_tool([sys.executable, renderer, '-d', 'xml', '-dn', root / name, '--', path],
                           root / f'{name}.log', 120)
        variants = alternatives(ET.parse(root / 'event.xml').getroot(), ET.parse(root / 'init.xml').getroot(),
                                bank.stem, identities)
        for stale in root.glob('*.txtp'):
            stale.unlink()
        await run_tool([sys.executable, renderer, '-d', 'none', '-g', '-gwd', '-gra', '-go', root,
                        '-gw', 'wem', '-gf', str(event_id(bank.stem)), '--', local_bank, local_init],
                       root / 'renderer.log', 120)
        log = (root / 'renderer.log').read_text(encoding='utf-8', errors='replace').casefold()
        require(not any(word in log for word in ('missing', 'error', 'unsupported', 'warning')),
                'Voice renderer reports unresolved dependencies')
        recipes = {}
        for path in root.glob('*.txtp'):
            body = path.read_text(encoding='utf-8-sig').split('# AUTOGENERATED', 1)[0]
            identity = rendition_media(body)
            recipes.setdefault(identity, []).append(body)
        require(set(recipes) == identities, 'Voice renderer omitted authored alternatives')
        for variant in variants:
            variant['renditions'] = sorted(recipes[variant['media_id']])
        return variants, {'schema': 1, **inputs, 'event_id': event_id(bank.stem),
                          'alternatives': variants}

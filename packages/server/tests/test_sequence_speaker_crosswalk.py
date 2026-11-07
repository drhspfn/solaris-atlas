import copy
import hashlib
import json
from pathlib import Path

import pytest

from wuwa_story.ingestion.sequence_speaker_crosswalk import (
    SEQUENCE_ACTOR_BASIS,
    sequence_speaker_links,
)

DA = '/Game/Sequence/DA'
GENERATED = '/Game/Sequence/Generated'
ACTOR = '/Game/Actors/BP_Xinyuehu'
MESH = '/Game/Models/R2T1XinMd10011'


def fixture():
    roles = [(7, {'Id': 1311, 'RoleType': 1, 'MeshId': 100071, 'Name': 'hsin'})]
    preloads = [(9, {'Id': 100071, 'Meshes': [MESH + '.R2T1XinMd10011']})]
    actions = [{'Name': 'ShowTalk', 'Params': {'SequenceDataAsset': DA + '.DA',
               'TalkItems': [{'WhoId': 400002}, {'WhoId': 400002}]}}]
    assets = {
        DA: [{'Properties': {'GeneratedData': {'ObjectPath': GENERATED}}}],
        GENERATED: [{'Properties': {'BindingBP': [{'ObjectPath': ACTOR + '.BP_Xinyuehu_C'}]}}],
        ACTOR: [{'Type': 'SkeletalMeshComponent', 'Properties': {'SkeletalMesh': {'ObjectPath': MESH}}},
                {'Name': 'Default__BP_Xinyuehu_C', 'Properties': {'TalkID': 450001}}],
    }
    english = {'hsin': {'content': 'Hsin'}, 'Speaker_400002_Name': {'content': 'Hsin'}}
    return roles, preloads, actions, assets, english


def run(data, *, encoded=True):
    roles, preloads, actions, assets, english = data
    return list(sequence_speaker_links(roles, preloads,
                [(3, {'Actions': json.dumps(actions) if encoded else actions})], assets, english))


def test_scene_proof_and_provenance():
    data = fixture()
    result = run(data)
    assert [(x['from'], x['to'], x['crosswalk']['resolution']) for x in result] == [
        ('speaker:450001', 'character:1311', 'default_talker'),
        ('speaker:400002', 'character:1311', 'cast_and_corroborated_name')]
    for edge in result:
        assert edge['source'] == 'BinData/flowState/flowstate.json'
        assert edge['raw_path'] == '$[3].Actions<json>[0].Params.SequenceDataAsset'
        assert edge['type'] == 'references_character' and edge['relation'] == 'exact_join'
        assert edge['basis'].startswith(SEQUENCE_ACTOR_BASIS)
        proof = edge['crosswalk']
        assert (proof['model_preload_id'], proof['model_preload_row'], proof['role_id'], proof['role_row']) == (100071, 9, 1311, 7)
        assert proof['actor_exports'] == data[3][ACTOR]
        assert proof['sequence_asset_exports'] == data[3][DA]
        assert proof['generated_asset_exports'] == data[3][GENERATED]
        for key, path in [('sequence_asset', DA), ('generated_asset', GENERATED), ('actor', ACTOR)]:
            raw = json.dumps(data[3][path], ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            assert proof[key + '_package'] == path
            assert proof[key + '_sha256'] == hashlib.sha256(raw.encode()).hexdigest()
    assert run(data, encoded=False)[0]['raw_path'] == '$[3].Actions[0].Params.SequenceDataAsset'


def test_missing_or_wrong_evidence_cannot_prove_identity():
    for mutation in ('mesh', 'da', 'generated', 'actor', 'name_only', 'suffix'):
        data = fixture()
        roles, preloads, actions, assets, english = data
        if mutation == 'mesh':
            preloads[0][1]['Meshes'] = []
        if mutation == 'da':
            assets.pop(DA)
        if mutation == 'generated':
            assets.pop(GENERATED)
        if mutation == 'actor':
            assets.pop(ACTOR)
        if mutation == 'name_only':
            assets[ACTOR] = []
        if mutation == 'suffix':
            preloads[0][1]['Meshes'] = ['/Game/Other/R2T1XinMd10011.R2T1XinMd10011']
        assert not run(data), mutation


def test_ambiguous_names_models_and_duplicate_ids():
    data = fixture()
    data[0].append((8, {'Id': 1312, 'RoleType': 1, 'MeshId': 8, 'Name': 'hsin'}))
    assert [x['from'] for x in run(data)] == ['speaker:450001']
    for mutation in ('shared_mesh', 'duplicate_role', 'duplicate_preload'):
        data = fixture()
        if mutation == 'shared_mesh':
            data[0].append((8, {'Id': 1312, 'RoleType': 1, 'MeshId': 100071}))
        if mutation == 'duplicate_role':
            data[0].append((8, copy.deepcopy(data[0][0][1])))
        if mutation == 'duplicate_preload':
            data[1].append((10, copy.deepcopy(data[1][0][1])))
        assert not run(data), mutation


def test_malformed_and_case_sensitive_paths():
    data = fixture()
    data[2][0]['Params']['SequenceDataAsset'] = DA.lower()
    assert not run(data)
    data = fixture()
    assert not list(sequence_speaker_links(data[0], data[1], [(0, {'Actions': '{'})], data[3], data[4]))
    data[3][GENERATED][0]['Properties']['BindingBP'] = 'invalid'
    assert not run(data)
    data = fixture()
    data[3][ACTOR][1]['Properties']['TalkID'] = 0
    assert not run(data)



def test_default_alias_conflict_across_all_assets_keeps_corroborated_who():
    data = fixture()
    roles, preloads, _, assets, english = data
    roles.append((8, {'Id': 1312, 'RoleType': 1, 'MeshId': 100072, 'Name': 'other'}))
    preloads.append((10, {'Id': 100072, 'Meshes': ['/Game/Models/Other']}))
    assets['/Game/Actors/Unbound'] = [
        {'Type': 'SkeletalMeshComponent', 'Properties': {'SkeletalMesh': {'ObjectPath': '/Game/Models/Other'}}},
        {'Name': 'Default__Other', 'Properties': {'TalkID': 450001}},
    ]
    result = run(data)
    assert [(x['from'], x['crosswalk']['resolution']) for x in result] == [
        ('speaker:400002', 'cast_and_corroborated_name')]
    # A second blueprint for the same role is not an ambiguity.
    assets['/Game/Actors/Unbound'][0]['Properties']['SkeletalMesh']['ObjectPath'] = MESH
    assert {x['from'] for x in run(data)} == {'speaker:450001', 'speaker:400002'}


def test_default_name_conflict_and_same_name_ambiguity():
    data = fixture()
    data[0].append((8, {'Id': 1312, 'RoleType': 1, 'Name': 'other'}))
    data[4].update({'other': {'content': 'Other'}, 'Speaker_450001_Name': {'content': 'Other'}})
    assert [x['from'] for x in run(data)] == ['speaker:400002']
    data[4]['other']['content'] = 'Hsin'
    data[4]['Speaker_450001_Name']['content'] = 'Hsin'
    assert not run(data)
    data[4]['Speaker_450001_Name']['content'] = 'Unknown'
    assert [x['from'] for x in run(data)] == ['speaker:450001']


@pytest.mark.parametrize('path', ['/Game/Sequence/../DA', '/Game//Sequence/DA', '/Game/Sequence/DA..DA'])
def test_unsafe_paths_rejected_at_each_join(path):
    for stage in ('sequence', 'generated', 'actor', 'mesh'):
        data = fixture()
        if stage == 'sequence':
            data[2][0]['Params']['SequenceDataAsset'] = path
        elif stage == 'generated':
            data[3][DA][0]['Properties']['GeneratedData']['ObjectPath'] = path
        elif stage == 'actor':
            data[3][GENERATED][0]['Properties']['BindingBP'][0]['ObjectPath'] = path
        else:
            data[1][0][1]['Meshes'] = [path]
            data[3][ACTOR][0]['Properties']['SkeletalMesh']['ObjectPath'] = path
        data[3][path] = data[3][DA]
        assert not run(data), stage


def test_full_raw_exports_and_combined_size_limit():
    data = fixture()
    data[3][DA][0]['Properties']['extra'] = 'source data'
    data[3][GENERATED][0]['Properties']['KeyFrames'] = [1, 2, 3]
    proof = run(data)[0]['crosswalk']
    assert proof['sequence_asset_exports'] == data[3][DA]
    assert proof['generated_asset_exports'] == data[3][GENERATED]
    for package in (DA, GENERATED, ACTOR):
        oversized = copy.deepcopy(data)
        oversized[3][package][0]['extra'] = 'x' * (2 * 1024 * 1024)
        assert not run(oversized)
    # The bound applies to the sum, even when no single export exceeds it.
    data[3][DA][0]['extra'] = 'x' * (1024 * 1024)
    data[3][GENERATED][0]['extra'] = 'x' * (1024 * 1024)
    assert not run(data)


def test_real_hsin_exports_when_available():
    root = Path(__file__).resolve().parents[3] / 'scratch'
    actor_root, blueprint_root = root / 'hsin-actor-proof/decoded', root / 'hsin-blueprint-proof/decoded'
    flow_file = Path('C:/Users/drhspfn/.codex/worktrees/story-map-performance/solaris-atlas/scratch/hsin-bounded-flowstate-evidence.json')
    if not actor_root.exists() or not blueprint_root.exists() or not flow_file.exists():
        pytest.skip('local Hsin evidence is not available')
    assets = {}
    for directory in (actor_root, blueprint_root):
        for path in directory.rglob('*.json'):
            package = '/Game/' + path.relative_to(directory / 'files/Client/Content').with_suffix('').as_posix()
            assets[package] = json.loads(path.read_text(encoding='utf-8-sig'))
    evidence = json.loads(flow_file.read_text(encoding='utf-8'))
    flow = evidence['dialogue_records'][0]['raw_flowstate']
    mesh = '/Game/Aki/Character/Role/FemaleXL/Xinyuehu/R2T1XinMd10011/Model/R2T1XinMd10011'
    roles = [(7, {'Id': 1311, 'RoleType': 1, 'MeshId': 100071, 'Name': 'hsin'})]
    preload = [(9, {'Id': 100071, 'Meshes': [mesh + '.R2T1XinMd10011']})]
    english = {'hsin': {'content': 'Hsin'}, 'Speaker_400002_Name': {'content': 'Hsin'}}
    result = list(sequence_speaker_links(roles, preload, [(3, flow)], assets, english))
    assert {x['from'] for x in result if x['to'] == 'character:1311'} == {'speaker:450001', 'speaker:400002'}

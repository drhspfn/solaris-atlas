from types import SimpleNamespace as NS

import pytest

from wuwa_story_worker.model_bundle import compatible_master_pose
from wuwa_story_worker.model_sources import npc_model_sources
from wuwa_story_worker.npc_setup import blueprint_sources, setup_sources


def ref(name, kind='SkeletalMesh'):
    return {'ObjectName': f"{kind}'{name}'", 'ObjectPath': f'/Game/Npc/{name}'}


def test_data_asset_preserves_authored_carrier_parts_and_materials():
    component = {'ModelType': {'Type': 'Npc', 'NpcModel': {'Type': 'Da', 'Da': '/Game/Npc/Setup.Setup'}}}
    assert npc_model_sources(component)['data_asset'] == 'Npc/Setup'
    rows = [{'Type': 'PD_NpcSetupData_C', 'Name': 'Setup', 'Properties': {
        'Skel_Main': ref('Low'), 'Skel_Body': ref('Suit'), 'Skel_Hair': ref('Hair'),
        'Skel_Face': ref('Face'), 'Face_Mat': ref('FaceMaterial', 'MaterialInstanceConstant'),
    }}]
    sources = setup_sources(rows, 'Npc/Setup')
    assert sources['model'] == 'Npc/Low'
    assert sources['extra_models'] == ['Npc/Suit', 'Npc/Face', 'Npc/Hair']
    assert sources['part_material_overrides'] == {'Npc/Face': {'0': 'Npc/FaceMaterial'}}
    assert sources['master_pose'] and sources['hide_primary_geometry']
    rows[0]['Properties']['Skel_Unknown'] = ref('Unknown')
    with pytest.raises(ValueError, match='mesh fields'):
        setup_sources(rows, 'Npc/Setup')


def test_setup_rejects_missing_master_and_ambiguous_identity():
    rows = [{'Type': 'PD_NpcSetupData_C', 'Name': 'Setup', 'Properties': {}}]
    with pytest.raises(ValueError, match='master'):
        setup_sources(rows, 'Npc/Setup')
    with pytest.raises(ValueError, match='ambiguous'):
        setup_sources(rows * 2, 'Npc/Setup')


def test_blueprint_resolves_only_cdo_primary_mesh_and_authored_parent():
    rows = [{'Type': 'BlueprintGeneratedClass', 'SuperStruct': {
        'ObjectPath': '/Game/Npc/Parent'}}, {'Type': 'BP_Test_C', 'Name': 'Default__BP_Test_C',
        'Properties': {'Mesh': {'ObjectName': "SkeletalMeshComponent'Default__BP_Test_C:CharacterMesh0'"}}},
        {'Type': 'SkeletalMeshComponent', 'Name': 'WeaponCase0', 'Properties': {'SkeletalMesh': ref('Weapon')}}]
    assert blueprint_sources(rows, 'Npc/Test') == {'parent': 'Npc/Parent'}
    rows.append({'Type': 'SkeletalMeshComponent', 'Name': 'CharacterMesh0', 'Properties': {
        'SkeletalMesh': ref('Body'), 'AnimClass': ref('ABP', 'AnimBlueprintGeneratedClass')}})
    assert blueprint_sources(rows, 'Npc/Test') == {'model': 'Npc/Body', 'blueprints': ['Npc/ABP']}


def test_model_id_uses_exact_preload_even_when_blueprint_and_mesh_names_differ():
    component = {'ModelType': {'Type': 'ModelId', 'ModelId': 390212}}
    preload = {'Id': 390212, 'Meshes': ['/Game/Npc/NA029'],
               'ActorClassPath': '/Game/Npc/BP_NA028.BP_NA028_C',
               'Animations': ['/Game/Npc/Death'], 'AnimationBlueprints': ['/Game/Npc/ABP_NA028']}
    sources = npc_model_sources(component, [preload])
    assert sources['model'] == 'Npc/NA029'
    assert sources['component_blueprint'] == 'Npc/BP_NA028'
    assert sources['animation_resources'] == ['Npc/Death']
    with pytest.raises(ValueError, match='exact authored'):
        npc_model_sources(component, [{**preload, 'Id': 390213}])


def bone(name, coordinate=1):
    return NS(name=name, parent=None, matrix_local=[[coordinate, 0], [0, 1]])


def test_master_pose_accepts_unused_helpers_but_never_unmapped_deformation():
    master = NS(data=NS(bones=[bone('root')]))
    part = NS(data=NS(bones=[bone('root'), bone('helper')]), children_recursive=[NS(
        type='MESH', vertex_groups=[NS(index=0, name='root'), NS(index=1, name='helper')],
        data=NS(vertices=[NS(groups=[NS(group=0, weight=1)])]))])
    assert compatible_master_pose(master, part)
    part.children_recursive[0].data.vertices[0].groups.append(NS(group=1, weight=.2))
    assert not compatible_master_pose(master, part)
    part.children_recursive[0].data.vertices[0].groups.pop()
    part.data.bones[0].matrix_local[0][0] = 2
    assert not compatible_master_pose(master, part)

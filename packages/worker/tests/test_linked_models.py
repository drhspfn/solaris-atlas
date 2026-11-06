import json

from wuwa_story_worker.linked_models import discover


def export(root, name, rows):
    path = root / "raw/Client/Content" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf8")


def ref(path, kind="AnimSequence"):
    return {"ObjectName": f"{kind}'{path.rsplit('/', 1)[-1]}'", "ObjectPath": "/Game/" + path}


def event(name, index, start=0, duration=2):
    return {"NotifyName": name, "NotifyStateClass": {"ObjectPath": f"local.{index}"},
            "LinkValue": start, "Duration": duration, "EndLink": {"LinkValue": start + duration}}


def test_component_follow_visibility_and_montage(tmp_path):
    bp, mesh, abp, parent, child, skel = ("Aki/BP", "Aki/Umbrella", "Aki/ABP", "Aki/Stand2",
                                          "Aki/Stand03", "Aki/Skeleton")
    export(tmp_path, bp, [
        {"Type": "SCS_Node", "Properties": {"InternalVariableName": "OtherCase1",
          "ComponentTemplate": {"ObjectName": "SkeletalMeshComponent'BP:OtherCase1_GEN_VARIABLE'"},
          "ParentComponentOrVariableName": "CharacterMesh0", "AttachToName": "WeaponProp05"}},
        {"Type": "SkeletalMeshComponent", "Name": "OtherCase1_GEN_VARIABLE", "Properties": {
          "SkeletalMesh": ref(mesh), "AnimClass": ref(abp), "bVisible": False}},
    ])
    export(tmp_path, mesh, [{"Type": "SkeletalMesh", "Properties": {}}])
    export(tmp_path, skel, [{"Type": "SkeletalMeshSocket", "Properties": {
        "SocketName": "WeaponProp05", "BoneName": "Hand", "RelativeLocation": {"X": 3}}}])
    export(tmp_path, abp, [{"Name": "Default__ABP", "Properties": {
        "AnimGraphNode_ExtraFollowAnims": {"DefaultSequenceName": "None", "bUseAllAnims": False},
        "ExtraRibbonAnims": [{"Key": "Stand2", "Value": ref(child)}],
        "SecondaryExtraFollowAnims": [{"Key": "Second", "Value": {"DataTable": [
            {"Key": "Stand2", "Value": ref("Aki/Alt")} ]}}]}}])
    export(tmp_path, "Aki/Montage", [{"Type": "AnimMontage", "Properties": {
        "SlotAnimTracks": [{"SlotName": "DefaultSlot", "AnimTrack": {"AnimSegments": [
            {"AnimReference": ref(parent), "StartPos": 0.3, "AnimStartTime": 0,
             "AnimEndTime": 2, "AnimPlayRate": 1, "LoopingCount": 1}]}}]}}])
    export(tmp_path, parent, [
        {"Type": "AnimSequence", "Properties": {"Notifies": [event("visibility", 1), event("slot", 2)]}},
        {"Type": "TsAnimNotifyStateSubMeshControl_C", "Name": "visibility", "Properties": {
            "MeshName": "OtherCase1", "开始是否可见": True}},
        {"Type": "TsAnimNotifyStateChangeSlot_C", "Name": "slot", "Properties": {
            "ComponentName": "OtherCase1", "SwitchToSlotName": "WeaponProp01"}},
    ])
    export(tmp_path, child, [{"Type": "AnimSequence", "Properties": {}}])
    result, = discover(tmp_path, {"component_blueprint": bp, "parent_skeleton": skel,
                                   "animation_resources": ["Aki/Montage"], "parent_form_id": "root",
                                   "baked_components": ["OtherCase1"]})
    assert result["default_visible"] is False and result["baked"] is True
    assert result["socket_transform"]["bone"] == "Hand"
    assert result["bindings"][0]["parent_clip"] == parent
    assert result["bindings"][0]["child_clip"] == child
    assert result["bindings"][1]["secondary_table"] == "Second"
    assert result["bindings"][1]["node"] == []
    assert "Aki/Alt" in result["required_resources"]
    assert result["cues"][0]["defaults"]["end_visible"] is True
    assert result["cues"][0]["segment_start"] == 0.3
    assert result["cues"][1]["properties"]["SwitchToSlotName"] == "WeaponProp01"


def test_effect_spawn_and_root_hide_with_cycle(tmp_path):
    export(tmp_path, "Aki/BP", [])
    export(tmp_path, "Aki/Idle", [
        {"Type": "AnimSequence", "Properties": {"Notifies": [event("effect", 1), event("hide", 2)]}},
        {"Type": "AnimNotifyStateEffect_C", "Name": "effect", "Properties": {
          "EffectDataAssetRef": {"AssetPathName": "/Game/Aki/Group.Group"},
          "SyncEventTimeToEffectTime": True, "SocketName": "Root"}},
        {"Type": "TsAnimNotifyStateHideMesh_C", "Name": "hide", "Properties": {}},
    ])
    export(tmp_path, "Aki/Group", [{"Type": "EffectModelGroup_C", "Properties": {"EffectData": [
        {"Key": "EffectModelGroup_C'/Game/Aki/Group.Group'", "Value": 0},
        {"Key": "EffectModelSkeletalMesh_C'/Game/Aki/Effect.Effect'", "Value": 0}]}}])
    export(tmp_path, "Aki/Effect", [{"Type": "EffectModelSkeletalMesh_C", "Properties": {
        "SkeletalMeshRef": ref("Aki/Riling"), "AnimationRef": ref("Aki/RilingIdle"), "Looping": False}}])
    result, = discover(tmp_path, {"component_blueprint": "Aki/BP", "animation_resources": ["Aki/Idle"]})
    assert result["kind"] == "effect" and result["default_visible"] is False
    assert result["source"]["component"] == result["id"]
    assert result["bindings"][0]["child_clip"] == "Aki/RilingIdle"
    assert result["bindings"][0]["source"]["looping"] is False
    assert result["socket"] == "Root"
    assert result["source"]["group_entries"] == [{"group": "Aki/Group", "index": 1, "value": 0}]
    assert result["cues"][1]["defaults"]["target"] == "notifying_mesh"
    assert any("cycle" in d for d in result["diagnostics"])
    assert result["required_resources"] == ["Aki/Riling", "Aki/RilingIdle"]


def test_missing_blueprint_and_unattached_node(tmp_path):
    missing, = discover(tmp_path, {"component_blueprint": "Aki/Missing"})
    assert missing["id"] == "$discovery" and missing["required_resources"] == ["Aki/Missing"]
    export(tmp_path, "Aki/BP", [
        {"Type": "SCS_Node", "Properties": {"InternalVariableName": "Rig",
          "ComponentTemplate": {"ObjectName": "SkeletalMeshComponent'BP:Rig_GEN_VARIABLE'"},
          "ParentComponentOrVariableName": "CharacterMesh0"}},
        {"Type": "SkeletalMeshComponent", "Name": "Rig_GEN_VARIABLE", "Properties": {
            "SkeletalMesh": ref("Aki/Rig")}},
    ])
    result, = discover(tmp_path, {"component_blueprint": "Aki/BP"})
    assert result["model"] == "Aki/Rig" and result["socket"] is None


def test_root_hide_is_preserved_without_components_and_json_sources(tmp_path):
    export(tmp_path, "Aki/BP", [])
    export(tmp_path, "Aki/Idle", [
        {"Type": "AnimSequence", "Properties": {"Notifies": [event("hide", 1)]}},
        {"Type": "TsAnimNotifyStateHideMesh_C", "Name": "hide", "Properties": {}},
    ])
    result, = discover(tmp_path, {"component_blueprint": "Aki/BP.json",
                                  "animation_resources": ["raw/Client/Content/Aki/Idle.json"]})
    assert result["id"] == "$discovery"
    assert result["cues"][0]["defaults"]["target"] == "notifying_mesh"
    assert result["cues"][0]["source_clip"] == "Aki/Idle"


def test_inherited_component_override_and_missing_parent(tmp_path):
    child = {"Type": "BlueprintGeneratedClass", "SuperStruct": ref("Aki/Base", "BlueprintGeneratedClass")}
    export(tmp_path, "Aki/Child", [child, {"Type": "InheritableComponentHandler", "Properties": {
        "Records": [{"ComponentKey": {"OwnerClass": ref("Aki/Base", "BlueprintGeneratedClass"),
                                      "SCSVariableName": "Hat"},
                     "ComponentTemplate": {"ObjectName": "StaticMeshComponent'Child:Hat_Override'"}}]}},
        {"Type": "StaticMeshComponent", "Name": "Hat_Override", "Properties": {"bVisible": False}}])
    initial, = discover(tmp_path, {"component_blueprint": "Aki/Child"})
    assert initial["required_resources"] == ["Aki/Base"]
    export(tmp_path, "Aki/Base", [
        {"Type": "SCS_Node", "Properties": {"InternalVariableName": "Hat",
          "ComponentTemplate": {"ObjectName": "StaticMeshComponent'Base:Hat_GEN_VARIABLE'"},
          "ParentComponentOrVariableName": "CharacterMesh0", "AttachToName": "Head"}},
        {"Type": "StaticMeshComponent", "Name": "Hat_GEN_VARIABLE", "Properties": {
            "StaticMesh": ref("Aki/Hat", "StaticMesh"), "bVisible": True}},
        {"Type": "BlueprintGeneratedClass", "SuperStruct": ref("Aki/Child", "BlueprintGeneratedClass")},
    ])
    result, = discover(tmp_path, {"component_blueprint": "Aki/Child"})
    assert result["model"] == "Aki/Hat" and result["default_visible"] is False
    assert result["source"]["owner_blueprint"] == "Aki/Base"
    assert result["source"]["override_blueprints"] == ["Aki/Child"]
    assert result["source"]["model_type"] == "StaticMeshComponent"
    assert any("StaticMesh" in d for d in result["diagnostics"])
    assert any("cycle" in d for d in result["diagnostics"])


def test_socket_can_be_an_authored_bone(tmp_path):
    export(tmp_path, "Aki/BP", [
        {"Type": "SCS_Node", "Properties": {"InternalVariableName": "Umbrella",
          "ComponentTemplate": {"ObjectName": "SkeletalMeshComponent'BP:Umbrella_GEN_VARIABLE'"},
          "AttachToName": "WeaponProp05"}},
        {"Type": "SkeletalMeshComponent", "Name": "Umbrella_GEN_VARIABLE", "Properties": {
          "SkeletalMesh": ref("Aki/Umbrella")}},
    ])
    export(tmp_path, "Aki/Skeleton", [{"Type": "Skeleton", "ReferenceSkeleton": {
        "FinalRefBoneInfo": [{"Name": "WeaponProp05"}]}}])
    result, = discover(tmp_path, {"component_blueprint": "Aki/BP", "parent_skeleton": "Aki/Skeleton"})
    assert result["socket_transform"] == {"bone": "WeaponProp05", "location": None,
                                          "rotation": None, "scale": None, "source": "Aki/Skeleton"}
    assert not result["diagnostics"]


def test_effect_group_skips_explicit_non_model_dependencies(tmp_path):
    export(tmp_path, "Aki/BP", [])
    export(tmp_path, "Aki/Idle", [
        {"Type": "AnimSequence", "Properties": {"Notifies": [event("effect", 1)]}},
        {"Type": "AnimNotifyStateEffect_C", "Name": "effect", "Properties": {
            "EffectDataAssetRef": {"AssetPathName": "/Game/Aki/Group.Group"}}},
    ])
    export(tmp_path, "Aki/Group", [{"Type": "EffectModelGroup_C", "Properties": {"EffectData": [
        {"Key": "EffectModelNiagara_C'/Game/Aki/Niagara.Niagara'", "Value": 0},
        {"Key": "EffectModelSkeletalMesh_C'/Game/Aki/ModelEffect.ModelEffect'", "Value": 0}]}}])
    result, = discover(tmp_path, {"component_blueprint": "Aki/BP", "animation_resources": ["Aki/Idle"]})
    assert result["required_resources"] == ["Aki/ModelEffect"]
    assert any("EffectModelNiagara_C" in d for d in result["diagnostics"])

import json
from pathlib import Path

from wuwa_story_worker.linked_models import discover


def test_case_mismatched_effect_package_keeps_model_and_missing_dependencies(tmp_path, monkeypatch):
    root = tmp_path / "Client/Content"

    def export(package, rows):
        path = root / f"{package}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows), encoding="utf-8")

    export("Aki/BP", [])
    export("Aki/Idle", [
        {"Type": "AnimSequence", "Properties": {"Notifies": [{
            "NotifyStateClass": {"ObjectPath": "local.1"}, "LinkValue": 1, "Duration": 2}]}},
        {"Type": "AnimNotifyStateEffect_C", "Name": "phone", "Properties": {
            "EffectDataAssetRef": {"AssetPathName": "/Game/Aki/Effects/PhoneGroup.PhoneGroup"}}},
    ])
    export("Aki/effects/phonegroup", [{"Type": "EffectModelGroup_C", "Properties": {
        "EffectData": [{"Key": "EffectModelSkeletalMesh_C'/Game/Aki/PhoneEffect.PhoneEffect'"}]}}])
    export("Aki/PhoneEffect", [{"Type": "EffectModelSkeletalMesh_C", "Properties": {
        "SkeletalMeshRef": {"ObjectPath": "/Game/Aki/Phone.Phone"}}}])
    export("Aki/Phone", [{"Type": "SkeletalMesh", "Properties": {
        "Skeleton": {"ObjectPath": "/Game/Aki/MissingSkeleton.MissingSkeleton"}}}])

    # Emulate a case-sensitive filesystem when the test runs on Windows.
    is_file = Path.is_file
    monkeypatch.setattr(Path, "is_file", lambda path: False if "PhoneGroup.json" in str(path)
                        else is_file(path))
    result, = discover(tmp_path, {"component_blueprint": "Aki/BP",
                                  "animation_resources": ["Aki/Idle"]})
    assert result["model"] == "Aki/Phone"
    assert result["source"]["effect_chain"] == ["Aki/Effects/PhoneGroup"]
    assert result["cues"][0]["start"] == 1
    assert result["required_resources"] == ["Aki/MissingSkeleton"]


def test_missing_and_ambiguous_effects_remain_required(tmp_path, monkeypatch):
    root = tmp_path / "Client/Content/Aki"
    root.mkdir(parents=True)
    (root / "BP.json").write_text("[]", encoding="utf-8")
    (root / "Idle.json").write_text(json.dumps([
        {"Type": "AnimSequence", "Properties": {"Notifies": [
            {"NotifyStateClass": {"ObjectPath": "local.1"}}]}},
        {"Type": "AnimNotifyStateEffect_C", "Name": "effect", "Properties": {
            "EffectDataAssetRef": {"AssetPathName": "/Game/Aki/Group.Group"}}},
    ]), encoding="utf-8")
    sources = {"component_blueprint": "Aki/BP", "animation_resources": ["Aki/Idle"]}
    result, = discover(tmp_path, sources)
    assert result["required_resources"] == ["Aki/Group"]

    iterdir = Path.iterdir

    def ambiguous(path):
        entries = list(iterdir(path))
        if path == root:
            entries.extend([path / "group.json", path / "GROUP.json"])
        return iter(entries)

    monkeypatch.setattr(Path, "iterdir", ambiguous)
    result, = discover(tmp_path, sources)
    assert result["required_resources"] == ["Aki/Group"]
    assert "Ambiguous case-insensitive export: Aki/Group" in result["diagnostics"]

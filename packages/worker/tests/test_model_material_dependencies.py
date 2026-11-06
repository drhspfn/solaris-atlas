import json

from wuwa_story_worker.model_material_dependencies import missing_material_resources


def put(root, package, value):
    path = root / "Client/Content" / (package + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def test_follows_exact_shared_parent_and_texture_without_scanning_other_materials(tmp_path):
    cooked, converted = tmp_path / "cooked", tmp_path / "converted"
    put(cooked, "Body", [{"Type": "SkeletalMesh", "SkeletalMaterials": [
        {"Material": {"ObjectPath": "/Game/MI_Body.MI_Body"}}]}])
    put(cooked, "MI_Body", [{"Properties": {"Parent": {
        "ObjectName": "MaterialInstanceConstant'MI_Base'", "ObjectPath": "/Game/MI_Base.MI_Base"}}}])
    put(converted, "MI_Body", {"Textures": {"MainTex": {"ObjectPath": "/Game/Shared/Color"}}})
    put(converted, "Unrelated", {"Textures": {"MainTex": {"ObjectPath": "/Game/NeverFetch"}}})
    assert missing_material_resources(cooked, converted, ["Body"]) == ["MI_Base", "Shared/Color"]
    put(cooked, "MI_Base", [{"Properties": {"Parent": {
        "ObjectName": "MaterialInstanceConstant'MI_Body'", "ObjectPath": "/Game/MI_Body"}}}])
    put(converted, "MI_Base", {})
    texture = converted / "Client/Content/Shared/Color.png"
    texture.parent.mkdir(parents=True)
    texture.touch()
    assert missing_material_resources(cooked, converted, ["Body"]) == []

import json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from wuwa_story_worker import character_models, model_cli, model_diagnostics, model_forms


@pytest.mark.asyncio
async def test_diagnose_scene_bundles_continues_on_component_error_and_skips_animation_encoding(tmp_path, monkeypatch):
    monkeypatch.delenv("WUWA_MODEL_BLENDER_EXPORT", raising=False)
    calls = []

    async def fake_build(root, form, include_animations=True, **options):
        calls.append((form["id"], include_animations,
                     options.get("local_client")))
        if form.get("component") and form["component"]["id"] == "broken-fx":
            raise ValueError("Missing cross-directory skeleton")
        output = root / "bundle"
        output.mkdir(parents=True, exist_ok=True)
        (output / "preview.glb").write_bytes(b"glb-preview")
        (output / "character.glb").write_bytes(b"glb-full")
        (output / "model.blend").write_bytes(b"blend")
        (output / "report.json").write_text(json.dumps({
            "materials": [{"name": "MI_Body", "texture": "Body.png", "fur_preview": False}],
            "missing_textures": [],
        }), encoding="utf-8")
        cooked_mesh = root / "source-json/Client/Content" / \
            (form["model"] + ".json")
        cooked_mesh.parent.mkdir(parents=True, exist_ok=True)
        cooked_mesh.write_text(json.dumps([{
            "Type": "SkeletalMesh",
            "Name": form["model"].rsplit("/", 1)[-1],
            "Properties": {"Skeleton": {"ObjectPath": "/Game/Aki/Character/Test/Skel.Skel"}},
        }]), encoding="utf-8")
        cooked_skel = root / "source-json/Client/Content/Aki/Character/Test/Skel.json"
        cooked_skel.parent.mkdir(parents=True, exist_ok=True)
        cooked_skel.write_text("[]", encoding="utf-8")
        converted_mesh = root / "converted/Client/Content" / \
            (form["model"] + ".uemodel")
        converted_mesh.parent.mkdir(parents=True, exist_ok=True)
        converted_mesh.touch()
        linked = []
        if not form.get("component"):
            linked = [
                {"model": "Aki/Effect/BrokenFx", "component": {
                    "id": "broken-fx", "kind": "effect", "name": "BrokenFx", "label": "BrokenFx",
                    "parent_form_id": form["id"], "parent_component": "CharacterMesh0",
                    "socket": "Root", "default_visible": False, "baked": False,
                    "source": {"component": "BrokenFx"}, "transform": {}, "bindings": [],
                    "cues": [], "diagnostics": [], "required_resources": [],
                }},
                {"model": "Aki/Prop/GoodProp", "component": {
                    "id": "good-prop", "kind": "accessory", "name": "GoodProp", "label": "GoodProp",
                    "parent_form_id": form["id"], "parent_component": "CharacterMesh0",
                    "socket": "Hand", "default_visible": True, "baked": True,
                    "source": {"component": "GoodProp"}, "transform": {}, "bindings": [],
                    "cues": [], "diagnostics": [], "required_resources": [],
                }},
            ]
        return output, {"preview": {"bytes": 11}, "download": {"bytes": 8}}, {
            "model": form["model"], "linked_components": linked}

    monkeypatch.setattr(character_models, "build_bundle", fake_build)
    monkeypatch.setattr(character_models, "prepare_linked_components",
                        lambda parent, recipes: [(r["component"]["id"], r["component"]) for r in recipes])

    form = {"id": "mesh-1", "label": "Default",
            "model": "Aki/Character/Test/Body", "blueprints": []}
    report = await model_diagnostics.diagnose_scene_bundles(tmp_path, [form], check_animations=False)
    assert report["format"] == "solaris-model-preflight-diagnostic-v1"
    assert report["animations_packed"] is False
    assert report["forms_total"] == 1
    assert report["entries_total"] == 3
    assert [e["errors"] for e in report["entries"]] == [
        [],
        [{"stage": "build_bundle", "error_type": "ValueError",
            "message": "Missing cross-directory skeleton"}],
        [],
    ]
    assert report["ok_count"] == 2
    assert report["error_count"] == 1
    assert all(include_anim is False for _, include_anim, _ in calls)
    assert report["entries"][0]["mesh_found"] is True
    assert report["entries"][0]["skeleton_found"] is True
    assert report["entries"][1]["id"] == "broken-fx"
    assert report["entries"][1]["status"] == "error"
    assert "Missing cross-directory skeleton" in report["entries"][1]["errors"][0]["message"]
    assert report["entries"][2]["id"] == "good-prop"
    assert report["entries"][2]["status"] == "ok"


@pytest.mark.asyncio
async def test_model_cli_diagnose_writes_report_without_archive(tmp_path, monkeypatch):
    recipe = tmp_path / "recipe.json"
    recipe.write_text(json.dumps({"format": "solaris-model-recipe-v1", "character_key": "character:1",
                                  "game_version": "3.8.0", "role": {}, "preloads": [], "morphs": []}))
    keys = tmp_path / "keys.txt"
    keys.write_text("keys")
    report_path = tmp_path / "diag.json"
    args = NS(recipe=recipe, game_dir=tmp_path, keys_file=keys, output=report_path,
              asset_version=None, tier="hd", diagnose=True, without_animations=False, workers=2,
              fmodel=None, converter=None, blender=None, ueformat_plugin=None)
    monkeypatch.setattr(model_forms, "resolve_recipe_forms",
                        lambda *_: [{"id": "mesh-1", "model": "Aki/Body"}])
    monkeypatch.setattr(model_diagnostics, "diagnose_scene_bundles", AsyncMock(return_value={
        "format": "solaris-model-preflight-diagnostic-v1",
        "animations_packed": False,
        "forms_total": 1,
        "entries_total": 1,
        "ok_count": 1,
        "error_count": 0,
        "entries": [{"id": "mesh-1", "status": "ok"}],
    }))
    await model_cli.build(args)
    data = json.loads(report_path.read_text(encoding="utf-8"))
    assert data["character_key"] == "character:1"
    assert data["game_version"] == "3.8.0"
    assert data["error_count"] == 0
    assert not list(tmp_path.glob(".solaris-model-diag-*"))


@pytest.mark.asyncio
async def test_component_animation_fetches_missing_cross_directory_mesh_skeleton(tmp_path, monkeypatch):
    extracted = []
    model_pkg = "Aki/Effect/SkeletalMesh/R2T1MoneMd10011/Model/Fx_Ain_Attack06"
    cross_skel = "Aki/Effect/SkeletalMesh/R2T1FuludelisiMd10011/Model/Fx_Ain_Attack04_Skeleton"
    anim_clip = "Aki/Effect/SkeletalMesh/R2T1MoneMd10011/Ani_Effect/Fx_Ain_AirLaser_02_Anim"

    async def fake_extract(root, sources, fmodel, converter, include_animations=True,
                           additional_filters=(), **_options):
        extracted.append((root.name, include_animations,
                         tuple(additional_filters)))
        converted = root / "converted"
        cooked = root / "source-json"
        mesh_json = cooked / "Client/Content" / (model_pkg + ".json")
        mesh_json.parent.mkdir(parents=True, exist_ok=True)
        mesh_json.write_text(json.dumps([{
            "Type": "SkeletalMesh",
            "Name": "Fx_Ain_Attack06",
            "Properties": {"Skeleton": {"ObjectPath": f"/Game/{cross_skel}.Fx_Ain_Attack04_Skeleton"}},
        }]), encoding="utf-8")
        anim_json = cooked / "Client/Content" / (anim_clip + ".json")
        anim_json.parent.mkdir(parents=True, exist_ok=True)
        anim_json.write_text(json.dumps([{
            "Type": "AnimSequence",
            "Name": "Fx_Ain_AirLaser_02_Anim",
            "Properties": {"Skeleton": {"ObjectPath": f"/Game/{cross_skel}.Fx_Ain_Attack04_Skeleton"}},
        }]), encoding="utf-8")
        uemodel = converted / "Client/Content" / (model_pkg + ".uemodel")
        uemodel.parent.mkdir(parents=True, exist_ok=True)
        uemodel.touch()
        if cross_skel in additional_filters:
            skel_json = cooked / "Client/Content" / (cross_skel + ".json")
            skel_json.parent.mkdir(parents=True, exist_ok=True)
            skel_json.write_text("[]", encoding="utf-8")
        return uemodel, converted, cooked, {"files": []}

    monkeypatch.setattr(character_models, "extract_model", fake_extract)

    def fake_tool_path(name):
        p = tmp_path / name
        p.touch()
        return p
    monkeypatch.setattr(character_models, "tool_path", fake_tool_path)
    plugin = tmp_path / "ueformat"
    (plugin / "io_scene_ueformat").mkdir(parents=True)
    monkeypatch.setenv("WUWA_UEFORMAT_PLUGIN", str(plugin))
    monkeypatch.setattr(
        character_models, "missing_material_resources", lambda *_: [], raising=False)
    import wuwa_story_worker.model_material_dependencies as mat_deps
    monkeypatch.setattr(mat_deps, "missing_material_resources", lambda *_: [])

    async def fake_blender(cmd, root, *_args, **_kwargs):
        out = Path(cmd[cmd.index("--output") + 1])
        out.mkdir(parents=True, exist_ok=True)
        (out / "preview.glb").write_bytes(b"p")
        (out / "character.glb").write_bytes(b"c")
        (out / "animation-plan.json").write_text(
            json.dumps({"clips": [], "facial": {}}), encoding="utf-8")

    monkeypatch.setattr(character_models, "run_blender", fake_blender)
    monkeypatch.setattr(character_models, "glb_summary",
                        lambda p: {"bytes": p.stat().st_size})
    monkeypatch.setattr(
        character_models, "use_smaller_preview", lambda *_: None)

    sources = {
        "id": "fx-ain",
        "label": "Fx_Ain",
        "model": model_pkg,
        "blueprints": [],
        "component": {
            "id": "fx-ain",
            "bindings": [{"parent_clip": "Aki/Body/Attack06", "child_clip": anim_clip}],
        },
    }
    await character_models.build_bundle(tmp_path / "work", sources, include_animations=True)
    assert any(name == "component-animation-skeletons" and cross_skel in filters
               for name, _, filters in extracted)

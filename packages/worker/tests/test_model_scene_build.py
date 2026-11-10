from unittest.mock import AsyncMock

import pytest

from wuwa_story_worker import character_models as models


@pytest.fixture(autouse=True)
def client_workspace(tmp_path):
    for name in ("plan.json", "status.json", "keys.txt"):
        (tmp_path / name).write_text(name, encoding="utf-8")
    (tmp_path / "game").mkdir()


def prop():
    return {"model": "Aki/Character/Prop/Umbrella/Model/Umbrella", "blueprints": [],
            "component": {"id": "umbrella", "name": "WeaponCase0", "label": "Umbrella",
                          "source": {"blueprint": "Aki/Character/NPC/BP_Test", "component": "WeaponCase0"},
                          "default_visible": True, "bindings": [], "cues": [],
                          "parent_component": "CharacterMesh0", "socket": "WeaponProp02"}}


@pytest.mark.asyncio
async def test_nested_export_inherits_client_workspace(tmp_path, monkeypatch):
    nested = tmp_path / "referenced-animations"
    nested.mkdir()

    async def export(root, *_):
        assert (root / "plan.json").read_text(encoding="utf-8") == "plan.json"
        assert (root / "status.json").is_file()
        assert (root / "keys.txt").is_file()
        assert (root / "game").resolve() == (tmp_path / "game").resolve()
        raise RuntimeError("export reached")

    monkeypatch.setattr(models, "export_assets", export)
    with pytest.raises(RuntimeError, match="export reached"):
        await models.extract_model(nested, {"model": "Aki/Character/Test", "blueprints": []},
                                   "fmodel", "converter", False)


@pytest.mark.asyncio
async def test_scene_retains_static_prop_without_requiring_body_animation_pack(tmp_path, monkeypatch):
    seen = []

    async def build(root, form, **options):
        seen.append((form, options))
        assert (root / "plan.json").read_text(encoding="utf-8") == "plan.json"
        assert (root / "status.json").is_file()
        assert (root / "keys.txt").is_file()
        assert (root / "game").resolve() == (tmp_path / "game").resolve()
        output = root / "bundle"
        output.mkdir()
        return output, {}, {"model": form["model"],
                            **({"linked_components": [prop()]} if not form.get("component") else {})}

    monkeypatch.setattr(models, "build_bundle", build)
    form = {"id": "default", "label": "Default", "model": "Aki/Character/NPC/Body", "source": {"id": 1}}
    bundles = await models.build_scene_bundles(tmp_path, [form], include_animations=True)
    assert len(bundles) == 2
    parent, child = bundles
    assert parent[3]["scene"]["components"] == [child[0]["id"]]
    assert child[0]["component"]["parent_form_id"] == "default"
    assert seen[0][1]["include_animations"] is True
    assert seen[1][1]["include_animations"] is False
    assert "linked_components" not in parent[3]
    assert all(output.is_dir() for _, output, *_ in bundles)


@pytest.mark.asyncio
async def test_scene_fails_before_returning_partial_bundle_on_child_error(tmp_path, monkeypatch):
    build = AsyncMock(side_effect=[(tmp_path, {}, {"linked_components": [prop()]}),
                                   ValueError("Missing authored child")])
    monkeypatch.setattr(models, "build_bundle", build)
    with pytest.raises(ValueError, match="Missing authored child"):
        await models.build_scene_bundles(tmp_path, [{"id": "default"}])


@pytest.mark.asyncio
async def test_scene_keeps_same_prop_as_distinct_instances_across_forms(tmp_path, monkeypatch):
    async def build(root, form, **options):
        return root, {}, {"linked_components": [] if form.get("component") else [prop()]}

    monkeypatch.setattr(models, "build_bundle", build)
    bundles = await models.build_scene_bundles(tmp_path, [{"id": "mesh-1"}, {"id": "mesh-2"}])
    assert len({form["id"] for form, *_ in bundles}) == 4
    assert bundles[1][0]["component"]["parent_form_id"] == "mesh-1"
    assert bundles[3][0]["component"]["parent_form_id"] == "mesh-2"


@pytest.mark.asyncio
async def test_root_visibility_cues_survive_without_exportable_child(tmp_path, monkeypatch):
    from wuwa_story_worker import linked_models, model_bundle

    cue = {"kind": "TsAnimNotifyStateHideMesh_C", "source_clip": "Aki/Body/Idle",
           "start": 0, "end": 1, "defaults": {"target": "notifying_mesh", "hide": True}}
    monkeypatch.setattr(model_bundle, "animation_sources", lambda *_: {})
    monkeypatch.setattr(linked_models, "discover", lambda *_: [{
        "kind": "discovery", "required_resources": [], "cues": [cue]}])
    scene = {}
    recipes = await models.discover_scene_components(tmp_path,
        {"component_blueprint": "Aki/BP", "model": "Aki/Body"}, tmp_path,
        skeleton="Aki/Skeleton", scene=scene)
    assert recipes == []
    assert scene == {"format": "solaris-model-scene-v1", "components": [], "cues": [cue]}


@pytest.mark.parametrize("per_root,allowed", [(35, True), (65, False)])
@pytest.mark.asyncio
async def test_scene_component_budget_is_total_across_roots(tmp_path, monkeypatch, per_root, allowed):
    async def build(root, form, **options):
        components = []
        if not form.get("component"):
            for i in range(per_root):
                item = prop()
                item["component"].update(id=f"effect-{i}", name=f"effect-{i}", label="Effect")
                item["component"]["source"]["component"] = f"effect-{i}"
                components.append(item)
        return root, {}, {"linked_components": components}

    monkeypatch.setattr(models, "build_bundle", build)
    forms = [{"id": "mesh-1"}, {"id": "mesh-2"}]
    if allowed:
        bundles = await models.build_scene_bundles(tmp_path, forms)
        assert len(bundles) == 72
        assert len({form["id"] for form, *_ in bundles}) == 72
    else:
        with pytest.raises(ValueError, match="128"):
            await models.build_scene_bundles(tmp_path, forms)


@pytest.mark.parametrize("count", [70, 129])
@pytest.mark.asyncio
async def test_discovery_enforces_component_budget(tmp_path, monkeypatch, count):
    from wuwa_story_worker import linked_models, model_bundle

    recipes = [{"id": f"effect-{i}", "kind": "effect", "model": "Aki/Prop/Shared",
                "parent_component": "CharacterMesh0", "socket": None, "socket_transform": None,
                "default_visible": False, "source": {"component": f"effect-{i}"}, "bindings": [],
                "cues": [], "diagnostics": [], "required_resources": [], "anim_blueprint": None}
               for i in range(count)]
    monkeypatch.setattr(model_bundle, "animation_sources", lambda *_: {})
    monkeypatch.setattr(linked_models, "discover", lambda *_: recipes)
    call = models.discover_scene_components(tmp_path,
        {"component_blueprint": "Aki/BP", "model": "Aki/Body"}, tmp_path, skeleton="Aki/Skeleton")
    if count > 128:
        with pytest.raises(ValueError, match="128"):
            await call
    else:
        result = await call
        assert len({item["component"]["id"] for item in result}) == count

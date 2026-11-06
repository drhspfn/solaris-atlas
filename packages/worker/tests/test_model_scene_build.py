from unittest.mock import AsyncMock

import pytest

from wuwa_story_worker import character_models as models


def prop():
    return {"model": "Aki/Character/Prop/Umbrella/Model/Umbrella", "blueprints": [],
            "component": {"id": "umbrella", "name": "WeaponCase0", "label": "Umbrella",
                          "source": {"blueprint": "Aki/Character/NPC/BP_Test", "component": "WeaponCase0"},
                          "default_visible": True, "bindings": [], "cues": [],
                          "parent_component": "CharacterMesh0", "socket": "WeaponProp02"}}


@pytest.mark.asyncio
async def test_scene_retains_static_prop_without_requiring_body_animation_pack(tmp_path, monkeypatch):
    seen = []

    async def build(root, form, **options):
        seen.append((form, options))
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

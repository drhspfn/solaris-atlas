import hashlib
import json
from copy import deepcopy

import pytest

from wuwa_story_worker.model_animation_packs import (
    compatible_rigs,
    install_pack,
    owned_sources,
    ownership_for,
    prepare_packs,
    variant_root,
    write_manifest,
)


def plan(count=400, *, rig="same", mesh="A"):
    clips = [{"name": f"clip-{i}", "action": f"clip-{i}",
              "source": f"Client/Content/Aki/Common/a{i}.json",
              "sha256": hashlib.sha256(str(i).encode()).hexdigest()}
             for i in range(count)]
    return {"skeleton": "aki/shared", "rig_fingerprint": rig, "model_sha256": mesh,
            "clips": clips, "animations": [{**clip, "included_in_glb": True} for clip in clips],
            "facial": {"format": "solaris-facial-v1", "clips": [
                {"name": clip["name"], "body_clip": clip["name"]} for clip in clips]}}


def seed(cache, first, recipe="recipe"):
    rig, reused, identities = prepare_packs(first, cache, recipe)
    assert not reused
    entry = {"id": "a" * 16, "identities": identities}
    cache[rig].append(entry)
    return entry


def test_400_shared_and_20_variant_clips_require_only_20_new_exports():
    cache = {}
    first = plan()
    original = deepcopy(first)
    entry = seed(cache, first)
    second = plan(420, mesh="skin")
    _, reused, new = prepare_packs(second, cache, "recipe")
    assert first == original
    assert len(second["clips"]) == len(new) == 20
    assert reused == [{"entry": entry, "clips": [f"clip-{i}" for i in range(400)]}]
    assert len(second["facial"]["clips"]) == 420


def test_equal_montage_sequences_keep_separate_actions_and_share_across_skins():
    first = plan(2)
    for i, clip in enumerate(first["clips"]):
        clip.update(source="Client/Content/Aki/Common/AM_Cableway.json",
                    file=f"Client/Content/Aki/Common/AM_Cableway_SEQ{i + 1}.ueanim",
                    sha256="a" * 64)
    skin = deepcopy(first)
    skin["model_sha256"] = "skin"
    cache = {}
    entry = seed(cache, first)
    assert len(first["clips"]) == len(entry["identities"]) == 2
    _, reused, unseen = prepare_packs(skin, cache, "recipe")
    assert not unseen and not skin["clips"]
    assert reused == [{"entry": entry, "clips": ["clip-0", "clip-1"]}]
    assert [row["body_clip"] for row in skin["facial"]["clips"]] == ["clip-0", "clip-1"]


def test_repeated_same_export_is_still_rejected():
    first = plan(2)
    first["clips"][1].update(source=first["clips"][0]["source"],
                             sha256=first["clips"][0]["sha256"])
    with pytest.raises(ValueError, match="duplicate animation source"):
        prepare_packs(first, {}, "recipe")


@pytest.mark.parametrize("change", ["rest_pose", "recipe", "contents"])
def test_skeleton_name_or_clip_name_alone_does_not_authorize_reuse(change):
    cache = {}
    seed(cache, plan(2))
    second = plan(2, rig="other" if change == "rest_pose" else "same", mesh="skin")
    if change == "contents":
        for clip in second["clips"]:
            clip["sha256"] = "b" * 64
    _, reused, unseen = prepare_packs(second, cache, "other" if change == "recipe" else "recipe")
    assert not reused and len(unseen) == 2


def test_partial_overlap_filters_base_only_clips_and_reuses_prior_delta():
    cache = {}
    seed(cache, plan(400))
    second = plan(420)
    rig, _, delta = prepare_packs(second, cache, "recipe")
    cache[rig].append({"id": "b" * 16, "identities": delta})
    third = plan(420)
    third["clips"] = third["clips"][100:]
    third["animations"] = third["animations"][100:]
    third["facial"]["clips"] = third["facial"]["clips"][100:]
    _, selected, delta = prepare_packs(third, cache, "recipe")
    assert not third["clips"] and not delta
    assert [len(item["clips"]) for item in selected] == [300, 20]


def test_variant_name_collision_updates_facial_binding():
    cache = {}
    seed(cache, plan(1))
    second = plan(2)
    second["clips"][1]["name"] = second["clips"][1]["action"] = "clip-0"
    # A real planner gives every local action a distinct name, but adding a skin
    # clip can shift suffixes: local clip-0 is the new clip, shared clip is .001.
    second["clips"][0]["name"] = "clip-0.001"
    second["animations"][0]["action"] = "clip-0.001"
    second["animations"][1]["action"] = "clip-0"
    second["facial"]["clips"] = [{"name": "clip-0", "body_clip": "clip-0"}]
    _, _, _ = prepare_packs(second, cache, "recipe")
    name = second["clips"][0]["name"]
    assert name.startswith("clip-0.") and name != "clip-0.001"
    assert second["facial"]["clips"][0]["body_clip"] == name


def test_only_verified_other_variant_paths_are_excluded():
    base = variant_root("Aki/Role/Base/Model/Base")
    skin = variant_root("Aki/Role/Skin/Model/Skin")
    sources = {key: {"source": path} for key, path in {
        "base": "Client/Content/Aki/Role/Base/Idle.json",
        "skin": "Client/Content/Aki/Role/Skin/Idle.json",
        "shared": "Client/Content/Aki/Cinematics/Scene.json",
    }.items()}
    scope = {"root": base, "roots": [base, skin]}
    assert set(owned_sources(sources, scope)) == {"base", "shared"}
    scope["explicit"] = ["/Game/Aki/Role/Skin/Idle.Idle"]
    assert owned_sources(sources, scope) == sources


def test_skin_inherits_its_authored_base_but_not_another_skin():
    base = {"model": "Aki/Role/Base/Model/Base", "source": {
        "table": "roleinfo", "id": 1508, "field": "MeshId"}}
    skin = {"model": "Aki/Role/Skin/Model/Skin", "source": {
        "table": "roleskin", "id": 81011508, "role_id": 1508, "skin_id": 81011508}}
    other = {"model": "Aki/Role/Other/Model/Other", "source": {
        "table": "roleskin", "id": 81021508, "role_id": 1508, "skin_id": 81021508}}
    forms = [base, skin, other]
    sources = {name: {"source": variant_root(form["model"]) + "Idle.json"}
               for name, form in zip(("base", "skin", "other"), forms, strict=True)}
    assert set(owned_sources(sources, ownership_for(base, forms))) == {"base"}
    assert set(owned_sources(sources, ownership_for(skin, forms))) == {"base", "skin"}


def test_pack_files_share_bytes_and_manifest_selects_only_owned_clips(tmp_path):
    source = tmp_path / "source.glb"
    source.write_bytes(b"verified-pack")
    output = tmp_path / "skin"
    output.mkdir()
    descriptor = install_pack(output, {"id": "a" * 16, "path": source}, ["Idle"])
    write_manifest(output, [descriptor])
    assert (output / "animation-packs" / ("a" * 16 + ".glb")).read_bytes() == source.read_bytes()
    assert json.loads((output / "animation-manifest.json").read_text())["packs"] == [descriptor]


def test_float_import_noise_does_not_force_a_skin_animation_rebuild():
    cache = {}
    first = plan(1)
    signature = {"object": [1.0] * 16, "bones": [["Root", None, [1.0] * 16]]}
    first["rig_signature"] = signature
    rig, _, identities = prepare_packs(first, cache, "recipe")
    from wuwa_story_worker.model_animation_packs import fingerprint
    cache[rig].append({"id": "a" * 16, "identities": identities, "rig_signature": signature,
                       "rig_namespace": fingerprint([first["skeleton"], "recipe"])})
    second = plan(1)
    second["rig_fingerprint"] = "different-float-bits"
    second["rig_signature"] = deepcopy(signature)
    second["rig_signature"]["bones"][0][2][0] += 0.000021
    _, reused, new = prepare_packs(second, cache, "recipe")
    assert len(reused) == 1 and not new
    second["rig_signature"]["bones"][0][2][0] += 0.01
    assert not compatible_rigs(signature, second["rig_signature"])


def test_reuse_rejects_delta_rig_outside_tolerance_of_later_form():
    from wuwa_story_worker.model_animation_packs import fingerprint

    cache = {}
    base = plan(1, rig="base")
    base["rig_signature"] = {"object": [1.0] * 16, "bones": [["Root", None, [1.0] * 16]]}
    namespace = fingerprint([base["skeleton"], "recipe"])
    rig, _, identities = prepare_packs(base, cache, "recipe")
    cache[rig].append({"id": "a" * 16, "identities": identities,
                       "rig_signature": deepcopy(base["rig_signature"]), "rig_namespace": namespace})

    skin = plan(2, rig="skin")
    skin["rig_signature"] = deepcopy(base["rig_signature"])
    skin["rig_signature"]["bones"][0][2][0] += 0.00009
    reused_rig, reused, delta = prepare_packs(skin, cache, "recipe")
    assert reused_rig == rig and len(reused) == 1 and len(delta) == 1
    cache[rig].append({"id": "b" * 16, "identities": delta,
                       "rig_signature": deepcopy(skin["rig_signature"]), "rig_namespace": namespace})

    other = plan(2, rig="other")
    other["rig_signature"] = deepcopy(base["rig_signature"])
    other["rig_signature"]["bones"][0][2][0] -= 0.00009
    assert compatible_rigs(base["rig_signature"], other["rig_signature"])
    assert not compatible_rigs(skin["rig_signature"], other["rig_signature"])
    other_rig, reused, unseen = prepare_packs(other, cache, "recipe")
    assert other_rig != rig and not reused and len(unseen) == 2

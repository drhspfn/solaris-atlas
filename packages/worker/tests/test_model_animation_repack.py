import json
import struct
from copy import deepcopy

import pytest

from test_model_animation_merge import part, read_glb, samples, write_glb
from wuwa_story_worker import model_animation_repack as repack
from wuwa_story_worker.model_animations import animation_summary
from wuwa_story_worker.model_sources import glb_summary


def test_semantic_fingerprints_ignore_indices_offsets_names_and_channel_order(tmp_path):
    a, b = tmp_path / "a.glb", tmp_path / "b.glb"
    for path, name, reordered in [(a, "Display A", False), (b, "Display B", True)]:
        data, binary = part(path, name, reordered=reordered, skin=True)
        clip = data["animations"][0]
        clip["extras"] = {"solaris_atlas": {"source": "Client\\Anims/Walk.JSON"}}
        if reordered:
            clip["channels"].reverse()
        write_glb(path, data, binary)
    first, second = repack.animation_pack_index(a), repack.animation_pack_index(b)
    assert first["rig"] == second["rig"]
    assert first["clips"][0]["identity"] == second["clips"][0]["identity"]
    assert first["clips"][0]["source"] == "client/anims/walk"
    data, binary = read_glb(b)
    data["nodes"][0]["translation"] = [1, 0, 0]
    write_glb(b, data, binary)
    assert first["rig"] != repack.animation_pack_index(b)["rig"]


def test_select_compacts_many_clips_and_preserves_samples_and_skin(tmp_path):
    source, dest = tmp_path / "source.glb", tmp_path / "selected.glb"
    data, binary = part(source, skin=True)
    binary = binary[: data["buffers"][0]["byteLength"]]
    binary += b"\0" * (-len(binary) % 4)
    ibm_offset = len(binary)
    binary += struct.pack("<32f", *([1.0] * 32))
    data["bufferViews"].append({"buffer": 0, "byteOffset": ibm_offset, "byteLength": 128})
    data["accessors"].append({"bufferView": 2, "componentType": 5126, "type": "MAT4", "count": 2})
    data["skins"][0]["inverseBindMatrices"] = 2
    base = data["animations"][0]
    data["animations"] = []
    for i in range(40):
        clip = deepcopy(base)
        clip["name"] = f"Clip{i}"
        start = len(binary)
        binary += struct.pack("<6f", *(float(i + j) for j in range(6)))
        data["bufferViews"].append({"buffer": 0, "byteOffset": start, "byteLength": 24})
        data["accessors"].append(
            {
                "bufferView": len(data["bufferViews"]) - 1,
                "componentType": 5126,
                "type": "VEC3",
                "count": 2,
            }
        )
        for sampler in clip["samplers"]:
            sampler["output"] = len(data["accessors"]) - 1
        clip["extras"] = {"solaris_atlas": {"source": f"Anim/{i}.json"}}
        data["animations"].append(clip)
    data["buffers"][0]["byteLength"] = len(binary)
    write_glb(source, data, binary)
    before = source.read_bytes()
    names = ["Clip3", "Clip7"]
    repack.select_animation_pack(source, dest, names)
    result, packed = read_glb(dest)
    assert source.read_bytes() == before
    assert [c["name"] for c in result["animations"]] == names
    assert result["buffers"][0]["byteLength"] == 128 + 8 + 2 * 24
    assert len(packed) < len(binary) / 3
    assert samples(result, packed, result["animations"][0]["samplers"][0]["output"]) == samples(
        data, binary, data["animations"][3]["samplers"][0]["output"]
    )
    assert animation_summary(dest)["animations"] == 2
    assert glb_summary(dest, allow_animations=True)["animations"] == 2
    index = repack.animation_pack_index(source)
    picked = repack.animation_pack_index(dest)
    assert picked["rig"] == index["rig"]
    assert [c["identity"] for c in picked["clips"]] == [
        index["clips"][i]["identity"] for i in (3, 7)
    ]


@pytest.mark.parametrize("names", [[], ["Walk", "Walk"], ["Missing"]])
def test_invalid_selection_is_atomic(tmp_path, names):
    source, dest = tmp_path / "source.glb", tmp_path / "output.glb"
    part(source)
    dest.write_bytes(b"original")
    with pytest.raises(ValueError):
        repack.select_animation_pack(source, dest, names)
    assert dest.read_bytes() == b"original"
    with pytest.raises(ValueError):
        repack.select_animation_pack(source, source, ["Walk"])


def test_invalid_input_and_source_change_are_atomic(tmp_path, monkeypatch):
    source, dest = tmp_path / "source.glb", tmp_path / "output.glb"
    data, binary = part(source)
    data["nodes"][0]["extensions"] = {"bad": {}}
    write_glb(source, data, binary)
    dest.write_bytes(b"original")
    with pytest.raises(ValueError):
        repack.select_animation_pack(source, dest, ["Walk"])
    part(source)
    original = repack._read

    def change(path):
        result = original(path)
        with path.open("ab") as handle:
            handle.write(b"changed")
        return result

    monkeypatch.setattr(repack, "_read", change)
    with pytest.raises(ValueError, match="changed"):
        repack.select_animation_pack(source, dest, ["Walk"])
    assert dest.read_bytes() == b"original"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["output.glb", "source.glb"]


def test_nonfinite_samples_rejected_by_existing_validator(tmp_path):
    path = tmp_path / "bad.glb"
    data, binary = part(path)
    binary = binary[:20] + struct.pack("<f", float("nan")) + binary[24:]
    write_glb(path, data, binary)
    with pytest.raises(ValueError):
        repack.animation_pack_index(path)


def test_fallback_and_sample_changes_affect_identity(tmp_path):
    path = tmp_path / "anim.glb"
    data, binary = part(path, "AuthoredName")
    first = repack.animation_pack_index(path)["clips"][0]
    assert first["source"] == "AuthoredName"
    binary = binary[:20] + struct.pack("<f", 9.0) + binary[24:]
    write_glb(path, data, binary)
    second = repack.animation_pack_index(path)["clips"][0]
    assert first["identity"] != second["identity"]


def _bundle(root, form_id, count, *, other=False, rig=False, component=False):
    folder = root / form_id
    folder.mkdir()
    (folder / "preview.glb").write_bytes(b"preview")
    (folder / "character.glb").write_bytes(b"model embedded textures")
    (folder / "physics.json").write_text('{"groups":[1]}', encoding="utf-8")
    data, binary = part(folder / "animations.glb", skin=True, reordered=other)
    if rig:
        data["nodes"][0]["translation"] = [0, 1, 0]
    template = data["animations"][0]
    data["animations"] = []
    for i in range(count):
        clip = deepcopy(template)
        clip["name"] = f"Clip{i}"
        if other and i == 0:
            clip["name"] = "RenamedCommon"
        if other and i == 400:
            clip["name"] = "Clip0"
        clip["extras"] = {"solaris_atlas": {"source": f"Common/Anim/{i}.json"}}
        data["animations"].append(clip)
    write_glb(folder / "animations.glb", data, binary)
    facial = {
        "format": "solaris-facial-v1",
        "clips": [
            {
                "name": "Smile",
                "body_clip": None,
                "seconds": 1,
                "tracks": [{"target": "Smile", "keys": [[0, 1]]}],
            },
            {
                "name": "Clip0",
                "body_clip": "Clip0",
                "seconds": 1,
                "tracks": [{"target": "Smile", "keys": [[0, 1]]}],
            },
        ],
    }
    (folder / "facial.json").write_text(json.dumps(facial), encoding="utf-8")
    form = {"id": form_id, **({"component": {"parent_form_id": "a"}} if component else {})}
    provenance = {
        "model": f"Characters/{form_id}/Model/Body",
        "scene": {"cues": [1]},
        "linked_components": [{"id": "child"}],
    }
    return (
        form,
        folder,
        {
            "preview": {"sha256": "preview"},
            "download": {"sha256": "model"},
            "animations": {"old": True},
        },
        provenance,
    )


def test_repackage_400_common_and_20_delta_with_renames(tmp_path):
    from wuwa_story.ingestion.model_animation_packs import validate_animation_packs

    first = _bundle(tmp_path, "a", 400)
    second = _bundle(tmp_path, "b", 420, other=True)
    first[3]["form_source"] = {"table": "roleinfo", "id": 123, "field": "MeshId"}
    second[3]["form_source"] = {"table": "roleskin", "role_id": 123, "field": "MeshId"}
    for bundle in (first, second):
        data, binary = read_glb(bundle[1] / "animations.glb")
        for i, clip in enumerate(data["animations"]):
            folder = "a" if i < 400 else "b"
            clip["extras"]["solaris_atlas"]["source"] = (
                f"Client/Content/Characters/{folder}/Anim/{i}.json"
            )
        write_glb(bundle[1] / "animations.glb", data, binary)
    snapshots = {p: p.read_bytes() for bundle in (first, second) for p in bundle[1].iterdir()}
    original_metadata = deepcopy([first[0], first[2], first[3], second[0], second[2], second[3]])
    result = repack.repackage_bundles([first, second], tmp_path / "repacked")
    a, b = result
    assert len(a[3]["animation_packs"]) == 1
    assert len(b[3]["animation_packs"]) == 2
    shared = a[3]["animation_packs"][0]
    assert len(shared["clips"]) == 400
    assert b[3]["animation_packs"][0] == shared
    delta = b[3]["animation_packs"][1]
    assert len(delta["clips"]) == 20
    renamed = next(name for name in delta["clips"] if name.startswith("Clip0."))
    assert b[3]["scene"] == {"cues": [1]}
    assert b[3]["linked_components"] == [{"id": "child"}]
    for _, output, summaries, provenance in result:
        validate_animation_packs(provenance["animation_packs"])
        assert "animations" not in summaries and not (output / "animations.glb").exists()
        assert json.loads((output / "animation-manifest.json").read_text()) == {
            "format": "solaris-animation-packs-v1",
            "packs": provenance["animation_packs"],
        }
        for desc in provenance["animation_packs"]:
            path = output / "animation-packs" / f"{desc['id']}.glb"
            assert glb_summary(path, allow_animations=True) == summaries[desc["key"]]
            assert [clip["name"] for clip in animation_summary(path)["clips"]] == desc["clips"]
            assert summaries[desc["key"]]["sha256"].startswith(desc["id"])
    facial = json.loads((b[1] / "facial.json").read_text())
    assert facial["clips"][0]["name"] == "Smile"
    assert facial["clips"][1]["name"] == facial["clips"][1]["body_clip"] == renamed
    assert (b[1] / "physics.json").read_bytes() == (second[1] / "physics.json").read_bytes()
    assert all(p.read_bytes() == content for p, content in snapshots.items())
    assert [first[0], first[2], first[3], second[0], second[2], second[3]] == original_metadata


def test_ownership_components_and_different_rigs(tmp_path):
    first = _bundle(tmp_path, "a", 2)
    second = _bundle(tmp_path, "b", 2, rig=True)
    component = _bundle(tmp_path, "component", 2, component=True)
    # Explicit other appearance source is removed for root a, retained by component.
    for bundle in (first, component):
        data, binary = read_glb(bundle[1] / "animations.glb")
        data["animations"][1]["extras"]["solaris_atlas"]["source"] = "Characters/b/Anim/Other.json"
        write_glb(bundle[1] / "animations.glb", data, binary)
    result = repack.repackage_bundles([first, second, component], tmp_path / "out")
    a, b, child = result
    a_names = {n for p in a[3]["animation_packs"] for n in p["clips"]}
    child_names = {n for p in child[3]["animation_packs"] for n in p["clips"]}
    assert a_names == {"Clip0"}
    assert child_names == {"Clip0", "Clip1"}
    assert set(p["id"] for p in b[3]["animation_packs"]).isdisjoint(
        p["id"] for p in a[3]["animation_packs"]
    )


def test_static_bundle_and_failure_cleanup(tmp_path):
    bundle = _bundle(tmp_path, "static", 1)
    (bundle[1] / "animations.glb").unlink()
    bundle[2].pop("animations")
    result = repack.repackage_bundles([bundle], tmp_path / "out")
    assert result[0][2:] == (bundle[2], bundle[3])
    assert (result[0][1] / "facial.json").read_bytes() == (bundle[1] / "facial.json").read_bytes()
    with pytest.raises(FileExistsError):
        repack.repackage_bundles([bundle], tmp_path / "out")
    (bundle[1] / "preview.glb").unlink()
    with pytest.raises(ValueError, match="Missing bundle"):
        repack.repackage_bundles([bundle], tmp_path / "failed")
    assert not (tmp_path / "failed").exists()


def test_same_display_name_with_different_content_is_not_shared(tmp_path):
    first = _bundle(tmp_path, "a", 1)
    second = _bundle(tmp_path, "b", 1)
    data, binary = read_glb(second[1] / "animations.glb")
    binary = binary[:20] + struct.pack("<f", 9.0) + binary[24:]
    write_glb(second[1] / "animations.glb", data, binary)
    results = repack.repackage_bundles([first, second], tmp_path / "out")
    assert results[0][3]["animation_packs"][0]["id"] != results[1][3]["animation_packs"][0]["id"]


def test_renames_validate_and_input_destination_overlap(tmp_path):
    path = tmp_path / "source.glb"
    part(path)
    with pytest.raises(ValueError, match="rename"):
        repack.select_animation_pack(path, tmp_path / "out.glb", ["Walk"], renames={"missing": "x"})
    bundle = _bundle(tmp_path, "a", 1)
    with pytest.raises(ValueError, match="overlaps"):
        repack.repackage_bundles([bundle], bundle[1] / "new")
    assert not (bundle[1] / "new").exists()


def test_shared_rename_preserves_colliding_facial_expression(tmp_path):
    first = _bundle(tmp_path, "a", 1)
    second = _bundle(tmp_path, "b", 1, other=True)
    facial_path = second[1] / "facial.json"
    facial = json.loads(facial_path.read_text())
    facial["clips"][0]["name"] = "Clip0"
    facial["clips"][1]["name"] = facial["clips"][1]["body_clip"] = "RenamedCommon"
    facial_path.write_text(json.dumps(facial))
    output = repack.repackage_bundles([first, second], tmp_path / "out")[1][1]
    clips = json.loads((output / "facial.json").read_text())["clips"]
    assert clips[0]["name"] == "Clip0" and clips[0]["body_clip"] is None
    assert clips[1]["body_clip"] == "Clip0" and clips[1]["name"].startswith("Clip0.")


def test_legacy_skin_inherits_base_and_explicit_sources_without_guessing(tmp_path):
    first = _bundle(tmp_path, "a", 5)
    second = _bundle(tmp_path, "b", 5)
    third = _bundle(tmp_path, "c", 5)
    component = _bundle(tmp_path, "child", 5, component=True)
    first[3]["form_source"] = {"table": "roleinfo", "id": 123, "field": "MeshId"}
    second[3]["form_source"] = {"table": "roleskin", "role_id": 123, "field": "MeshId"}
    third[0]["source"] = {"table": "roleskin", "role_id": 123, "field": "MeshId"}
    second[0]["montages"] = ["/Game/Characters/c/Anim/Explicit"]
    paths = [
        "Characters/a/Anim/Base",
        "Characters/b/Anim/SkinB",
        "Characters/c/Anim/SkinC",
        "Common/Anim/Unknown",
        "Characters/c/Anim/Explicit",
    ]
    bundles = (first, second, third, component)
    for bundle in bundles:
        data, binary = read_glb(bundle[1] / "animations.glb")
        for clip, path in zip(data["animations"], paths, strict=True):
            clip["extras"]["solaris_atlas"]["source"] = f"Client/Content/{path}.json"
        write_glb(bundle[1] / "animations.glb", data, binary)
    results = repack.repackage_bundles(bundles, tmp_path / "out")
    clipsets = [
        {name for pack in item[3]["animation_packs"] for name in pack["clips"]} for item in results
    ]
    assert clipsets == [
        {"Clip0", "Clip3"},
        {"Clip0", "Clip1", "Clip3", "Clip4"},
        {"Clip0", "Clip2", "Clip3", "Clip4"},
        {"Clip0", "Clip1", "Clip2", "Clip3", "Clip4"},
    ]
    for item in results:
        for pack in item[3]["animation_packs"]:
            assert animation_summary(item[1] / "animation-packs" / f"{pack['id']}.glb")[
                "animations"
            ] == len(pack["clips"])


def _proof(bundle, path="Common/Anim/0", asset="a", export="b"):
    bundle[3]["source_files"] = [
        {"path": f"Client/Content/{path}.uasset", "sha256": asset * 64},
        {"path": f"Client/Content/{path}.uexp", "sha256": export * 64},
    ]


def _noisy_bundle(bundle, *, rig=0.00002, value=0.00002, time=0.0):
    path = bundle[1] / "animations.glb"
    data, binary = read_glb(path)
    data["nodes"][0]["translation"] = [rig, 0, 0]
    if value:
        binary = binary[:20] + struct.pack("<f", 1 + value) + binary[24:]
    if time:
        binary = binary[:12] + struct.pack("<f", 2 + time) + binary[16:]
    write_glb(path, data, binary)


def test_near_rigs_and_proven_sources_share_noisy_clips(tmp_path):
    first = _bundle(tmp_path, "a", 1)
    second = _bundle(tmp_path, "b", 1)
    _noisy_bundle(second)
    _proof(first)
    _proof(second)
    left, right = (
        repack.animation_pack_index(item[1] / "animations.glb") for item in (first, second)
    )
    assert left["rig"] != right["rig"]
    assert left["clips"][0]["identity"] != right["clips"][0]["identity"]
    assert repack.equivalent_animation(
        first[1] / "animations.glb", "Clip0", second[1] / "animations.glb", "Clip0"
    )
    results = repack.repackage_bundles([first, second], tmp_path / "out")
    assert results[0][3]["animation_packs"] == results[1][3]["animation_packs"]


@pytest.mark.parametrize(
    "case",
    [
        "no_proof",
        "asset_hash",
        "export_hash",
        "missing_export",
        "rig",
        "value",
        "time",
        "interpolation",
        "duplicate_source",
        "missing_authored_source",
    ],
)
def test_noisy_fallback_rejects_insufficient_evidence(tmp_path, case):
    count = 2 if case == "duplicate_source" else 1
    first, second = _bundle(tmp_path, "a", count), _bundle(tmp_path, "b", count)
    _noisy_bundle(
        second,
        rig=0.0002 if case == "rig" else 0.00002,
        value=0.0002 if case == "value" else 0.00002,
        time=0.00001 if case == "time" else 0.0,
    )
    _proof(first)
    _proof(
        second,
        asset="c" if case == "asset_hash" else "a",
        export="c" if case == "export_hash" else "b",
    )
    if case == "no_proof":
        second[3].pop("source_files")
    if case == "missing_export":
        second[3]["source_files"].pop()
    if case in ("interpolation", "duplicate_source", "missing_authored_source"):
        for bundle in (first, second):
            data, binary = read_glb(bundle[1] / "animations.glb")
            if case == "interpolation" and bundle is second:
                data["animations"][0]["samplers"][0]["interpolation"] = "STEP"
            if case == "duplicate_source":
                data["animations"][1]["extras"] = deepcopy(data["animations"][0]["extras"])
                data["animations"][1]["samplers"][0]["interpolation"] = "STEP"
            if case == "missing_authored_source":
                data["animations"][0].pop("extras")
                data["animations"][0]["name"] = "Common/Anim/0"
                facial = json.loads((bundle[1] / "facial.json").read_text())
                facial["clips"][1]["name"] = facial["clips"][1]["body_clip"] = "Common/Anim/0"
                (bundle[1] / "facial.json").write_text(json.dumps(facial))
            write_glb(bundle[1] / "animations.glb", data, binary)
    results = repack.repackage_bundles([first, second], tmp_path / "out")
    a = {p["id"] for p in results[0][3]["animation_packs"]}
    b = {p["id"] for p in results[1][3]["animation_packs"]}
    assert a.isdisjoint(b)


def test_rig_tolerance_compares_values_without_quantization(tmp_path):
    a, b = _bundle(tmp_path, "a", 1), _bundle(tmp_path, "b", 1)
    _noisy_bundle(a, rig=0.000049, value=0)
    _noisy_bundle(b, rig=0.000051, value=0)
    result = repack.repackage_bundles([a, b], tmp_path / "out")
    assert result[0][3]["animation_packs"] == result[1][3]["animation_packs"]


def test_noisy_400_clip_pack_reuses_only_with_full_source_proof(tmp_path):
    first, second = _bundle(tmp_path, "a", 400), _bundle(tmp_path, "b", 420)
    _noisy_bundle(second)
    for bundle in (first, second):
        bundle[3]["source_files"] = [
            {"path": f"Client/Content/Common/Anim/{i}.{suffix}", "sha256": digest * 64}
            for i in range(400)
            for suffix, digest in (("uasset", "a"), ("uexp", "b"))
        ]
    a, b = repack.repackage_bundles([first, second], tmp_path / "out")
    assert len(a[3]["animation_packs"]) == 1
    assert len(a[3]["animation_packs"][0]["clips"]) == 400
    assert b[3]["animation_packs"][0] == a[3]["animation_packs"][0]
    assert len(b[3]["animation_packs"][1]["clips"]) == 20


def test_identical_duplicate_clips_remain_distinct_and_strict(tmp_path):
    first, second = _bundle(tmp_path, "a", 2), _bundle(tmp_path, "b", 2)
    for bundle in (first, second):
        data, binary = read_glb(bundle[1] / "animations.glb")
        data["animations"][1]["extras"] = deepcopy(data["animations"][0]["extras"])
        write_glb(bundle[1] / "animations.glb", data, binary)
    _noisy_bundle(second)
    _proof(first)
    _proof(second)
    a, b = repack.repackage_bundles([first, second], tmp_path / "out")
    assert len(a[3]["animation_packs"][0]["clips"]) == 2
    assert len(b[3]["animation_packs"][0]["clips"]) == 2
    assert a[3]["animation_packs"][0]["id"] != b[3]["animation_packs"][0]["id"]


def test_source_proof_does_not_conflate_dotted_directories(tmp_path):
    first, second = _bundle(tmp_path, "a", 1), _bundle(tmp_path, "b", 1)
    _noisy_bundle(second)
    for bundle, folder in ((first, "Dir.One"), (second, "Dir.Two")):
        data, binary = read_glb(bundle[1] / "animations.glb")
        data["animations"][0]["extras"]["solaris_atlas"]["source"] = f"Common/{folder}/Anim.json"
        write_glb(bundle[1] / "animations.glb", data, binary)
        _proof(bundle, f"Common/{folder}/Anim")
    a, b = repack.repackage_bundles([first, second], tmp_path / "out")
    assert a[3]["animation_packs"][0]["id"] != b[3]["animation_packs"][0]["id"]


def test_sample_copy_and_comparison_reads_are_bounded(monkeypatch):
    import hashlib
    import io

    monkeypatch.setattr(repack, "_CHUNK", 64)
    data = b"".join(struct.pack("<f", float(i)) + b"padding!" for i in range(10000))

    class BoundedReader(io.BytesIO):
        def read(self, size=-1):
            assert 0 < size <= repack._CHUNK
            return super().read(size)

    layout = (0, 10000, 4, 12)
    digest = hashlib.sha256()
    repack._copy_elements(BoundedReader(data), layout, digest.update)
    expected = hashlib.sha256(b"".join(struct.pack("<f", float(i)) for i in range(10000)))
    assert digest.digest() == expected.digest()
    blocks = list(repack._element_blocks(BoundedReader(data), layout))
    assert hashlib.sha256(b"".join(blocks)).digest() == expected.digest()

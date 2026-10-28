import json
import struct
from copy import deepcopy
from pathlib import Path

import pytest

from wuwa_story_worker import model_animation_merge as merger
from wuwa_story_worker.model_animation_catalog import classify_animation
from wuwa_story_worker.model_animations import animation_summary


def write_glb(path, data, binary):
    payload = json.dumps(data).encode()
    payload += b" " * (-len(payload) % 4)
    binary += b"\0" * (-len(binary) % 4)
    path.write_bytes(
        struct.pack("<4sIII4s", b"glTF", 2, 28 + len(payload) + len(binary), len(payload), b"JSON")
        + payload
        + struct.pack("<I4s", len(binary), b"BIN\0")
        + binary
    )


def read_glb(path):
    raw = path.read_bytes()
    length = struct.unpack_from("<I", raw, 12)[0]
    assert len(raw) == struct.unpack_from("<I", raw, 8)[0]
    return json.loads(raw[20 : 20 + length]), raw[28 + length :]


def test_merge_preserves_joint_identity_without_counting_wrapper_as_bone(tmp_path):
    paths = [tmp_path / "a.glb", tmp_path / "b.glb"]
    for path, name in zip(paths, ["Walk", "Run"], strict=True):
        part(path, name, skin=True)
        data, binary = read_glb(path)
        data["nodes"].append({"name": "Armature", "children": [0]})
        data["scenes"][0]["nodes"] = [2]
        write_glb(path, data, binary)
    output = tmp_path / "all.glb"
    merger.merge_animation_glbs(paths, output)
    assert animation_summary(output)["bones"] == 2


def part(path, name="Walk", reordered=False, skin=False):
    # Both view and accessor offsets matter; output samples are interleaved.
    binary = b"HEAD" + b"pad!" + struct.pack("<2f", 0, 2)
    binary += b"pad!" + struct.pack("<3f", 1, 2, 3)
    binary += b"pad!" + struct.pack("<3f", 4, 5, 6) + b"TAILx"
    root, child = (1, 0) if reordered else (0, 1)
    nodes = [{"name": "Root", "children": [child]}, {"name": "Child"}]
    if reordered:
        nodes.reverse()
    views = [
        {"buffer": 0, "byteOffset": 4, "byteLength": 12},
        {"buffer": 0, "byteOffset": 16, "byteLength": 36, "byteStride": 16},
    ]
    accessors = [
        {"bufferView": 0, "byteOffset": 4, "componentType": 5126, "type": "SCALAR", "count": 2},
        {"bufferView": 1, "byteOffset": 4, "componentType": 5126, "type": "VEC3", "count": 2},
    ]
    if reordered:
        views.reverse()
        for accessor in accessors:
            accessor["bufferView"] = 1 - accessor["bufferView"]
        accessors.reverse()
    data = {
        "asset": {"version": "2.0", "generator": "test"},
        "nodes": nodes,
        "scenes": [{"nodes": [root], "name": "Authored scene"}],
        "scene": 0,
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": views,
        "accessors": accessors,
        "animations": [
            {
                "name": name,
                "samplers": [
                    {"input": int(reordered), "output": int(not reordered)},
                    {
                        "input": int(reordered),
                        "output": int(not reordered),
                        "interpolation": "STEP",
                    },
                ],
                "channels": [
                    {"sampler": 1, "target": {"node": child, "path": "translation"}},
                    {"sampler": 0, "target": {"node": root, "path": "scale"}},
                ],
            }
        ],
    }
    if skin:
        data["skins"] = [{"joints": [root, child], "skeleton": root}]
    write_glb(path, data, binary)
    return data, binary


def samples(data, binary, accessor_index):
    accessor = data["accessors"][accessor_index]
    view = data["bufferViews"][accessor["bufferView"]]
    width = {"SCALAR": 1, "VEC3": 3}[accessor["type"]]
    offset = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    stride = view.get("byteStride", width * 4)
    return [
        struct.unpack_from("<" + "f" * width, binary, offset + i * stride)
        for i in range(accessor["count"])
    ]


@pytest.mark.parametrize("first_reordered", [False, True])
def test_rebases_reordered_nodes_accessors_views_and_preserves_samples(tmp_path, first_reordered):
    paths = [tmp_path / "first.glb", tmp_path / "second.glb"]
    first, first_binary = part(paths[0], reordered=first_reordered, skin=True)
    second, second_binary = part(paths[1], "Run", reordered=not first_reordered, skin=True)
    output = tmp_path / "merged.glb"
    merger.merge_animation_glbs(paths, output)
    data, binary = read_glb(output)
    assert data["nodes"] == first["nodes"]
    assert data["scenes"] == first["scenes"]
    assert data["scene"] == first["scene"]
    assert data["skins"] == first["skins"]
    padded_length = len(first_binary) + (-len(first_binary) % 4)
    assert data["buffers"] == [{"byteLength": padded_length * 2}]
    assert binary == first_binary + b"\0" * 3 + second_binary + b"\0" * 3
    for i, (original, original_binary) in enumerate(
        [(first, first_binary), (second, second_binary)]
    ):
        clip = data["animations"][i]
        assert [channel["sampler"] for channel in clip["channels"]] == [1, 0]
        for old, new in zip(original["animations"][0]["channels"], clip["channels"], strict=True):
            assert (
                data["nodes"][new["target"]["node"]]["name"]
                == original["nodes"][old["target"]["node"]]["name"]
            )
        for old, new in zip(original["animations"][0]["samplers"], clip["samplers"], strict=True):
            for key in ("input", "output"):
                assert new[key] == old[key] + i * 2
                assert samples(data, binary, new[key]) == samples(
                    original, original_binary, old[key]
                )
    assert animation_summary(output)["clips"] == [
        {"name": "Walk", "channels": 2, "seconds": 2.0},
        {"name": "Run", "channels": 2, "seconds": 2.0},
    ]


def test_single_part_can_atomically_replace_itself(tmp_path):
    path = tmp_path / "same.glb"
    original, binary = part(path)
    merger.merge_animation_glbs([path], path)
    data, merged_binary = read_glb(path)
    original["animations"][0]["extras"] = {"solaris_atlas": classify_animation("Walk")}
    assert data["animations"] == original["animations"]
    assert data["accessors"] == original["accessors"]
    assert merged_binary[: len(binary)] == binary
    assert animation_summary(path)["animations"] == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == ["same.glb"]


def test_explicit_identity_transforms_and_quaternion_sign_are_equivalent(tmp_path):
    a, b, output = [tmp_path / name for name in ("a.glb", "b.glb", "out.glb")]
    part(a)
    data, binary = part(b, "Run")
    data["nodes"][0].update(translation=[0, 0, 0], rotation=[0, 0, 0, -1], scale=[1, 1, 1])
    write_glb(b, data, binary)
    merger.merge_animation_glbs([a, b], output)
    assert animation_summary(output)["animations"] == 2


def test_multiple_clips_per_part_keep_local_sampler_indices(tmp_path):
    paths = [tmp_path / "a.glb", tmp_path / "b.glb"]
    for index, path in enumerate(paths):
        data, binary = part(path, f"Walk{index}", reordered=bool(index))
        clip = deepcopy(data["animations"][0])
        clip["name"] = f"Run{index}"
        data["animations"].append(clip)
        write_glb(path, data, binary)
    output = tmp_path / "out.glb"
    merger.merge_animation_glbs(paths, output)
    assert [clip["name"] for clip in animation_summary(output)["clips"]] == [
        "Walk0",
        "Run0",
        "Walk1",
        "Run1",
    ]


def test_cubic_spline_samples_are_copied_without_modification(tmp_path):
    source, other, output = [tmp_path / name for name in ("a.glb", "b.glb", "out.glb")]
    data, binary = part(source)
    binary += b"\0" * (-len(binary) % 4)
    offset = len(binary)
    values = [float(value) for value in range(18)]
    binary += struct.pack("<18f", *values)
    data["buffers"][0]["byteLength"] = len(binary)
    data["bufferViews"].append({"buffer": 0, "byteOffset": offset, "byteLength": 72})
    data["accessors"][1].update(bufferView=2, byteOffset=0, count=6)
    for sampler in data["animations"][0]["samplers"]:
        sampler["interpolation"] = "CUBICSPLINE"
    write_glb(source, data, binary)
    part(other, "Run")
    merger.merge_animation_glbs([source, other], output)
    merged, merged_binary = read_glb(output)
    assert samples(merged, merged_binary, 1) == samples(data, binary, 1)
    assert animation_summary(output)["animations"] == 2


def test_static_matrix_and_trs_identity_are_equivalent(tmp_path):
    paths = [tmp_path / "a.glb", tmp_path / "b.glb"]
    for index, path in enumerate(paths):
        data, binary = part(path, f"Walk{index}")
        node = {"name": "Container", "children": [0]}
        if index == 0:
            node["matrix"] = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
        data["nodes"].append(node)
        data["scenes"][0]["nodes"] = [2]
        write_glb(path, data, binary)
    output = tmp_path / "out.glb"
    merger.merge_animation_glbs(paths, output)
    assert animation_summary(output)["animations"] == 2
    assert "matrix" in read_glb(output)[0]["nodes"][2]


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["animations"][0].update(name="Walk"),
        lambda d: d["nodes"][1].update(name="OtherBone"),
        lambda d: d["nodes"][1].update(name="Root"),
        lambda d: d["nodes"][0].update(children=[]),
        lambda d: d["nodes"][1].update(children=[0]),
        lambda d: d["nodes"][0].update(children=[1, 1]),
        lambda d: d["nodes"][0].update(translation=[0, 0, 1]),
        lambda d: d["nodes"][0].update(rotation=[0, 0, 0, 0]),
        lambda d: d["nodes"][0].update(scale=[1, 2, 1]),
        lambda d: d["nodes"][0].update(mesh=0),
        lambda d: d["nodes"][0].update(skin=0),
        lambda d: d["scenes"][0].update(nodes=[1]),
        lambda d: d.update(scene=1),
        lambda d: d.update(skins=[{"joints": [1]}]),
        lambda d: d.update(skins=[{"joints": [0, 7]}]),
        lambda d: d.update(skins=[{"joints": [0, 1], "inverseBindMatrices": 100}]),
        lambda d: d.update(meshes=[{}]),
        lambda d: d.update(materials=[{}]),
        lambda d: d.update(images=[{}]),
        lambda d: d.update(textures=[{}]),
        lambda d: d.update(extensionsUsed=["unknown"]),
        lambda d: d["nodes"][0].update(extensions={"unknown": {}}),
        lambda d: d["buffers"][0].update(uri="external.bin"),
        lambda d: d["buffers"].append({"byteLength": 4}),
        lambda d: d["bufferViews"][0].update(buffer=1),
        lambda d: d["bufferViews"][0].update(byteLength=10000),
        lambda d: d["bufferViews"][0].update(byteOffset=5),
        lambda d: d["accessors"][0].update(bufferView=-1),
        lambda d: d["accessors"][0].update(count=2.0),
        lambda d: d["accessors"][0].update(count=True),
        lambda d: d["accessors"][0].update(byteOffset=-4),
        lambda d: d["accessors"][0].update(sparse={}),
        lambda d: d["accessors"][0].update(componentType=5123),
        lambda d: d["accessors"][0].update(componentType=5126.0),
        lambda d: d["accessors"][0].update(normalized=0),
        lambda d: d["animations"][0]["samplers"][0].update(input=True),
        lambda d: d["animations"][0]["samplers"][0].update(interpolation="unknown"),
        lambda d: d["animations"][0]["channels"][0]["target"].update(node=-1),
        lambda d: d["animations"][0]["channels"][0]["target"].update(path="weights"),
        lambda d: d["animations"][0]["channels"].append(
            deepcopy(d["animations"][0]["channels"][0])
        ),
    ],
)
def test_invalid_or_incompatible_input_preserves_destination(tmp_path, change):
    a, b, output = [tmp_path / name for name in ("a.glb", "b.glb", "out.glb")]
    part(a)
    data, binary = part(b, "Run")
    change(data)
    write_glb(b, data, binary)
    output.write_bytes(b"existing destination")
    with pytest.raises(ValueError):
        merger.merge_animation_glbs([a, b], output)
    assert output.read_bytes() == b"existing destination"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.glb", "b.glb", "out.glb"]


@pytest.mark.parametrize(
    "corruption", ["header", "truncated", "trailing", "nan", "times", "duplicate_json"]
)
def test_rejects_corrupt_binary_or_json(tmp_path, corruption):
    path, output = tmp_path / "a.glb", tmp_path / "out.glb"
    data, binary = part(path)
    if corruption == "nan":
        binary = binary[:20] + struct.pack("<f", float("nan")) + binary[24:]
        write_glb(path, data, binary)
    elif corruption == "times":
        binary = binary[:8] + struct.pack("<2f", 2, 1) + binary[16:]
        write_glb(path, data, binary)
    elif corruption == "duplicate_json":
        payload = (
            json.dumps(data)
            .replace('"version": "2.0"', '"version": "2.0", "version": "2.0"')
            .encode()
        )
        payload += b" " * (-len(payload) % 4)
        binary += b"\0" * (-len(binary) % 4)
        path.write_bytes(
            struct.pack(
                "<4sIII4s", b"glTF", 2, 28 + len(payload) + len(binary), len(payload), b"JSON"
            )
            + payload
            + struct.pack("<I4s", len(binary), b"BIN\0")
            + binary
        )
    else:
        raw = path.read_bytes()
        path.write_bytes(
            {"header": b"bad!" + raw[4:], "truncated": raw[:-1], "trailing": raw + b"xxxx"}[
                corruption
            ]
        )
    with pytest.raises(ValueError):
        merger.merge_animation_glbs([path], output)
    assert not output.exists()
    assert list(tmp_path.iterdir()) == [path]


def test_empty_parts_preserves_destination(tmp_path):
    output = tmp_path / "out.glb"
    output.write_bytes(b"existing")
    with pytest.raises(ValueError, match="No animation"):
        merger.merge_animation_glbs([], output)
    assert output.read_bytes() == b"existing"


def test_replace_failure_removes_temporary_and_preserves_destination(tmp_path, monkeypatch):
    source, output = tmp_path / "a.glb", tmp_path / "out.glb"
    part(source)
    output.write_bytes(b"existing")

    def fail_replace(temporary, destination):
        assert destination == output
        assert animation_summary(temporary)["animations"] == 1
        raise OSError("replace failed")

    monkeypatch.setattr(merger.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        merger.merge_animation_glbs([source], output)
    assert output.read_bytes() == b"existing"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.glb", "out.glb"]


def test_input_changed_after_validation_is_rejected_atomically(tmp_path, monkeypatch):
    source, output = tmp_path / "a.glb", tmp_path / "out.glb"
    part(source)
    output.write_bytes(b"existing")
    original_summary = merger.animation_summary

    def validate_then_change(path):
        summary = original_summary(path)
        with path.open("ab") as handle:
            handle.write(b"changed")
        return summary

    monkeypatch.setattr(merger, "animation_summary", validate_then_change)
    with pytest.raises(ValueError, match="input changed"):
        merger.merge_animation_glbs([source], output)
    assert output.read_bytes() == b"existing"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.glb", "out.glb"]


def test_binary_copy_is_bounded_and_copy_failure_is_atomic(tmp_path, monkeypatch):
    source, output = tmp_path / "a.glb", tmp_path / "out.glb"
    data, binary = part(source)
    binary += b"\0" * (3 * 1024 * 1024)
    data["buffers"][0]["byteLength"] = len(binary)
    write_glb(source, data, binary)
    output.write_bytes(b"existing")
    original_open = Path.open
    reads = []
    opens = 0

    class CopyReader:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.handle.close()

        def fileno(self):
            return self.handle.fileno()

        def seek(self, offset):
            return self.handle.seek(offset)

        def read(self, size):
            reads.append(size)
            assert 0 < size <= 1024 * 1024
            if len(reads) == 3:
                raise OSError("copy failed")
            return self.handle.read(size)

    def checked_open(path, *args, **kwargs):
        nonlocal opens
        handle = original_open(path, *args, **kwargs)
        if path == source:
            opens += 1
            # Metadata reader, existing per-part validator, streaming copy.
            if opens == 3:
                return CopyReader(handle)
        return handle

    monkeypatch.setattr(Path, "open", checked_open)
    with pytest.raises(OSError, match="copy failed"):
        merger.merge_animation_glbs([source], output)
    assert reads == [1024 * 1024] * 3
    assert output.read_bytes() == b"existing"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.glb", "out.glb"]


def test_catalog_injected_per_clip_preserving_other_extras_and_bindings(tmp_path):
    source, output = tmp_path / "source.glb", tmp_path / "out.glb"
    data, binary = part(source, "AM_AirAttack_Start")
    data["animations"][0]["extras"] = {"exporter": {"source": 42}, "solaris_atlas": None}
    other = deepcopy(data["animations"][0])
    other["name"] = "UnknownThing"
    data["animations"].append(other)
    write_glb(source, data, binary)
    merger.merge_animation_glbs([source], output)
    merged, merged_binary = read_glb(output)
    for before, after in zip(data["animations"], merged["animations"], strict=True):
        assert after["extras"] == {
            "exporter": {"source": 42},
            "solaris_atlas": classify_animation(before["name"]),
        }
        assert {k: v for k, v in after.items() if k != "extras"} == {
            k: v for k, v in before.items() if k != "extras"
        }
    assert merged_binary[: len(binary)] == binary
    assert [c["seconds"] for c in animation_summary(output)["clips"]] == [2.0, 2.0]


@pytest.mark.parametrize("extras", [None, [], "invalid", 42])
def test_non_object_extras_fail_atomically(tmp_path, extras):
    source, output = tmp_path / "source.glb", tmp_path / "out.glb"
    data, binary = part(source)
    data["animations"][0]["extras"] = extras
    write_glb(source, data, binary)
    output.write_bytes(b"existing")
    with pytest.raises(ValueError, match="extras must be an object"):
        merger.merge_animation_glbs([source], output)
    assert output.read_bytes() == b"existing"

import json
import struct
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from wuwa_story_worker.model_bundle import (
    animation_sources,
    base_color_texture,
    indexed_gltf_references,
    main,
    material_metadata_path,
    package_path,
    preview_materials,
    retime_tracks,
    rig_glb_stats,
)


def test_package_identity_ignores_object_suffix_and_preserves_path():
    assert package_path("/Game/Aki/Character/Test.Test") == package_path(
        "Client/Content/Aki/Character/Test.42"
    )
    assert package_path("Client\\Content\\Aki\\Character\\Test") == package_path(
        "/Game/Aki/Character/Test"
    )


def test_animation_catalog_uses_exact_skeleton_reference_not_folder_name(tmp_path):
    folder = tmp_path / "Client/Content/Aki/Shouanren"
    folder.mkdir(parents=True)
    for name, kind, skeleton in (
        ("Stand1", "AnimSequence", "/Game/Aki/Model/Shorekeeper_Skeleton"),
        ("Stand2", "AnimMontage", "Client/Content/Aki/Model/Shorekeeper_Skeleton.3"),
        ("Other", "AnimSequence", "/Game/Aki/Model/Other_Skeleton"),
        ("Mesh", "SkeletalMesh", "/Game/Aki/Model/Shorekeeper_Skeleton"),
    ):
        (folder / f"{name}.json").write_text(
            json.dumps(
                [
                    {
                        "Type": kind,
                        "Name": name,
                        "Properties": {"Skeleton": {"ObjectPath": skeleton}},
                    }
                ]
            ),
            encoding="utf-8",
        )
    rows = animation_sources(tmp_path, "/Game/Aki/Model/Shorekeeper_Skeleton")
    assert len(rows) == 3
    assert {row["name"] for row in rows.values() if row["matches_skeleton"]} == {"Stand1", "Stand2"}


def test_constant_tracks_retain_values_and_duration_without_single_key_baking():
    position = [SimpleNamespace(frame=0, value=(1, 2, 3))]
    rotation = [
        SimpleNamespace(frame=0, value=(0, 0, 0, 1)),
        SimpleNamespace(frame=60, value=(0, 0, 1, 0)),
    ]
    dto = SimpleNamespace(
        metadata=SimpleNamespace(num_frames=61, frames_per_second=60),
        tracks=[SimpleNamespace(position_keys=position, rotation_keys=rotation, scale_keys=[])],
    )
    retime_tracks(dto)
    assert [key.frame for key in position] == [0, 30]
    assert position[0].value == position[1].value == (1, 2, 3)
    assert position[0] is not position[1]
    assert [key.frame for key in rotation] == [0, 30]
    assert dto.tracks[0].scale_keys == []


def legacy_index(target, obj):
    if obj in target:
        return target.index(obj)
    index = len(target)
    target.append(obj)
    return index


def test_gltf_index_preserves_references_value_fallback_and_restores_on_failure():
    name = "_GlTF2Exporter__append_unique_and_get_index"
    exporter = type("GlTF2Exporter", (), {name: staticmethod(legacy_index)})
    original = vars(exporter)[name]

    class Reference:
        pass

    first, second = Reference(), Reference()
    refs = []
    with (
        pytest.raises(ValueError, match="export failed"),
        indexed_gltf_references(exporter, (Reference,)),
    ):
        add = getattr(exporter, name)
        assert add(refs, first) == 0
        assert add(refs, first) == 0
        refs.append(second)
        assert add(refs, second) == 1
        assert refs == [first, second]
        assert add([[1]], [1]) == 0
        raise ValueError("export failed")
    assert vars(exporter)[name] is original


def test_gltf_identity_index_does_not_scan_growing_root_arrays():
    class Reference:
        pass

    class ScanCountingList(list):
        scanned = 0

        def __contains__(self, value):
            self.scanned += len(self)
            return super().__contains__(value)

        def index(self, value):
            self.scanned += len(self)
            return super().index(value)

    name = "_GlTF2Exporter__append_unique_and_get_index"
    exporter = type("GlTF2Exporter", (), {name: staticmethod(legacy_index)})
    refs = ScanCountingList()
    values = [Reference() for _ in range(1000)]
    with indexed_gltf_references(exporter, (Reference,)):
        add = getattr(exporter, name)
        assert [add(refs, value) for value in values] == list(range(1000))
        assert [add(refs, value) for value in values] == list(range(1000))
    assert refs.scanned < 1000


def test_material_metadata_prefers_exact_model_neighbor_and_rejects_ambiguity(tmp_path):
    model = tmp_path / "Model/Character.uemodel"
    local = model.with_name("Body.json")
    other = tmp_path / "Other/Body.json"
    assert material_metadata_path("Body", model, [other, local]) == local
    assert material_metadata_path("Body", model, [other]) == other
    assert material_metadata_path("Body", model, [tmp_path / "BodyExtra.json"]) is None
    with pytest.raises(ValueError, match="Ambiguous"):
        material_metadata_path("Body", model, [other, tmp_path / "Third/Body.json"])


def test_base_color_uses_exact_maintex_then_basecolor_without_fuzzy_fallback(tmp_path):
    texture = tmp_path / "Client/Content/Test/Body.png"
    texture.parent.mkdir(parents=True)
    texture.touch()
    base = {"ObjectPath": "/Game/Test/Body.Body"}
    assert base_color_texture({"Textures": {"BaseColor": base}}, tmp_path)[1] == texture
    assert base_color_texture({"Textures": {"MainTex": base}}, tmp_path)[1] == texture
    metadata = {"Textures": {"MainTex": {"ObjectPath": "/Game/Missing.0"}, "BaseColor": base}}
    assert base_color_texture(metadata, tmp_path) == ("/Game/Missing.0", None)
    assert base_color_texture({"Textures": {"Body_D": base}}, tmp_path) == (None, None)
    outside = tmp_path.parent / f"{tmp_path.name}-outside.png"
    outside.touch()
    assert (
        base_color_texture(
            {"Textures": {"MainTex": {"ObjectPath": str(outside.with_suffix(""))}}}, tmp_path
        )[1]
        is None
    )


def test_preview_resizes_copies_preserves_aspect_alpha_and_restores_slots_on_failure():
    class Image:
        size = (4096, 2048)
        alpha_mode = "STRAIGHT"
        packed = False

        def copy(self):
            return deepcopy(self)

        def scale(self, width, height):
            self.size = (width, height)

        def pack(self):
            self.packed = True

    class Material:
        def __init__(self, image):
            self.node_tree = SimpleNamespace(nodes=[SimpleNamespace(type="TEX_IMAGE", image=image)])

        def copy(self):
            return Material(self.node_tree.nodes[0].image)

    source = Image()
    material = Material(source)
    mesh = SimpleNamespace(data=SimpleNamespace(materials=[material, material]))
    with pytest.raises(RuntimeError, match="export failed"), preview_materials([mesh], 1024):
        first, second = mesh.data.materials
        assert first is second and first is not material
        image = first.node_tree.nodes[0].image
        assert image is not source and image.size == (1024, 512)
        assert image.packed and image.file_format == "PNG" and image.alpha_mode == "STRAIGHT"
        assert source.size == (4096, 2048) and not source.packed
        raise RuntimeError("export failed")
    assert mesh.data.materials == [material, material]


def write_test_glb(path, document):
    payload = json.dumps(document).encode()
    payload += b" " * (-len(payload) % 4)
    path.write_bytes(
        struct.pack("<4sIII4s", b"glTF", 2, 20 + len(payload), len(payload), b"JSON") + payload
    )


def rig_document():
    return {
        "skins": [{"joints": [0, 1]}],
        "meshes": [
            {"primitives": [{"attributes": {"POSITION": 0, "JOINTS_0": 1}, "targets": [{}]}]}
        ],
        "accessors": [{"count": 3}],
        "images": [{"bufferView": 0, "mimeType": "image/png"}],
        "buffers": [{"byteLength": 0}],
    }


def test_rig_glb_stats_checks_actual_header_joints_morphs_and_embedded_textures(tmp_path):
    path = tmp_path / "character.glb"
    write_test_glb(path, rig_document())
    assert rig_glb_stats(path, 2, 1, 1) == {
        "bytes": path.stat().st_size,
        "animations": 0,
        "joints": 2,
        "morph_targets": 1,
        "vertices": 3,
        "textures": 1,
    }


@pytest.mark.parametrize(
    "change", ["animation", "joint", "morph", "external", "missing", "unskinned", "header"]
)
def test_rig_glb_rejects_broken_export_contract(tmp_path, change):
    document = rig_document()
    if change == "animation":
        document["animations"] = [{}]
    elif change == "joint":
        document["skins"][0]["joints"].pop()
    elif change == "morph":
        document["meshes"][0]["primitives"][0]["targets"] = []
    elif change == "external":
        document["images"][0]["uri"] = "external.png"
    elif change == "missing":
        document["images"] = []
    elif change == "unskinned":
        del document["meshes"][0]["primitives"][0]["attributes"]["JOINTS_0"]
    path = tmp_path / "character.glb"
    write_test_glb(path, document)
    if change == "header":
        path.write_bytes(path.read_bytes() + b"extra")
    with pytest.raises(ValueError):
        rig_glb_stats(path, 2, 1, 1)


def test_preview_glb_requires_zero_morph_targets(tmp_path):
    path = tmp_path / "preview.glb"
    document = rig_document()
    write_test_glb(path, document)
    with pytest.raises(ValueError, match="lost joints/morphs or contains animations"):
        rig_glb_stats(path, 2, 0, 1)
    del document["meshes"][0]["primitives"][0]["targets"]
    write_test_glb(path, document)
    assert rig_glb_stats(path, 2, 0, 1)["morph_targets"] == 0


@pytest.mark.parametrize("rig_only,missing_texture", [(False, False), (True, False), (True, True)])
def test_rig_mode_skips_animation_catalog_scan_and_blend_but_full_mode_requires_clips(
    tmp_path,
    monkeypatch,
    rig_only,
    missing_texture,
):
    model = tmp_path / "Character.uemodel"
    model.touch()
    converted = tmp_path / "converted"
    converted.mkdir()
    output = tmp_path / "output"
    argv = [
        "blender",
        "--",
        "--model",
        str(model),
        "--converted",
        str(converted),
        "--source-json",
        str(tmp_path / "absent"),
        "--skeleton",
        "unused",
        "--ueformat-plugin",
        str(tmp_path),
        "--output",
        str(output),
    ]
    if rig_only:
        argv.append("--rig-only")
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(sys, "path", sys.path.copy())
    armature = SimpleNamespace(type="ARMATURE", data=SimpleNamespace(bones=[0, 1]))
    mesh = SimpleNamespace(
        type="MESH",
        data=SimpleNamespace(
            vertices=[0, 1, 2],
            materials=[],
            shape_keys=SimpleNamespace(key_blocks=list(range(87))),
        ),
    )
    if missing_texture:
        material = type("Material", (), {})()
        material.name = "Body"
        material.node_tree = SimpleNamespace(
            nodes={
                "Principled BSDF": SimpleNamespace(
                    inputs={
                        "Roughness": SimpleNamespace(default_value=0),
                        "Metallic": SimpleNamespace(default_value=0),
                    }
                ),
            }
        )
        mesh.data.materials.append(material)
    calls = []

    def export(**kwargs):
        calls.append(kwargs)
        document = rig_document()
        document["images"] = []
        primitive = document["meshes"][0]["primitives"][0]
        if kwargs["export_morph"]:
            primitive["targets"] = [{}] * 86
        else:
            del primitive["targets"]
        write_test_glb(Path(kwargs["filepath"]), document)
        return {"FINISHED"}

    bpy = SimpleNamespace(
        data=SimpleNamespace(objects=[mesh]),
        app=SimpleNamespace(version_string="test"),
        context=SimpleNamespace(scene=SimpleNamespace(render=SimpleNamespace(fps=0))),
        ops=SimpleNamespace(
            object=SimpleNamespace(select_all=lambda **kw: None, delete=lambda **kw: None),
            export_scene=SimpleNamespace(gltf=export),
        ),
    )
    monkeypatch.setitem(sys.modules, "bpy", bpy)
    modules = {
        "io_scene_ueformat.importer.archive.reader": {"FArchiveReader": None},
        "io_scene_ueformat.importer.create.anim": {"create_anim": None},
        "io_scene_ueformat.importer.create.space": {"to_blender_space": None},
        "io_scene_ueformat.importer.import_context": {
            "UEFormatImport": lambda options: SimpleNamespace(import_file=lambda path: armature),
            "parse_body": None,
            "parse_header": None,
        },
        "io_scene_ueformat.options": {"UEAnimOptions": None, "UEModelOptions": lambda: None},
        "io_scene_gltf2.blender.exp.exporter": {
            "GlTF2Exporter": type(
                "Exporter",
                (),
                {
                    "_GlTF2Exporter__append_unique_and_get_index": staticmethod(legacy_index),
                },
            )
        },
        "io_scene_gltf2.io.com.gltf2_io": {
            "Accessor": type("Accessor", (), {}),
            "BufferView": type("BufferView", (), {}),
        },
    }
    for name, attrs in modules.items():
        monkeypatch.setitem(sys.modules, name, SimpleNamespace(**attrs))
    original_rglob = Path.rglob

    def rglob(path, pattern):
        assert not (rig_only and (pattern == "*.ueanim" or path.name == "absent"))
        return original_rglob(path, pattern)

    monkeypatch.setattr(Path, "rglob", rglob)
    if missing_texture:
        with pytest.raises(ValueError, match="Missing required base-color textures"):
            main()
        assert not calls
        report = json.loads((output / "report.json").read_text())
        assert report["missing_textures"] == [{"material": "Body", "reference": None}]
        assert not (output / "character.glb").exists()
        return
    if not rig_only:
        with pytest.raises(ValueError, match="No verified"):
            main()
        assert not calls
        return
    main()
    assert len(calls) == 2
    assert all(not call["export_animations"] and call["export_skins"] for call in calls)
    assert calls[0]["export_morph"] is True
    assert calls[1]["export_morph"] is False
    assert len(mesh.data.shape_keys.key_blocks) == 87
    assert not (output / "character.blend").exists()
    report = json.loads((output / "report.json").read_text())
    assert report["preview_texture_size"] == 1024
    assert report["animations"] == report["missing_exports"] == report["missing_textures"] == []
    assert report["morph_targets"] == report["character_glb"]["morph_targets"] == 86
    assert report["preview_glb"]["morph_targets"] == 0
    assert report["glb_bytes"] > report["preview_glb_bytes"] > 0

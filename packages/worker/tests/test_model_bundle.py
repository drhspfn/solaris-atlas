import json
import struct
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from wuwa_story_worker.model_bundle import (
    animation_batch,
    animation_file,
    animation_sources,
    base_color_texture,
    build_animation_plan,
    file_sha256,
    indexed_gltf_references,
    inherited_base_color_texture,
    main,
    material_metadata_path,
    missing_animation_sources,
    package_path,
    preview_materials,
    preview_meshes,
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


def test_animation_preflight_reports_only_missing_owned_packages():
    owned = {"matches_skeleton": True, "source": "AM_Move_F.json"}
    sources = {
        "move": owned,
        "stand": {"matches_skeleton": True, "source": "Stand1.json"},
        "other": {"matches_skeleton": False, "source": "Other.json"},
    }
    assert missing_animation_sources(sources, {"stand"}) == [owned]
    assert missing_animation_sources(sources, {"move", "stand"}) == []


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


def test_material_color_inherits_from_explicit_parent_and_rejects_cycles(tmp_path):
    converted, sources = tmp_path / "converted", tmp_path / "source"
    relative = Path("Client/Content/Aki/Model")
    for root in (converted, sources):
        (root / relative).mkdir(parents=True)
    child, parent = converted / relative / "Fur.json", converted / relative / "Cloth.json"
    child.write_text('{"Textures": {}}')
    parent.write_text(json.dumps({"Textures": {"MainTex": {"ObjectPath": "/Game/Aki/Model/Cloth.Cloth"}}}))
    texture = parent.with_suffix(".png")
    texture.touch()
    def source(name, target):
        (sources / relative / f"{name}.json").write_text(json.dumps([{
            "Type": "MaterialInstanceConstant", "Name": name,
            "Properties": {"Parent": {"ObjectPath": f"Client/Content/Aki/Model/{target}.0"}},
        }]))
    source("Fur", "Cloth")
    assert inherited_base_color_texture({"Textures": {}}, child, converted, sources)[1] == texture
    parent.write_text('{"Textures": {}}')
    source("Cloth", "Fur")
    with pytest.raises(ValueError, match="Cyclic material parent"):
        inherited_base_color_texture({"Textures": {}}, child, converted, sources)


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


def test_preview_mesh_copies_keep_render_uv_and_restore_original_attributes_on_failure():
    class MeshData(SimpleNamespace):
        def copy(self):
            return deepcopy(self)

    original = MeshData(
        uv_layers=[
            SimpleNamespace(name="UV0", active_render=False),
            SimpleNamespace(name="UV1", active_render=True),
        ],
        color_attributes=["COL0"],
        shape_keys=SimpleNamespace(key_blocks=list(range(87))),
    )
    mesh = SimpleNamespace(data=original)
    with pytest.raises(RuntimeError, match="export failed"), preview_meshes([mesh]):
        assert mesh.data is not original
        assert [uv.name for uv in mesh.data.uv_layers] == ["UV1"]
        assert mesh.data.color_attributes == []
        assert len(mesh.data.shape_keys.key_blocks) == 87
        raise RuntimeError("export failed")
    assert mesh.data is original
    assert len(original.uv_layers) == 2 and original.color_attributes == ["COL0"]


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


@pytest.mark.parametrize(
    "rig_only,missing_texture,planned",
    [(False, False, False), (True, False, False), (True, True, False), (True, False, True)],
)
def test_rig_mode_skips_animation_catalog_scan_and_blend_but_full_mode_requires_clips(
    tmp_path,
    monkeypatch,
    rig_only,
    missing_texture,
    planned,
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
    plan = {"animations": [{"action": "Stand1"}], "animation_coverage": {"playable_clips": 1}}
    if planned:
        argv.extend(["--animation-plan", str(tmp_path / "plan.json")])
        monkeypatch.setattr("wuwa_story_worker.model_bundle.animation_sources", lambda *a: {})
        monkeypatch.setattr("wuwa_story_worker.model_bundle.build_animation_plan", lambda *a: plan)
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(sys, "path", sys.path.copy())
    armature = SimpleNamespace(
        type="ARMATURE",
        data=SimpleNamespace(bones=[SimpleNamespace(name="root"), SimpleNamespace(name="child")]),
    )

    class MeshData(SimpleNamespace):
        def copy(self):
            return deepcopy(self)

    mesh = SimpleNamespace(
        type="MESH",
        data=MeshData(
            vertices=[0, 1, 2],
            materials=[],
            shape_keys=SimpleNamespace(key_blocks=list(range(87))),
            uv_layers=[],
            color_attributes=[],
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
    assert report["missing_exports"] == report["missing_textures"] == []
    if planned:
        assert json.loads((tmp_path / "plan.json").read_text()) == plan
        assert report["animations"] == plan["animations"]
        assert report["animation_coverage"] == plan["animation_coverage"]
    else:
        assert report["animations"] == []
    assert report["morph_targets"] == report["character_glb"]["morph_targets"] == 86
    assert report["preview_glb"]["morph_targets"] == 0
    assert report["glb_bytes"] > report["preview_glb_bytes"] > 0


def plan_fixture(tmp_path, monkeypatch, *, animation=None, change=None):
    model = tmp_path / "Character.uemodel"
    model.write_bytes(b"model")
    converted = tmp_path / "converted"
    converted.mkdir()
    sources, decoded = {}, {}
    for index, (name, excluded) in enumerate(
        [
            ("Stand1", None),
            ("Stand1", None),
            ("Stand1.001", None),
            ("Additive", "additive"),
            ("Curve", "curves_only"),
            ("Pose", "single_frame"),
        ]
    ):
        path = converted / f"{index}.ueanim"
        path.write_bytes(str(index).encode())
        key = f"client/content/clip{index}"
        sources[key] = {
            "source": f"{index}.json",
            "name": name,
            "type": "AnimSequence",
            "skeleton": "/Game/Rig",
            "matches_skeleton": True,
        }
        dto = SimpleNamespace(
            metadata=SimpleNamespace(
                num_frames=1 if excluded == "single_frame" else 31,
                frames_per_second=30,
                additive_anim_type=int(excluded == "additive"),
            ),
            tracks=[] if excluded == "curves_only" else [SimpleNamespace(name="root")],
            curves=[1] if excluded == "curves_only" else [],
        )
        decoded[path] = (
            SimpleNamespace(object_path=key, object_name=name),
            dto,
        )
    if change:
        change(sources, decoded)
    calls = []

    def read(path, catalog):
        assert catalog is sources
        calls.append(path)
        return decoded[path]

    plan = build_animation_plan(
        model,
        converted,
        "/Game/Rig.Rig",
        sources,
        {"root"},
        read,
        animation,
    )
    assert len(calls) == 6
    return model, converted, plan


def test_plan_preflights_coverage_exclusions_and_names_globally(tmp_path, monkeypatch):
    model, converted, plan = plan_fixture(tmp_path, monkeypatch)
    assert plan["schema_version"] == 1
    assert plan["skeleton"] == "client/content/rig"
    assert plan["model_sha256"] == file_sha256(model)
    assert [clip["name"] for clip in plan["clips"]] == ["Stand1", "Stand1.001", "Stand1.001.001"]
    assert all(clip["sha256"] == file_sha256(converted / clip["file"]) for clip in plan["clips"])
    assert plan["animation_coverage"] == {
        "matching_sources": 6,
        "decoded_clips": 6,
        "playable_clips": 3,
        "additive_clips": 1,
        "single_frame_clips": 1,
        "curves_only_clips": 1,
        "excluded_clips": [
            {"source": "3.json", "reason": "additive"},
            {"source": "4.json", "reason": "curves_only"},
            {"source": "5.json", "reason": "single_frame"},
        ],
        "missing_exports": 0,
    }


def test_plan_optional_filter_retains_full_coverage_and_exclusions(tmp_path, monkeypatch):
    _, _, plan = plan_fixture(tmp_path, monkeypatch, animation="Stand1")
    assert len(plan["clips"]) == 2
    assert len(plan["animations"]) == 6
    assert plan["animation_coverage"]["missing_exports"] == 0


@pytest.mark.parametrize("decoded", [True, False])
def test_cinematic_body_clip_requires_decoded_tracks_and_reports_source(
    tmp_path, monkeypatch, decoded
):
    name = "M0260_Cam002_R2T1KatixiyaMd10011"

    def change(sources, animations):
        source = next(iter(sources.values()))
        source.update(name=name, source=f"M0260/Anim/{name}.json")
        header, dto = next(iter(animations.values()))
        header.object_name = name
        dto.metadata.num_frames = 71
        dto.metadata.frames_per_second = 30.428571701049805
        dto.tracks = [SimpleNamespace(name="root")] if decoded else []

    if decoded:
        _, _, plan = plan_fixture(tmp_path, monkeypatch, change=change)
        assert plan["clips"][0]["name"] == name
        assert plan["clips"][0]["included_in_glb"]
    else:
        with pytest.raises(ValueError) as error:
            plan_fixture(tmp_path, monkeypatch, change=change)
        assert f"M0260/Anim/{name}.json (0.ueanim)" in str(error.value)
        assert "no decoded tracks or curves" in str(error.value)
        assert isinstance(error.value.__cause__, ValueError)


def test_entrypoint_flushes_importer_stdout_before_traceback():
    script = """
import builtins
import runpy
import sys

original_import = builtins.__import__
def import_module(name, *args, **kwargs):
    if name == "bpy":
        print("[UEFORMAT] Importing buffered output\\n" * 1000, end="")
        raise RuntimeError("animation failure sentinel")
    return original_import(name, *args, **kwargs)
builtins.__import__ = import_module
path = sys.argv[1]
sys.argv = [path, "--", "--model", "model", "--converted", "converted",
            "--source-json", "source", "--skeleton", "rig",
            "--ueformat-plugin", "plugin", "--output", "output"]
runpy.run_path(path, run_name="__main__")
"""
    result = subprocess.run(
        [sys.executable, "-E", "-c", script, str(Path(main.__code__.co_filename).resolve())],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    assert result.returncode != 0
    assert result.stdout.index(b"Traceback (most recent call last)") > result.stdout.rindex(
        b"[UEFORMAT] Importing"
    )
    assert b"RuntimeError: animation failure sentinel" in result.stdout[-2048:]


@pytest.mark.parametrize("problem", ["missing", "decode", "bone"])
def test_plan_rejects_missing_exports_failed_decode_and_unknown_bones(
    tmp_path, monkeypatch, problem
):
    def change(sources, decoded):
        if problem == "missing":
            sources["missing"] = {"matches_skeleton": True, "source": "missing.json"}
        else:
            dto = next(iter(decoded.values()))[1]
            if problem == "decode":
                dto.tracks = []
            else:
                dto.tracks[0].name = "foreign"

    with pytest.raises(
        ValueError,
        match={
            "missing": "Missing matching",
            "decode": "no decoded",
            "bone": "unknown bones",
        }[problem],
    ):
        plan_fixture(tmp_path, monkeypatch, change=change)


def test_batches_select_exact_ranges_and_hash_only_selected_clips(tmp_path, monkeypatch):
    model, converted, plan = plan_fixture(tmp_path, monkeypatch)
    assert animation_batch(plan, model, converted, "/Game/Rig", 0, 2) == plan["clips"][:2]
    assert animation_batch(plan, model, converted, "/Game/Rig", 1, 2) == plan["clips"][2:]
    (converted / plan["clips"][0]["file"]).write_bytes(b"changed")
    assert animation_batch(plan, model, converted, "/Game/Rig", 1, 2) == plan["clips"][2:]
    with pytest.raises(ValueError, match="hash mismatch"):
        animation_batch(plan, model, converted, "/Game/Rig", 0, 2)


@pytest.mark.parametrize(
    "problem", ["schema", "model", "skeleton", "names", "index", "size", "empty"]
)
def test_batch_rejects_identity_and_selection_errors(tmp_path, monkeypatch, problem):
    model, converted, plan = plan_fixture(tmp_path, monkeypatch)
    index, size, skeleton = 0, 2, "/Game/Rig"
    if problem == "schema":
        plan["schema_version"] = 2
    elif problem == "model":
        model.write_bytes(b"different model")
    elif problem == "skeleton":
        skeleton = "/Game/OtherRig"
    elif problem == "names":
        plan["clips"][1]["name"] = plan["clips"][0]["name"]
    elif problem == "index":
        index = -1
    elif problem == "size":
        size = 51
    elif problem == "empty":
        index = 2
    with pytest.raises(ValueError):
        animation_batch(plan, model, converted, skeleton, index, size)


@pytest.mark.parametrize(
    "relative",
    [
        "../outside.ueanim",
        "/outside.ueanim",
        "C:/outside.ueanim",
        "C:outside.ueanim",
        "folder/../../outside.ueanim",
        "folder\\outside.ueanim",
        "bad.json",
        "",
        "missing.ueanim",
    ],
)
def test_animation_paths_reject_escape_absolute_wrong_extension_and_missing(tmp_path, relative):
    with pytest.raises(ValueError):
        animation_file(tmp_path, relative)


def test_animation_paths_resolve_links_before_containment_check(tmp_path):
    converted = tmp_path / "converted"
    converted.mkdir()
    outside = tmp_path / "outside.ueanim"
    outside.touch()
    try:
        (converted / "linked.ueanim").symlink_to(outside)
    except OSError:
        pytest.skip("Creating symlinks requires Windows developer mode")
    with pytest.raises(ValueError, match="under converted"):
        animation_file(converted, "linked.ueanim")


@pytest.mark.parametrize(
    "flags",
    [
        ["--animation-plan", "plan.json"],
        ["--with-animations", "--animation-plan", "plan.json"],
        ["--animation-batch", "0"],
        ["--rig-only", "--animation-plan", "plan.json", "--animation-batch", "0"],
        ["--with-animations", "--animation-plan", "plan.json", "--animation-batch", "0"],
        ["--animation-plan", "plan.json", "--animation-batch", "-1"],
        ["--animation-plan", "plan.json", "--animation-batch", "0", "--animation", "Stand1"],
        ["--animation-plan", "plan.json", "--animation-batch", "0", "--animation-batch-size", "0"],
        ["--animation-plan", "plan.json", "--animation-batch", "0", "--animation-batch-size", "51"],
        ["--rig-only", "--animation-batch-size", "20"],
    ],
)
def test_invalid_process_modes_fail_before_blender_import(monkeypatch, flags):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "blender",
            "--",
            "--model",
            "model",
            "--converted",
            "converted",
            "--source-json",
            "source",
            "--skeleton",
            "rig",
            "--ueformat-plugin",
            "plugin",
            "--output",
            "output",
            *flags,
        ],
    )
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2


def test_batch_main_imports_only_skeleton_and_selected_actions_without_source_scan(
    tmp_path, monkeypatch
):
    model, converted, plan = plan_fixture(tmp_path, monkeypatch)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))
    output = tmp_path / "output"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "blender",
            "--",
            "--model",
            str(model),
            "--converted",
            str(converted),
            "--source-json",
            str(tmp_path / "absent-source"),
            "--skeleton",
            "/Game/Rig",
            "--ueformat-plugin",
            str(tmp_path),
            "--output",
            str(output),
            "--animation-plan",
            str(plan_path),
            "--animation-batch",
            "1",
            "--animation-batch-size",
            "2",
        ],
    )
    monkeypatch.setattr(sys, "path", sys.path.copy())
    calls, actions, reset = [], [], []
    armature = SimpleNamespace(
        type="ARMATURE",
        data=SimpleNamespace(bones=[SimpleNamespace(name="root")]),
        pose=SimpleNamespace(
            bones=[
                SimpleNamespace(
                    matrix_basis=SimpleNamespace(identity=lambda: reset.append(True)),
                )
            ]
        ),
        animation_data=SimpleNamespace(action="old"),
        select_set=lambda selected: None,
    )
    model_dto = SimpleNamespace(lods=["geometry"], collisions=["collision"])

    class Archive:
        def __init__(self, raw):
            self.raw = raw

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

    def header(archive):
        calls.append(archive.raw)
        return SimpleNamespace(
            object_name="Character", object_path="/Game/Clip", file_version=1
        ), archive

    def body(header, archive):
        if archive.raw == b"model":
            return model_dto
        return SimpleNamespace(
            metadata=SimpleNamespace(num_frames=31, frames_per_second=30, additive_anim_type=0),
            tracks=[
                SimpleNamespace(name="root", position_keys=[], rotation_keys=[], scale_keys=[])
            ],
            curves=[],
        )

    def create_model(dto, options, name):
        assert not dto.lods and not dto.collisions
        return armature

    class Action(dict):
        def __init__(self, name):
            self.name = name

    def create_anim(dto, options, name):
        action = Action(name)
        actions.append(action)
        armature.animation_data.action = action
        return action

    def export(**kwargs):
        assert armature.animation_data.action is None and reset == [True]
        assert kwargs["use_selection"] and kwargs["export_animation_mode"] == "ACTIONS"
        assert not kwargs["export_morph"]
        Path(kwargs["filepath"]).touch()
        return {"FINISHED"}

    bpy = SimpleNamespace(
        context=SimpleNamespace(
            scene=SimpleNamespace(render=SimpleNamespace(fps=0)),
            view_layer=SimpleNamespace(objects=SimpleNamespace(active=None), update=lambda: None),
        ),
        ops=SimpleNamespace(
            object=SimpleNamespace(select_all=lambda **kw: None, delete=lambda **kw: None),
            export_scene=SimpleNamespace(gltf=export),
        ),
    )
    summary = {"animations": 1, "clips": [{"name": plan["clips"][2]["name"]}]}
    modules = {
        "bpy": bpy,
        "io_scene_ueformat.importer.archive.reader": SimpleNamespace(FArchiveReader=Archive),
        "io_scene_ueformat.importer.create.anim": SimpleNamespace(create_anim=create_anim),
        "io_scene_ueformat.importer.create.model": SimpleNamespace(create_model=create_model),
        "io_scene_ueformat.importer.create.space": SimpleNamespace(
            to_blender_space=lambda *a: None
        ),
        "io_scene_ueformat.importer.import_context": SimpleNamespace(
            UEFormatImport=None,
            parse_body=body,
            parse_header=header,
        ),
        "io_scene_ueformat.options": SimpleNamespace(
            UEAnimOptions=lambda **kw: kw,
            UEModelOptions=lambda: None,
        ),
        "io_scene_gltf2.blender.exp.exporter": SimpleNamespace(
            GlTF2Exporter=type(
                "Exporter",
                (),
                {"_GlTF2Exporter__append_unique_and_get_index": staticmethod(legacy_index)},
            )
        ),
        "io_scene_gltf2.io.com.gltf2_io": SimpleNamespace(
            Accessor=type("Accessor", (), {}),
            BufferView=type("BufferView", (), {}),
        ),
        "model_animations": SimpleNamespace(animation_summary=lambda path, bones: summary),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    def forbid_scan(*args):
        raise AssertionError("Batch must not scan source JSON, animations or material metadata")

    monkeypatch.setattr(Path, "rglob", forbid_scan)
    main()
    assert calls == [b"model", b"2"]
    assert [action.name for action in actions] == [plan["clips"][2]["name"]]
    assert (output / "animation-parts/part-0001.glb").exists()
    assert json.loads((output / "animation-parts/part-0001.json").read_text()) == summary
    assert not (output / "report.json").exists()


def test_plan_skips_foreign_skeleton_before_dto_validation(tmp_path):
    model = tmp_path / "model.uemodel"
    model.touch()
    converted = tmp_path / "converted"
    converted.mkdir()
    (converted / "owned.ueanim").touch()
    (converted / "foreign.ueanim").touch()
    sources = {
        "owned": {"matches_skeleton": True, "source": "owned.json", "name": "Stand1"},
        "foreign": {"matches_skeleton": False, "source": "foreign.json"},
    }

    def read(path, sources):
        header = SimpleNamespace(object_path=path.stem, object_name="Stand1")
        # A foreign skeleton's body must not be needed for plan coverage.
        dto = (
            None
            if path.stem == "foreign"
            else SimpleNamespace(
                metadata=SimpleNamespace(num_frames=31, frames_per_second=30, additive_anim_type=0),
                tracks=[SimpleNamespace(name="root")],
                curves=[],
            )
        )
        return header, dto

    plan = build_animation_plan(model, converted, "rig", sources, {"root"}, read)
    assert [clip["file"] for clip in plan["clips"]] == ["owned.ueanim"]
    assert plan["animation_coverage"]["matching_sources"] == 1

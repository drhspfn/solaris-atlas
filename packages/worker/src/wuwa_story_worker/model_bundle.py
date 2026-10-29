"""Build a local, inspectable GLB from CUE4Parse exports using Blender.

Run with Blender's --python, not the worker interpreter. Game shaders are not
portable: this preview deliberately uses exported base-color textures only.
"""

import argparse
import hashlib
import json
import struct
import sys
from contextlib import contextmanager
from copy import copy
from pathlib import Path


def package_path(value: str) -> str:
    value = value.replace("\\", "/").split(".", 1)[0]
    if value.startswith("/Game/"):
        value = "Client/Content/" + value[6:]
    return value.casefold()


def animation_sources(root: Path, skeleton: str) -> dict[str, dict]:
    result = {}
    for path in sorted(root.rglob("*.json")):
        for row in json.loads(path.read_text(encoding="utf-8-sig")):
            if row.get("Type") not in {"AnimSequence", "AnimMontage"}:
                continue
            properties = row.get("Properties", {})
            reference = properties.get("Skeleton", {}).get("ObjectPath", "")
            key = package_path(path.relative_to(root).with_suffix("").as_posix())
            result[key] = {
                "source": path.relative_to(root).as_posix(),
                "name": row["Name"],
                "type": row["Type"],
                "skeleton": reference,
                "matches_skeleton": package_path(reference) == package_path(skeleton),
            }
    return result


def retime_tracks(dto, fps=30):
    """Preserve constant channels without Blender's single-key baking fallback."""
    end = (dto.metadata.num_frames - 1) * fps / dto.metadata.frames_per_second
    for track in dto.tracks:
        for keys in (track.position_keys, track.rotation_keys, track.scale_keys):
            for key in keys:
                key.frame *= fps / dto.metadata.frames_per_second
            if len(keys) == 1 and end > keys[0].frame:
                last = copy(keys[0])
                last.frame = end
                keys.append(last)


def missing_animation_sources(sources, exported_keys):
    return [
        row for key, row in sources.items() if row["matches_skeleton"] and key not in exported_keys
    ]


def animation_exclusion(dto, *, require_decoded=False):
    """Distinguish intentional non-body clips from failed animation decoding."""
    if not dto.metadata or not dto.tracks:
        if require_decoded and (not dto.metadata or not dto.curves):
            raise ValueError("Animation has no decoded tracks or curves")
        return "curves_only" if dto.curves else "no_decoded_tracks"
    if dto.metadata.additive_anim_type:
        return "additive"
    if dto.metadata.num_frames < 2:
        return "single_frame"
    return None


def file_sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def animation_file(converted, relative):
    # Check both path dialects, even when unit tests run outside Windows.
    from pathlib import PureWindowsPath

    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError("Animation file must be a relative converted path")
    path = Path(relative)
    if path.is_absolute() or PureWindowsPath(relative).drive or ".." in path.parts:
        raise ValueError("Animation file must stay under converted")
    resolved = (converted / path).resolve()
    if not resolved.is_relative_to(converted.resolve()) or path.suffix != ".ueanim":
        raise ValueError("Animation file must stay under converted and end in .ueanim")
    if not resolved.is_file():
        raise ValueError(f"Animation file missing: {relative}")
    return resolved


def validate_animation_bones(dto, bone_names):
    unknown = {track.name.casefold() for track in dto.tracks} - {
        name.casefold() for name in bone_names
    }
    if unknown:
        raise ValueError(f"Animation targets unknown bones: {sorted(unknown)}")


def build_animation_plan(
    model, converted, skeleton, sources, bone_names, read_animation, animation=None, morph_names=()
):
    """Decode one DTO at a time; never allocate Blender actions during preflight."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from model_facial import FORMAT, facial_clip

    rows, clips, seen, names, facial = [], [], set(), set(), []
    for path in sorted(converted.rglob("*.ueanim")):
        header, dto = read_animation(path, sources)
        key = package_path(header.object_path)
        source = sources.get(key)
        if not source or not source["matches_skeleton"]:
            continue
        seen.add(key)
        metadata = dto.metadata
        row = {
            **source,
            "file": path.relative_to(converted).as_posix(),
            "frames": metadata.num_frames if metadata else 0,
            "fps": metadata.frames_per_second if metadata else 0,
            "tracks": len(dto.tracks),
            "curves": len(dto.curves),
            "additive": int(metadata.additive_anim_type) if metadata else None,
            "included_in_glb": False,
        }
        rows.append(row)
        try:
            excluded = animation_exclusion(dto, require_decoded=True)
        except ValueError as exc:
            raise ValueError(f"{source['source']} ({row['file']}): {exc}") from exc
        if excluded:
            row["excluded_reason"] = excluded
            # Facial curves are weights, independent of additive bone transforms.
            face = facial_clip(dto, f"Expression {len(facial) + 1}: {header.object_name}", morph_names)
            if face is not None:
                facial.append(face)
            del dto
            continue
        validate_animation_bones(dto, bone_names)
        if animation and header.object_name != animation:
            del dto
            continue
        # Blender limits ID names to 63 UTF-8 bytes. Reserve room for suffixes.
        base = header.object_name.encode("utf-8")[:48].decode("utf-8", errors="ignore")
        name, suffix = base, 1
        while name in names:
            name = f"{base}.{suffix:03d}"
            suffix += 1
        names.add(name)
        row.update(action=name, included_in_glb=True)
        clips.append({**row, "name": name, "sha256": file_sha256(path)})
        face = facial_clip(dto, name, morph_names, body_clip=name)
        if face is not None:
            facial.append(face)
        del dto
    missing = missing_animation_sources(sources, seen)
    if missing:
        raise ValueError(f"Missing matching animation exports: {missing}")
    if not clips:
        raise ValueError("No verified, non-additive animation tracks available")
    return {
        "schema_version": 1,
        "skeleton": package_path(skeleton),
        "model_sha256": file_sha256(model),
        "bones": sorted(bone_names),
        "clips": clips,
        "animations": rows,
        "facial": {"format": FORMAT, "clips": facial},
        "animation_coverage": {
            "matching_sources": sum(row["matches_skeleton"] for row in sources.values()),
            "decoded_clips": len(rows),
            "playable_clips": len(clips),
            "additive_clips": sum(bool(row["additive"]) for row in rows),
            "single_frame_clips": sum(row["frames"] < 2 for row in rows),
            "curves_only_clips": sum(row.get("excluded_reason") == "curves_only" for row in rows),
            "excluded_clips": [
                {"source": row["source"], "reason": row["excluded_reason"]}
                for row in rows
                if "excluded_reason" in row
            ],
            "missing_exports": 0,
        },
    }


def animation_batch(plan, model, converted, skeleton, index, size):
    if type(index) is not int or index < 0 or type(size) is not int or not 1 <= size <= 50:
        raise ValueError("Animation batch index must be nonnegative and size must be 1..50")
    if (
        plan.get("schema_version") != 1
        or plan.get("skeleton") != package_path(skeleton)
        or plan.get("model_sha256") != file_sha256(model)
    ):
        raise ValueError("Animation plan identity does not match model/skeleton/schema")
    clips = plan["clips"]
    names = [clip["name"] for clip in clips]
    if any(not name for name in names) or len(set(names)) != len(names):
        raise ValueError("Animation plan action names must be globally unique")
    for clip in clips:
        animation_file(converted, clip["file"])
    selected = clips[index * size : (index + 1) * size]
    if not selected:
        raise ValueError("Animation batch index is out of range")
    for clip in selected:
        if file_sha256(animation_file(converted, clip["file"])) != clip["sha256"]:
            raise ValueError(f"Animation file hash mismatch: {clip['file']}")
    return selected


@contextmanager
def indexed_gltf_references(exporter_class, identity_types):
    """Index Blender 4.5's append-only accessor/view lists during this export.

    These generated glTF classes use object identity for equality. Other root
    types retain Blender's original value/equality behavior. No installed file
    is modified, and the original static method is restored even on failure.
    """
    name = "_GlTF2Exporter__append_unique_and_get_index"
    descriptor = vars(exporter_class)[name]
    original = getattr(exporter_class, name)
    if original.__code__.co_names != ("index", "len", "append") or any(
        cls.__eq__ is not object.__eq__ or cls.__hash__ is not object.__hash__
        for cls in identity_types
    ):
        raise RuntimeError("Pinned Blender glTF reference contract changed")
    caches = {}

    def append_reference(target, obj):
        if type(obj) not in identity_types:
            return original(target, obj)
        cached = caches.get(id(target))
        if cached is None or cached[1] != len(target):
            indices = {}
            for index, value in enumerate(target):
                indices.setdefault(value, index)
            cached = [target, len(target), indices]
            caches[id(target)] = cached
        indices = cached[2]
        if obj not in indices:
            indices[obj] = len(target)
            target.append(obj)
            cached[1] = len(target)
        return indices[obj]

    setattr(exporter_class, name, staticmethod(append_reference))
    try:
        yield
    finally:
        setattr(exporter_class, name, descriptor)


def material_metadata_path(name: str, model: Path, files: list[Path]) -> Path | None:
    matches = [path for path in files if path.stem == name]
    local = model.parent / f"{name}.json"
    if local in matches:
        return local
    if len(matches) > 1:
        raise ValueError(f"Ambiguous material metadata: {name}")
    return next(iter(matches), None)


def base_color_texture(metadata: dict, converted: Path) -> tuple[str | None, Path | None]:
    textures = metadata.get("Textures", {})
    reference = next(
        (textures[key].get("ObjectPath") for key in ("MainTex", "BaseColor") if key in textures),
        None,
    )
    if not reference:
        return None, None
    relative = reference.replace("\\", "/").split(".", 1)[0]
    if relative.startswith("/Game/"):
        relative = "Client/Content/" + relative[6:]
    candidate = converted / (relative + ".png")
    if candidate.resolve().is_relative_to(converted.resolve()) and candidate.is_file():
        return reference, candidate
    return reference, None


def inherited_base_color_texture(metadata: dict, path: Path | None, converted: Path, source_json: Path):
    """Follow explicit material-instance parents when a child has no color texture."""
    visited = set()
    while True:
        reference, texture = base_color_texture(metadata, converted)
        if reference or path is None:
            return reference, texture
        relative = path.relative_to(converted)
        if relative in visited:
            raise ValueError("Cyclic material parent chain")
        visited.add(relative)
        source = source_json / relative
        if not source.is_file():
            return None, None
        rows = json.loads(source.read_text(encoding="utf-8-sig"))
        parents = [
            row.get("Properties", {}).get("Parent", {}).get("ObjectPath")
            for row in rows
            if row.get("Type") == "MaterialInstanceConstant" and row.get("Name") == path.stem
        ]
        if len(parents) != 1 or not parents[0]:
            return None, None
        parent = parents[0].replace("\\", "/").split(".", 1)[0]
        if parent.startswith("/Game/"):
            parent = "Client/Content/" + parent[6:]
        if not parent.startswith("Client/Content/"):
            return None, None
        candidate = converted / (parent + ".json")
        if not candidate.resolve().is_relative_to(converted.resolve()) or not candidate.is_file():
            return None, None
        path = candidate
        metadata = json.loads(path.read_text(encoding="utf-8-sig"))


def opacity_mask_cutoff(metadata: dict) -> float:
    """Use the cooked Unreal mask cutoff, or its engine default."""
    cutoff = metadata.get("Properties", {}).get("BasePropertyOverrides", {}).get(
        "OpacityMaskClipValue", 1 / 3
    )
    if type(cutoff) not in (int, float) or not 0 <= cutoff <= 1:
        raise ValueError("Invalid opacity mask cutoff")
    return float(cutoff)


def effect_space_only(metadata: dict) -> bool:
    """Effect-space write passes do not render as ordinary character surfaces."""
    overrides = metadata.get("Properties", {}).get("BasePropertyOverrides", {})
    return overrides.get("bEffectSpaceWrite") is True and "MaskTex" in metadata.get("Textures", {})


def base_color_alpha_is_opacity(material_name: str, metadata: dict) -> bool:
    """The base-color fallback must not treat Denia's skin shader data as holes."""
    if material_name in {"MI_R2T1DaniyaMd10011Up01", "MI_R2T1DaniyaMd10011Up02"} and (
        metadata.get("Switches", {}).get("UseOnlySkinRampColor") is True
    ):
        # Verified against both cooked skin passes and before/after renders.
        # Their diffuse alpha clips the neck; retain cloth/hair mask behavior.
        return False
    return metadata.get("BlendMode") in {1, 2}


def same_rest_skeleton(primary, extra) -> bool:
    primary_bones = {bone.name: bone for bone in primary.data.bones}
    extra_bones = {bone.name: bone for bone in extra.data.bones}
    if primary_bones.keys() != extra_bones.keys():
        return False
    for name, bone in primary_bones.items():
        other = extra_bones[name]
        if (bone.parent.name if bone.parent else None) != (
            other.parent.name if other.parent else None
        ) or any(
            abs(a - b) > 1e-4
            for row_a, row_b in zip(bone.matrix_local, other.matrix_local, strict=True)
            for a, b in zip(row_a, row_b, strict=True)
        ):
            return False
    return True


def canonical_animation_rig(armature):
    """Keep animation-only container/skin names independent of a form's mesh."""
    name = "SolarisAnimationRig"
    bone_names = {bone.name for bone in armature.data.bones}
    while name in bone_names:
        name += "_"
    armature.name = name
    armature.data.name = name


@contextmanager
def preview_meshes(meshes):
    """The generated base-color shaders use only the render UV and no colors."""
    originals = []
    try:
        for mesh in meshes:
            original = mesh.data
            originals.append((mesh, original))
            mesh.data = original.copy()
            uv_name = next(
                (uv.name for uv in mesh.data.uv_layers if uv.active_render),
                mesh.data.uv_layers[0].name if mesh.data.uv_layers else None,
            )
            for index in reversed(range(len(mesh.data.uv_layers))):
                uv = mesh.data.uv_layers[index]
                if uv.name != uv_name:
                    mesh.data.uv_layers.remove(uv)
            while mesh.data.color_attributes:
                mesh.data.color_attributes.remove(mesh.data.color_attributes[0])
        yield
    finally:
        for mesh, original in originals:
            mesh.data = original


@contextmanager
def preview_materials(meshes, texture_size):
    """Resize isolated images after the canonical export, restoring all slots."""
    originals, materials, images = [], {}, {}
    try:
        for mesh in meshes:
            for index, original in enumerate(mesh.data.materials):
                if original is None:
                    continue
                if original not in materials:
                    material = original.copy()
                    materials[original] = material
                    for node in material.node_tree.nodes:
                        if node.type != "TEX_IMAGE" or node.image is None:
                            continue
                        source = node.image
                        if source not in images:
                            image = source.copy()
                            images[source] = image
                            width, height = source.size
                            ratio = min(1, texture_size / max(width, height))
                            if ratio < 1:
                                image.scale(
                                    max(1, round(width * ratio)), max(1, round(height * ratio))
                                )
                            image.file_format = "PNG"
                            image.pack()
                        node.image = images[source]
                originals.append((mesh, index, original))
                mesh.data.materials[index] = materials[original]
        yield
    finally:
        for mesh, index, original in originals:
            mesh.data.materials[index] = original


def rig_glb_stats(path: Path, bones: int, morph_targets: int, textures: int) -> dict:
    """Check the actual GLB contract before reporting a successful rig export."""
    with path.open("rb") as stream:
        magic, version, length = struct.unpack("<4sII", stream.read(12))
        size, kind = struct.unpack("<I4s", stream.read(8))
        if magic != b"glTF" or version != 2 or length != path.stat().st_size or kind != b"JSON":
            raise ValueError(f"Invalid GLB header: {path}")
        document = json.loads(stream.read(size))
    primitives = [p for mesh in document.get("meshes", []) for p in mesh["primitives"]]
    stats = {
        "bytes": length,
        "animations": len(document.get("animations", [])),
        "joints": len({joint for skin in document.get("skins", []) for joint in skin["joints"]}),
        "morph_targets": sum(
            max((len(p.get("targets", [])) for p in mesh["primitives"]), default=0)
            for mesh in document.get("meshes", [])
        ),
        "vertices": sum(
            document["accessors"][p["attributes"]["POSITION"]]["count"] for p in primitives
        ),
        "textures": len(document.get("images", [])),
    }
    if stats["animations"] or stats["joints"] != bones or stats["morph_targets"] != morph_targets:
        raise ValueError(f"Rig GLB lost joints/morphs or contains animations: {stats}")
    if (
        stats["textures"] != textures
        or any("bufferView" not in image or "uri" in image for image in document.get("images", []))
        or any("uri" in buffer for buffer in document.get("buffers", []))
    ):
        raise ValueError(f"Rig GLB textures must be present and embedded: {stats}")
    if not primitives or any("JOINTS_0" not in p["attributes"] for p in primitives):
        raise ValueError("Rig GLB has no skinned geometry")
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--extra-model", action="append", type=Path, default=[])
    parser.add_argument("--converted", type=Path, required=True)
    parser.add_argument("--source-json", type=Path, required=True)
    parser.add_argument("--skeleton", required=True)
    parser.add_argument("--ueformat-plugin", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--animation", help="Optional source name for a small verification bundle")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--with-animations",
        action="store_true",
        help="Export static rigs plus a separate skeleton/actions-only GLB",
    )
    modes.add_argument(
        "--rig-only",
        action="store_true",
        help="Export a rig and compact preview without animations or a blend file",
    )
    parser.add_argument(
        "--preview-texture-size",
        type=int,
        default=1024,
        help="Maximum preview texture dimension (rig-only mode)",
    )
    parser.add_argument("--animation-plan", type=Path)
    parser.add_argument("--animation-batch", type=int)
    parser.add_argument("--animation-batch-size", type=int, default=None)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    if args.preview_texture_size < 1:
        parser.error("--preview-texture-size must be positive")
    batch_mode = args.animation_batch is not None
    if args.animation_batch_size is not None and not 1 <= args.animation_batch_size <= 50:
        parser.error("--animation-batch-size must be 1..50")
    if batch_mode and args.animation_batch < 0:
        parser.error("--animation-batch must be nonnegative")
    if batch_mode and (
        not args.animation_plan or args.rig_only or args.with_animations or args.animation
    ):
        parser.error(
            "--animation-batch requires --animation-plan and no other animation mode/filter"
        )
    if args.animation_batch_size is not None and not batch_mode:
        parser.error("--animation-batch-size requires --animation-batch")
    if args.animation_plan and not batch_mode and not args.rig_only:
        parser.error("Creating --animation-plan requires --rig-only")
    rig_mode = args.rig_only or args.with_animations
    import bpy

    sys.path.insert(0, str(args.ueformat_plugin.resolve()))
    from io_scene_ueformat.importer.archive.reader import FArchiveReader
    from io_scene_ueformat.importer.create.anim import create_anim
    from io_scene_ueformat.importer.create.space import to_blender_space
    from io_scene_ueformat.importer.import_context import UEFormatImport, parse_body, parse_header
    from io_scene_ueformat.options import UEAnimOptions, UEModelOptions

    selected_clips = None
    if batch_mode:
        plan = json.loads(args.animation_plan.read_text(encoding="utf-8"))
        selected_clips = animation_batch(
            plan,
            args.model,
            args.converted,
            args.skeleton,
            args.animation_batch,
            args.animation_batch_size or 20,
        )

    def read_animation(path, sources=None):
        with FArchiveReader(path.read_bytes()) as archive:
            header, body = parse_header(archive)
            source = sources.get(package_path(header.object_path)) if sources is not None else None
            dto = (
                parse_body(header, body)
                if sources is None or (source and source["matches_skeleton"])
                else None
            )
        return header, dto

    args.output.mkdir(parents=True, exist_ok=True)
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    if batch_mode:
        # UEFormat has no skeleton-only import option: discard geometry before
        # creation, then use the same space conversion and skeleton creator.
        from io_scene_ueformat.importer.create.model import create_model

        header, dto = read_animation(args.model)
        dto.lods.clear()
        dto.collisions.clear()
        to_blender_space(dto, header.file_version, 0.01)
        armature = create_model(dto, UEModelOptions(), header.object_name)
        del dto
    else:
        armature = UEFormatImport(UEModelOptions()).import_file(args.model)
    if armature is None or armature.type != "ARMATURE":
        raise ValueError("Export has no skinned armature")
    if not batch_mode:
        for extra_model in args.extra_model:
            extra = UEFormatImport(UEModelOptions()).import_file(extra_model)
            if extra is None or extra.type != "ARMATURE":
                raise ValueError("Extra model has no skinned armature")
            if not same_rest_skeleton(armature, extra):
                raise ValueError("Extra model skeleton differs from the primary rig")
            for obj in list(extra.children):
                obj.parent = armature
                for modifier in obj.modifiers:
                    if modifier.type == "ARMATURE" and modifier.object == extra:
                        modifier.object = armature
            bpy.data.objects.remove(extra, do_unlink=True)
    if batch_mode:
        bones = {bone.name for bone in armature.data.bones}
        if set(plan["bones"]) != bones:
            raise ValueError("Animation plan bones do not match imported rig")
        bpy.context.scene.render.fps = 30
        actions = []
        for clip in selected_clips:
            header, dto = read_animation(animation_file(args.converted, clip["file"]))
            if animation_exclusion(dto, require_decoded=True):
                raise ValueError("Planned animation is no longer playable")
            validate_animation_bones(dto, bones)
            to_blender_space(dto, header.file_version, 0.01)
            retime_tracks(dto)
            action = create_anim(
                dto, UEAnimOptions(override_skeleton=armature, import_curves=False), clip["name"]
            )
            if action.name != clip["name"]:
                raise ValueError("Blender changed planned action name")
            action.use_fake_user = True
            action["source_path"] = header.object_path
            actions.append(action)
            del dto
        # The last imported action may leave an evaluated pose behind. Node
        # transforms must describe the same rest hierarchy in every process.
        armature.animation_data.action = None
        for bone in armature.pose.bones:
            bone.matrix_basis.identity()
        bpy.context.view_layer.update()
        bpy.ops.object.select_all(action="DESELECT")
        armature.select_set(True)
        bpy.context.view_layer.objects.active = armature
        part = args.output / "animation-parts" / f"part-{args.animation_batch:04d}.glb"
        part.parent.mkdir(parents=True, exist_ok=True)
        from io_scene_gltf2.blender.exp.exporter import GlTF2Exporter
        from io_scene_gltf2.io.com.gltf2_io import Accessor, BufferView

        canonical_animation_rig(armature)
        with indexed_gltf_references(GlTF2Exporter, (Accessor, BufferView)):
            result = bpy.ops.export_scene.gltf(
                filepath=str(part.resolve()),
                export_format="GLB",
                use_selection=True,
                export_animations=True,
                export_animation_mode="ACTIONS",
                export_force_sampling=False,
                export_morph=False,
                export_morph_animation=False,
                export_skins=True,
                export_anim_single_armature=True,
                export_optimize_disable_viewport=True,
            )
        if "FINISHED" not in result:
            raise RuntimeError(f"GLB export failed: {part}")
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from model_animations import animation_summary

        summary = animation_summary(part, bones)
        if summary["animations"] != len(selected_clips) or {
            clip["name"] for clip in summary["clips"]
        } != {clip["name"] for clip in selected_clips}:
            raise ValueError("Animation batch export lost or renamed actions")
        part.with_suffix(".json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps(summary))
        return
    meshes = [obj for obj in bpy.data.objects if obj.type == "MESH"]
    sources = (
        {}
        if args.rig_only and not args.animation_plan
        else animation_sources(args.source_json, args.skeleton)
    )
    report = {
        "model_source": str(args.model.resolve()),
        "model_sha256": hashlib.sha256(args.model.read_bytes()).hexdigest(),
        "vertices": sum(len(obj.data.vertices) for obj in meshes),
        "bones": len(armature.data.bones),
        "materials": [],
        "animations": [],
        "missing_exports": [],
        "missing_textures": [],
        "appearance": "Base-color preview; original game shaders and physics are not reproduced.",
    }
    if args.animation_plan:
        plan = build_animation_plan(
            args.model,
            args.converted,
            args.skeleton,
            sources,
            {bone.name for bone in armature.data.bones},
            read_animation,
            args.animation,
            morph_names={
                key.name for mesh in meshes if mesh.data.shape_keys
                for key in list(mesh.data.shape_keys.key_blocks)[1:]
            },
        )
        args.animation_plan.parent.mkdir(parents=True, exist_ok=True)
        args.animation_plan.write_text(json.dumps(plan, indent=2), encoding="utf-8")
        report["animations"] = plan["animations"]
        report["animation_coverage"] = plan["animation_coverage"]
        (args.output / "facial.json").write_text(
            json.dumps(plan["facial"], separators=(",", ":")), encoding="utf-8"
        )
    metadata_files = list(args.converted.rglob("*.json"))
    material_files = {path.stem: path for path in metadata_files}
    # An effect-space write is a non-color game render pass, not visible geometry.
    for mesh in meshes:
        skipped = set()
        for index, material in enumerate(mesh.data.materials):
            path = material_metadata_path(material.name, args.model, metadata_files) if material else None
            metadata = json.loads(path.read_text(encoding="utf-8-sig")) if path else {}
            if args.extra_model and effect_space_only(metadata):
                skipped.add(index)
        if skipped:
            import bmesh

            bm = bmesh.new()
            bm.from_mesh(mesh.data)
            bmesh.ops.delete(bm, geom=[face for face in bm.faces if face.material_index in skipped], context="FACES")
            bm.to_mesh(mesh.data)
            bm.free()
    for material in sorted(
        {material for mesh in meshes for material in mesh.data.materials if material},
        key=lambda material: material.name,
    ):
        path = (
            material_metadata_path(material.name, args.model, metadata_files)
            if rig_mode
            else material_files.get(material.name)
        )
        metadata = json.loads(path.read_text(encoding="utf-8-sig")) if path else {}
        if args.extra_model and effect_space_only(metadata):
            material.use_nodes = True
            continue
        reference, texture = inherited_base_color_texture(metadata, path, args.converted, args.source_json)
        if texture is None:
            report["missing_textures"].append({"material": material.name, "reference": reference})
        material.use_nodes = True
        shader = material.node_tree.nodes.get("Principled BSDF")
        shader.inputs["Roughness"].default_value = 0.85
        shader.inputs["Metallic"].default_value = 0
        if texture:
            image = bpy.data.images.load(str(texture.resolve()), check_existing=True)
            image.pack()
            node = material.node_tree.nodes.new("ShaderNodeTexImage")
            node.image = image
            material.node_tree.links.new(node.outputs["Color"], shader.inputs["Base Color"])
            blend_mode = metadata.get("BlendMode")
            if base_color_alpha_is_opacity(path.stem if path else material.name, metadata):
                alpha = node.outputs["Alpha"]
                if blend_mode == 1:
                    clip = material.node_tree.nodes.new("ShaderNodeMath")
                    clip.operation = "GREATER_THAN"
                    clip.inputs[1].default_value = opacity_mask_cutoff(metadata)
                    material.node_tree.links.new(alpha, clip.inputs[0])
                    alpha = clip.outputs[0]
                material.node_tree.links.new(alpha, shader.inputs["Alpha"])
                material.surface_render_method = "DITHERED"
        report["materials"].append(
            {"name": material.name, "texture": str(texture) if texture else None}
        )
    if rig_mode and report["missing_textures"]:
        (args.output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        raise ValueError(f"Missing required base-color textures: {report['missing_textures']}")

    actions = []

    def import_actions():
        seen_sources = set()
        bpy.context.scene.render.fps = 30
        paths = [] if args.rig_only else sorted(args.converted.rglob("*.ueanim"))
        if args.with_animations:
            # Check coverage before allocating hundreds of Blender actions.
            exported_keys = set()
            for path in paths:
                with FArchiveReader(path.read_bytes()) as archive:
                    header, _ = parse_header(archive)
                    exported_keys.add(package_path(header.object_path))
            report["missing_exports"] = missing_animation_sources(sources, exported_keys)
            if report["missing_exports"]:
                raise ValueError(f"Missing matching animation exports: {report['missing_exports']}")
        for path in paths:
            with FArchiveReader(path.read_bytes()) as archive:
                header, body = parse_header(archive)
                source_key = package_path(header.object_path)
                source = sources.get(source_key)
                if not source or not source["matches_skeleton"]:
                    continue
                seen_sources.add(source_key)
                dto = parse_body(header, body)
            metadata = dto.metadata
            row = {
                **source,
                "file": str(path.relative_to(args.converted)),
                "frames": metadata.num_frames if metadata else 0,
                "fps": metadata.frames_per_second if metadata else 0,
                "tracks": len(dto.tracks),
                "curves": len(dto.curves),
                "additive": int(metadata.additive_anim_type) if metadata else None,
                "included_in_glb": False,
            }
            report["animations"].append(row)
            # Additive clips need a base pose/composition; retain them in UEFormat
            # rather than presenting them as independent full-body animations.
            excluded = animation_exclusion(dto, require_decoded=args.with_animations)
            if excluded:
                row["excluded_reason"] = excluded
                continue
            if args.animation and header.object_name != args.animation:
                continue
            unknown = {track.name.casefold() for track in dto.tracks} - {
                bone.name.casefold() for bone in armature.data.bones
            }
            if unknown:
                row["unknown_bones"] = sorted(unknown)
                if args.with_animations:
                    raise ValueError(f"Animation targets unknown bones: {row}")
                continue
            to_blender_space(dto, header.file_version, 0.01)
            retime_tracks(dto)
            action = create_anim(
                dto,
                UEAnimOptions(override_skeleton=armature, import_curves=False),
                header.object_name,
            )
            action.use_fake_user = True
            action["source_path"] = header.object_path
            row["action"] = action.name
            row["included_in_glb"] = True
            actions.append(action)
        report["missing_exports"] = missing_animation_sources(sources, seen_sources)
        if args.with_animations and report["missing_exports"]:
            raise ValueError(f"Missing matching animation exports: {report['missing_exports']}")
        if not actions:
            raise ValueError("No verified, non-additive animation tracks available")
        report["animation_coverage"] = {
            "matching_sources": sum(row["matches_skeleton"] for row in sources.values()),
            "decoded_clips": len(report["animations"]),
            "playable_clips": len(actions),
            "additive_clips": sum(bool(row["additive"]) for row in report["animations"]),
            "single_frame_clips": sum(row["frames"] < 2 for row in report["animations"]),
            "curves_only_clips": sum(
                row.get("excluded_reason") == "curves_only" for row in report["animations"]
            ),
            "excluded_clips": [
                {"source": row["source"], "reason": row["excluded_reason"]}
                for row in report["animations"]
                if "excluded_reason" in row
            ],
            "missing_exports": len(report["missing_exports"]),
        }

    from io_scene_gltf2.blender.exp.exporter import GlTF2Exporter
    from io_scene_gltf2.io.com.gltf2_io import Accessor, BufferView

    def export_glb(name, *, morph=True, animations=False, selected=False):
        options = dict(
            filepath=str((args.output / name).resolve()),
            export_format="GLB",
            export_animations=animations,
            use_selection=selected,
            export_animation_mode="ACTIONS",
            export_force_sampling=False,
            export_morph=morph,
            export_morph_normal=False,
            export_morph_animation=False,
            export_optimize_disable_viewport=True,
            export_skins=True,
            export_anim_single_armature=True,
        )
        with indexed_gltf_references(GlTF2Exporter, (Accessor, BufferView)):
            if "FINISHED" not in bpy.ops.export_scene.gltf(**options):
                raise RuntimeError(f"GLB export failed: {name}")

    if not rig_mode:
        import_actions()
        armature.animation_data.action = next(
            (action for action in actions if action.name == "Stand1"), actions[0]
        )
        bpy.context.scene.frame_set(30)
        bpy.context.preferences.filepaths.save_version = 0
        bpy.ops.wm.save_as_mainfile(
            filepath=str((args.output / "character.blend").resolve()), compress=True
        )
    export_glb("character.glb", animations=not rig_mode)
    if rig_mode:
        report["rig_only"] = args.rig_only
        report["textures"] = len({row["texture"] for row in report["materials"]})
        report["morph_targets"] = sum(
            len(mesh.data.shape_keys.key_blocks) - 1 for mesh in meshes if mesh.data.shape_keys
        )
        report["character_glb"] = rig_glb_stats(
            args.output / "character.glb",
            report["bones"],
            report["morph_targets"],
            report["textures"],
        )
        with preview_meshes(meshes), preview_materials(meshes, args.preview_texture_size):
            export_glb("preview.glb", morph=False)
        report["preview_glb"] = rig_glb_stats(
            args.output / "preview.glb",
            report["bones"],
            0,
            report["textures"],
        )
        report["preview_texture_size"] = args.preview_texture_size
        report["preview_glb_bytes"] = report["preview_glb"]["bytes"]
    if args.with_animations:
        try:
            import_actions()
        finally:
            (args.output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        armature.animation_data.action = actions[0]
        bpy.ops.object.select_all(action="DESELECT")
        armature.select_set(True)
        bpy.context.view_layer.objects.active = armature
        canonical_animation_rig(armature)
        export_glb("animations.glb", morph=False, animations=True, selected=True)
        # Blender runs this file as a script, outside the worker package imports.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from model_animations import animation_summary

        summary = animation_summary(
            args.output / "animations.glb", {bone.name for bone in armature.data.bones}
        )
        if summary["animations"] != len(actions):
            raise ValueError("Animation export lost actions")
        report["animations_glb"] = summary
        (args.output / "animations.json").write_text(
            json.dumps(summary, indent=2), encoding="utf-8"
        )
    report["glb_bytes"] = (args.output / "character.glb").stat().st_size
    report["blender_version"] = bpy.app.version_string
    (args.output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "vertices": report["vertices"],
                "bones": report["bones"],
                "exported_animations": len(report["animations"]),
                "glb_actions": len(actions),
                "missing_exports": len(report["missing_exports"]),
                "glb_bytes": report["glb_bytes"],
                "preview_glb_bytes": report.get("preview_glb_bytes"),
                "missing_textures": len(report["missing_textures"]),
            }
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Blender buffers stdout; drain importer logs before stderr's traceback
        # so bounded combined-log tails retain the actual failure.
        sys.stdout.flush()
        raise

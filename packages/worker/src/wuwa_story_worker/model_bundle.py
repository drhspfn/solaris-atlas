"""Build a local, inspectable GLB from CUE4Parse exports using Blender.

Run with Blender's --python, not the worker interpreter. Game shaders are not
portable: this preview deliberately uses exported base-color textures only.
"""

import argparse
import hashlib
import json
import sys
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--converted", type=Path, required=True)
    parser.add_argument("--source-json", type=Path, required=True)
    parser.add_argument("--skeleton", required=True)
    parser.add_argument("--ueformat-plugin", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--animation", help="Optional source name for a small verification bundle")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    import bpy

    sys.path.insert(0, str(args.ueformat_plugin.resolve()))
    from io_scene_ueformat.importer.archive.reader import FArchiveReader
    from io_scene_ueformat.importer.create.anim import create_anim
    from io_scene_ueformat.importer.create.space import to_blender_space
    from io_scene_ueformat.importer.import_context import UEFormatImport, parse_body, parse_header
    from io_scene_ueformat.options import UEAnimOptions, UEModelOptions

    args.output.mkdir(parents=True, exist_ok=True)
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    armature = UEFormatImport(UEModelOptions()).import_file(args.model)
    if armature is None or armature.type != "ARMATURE":
        raise ValueError("Export has no skinned armature")
    meshes = [obj for obj in bpy.data.objects if obj.type == "MESH"]
    sources = animation_sources(args.source_json, args.skeleton)
    report = {
        "model_source": str(args.model.resolve()),
        "model_sha256": hashlib.sha256(args.model.read_bytes()).hexdigest(),
        "vertices": sum(len(obj.data.vertices) for obj in meshes),
        "bones": len(armature.data.bones),
        "materials": [],
        "animations": [],
        "missing_exports": [],
        "appearance": "Base-color preview; original game shaders and physics are not reproduced.",
    }
    material_files = {path.stem: path for path in args.converted.rglob("*.json")}
    for material in {material for mesh in meshes for material in mesh.data.materials}:
        path = material_files.get(material.name)
        metadata = json.loads(path.read_text(encoding="utf-8-sig")) if path else {}
        reference = metadata.get("Textures", {}).get("MainTex", {}).get("ObjectPath")
        texture = None
        if reference:
            relative = reference.split(".", 1)[0].replace("\\", "/").removeprefix("/Game/")
            if reference.startswith("/Game/"):
                relative = "Client/Content/" + relative
            candidate = args.converted / (relative + ".png")
            if candidate.resolve().is_relative_to(args.converted.resolve()) and candidate.is_file():
                texture = candidate
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
            if metadata.get("BlendMode") in {1, 2}:
                material.node_tree.links.new(node.outputs["Alpha"], shader.inputs["Alpha"])
                material.surface_render_method = "DITHERED"
        report["materials"].append(
            {"name": material.name, "texture": str(texture) if texture else None}
        )

    actions = []
    seen_sources = set()
    bpy.context.scene.render.fps = 30
    for path in sorted(args.converted.rglob("*.ueanim")):
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
            "additive": int(metadata.additive_anim_type) if metadata else None,
            "included_in_glb": False,
        }
        report["animations"].append(row)
        # Additive clips need a base pose/composition; retain them in UEFormat
        # rather than presenting them as independent full-body animations.
        if not metadata or not dto.tracks or metadata.num_frames < 2 or metadata.additive_anim_type:
            continue
        if args.animation and header.object_name != args.animation:
            continue
        unknown = {track.name.casefold() for track in dto.tracks} - {
            bone.name.casefold() for bone in armature.data.bones
        }
        if unknown:
            row["unknown_bones"] = sorted(unknown)
            continue
        to_blender_space(dto, header.file_version, 0.01)
        for track in dto.tracks:
            for keys in (track.position_keys, track.rotation_keys, track.scale_keys):
                for key in keys:
                    key.frame *= 30 / metadata.frames_per_second
        action = create_anim(
            dto, UEAnimOptions(override_skeleton=armature, import_curves=False), header.object_name
        )
        action.use_fake_user = True
        row["action"] = action.name
        row["included_in_glb"] = True
        actions.append(action)
    report["missing_exports"] = [
        row for key, row in sources.items() if row["matches_skeleton"] and key not in seen_sources
    ]
    if not actions:
        raise ValueError("No verified, non-additive animation tracks available")
    armature.animation_data.action = next(
        (action for action in actions if action.name == "Stand1"), actions[0]
    )
    bpy.context.scene.frame_set(30)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(
        filepath=str((args.output / "character.blend").resolve()), compress=True
    )
    bpy.ops.export_scene.gltf(
        filepath=str((args.output / "character.glb").resolve()),
        export_format="GLB",
        export_animations=True,
        export_animation_mode="ACTIONS",
        export_force_sampling=False,
        export_morph=True,
        export_morph_normal=False,
        export_skins=True,
        export_anim_single_armature=True,
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
            }
        )
    )


if __name__ == "__main__":
    main()

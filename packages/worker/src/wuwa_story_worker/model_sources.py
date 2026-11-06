"""Authored model identities and portable, explicitly approximate spring profiles."""

import hashlib
import json
import re
from pathlib import PurePosixPath


def authored_package(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"/Game/[A-Za-z0-9_/-]+(?:\.[A-Za-z0-9_]+)?", value
    ):
        raise ValueError("Expected an authored /Game/ model resource")
    package = value[6:].split(".", 1)[0]
    if (
        not re.fullmatch(r"[A-Za-z0-9_/-]+", package)
        or ".." in PurePosixPath(package).parts
        or "//" in package
    ):
        raise ValueError("Unsafe authored model resource")
    return package


def model_sources(role, preload):
    if role.get("MeshId") != preload.get("Id"):
        raise ValueError("Model preload does not match the character MeshId")
    meshes = preload.get("Meshes", [])
    if len(meshes) != 1:
        raise ValueError(
            "A single authored character mesh is required; composite rigs are unsupported"
        )
    mesh = authored_package(meshes[0])
    if not mesh.startswith("Aki/Character/Role/") or "/Model/" not in mesh:
        raise ValueError("Authored mesh is not a character rig")
    blueprints = [authored_package(path) for path in preload.get("AnimationBlueprints", [])]
    return {"model": mesh, "blueprints": sorted(set(blueprints))}


def npc_model_sources(component):
    """Resolve only the mesh and animation blueprint explicitly authored for an NPC."""
    if not isinstance(component, dict) or component.get("Disabled") is True:
        raise ValueError("NPC model component is missing or disabled")
    model_type = component.get("ModelType")
    if not isinstance(model_type, dict) or model_type.get("Type") != "Npc":
        raise ValueError("Expected an NPC model component")
    npc_model = model_type.get("NpcModel")
    if not isinstance(npc_model, dict) or npc_model.get("Type") != "Mesh":
        raise ValueError("NPC model does not specify an exact mesh")
    mesh = authored_package(npc_model.get("Mesh"))
    if not mesh.startswith("Aki/Character/NPC/"):
        raise ValueError("Authored mesh is not an NPC rig")
    abp = model_type.get("Abp")
    return {"model": mesh, "blueprints": [authored_package(abp)] if abp else []}


def npc_blueprint_attachments(rows, primary_model=None):
    """Read explicitly attached component templates from cooked Blueprint metadata."""
    components = {row.get("Name"): row for row in rows if isinstance(row, dict)}
    attachments = []
    for row in rows:
        if not isinstance(row, dict) or row.get("Type") != "SCS_Node":
            continue
        node = row.get("Properties", {})
        template = node.get("ComponentTemplate", {}).get("ObjectName", "")
        name = template.rsplit(":", 1)[-1].removesuffix("'")
        component = components.get(name, {})
        properties = component.get("Properties", {})
        mesh = properties.get("SkeletalMesh") or properties.get("StaticMesh")
        if not isinstance(mesh, dict) or not mesh.get("ObjectPath"):
            continue
        model = exported_package(mesh["ObjectPath"])
        if model == primary_model and not node.get("AttachToName"):
            continue
        if not node.get("ParentComponentOrVariableName") or not node.get("AttachToName"):
            raise ValueError("NPC accessory is missing its explicit parent or socket")
        attachments.append({
            "name": node.get("InternalVariableName") or name,
            "model": model,
            "parent": node["ParentComponentOrVariableName"],
            "socket": node["AttachToName"],
            "location": properties.get("RelativeLocation"),
            "rotation": properties.get("RelativeRotation"),
            "scale": properties.get("RelativeScale3D"),
            "visible": properties.get("bVisible", True) and not properties.get("bHiddenInGame", False),
        })
    return attachments


def npc_animation_references(value):
    """Find explicit sequence references in authored ABP and montage exports."""
    result = set()
    if isinstance(value, dict):
        if str(value.get("ObjectName", "")).startswith("AnimSequence'"):
            result.add(exported_package(value.get("ObjectPath", "")))
        for child in value.values():
            result.update(npc_animation_references(child))
    elif isinstance(value, list):
        for child in value:
            result.update(npc_animation_references(child))
    return result


def npc_material_overrides(rows):
    """Resolve indexed material overrides on the authored primary mesh component."""
    result = {}
    names = {"CharacterMesh0"}
    for row in rows:
        if row.get("Type") != "SCS_Node":
            continue
        properties = row.get("Properties", {})
        if properties.get("InternalVariableName") == "CharacterMesh0":
            reference = properties.get("ComponentTemplate", {}).get("ObjectName", "")
            names.add(reference.rsplit(":", 1)[-1].removesuffix("'"))
    for row in rows:
        if row.get("Type") != "SkeletalMeshComponent" or row.get("Name") not in names:
            continue
        properties = row.get("Properties", {})
        for index, material in enumerate(properties.get("OverrideMaterials", [])):
            if material is None:
                continue
            if not isinstance(material, dict) or not material.get("ObjectPath"):
                raise ValueError("Invalid NPC material slot override")
            path = exported_package(material["ObjectPath"])
            if index in result and result[index] != path:
                raise ValueError("Conflicting NPC material slot overrides")
            result[index] = path
    return result


def exported_package(value):
    if value.startswith("Client/Content/"):
        value = "/Game/" + value[len("Client/Content/") :]
    return authored_package(value)


def spring_profile(rows, source, digest, expected_skeleton=None):
    if expected_skeleton is not None:
        targets = [
            row.get("Properties", {}).get("TargetSkeleton", {}).get("ObjectPath")
            for row in rows
            if row.get("Type") == "AnimBlueprintGeneratedClass"
        ]
        if not any(value and exported_package(value) == expected_skeleton for value in targets):
            raise ValueError("Physics blueprint does not target the exported model skeleton")
    groups = []
    for row in rows:
        if not row.get("Name", "").startswith("Default__"):
            continue
        for name, node in row.get("Properties", {}).items():
            if (
                not isinstance(node, dict)
                or not node.get("RootBones")
                or not node.get("PhysicsSettings")
            ):
                continue
            if node.get("BoneForwardAxis") != "EBoneForwardAxis::X_Positive":
                raise ValueError("Unsupported spring dummy-bone axis")
            settings = node["PhysicsSettings"]
            numbers = [settings.get(key) for key in ("Damping", "Stiffness", "LimitAngle")]
            length = node.get("DummyBoneLength")
            if any(type(value) not in (int, float) or not 0 <= value <= 180 for value in numbers):
                raise ValueError("Invalid spring settings")
            if type(length) not in (int, float) or not 0 < length <= 1000:
                raise ValueError("Invalid spring dummy length")
            groups.append(
                {
                    "name": name,
                    "roots": [r["BoneName"] for r in node["RootBones"]],
                    "excluded": [r["BoneName"] for r in node.get("ExcludeBones", [])],
                    "damping": numbers[0],
                    "stiffness": numbers[1],
                    "limitAngle": min(numbers[2], 90),
                    "dummyLength": length / 100,
                }
            )
    return {
        "format": "solaris-spring-preview-v1",
        "source": source,
        "sha256": digest,
        "approximation": "Animation-relative springs; no game collision, wind or cloth solver.",
        "groups": groups,
    }


def glb_summary(path, allow_animations=False):
    """Verify the publication contract before any R2 upload."""
    import struct

    if allow_animations:
        from wuwa_story_worker.model_animations import animation_summary

        return animation_summary(path)

    with path.open("rb") as stream:
        magic, version, size = struct.unpack("<4sII", stream.read(12))
        length, kind = struct.unpack("<II", stream.read(8))
        if magic != b"glTF" or version != 2 or size != path.stat().st_size or kind != 0x4E4F534A:
            raise ValueError("Invalid GLB output")
        data = json.loads(stream.read(length))
    skins = data.get("skins", [])
    if len(skins) != 1 or not skins[0].get("joints") or not data.get("meshes"):
        raise ValueError("Model output has no single skinned character rig")
    if data.get("animations"):
        raise ValueError("Character model publication must not embed animation collections")
    if any("uri" in image for image in data.get("images", [])):
        raise ValueError("Model textures must be embedded")
    if any("uri" in buffer for buffer in data.get("buffers", [])):
        raise ValueError("Model buffers must be embedded")
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {
        "bytes": size,
        "bones": len(skins[0]["joints"]),
        "textures": len(data.get("images", [])),
        "sha256": digest,
    }

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

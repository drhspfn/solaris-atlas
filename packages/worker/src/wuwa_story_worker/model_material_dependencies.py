"""Resolve referenced mesh materials and supported color textures, never nearby assets."""

import json

from wuwa_story_worker.model_bundle import base_color_texture
from wuwa_story_worker.model_sources import exported_package


def missing_material_resources(cooked, converted, models, overrides=()):
    pending, missing, seen = list(overrides), set(), set()
    for model in models:
        path = cooked / "Client/Content" / (model + ".json")
        if not path.is_file():
            raise ValueError("Material discovery requires the exact model metadata")
        for row in json.loads(path.read_text(encoding="utf-8-sig")):
            if row.get("Type") not in {"SkeletalMesh", "StaticMesh"}:
                continue
            for slot in row.get("SkeletalMaterials", row.get("StaticMaterials", [])):
                reference = slot.get("Material") or slot.get("MaterialInterface")
                if reference and reference.get("ObjectPath"):
                    pending.append(exported_package(reference["ObjectPath"]))
    while pending:
        material = pending.pop()
        if material in seen:
            continue
        seen.add(material)
        if len(seen) > 256:
            raise ValueError("Model material dependencies exceed 256 resources")
        source = cooked / "Client/Content" / (material + ".json")
        if not source.is_file():
            missing.add(material)
            continue
        for row in json.loads(source.read_text(encoding="utf-8-sig")):
            parent = row.get("Properties", {}).get("Parent") or {}
            if str(parent.get("ObjectName", "")).startswith("MaterialInstanceConstant'"):
                pending.append(exported_package(parent["ObjectPath"]))
        path = converted / "Client/Content" / (material + ".json")
        if not path.is_file():
            missing.add(material)
            continue
        metadata = json.loads(path.read_text(encoding="utf-8-sig"))
        reference, texture = base_color_texture(metadata, converted)
        if reference and texture is None:
            missing.add(exported_package(reference))
    return sorted(missing)

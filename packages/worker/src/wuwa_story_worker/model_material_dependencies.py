"""Resolve referenced mesh materials and supported color textures, never nearby assets."""

import json

from wuwa_story_worker.model_bundle import base_color_texture, material_metadata, material_parent
from wuwa_story_worker.model_sources import exported_package


def fur_material_resources(cooked, converted, source, metadata, names):
    """Resolve authored overrides and master defaults for fur shader inputs."""
    references, missing, seen = {}, set(), set()
    while source.is_file():
        if source in seen:
            raise ValueError('Cyclic fur material inheritance')
        seen.add(source)
        rows = json.loads(source.read_text(encoding='utf-8-sig'))
        material = next((row for row in rows if row.get('Type') in
                         {'MaterialInstanceConstant', 'Material'}), None)
        if not material:
            break
        if material['Type'] == 'Material':
            cached = material.get('CachedExpressionData') or material.get('Properties', {}).get('CachedExpressionData') or {}
            params = cached.get('Parameters') or {}
            entries = (params.get('RuntimeEntries[2]') or {}).get('ParameterInfos') or []
            values = params.get('TextureValues') or []
            for entry, value in zip(entries, values):
                name = entry.get('Name')
                if name in names and name not in references and isinstance(value, dict) and value.get('ObjectPath'):
                    references[name] = value['ObjectPath']
            break
        resolved = metadata if len(seen) == 1 else material_metadata(rows)
        for name in names:
            entry = (resolved.get('Textures') or {}).get(name) or {}
            if name not in references and entry.get('ObjectPath'):
                references[name] = entry['ObjectPath']
        if names <= references.keys():
            break
        parent = material.get('Properties', {}).get('Parent') or {}
        if not parent.get('ObjectPath'):
            break
        package = exported_package(parent['ObjectPath'])
        source = cooked / 'Client/Content' / (package + '.json')
        if not source.is_file():
            missing.add(package)
    for reference in references.values():
        package = exported_package(reference)
        if not (converted / 'Client/Content' / (package + '.png')).is_file():
            missing.add(package)
    return missing


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
        rows = json.loads(source.read_text(encoding="utf-8-sig"))
        for row in rows:
            parent = row.get("Properties", {}).get("Parent") or {}
            if str(parent.get("ObjectName", "")).startswith("MaterialInstanceConstant'"):
                pending.append(exported_package(parent["ObjectPath"]))
        path = converted / "Client/Content" / (material + ".json")
        if not path.is_file():
            missing.add(material)
            continue
        metadata = json.loads(path.read_text(encoding="utf-8-sig"))
        resolved = material_metadata(metadata)
        if resolved.get('Switches', {}).get('UseFurry') is True:
            fur = resolved.get('Textures', {}).get('Fur_Tex', {}).get('ObjectPath')
            if fur:
                package = exported_package(fur)
                if not (converted / 'Client/Content' / (package + '.png')).is_file():
                    missing.add(package)
            names = set()
            if resolved.get('Switches', {}).get('UseFurryParallax') is True:
                names.update({'HeightMap', 'FlowmapMap'})
            if resolved.get('Switches', {}).get('FurMaskUseID') is True:
                names.update({'RegionIDTex', 'TypeMask'})
            if names:
                missing.update(fur_material_resources(cooked, converted, source, resolved, names))
        if material_parent(rows) == "/Game/Aki/Render/Shaders/Character/M_WriteStencil":
            mask = material_metadata(metadata).get("Textures", {}).get("Mask", {}).get("ObjectPath")
            if mask:
                package = exported_package(mask)
                if not (converted / "Client/Content" / (package + ".png")).is_file():
                    missing.add(package)
        reference, texture = base_color_texture(metadata, converted, material_parent(rows))
        if reference and texture is None:
            missing.add(exported_package(reference))
    return sorted(missing)

"""Portable model bundles: final assets only, with content-addressed ZIP members."""

import hashlib
import json
import re
import shutil
import stat
import zipfile
from pathlib import Path

from wuwa_story.ingestion.model_animation_packs import MAX_ANIMATION_PACKS, validate_animation_packs
from wuwa_story.ingestion.model_limits import MAX_MODEL_COMPONENTS, MAX_MODEL_FORMS, MAX_MODEL_ROOTS

FORMAT = "solaris-model-bundle-v1"
MAX_ARCHIVE_BYTES = 4 * 1024**3
MAX_EXPANDED_BYTES = 8 * 1024**3
CHUNK_BYTES = 8 * 1024**2
FILES = {"preview": "preview.glb", "download": "character.glb", "animations": "animations.glb",
         "facial": "facial.json", "physics": "physics.json",
         "animation_manifest": "animation-manifest.json"}
MAX_ARCHIVE_OBJECTS = (len(FILES) + MAX_ANIMATION_PACKS) * MAX_MODEL_FORMS


def model_file_name(kind):
    if kind in FILES:
        return FILES[kind]
    if isinstance(kind, str) and re.fullmatch(r"animation_pack_[0-9a-f]{16}", kind):
        return "animation-packs/" + kind.removeprefix("animation_pack_") + ".glb"
    raise ValueError("Invalid model file kind")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_manifest(data):
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ValueError("Unsupported model archive")
    if not re.fullmatch(r"character:(?:npc:)?[1-9][0-9]*", str(data.get("character_key", ""))):
        raise ValueError("Invalid archive character")
    if not isinstance(data.get("forms"), list) or not 1 <= len(data["forms"]) <= MAX_MODEL_FORMS:
        raise ValueError(f"Archive must contain 1..{MAX_MODEL_ROOTS} roots and at most {MAX_MODEL_COMPONENTS} components")
    objects, ids = data.get("objects"), set()
    if not isinstance(objects, dict) or not 1 <= len(objects) <= MAX_ARCHIVE_OBJECTS:
        raise ValueError("Invalid archive objects")
    for key, size in objects.items():
        if not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{64}", key) or type(size) is not int or size < 1:
            raise ValueError("Invalid archive object identity")
    if sum(objects.values()) > MAX_EXPANDED_BYTES:
        raise ValueError("Expanded model archive exceeds size limit")
    referenced = set()
    parents, components, component_sources, scenes = {}, {}, set(), {}
    root_count = 0
    for entry in data["forms"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("form"), dict):
            raise ValueError("Invalid archive form")
        form = entry["form"]
        if not re.fullmatch(r"default|mesh-[1-9][0-9]*(?:-form-[1-9][0-9]*)?|component-[a-f0-9]{16}", str(form.get("id", ""))):
            raise ValueError("Invalid archive form identity")
        if form["id"] in ids:
            raise ValueError("Duplicate archive form")
        ids.add(form["id"])
        component = form.get("component")
        if form["id"].startswith("component-"):
            if not isinstance(component, dict):
                raise ValueError("Archive component metadata is missing")
            source = component.get("source")
            if (not isinstance(component.get("id"), str) or not component["id"].strip()
                    or not isinstance(component.get("parent_form_id"), str)
                    or not isinstance(source, dict)
                    or any(not isinstance(source.get(k), str) or not source[k].strip()
                           for k in ("blueprint", "component"))):
                raise ValueError("Invalid archive component identity")
            if (not isinstance(form.get("label"), str) or not form["label"].strip()
                    or type(form.get("order")) is not int or form["order"] < 0):
                raise ValueError("Invalid archive component label or order")
            for key in ("name", "label"):
                if key in component and (not isinstance(component[key], str) or not component[key].strip()):
                    raise ValueError("Invalid archive component metadata")
            if ("parent_component" in component and component["parent_component"] is not None
                    and (not isinstance(component["parent_component"], str) or not component["parent_component"].strip())
                    or "default_visible" in component and component["default_visible"] is not None
                    and type(component["default_visible"]) is not bool
                    or "socket" in component and component["socket"] is not None
                    and (not isinstance(component["socket"], str) or not component["socket"].strip())
                    or any(key in component and not isinstance(component[key], list)
                           for key in ("bindings", "cues"))):
                raise ValueError("Invalid archive component metadata")
            transform = component.get("transform")
            if "transform" in component and (not isinstance(transform, dict)
                    or any(not isinstance(transform.get(key), dict)
                           for key in ("location", "rotation", "scale"))):
                raise ValueError("Invalid archive component transform")
            identity = (component["parent_form_id"], source["blueprint"], source["component"])
            if identity in component_sources:
                raise ValueError("Duplicate archive component")
            component_sources.add(identity)
            components[form["id"]] = component["id"]
            parents[form["id"]] = component["parent_form_id"]
        else:
            if component is not None:
                raise ValueError("Root form cannot be a component")
            root_count += 1
        if not isinstance(entry.get("provenance"), dict):
            raise ValueError("Archive lacks model provenance")
        packs = validate_animation_packs(entry["provenance"].get("animation_packs", []))
        pack_keys = {pack["key"] for pack in packs}
        files = entry.get("files")
        if (not isinstance(files, dict) or not {"preview", "download"} <= files.keys()
                or files.keys() - (FILES.keys() | pack_keys)
                or not pack_keys <= files.keys()
                or bool(packs) != ("animation_manifest" in files)):
            raise ValueError("Invalid model archive files")
        for value in files.values():
            if not isinstance(value, str) or value not in objects:
                raise ValueError("Archive references a missing object")
            referenced.add(value)
        scene = entry["provenance"].get("scene")
        if scene is not None:
            if (component is not None or not isinstance(scene, dict)
                    or scene.get("format") != "solaris-model-scene-v1"
                    or not isinstance(scene.get("components"), list)
                    or not isinstance(scene.get("cues"), list)
                    or any(not isinstance(item, str) for item in scene["components"])
                    or len(set(scene["components"])) != len(scene["components"])):
                raise ValueError("Invalid archive scene")
            scenes[form["id"]] = scene
    if not 1 <= root_count <= MAX_MODEL_ROOTS or len(parents) > MAX_MODEL_COMPONENTS:
        raise ValueError(f"Archive must contain 1..{MAX_MODEL_ROOTS} roots and at most {MAX_MODEL_COMPONENTS} components")
    component_ids = set()
    for child in parents:
        seen, current = set(), child
        while current in parents:
            if current in seen:
                raise ValueError("Archive component graph contains a cycle")
            seen.add(current)
            current = parents[current]
        if current not in ids:
            raise ValueError("Archive component parent is missing")
        identity = (current, components[child])
        if identity in component_ids:
            raise ValueError("Duplicate archive component")
        component_ids.add(identity)
    for root, scene in scenes.items():
        for child in scene["components"]:
            if child not in parents:
                raise ValueError("Archive scene references a missing component")
            ancestor = child
            while ancestor in parents:
                ancestor = parents[ancestor]
            if ancestor != root:
                raise ValueError("Archive scene references another root's component")
    if referenced != objects.keys():
        raise ValueError("Archive contains unreferenced objects")
    return data


def write_archive(destination, metadata, bundles):
    """Never include cooked exports, local game keys, tools or arbitrary files."""
    objects, sources, forms = {}, {}, []
    for form, output, _summaries, provenance in bundles:
        files = {}
        packs = validate_animation_packs(provenance.get("animation_packs", []))
        for kind in (*FILES, *(pack["key"] for pack in packs)):
            filename = model_file_name(kind)
            source = output / filename
            if source.is_file():
                key = digest(source)
                objects[key], sources[key] = source.stat().st_size, source
                files[kind] = key
        forms.append({"form": form, "files": files, "provenance": provenance})
    manifest = validate_manifest({**metadata, "format": FORMAT, "forms": forms, "objects": objects})
    destination = Path(destination)
    created = False
    try:
        with destination.open("xb") as target:
            created = True
            with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
                archive.writestr("manifest.json", json.dumps(manifest, separators=(",", ":")))
                for key, source in sources.items():
                    archive.write(source, "objects/" + key)
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
        raise
    if destination.stat().st_size > MAX_ARCHIVE_BYTES:
        destination.unlink()
        raise ValueError("Model archive exceeds upload limit")
    return manifest


def read_archive(source, destination):
    """Stream verified members into a fresh scratch directory; never extractall."""
    if source.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("Model archive exceeds upload limit")
    with zipfile.ZipFile(source) as archive:
        infos = archive.infolist()
        if len(infos) > 1 + MAX_ARCHIVE_OBJECTS or len({info.filename for info in infos}) != len(infos):
            raise ValueError("Duplicate or excessive archive members")
        if any(stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 1 for info in infos):
            raise ValueError("Encrypted files and links are not allowed")
        info = archive.getinfo("manifest.json")
        if info.file_size > 16 * 1024**2:
            raise ValueError("Archive manifest exceeds size limit")
        manifest = validate_manifest(json.loads(archive.read(info)))
        expected = {"manifest.json", *("objects/" + key for key in manifest["objects"])}
        if {info.filename for info in infos} != expected:
            raise ValueError("Unexpected archive member or path")
        objects = destination / "objects"
        objects.mkdir(parents=True, exist_ok=False)
        for key, size in manifest["objects"].items():
            member = archive.getinfo("objects/" + key)
            if member.file_size != size:
                raise ValueError("Archive object size mismatch")
            path = objects / key
            with archive.open(member) as src, path.open("xb") as dst:
                shutil.copyfileobj(src, dst, length=1024**2)
            if path.stat().st_size != size or digest(path) != key:
                raise ValueError("Archive object checksum mismatch")
    return manifest

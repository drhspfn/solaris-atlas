"""Portable model bundles: final assets only, with content-addressed ZIP members."""

import hashlib
import json
import re
import shutil
import stat
import zipfile
from pathlib import Path

FORMAT = "solaris-model-bundle-v1"
MAX_ARCHIVE_BYTES = 4 * 1024**3
MAX_EXPANDED_BYTES = 8 * 1024**3
CHUNK_BYTES = 8 * 1024**2
FILES = {"preview": "preview.glb", "download": "character.glb", "animations": "animations.glb",
         "facial": "facial.json", "physics": "physics.json"}


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_manifest(data):
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ValueError("Unsupported model archive")
    if not re.fullmatch(r"character:[1-9][0-9]*", str(data.get("character_key", ""))):
        raise ValueError("Invalid archive character")
    if not isinstance(data.get("forms"), list) or not 1 <= len(data["forms"]) <= 16:
        raise ValueError("Archive must contain 1..16 forms")
    objects, ids = data.get("objects"), set()
    if not isinstance(objects, dict) or not 1 <= len(objects) <= 80:
        raise ValueError("Invalid archive objects")
    for key, size in objects.items():
        if not re.fullmatch(r"[a-f0-9]{64}", key) or type(size) is not int or size < 1:
            raise ValueError("Invalid archive object identity")
    if sum(objects.values()) > MAX_EXPANDED_BYTES:
        raise ValueError("Expanded model archive exceeds size limit")
    referenced = set()
    for entry in data["forms"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("form"), dict):
            raise ValueError("Invalid archive form")
        form = entry["form"]
        if not re.fullmatch(r"default|mesh-[1-9][0-9]*(?:-form-[1-9][0-9]*)?", str(form.get("id", ""))):
            raise ValueError("Invalid archive form identity")
        if form["id"] in ids:
            raise ValueError("Duplicate archive form")
        ids.add(form["id"])
        files = entry.get("files")
        if not isinstance(files, dict) or not {"preview", "download"} <= files.keys() or files.keys() - FILES.keys():
            raise ValueError("Invalid model archive files")
        for value in files.values():
            if not isinstance(value, str) or value not in objects:
                raise ValueError("Archive references a missing object")
            referenced.add(value)
        if not isinstance(entry.get("provenance"), dict):
            raise ValueError("Archive lacks model provenance")
    if referenced != objects.keys():
        raise ValueError("Archive contains unreferenced objects")
    return data


def write_archive(destination, metadata, bundles):
    """Never include cooked exports, local game keys, tools or arbitrary files."""
    objects, sources, forms = {}, {}, []
    for form, output, _summaries, provenance in bundles:
        files = {}
        for kind, filename in FILES.items():
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
        if len(infos) > 81 or len({info.filename for info in infos}) != len(infos):
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

"""Merge separate material-override GLB exports into one KHR_materials_variants GLB."""

from __future__ import annotations

import hashlib
import json
import struct
from copy import deepcopy
from pathlib import Path

_GLB_MAGIC = b"glTF"
_GLB_VERSION = 2
_CHUNK_JSON = 0x4E4F534A
_CHUNK_BIN = 0x004E4942
_EXT_NAME = "KHR_materials_variants"


def parse_glb(path: Path) -> tuple[dict, bytearray]:
    """Read a GLB v2 file and return its parsed JSON chunk and BIN bytearray."""
    raw = path.read_bytes()
    if len(raw) < 20:
        raise ValueError(f"Invalid GLB file (too small): {path}")
    magic, version, total_len = struct.unpack_from("<4sII", raw, 0)
    if magic != _GLB_MAGIC or version != _GLB_VERSION or total_len != len(raw):
        raise ValueError(f"Invalid GLB header: {path}")

    offset = 12
    gltf_json: dict | None = None
    bin_data = bytearray()
    while offset < total_len:
        if offset + 8 > total_len:
            raise ValueError(f"Truncated GLB chunk header: {path}")
        chunk_len, chunk_type = struct.unpack_from("<II", raw, offset)
        offset += 8
        if offset + chunk_len > total_len:
            raise ValueError(f"Truncated GLB chunk payload: {path}")
        payload = raw[offset: offset + chunk_len]
        offset += chunk_len
        if chunk_type == _CHUNK_JSON and gltf_json is None:
            gltf_json = json.loads(payload.decode("utf-8"))
        elif chunk_type == _CHUNK_BIN and not bin_data:
            bin_data.extend(payload)

    if gltf_json is None:
        raise ValueError(f"GLB missing JSON chunk: {path}")
    return gltf_json, bin_data


def write_glb(path: Path, gltf_json: dict, bin_data: bytearray | bytes) -> None:
    """Serialize JSON and BIN chunks into a valid 4-byte-aligned GLB v2 file."""
    json_bytes = json.dumps(gltf_json, separators=(
        ",", ":"), ensure_ascii=False).encode("utf-8")
    json_pad = (-len(json_bytes)) % 4
    if json_pad:
        json_bytes += b" " * json_pad

    bin_bytes = bytes(bin_data)
    bin_pad = (-len(bin_bytes)) % 4
    if bin_pad:
        bin_bytes += b"\x00" * bin_pad

    has_bin = len(bin_bytes) > 0
    total_len = 12 + 8 + len(json_bytes) + \
        ((8 + len(bin_bytes)) if has_bin else 0)
    header = struct.pack("<4sII", _GLB_MAGIC, _GLB_VERSION, total_len)
    json_header = struct.pack("<II", len(json_bytes), _CHUNK_JSON)
    parts = [header, json_header, json_bytes]
    if has_bin:
        parts.append(struct.pack("<II", len(bin_bytes), _CHUNK_BIN))
        parts.append(bin_bytes)
    path.write_bytes(b"".join(parts))


def _pad4(buf: bytearray) -> None:
    rem = len(buf) % 4
    if rem:
        buf.extend(b"\x00" * (4 - rem))


def _rewrite_texture_info(
    obj: object,
    copy_texture_fn,
) -> object:
    if isinstance(obj, list):
        return [_rewrite_texture_info(item, copy_texture_fn) for item in obj]
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "index" and isinstance(v, int) and ("texCoord" in obj or len(obj) <= 3):
                out[k] = copy_texture_fn(v)
            else:
                out[k] = _rewrite_texture_info(v, copy_texture_fn)
        return out
    return obj


def merge_glb_material_variants(
    base_glb: Path,
    variant_glbs: list[tuple[str, Path]],
    base_name: str = "Form 1",
) -> None:
    """Merge variant GLBs' materials into base_glb under KHR_materials_variants."""
    if not variant_glbs:
        return
    base_json, base_bin = parse_glb(base_glb)
    base_meshes = base_json.get("meshes") or []
    if not base_meshes:
        return

    base_materials = base_json.setdefault("materials", [])
    base_textures = base_json.setdefault("textures", [])
    base_images = base_json.setdefault("images", [])
    base_samplers = base_json.setdefault("samplers", [])
    base_views = base_json.setdefault("bufferViews", [])
    base_buffers = base_json.setdefault("buffers", [])
    if not base_buffers:
        base_buffers.append({"byteLength": len(base_bin)})

    # Index existing images by sha256 of their bufferView bytes so identical textures aren't duplicated.
    image_sha_to_idx: dict[str, int] = {}
    for idx, img in enumerate(base_images):
        bv_idx = img.get("bufferView")
        if isinstance(bv_idx, int) and 0 <= bv_idx < len(base_views):
            bv = base_views[bv_idx]
            start = bv.get("byteOffset", 0)
            end = start + bv.get("byteLength", 0)
            digest = hashlib.sha256(base_bin[start:end]).hexdigest()
            image_sha_to_idx.setdefault(digest, idx)

    sampler_to_idx: dict[str, int] = {
        json.dumps(s, sort_keys=True): i for i, s in enumerate(base_samplers)
    }
    texture_to_idx: dict[str, int] = {
        json.dumps(t, sort_keys=True): i for i, t in enumerate(base_textures)
    }

    variant_names = [base_name, *(name for name, _ in variant_glbs)]
    # Track per-(mesh_idx, prim_idx) mapping: {variant_idx: base_material_idx}
    prim_mappings: dict[tuple[int, int], dict[int, int]] = {}
    for m_idx, mesh in enumerate(base_meshes):
        for p_idx, prim in enumerate(mesh.get("primitives") or []):
            mat_idx = prim.get("material")
            if isinstance(mat_idx, int):
                prim_mappings[(m_idx, p_idx)] = {0: mat_idx}

    for v_offset, (_v_name, var_path) in enumerate(variant_glbs, start=1):
        var_json, var_bin = parse_glb(var_path)
        var_meshes = var_json.get("meshes") or []
        var_materials = var_json.get("materials") or []
        var_textures = var_json.get("textures") or []
        var_images = var_json.get("images") or []
        var_samplers = var_json.get("samplers") or []
        var_views = var_json.get("bufferViews") or []

        copied_mat_cache: dict[int, int] = {}

        def copy_image(var_img_idx: int) -> int:
            img = deepcopy(var_images[var_img_idx])
            bv_idx = img.get("bufferView")
            if isinstance(bv_idx, int) and 0 <= bv_idx < len(var_views):
                bv = var_views[bv_idx]
                start = bv.get("byteOffset", 0)
                end = start + bv.get("byteLength", 0)
                payload = bytes(var_bin[start:end])
                digest = hashlib.sha256(payload).hexdigest()
                if digest in image_sha_to_idx:
                    return image_sha_to_idx[digest]
                _pad4(base_bin)
                new_offset = len(base_bin)
                base_bin.extend(payload)
                new_bv = {"buffer": 0, "byteOffset": new_offset,
                          "byteLength": len(payload)}
                new_bv_idx = len(base_views)
                base_views.append(new_bv)
                img["bufferView"] = new_bv_idx
                new_img_idx = len(base_images)
                base_images.append(img)
                image_sha_to_idx[digest] = new_img_idx
                return new_img_idx
            new_img_idx = len(base_images)
            base_images.append(img)
            return new_img_idx

        def copy_sampler(var_sampler_idx: int) -> int:
            sampler = var_samplers[var_sampler_idx]
            key = json.dumps(sampler, sort_keys=True)
            if key in sampler_to_idx:
                return sampler_to_idx[key]
            idx = len(base_samplers)
            base_samplers.append(deepcopy(sampler))
            sampler_to_idx[key] = idx
            return idx

        def copy_texture(var_tex_idx: int) -> int:
            tex = deepcopy(var_textures[var_tex_idx])
            if isinstance(tex.get("source"), int):
                tex["source"] = copy_image(tex["source"])
            if isinstance(tex.get("sampler"), int):
                tex["sampler"] = copy_sampler(tex["sampler"])
            key = json.dumps(tex, sort_keys=True)
            if key in texture_to_idx:
                return texture_to_idx[key]
            idx = len(base_textures)
            base_textures.append(tex)
            texture_to_idx[key] = idx
            return idx

        def copy_material(var_mat_idx: int) -> int:
            if var_mat_idx in copied_mat_cache:
                return copied_mat_cache[var_mat_idx]
            mat = deepcopy(var_materials[var_mat_idx])
            for section in ("pbrMetallicRoughness", "normalTexture", "occlusionTexture", "emissiveTexture"):
                if section in mat:
                    mat[section] = _rewrite_texture_info(
                        mat[section], copy_texture)
            if "extensions" in mat:
                mat["extensions"] = _rewrite_texture_info(
                    mat["extensions"], copy_texture)
            new_idx = len(base_materials)
            base_materials.append(mat)
            copied_mat_cache[var_mat_idx] = new_idx
            return new_idx

        for m_idx, mesh in enumerate(base_meshes):
            if m_idx >= len(var_meshes):
                continue
            var_prims = var_meshes[m_idx].get("primitives") or []
            for p_idx, _prim in enumerate(mesh.get("primitives") or []):
                if p_idx >= len(var_prims):
                    continue
                key = (m_idx, p_idx)
                if key not in prim_mappings:
                    continue
                base_mat_idx = prim_mappings[key][0]
                var_mat_idx = var_prims[p_idx].get("material")
                if not isinstance(var_mat_idx, int) or var_mat_idx >= len(var_materials):
                    prim_mappings[key][v_offset] = base_mat_idx
                    continue
                base_mat_name = base_materials[base_mat_idx].get("name")
                var_mat_name = var_materials[var_mat_idx].get("name")
                if base_mat_name and var_mat_name == base_mat_name:
                    prim_mappings[key][v_offset] = base_mat_idx
                else:
                    prim_mappings[key][v_offset] = copy_material(var_mat_idx)

    # Attach KHR_materials_variants extension metadata.
    ext_used = base_json.setdefault("extensionsUsed", [])
    if _EXT_NAME not in ext_used:
        ext_used.append(_EXT_NAME)
    base_json.setdefault("extensions", {})[_EXT_NAME] = {
        "variants": [{"name": name} for name in variant_names]
    }

    for (m_idx, p_idx), v_map in prim_mappings.items():
        by_mat: dict[int, list[int]] = {}
        for v_idx, mat_idx in sorted(v_map.items()):
            by_mat.setdefault(mat_idx, []).append(v_idx)
        prim = base_meshes[m_idx]["primitives"][p_idx]
        prim.setdefault("extensions", {})[_EXT_NAME] = {
            "mappings": [
                {"material": mat_idx, "variants": variants}
                for mat_idx, variants in by_mat.items()
            ]
        }

    _pad4(base_bin)
    base_buffers[0]["byteLength"] = len(base_bin)
    write_glb(base_glb, base_json, base_bin)

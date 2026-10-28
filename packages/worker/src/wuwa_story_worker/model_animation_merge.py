"""Merge bounded animation exports without retaining their binary payloads."""

import json
import math
import os
import struct
import tempfile
from pathlib import Path

from .model_animations import animation_summary


def _file_identity(stat):
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def _index(value, length):
    if type(value) is not int or not 0 <= value < length:
        raise ValueError("Invalid animation reference index")
    return value


def _integer(value, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError("Invalid animation byte range or count")
    return value


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate animation JSON key")
        result[key] = value
    return result


def _no_extensions(value):
    if isinstance(value, dict):
        if value.get("extensions"):
            raise ValueError("Animation extensions are unsupported")
        for key, child in value.items():
            if key != "extras":
                _no_extensions(child)
    elif isinstance(value, list):
        for child in value:
            _no_extensions(child)


def _transform(node):
    def vector(key, default):
        values = node.get(key, default)
        if (
            not isinstance(values, list)
            or len(values) != len(default)
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in values)
        ):
            raise ValueError("Invalid animation rest transform")
        return values

    if "matrix" in node:
        if any(key in node for key in ("translation", "rotation", "scale")):
            raise ValueError("Animation node mixes matrix and TRS")
        matrix = vector("matrix", [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1])
        if matrix[3::4] != [0, 0, 0, 1]:
            raise ValueError("Animation rest matrix must be affine")
        return matrix
    x, y, z, w = vector("rotation", [0, 0, 0, 1])
    if not math.isclose(x * x + y * y + z * z + w * w, 1, abs_tol=1e-5):
        raise ValueError("Invalid animation rest rotation")
    sx, sy, sz = vector("scale", [1, 1, 1])
    tx, ty, tz = vector("translation", [0, 0, 0])
    return [
        (1 - 2 * (y * y + z * z)) * sx,
        2 * (x * y + z * w) * sx,
        2 * (x * z - y * w) * sx,
        0,
        2 * (x * y - z * w) * sy,
        (1 - 2 * (x * x + z * z)) * sy,
        2 * (y * z + x * w) * sy,
        0,
        2 * (x * z + y * w) * sz,
        2 * (y * z - x * w) * sz,
        (1 - 2 * (x * x + y * y)) * sz,
        0,
        tx,
        ty,
        tz,
        1,
    ]


def _skeleton(data):
    nodes = data["nodes"]
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("Animation skeleton is empty")
    names = [node["name"] for node in nodes]
    if any(not isinstance(name, str) or not name for name in names) or len(set(names)) != len(
        names
    ):
        raise ValueError("Animation nodes need unique authored names")
    parents = {}
    hierarchy = {}
    transforms = {}
    for index, node in enumerate(nodes):
        if any(key in node for key in ("mesh", "skin", "camera", "weights")):
            raise ValueError("Animation nodes must contain skeleton data only")
        children = node.get("children", [])
        if not isinstance(children, list):
            raise ValueError("Invalid animation hierarchy")
        for child in children:
            _index(child, len(nodes))
            if child in parents or child == index:
                raise ValueError("Animation hierarchy has repeated parents or a cycle")
            parents[child] = index
        hierarchy[names[index]] = sorted(names[child] for child in children)
        transforms[names[index]] = _transform(node)
    # A forest must be reachable from its roots; this also rejects disconnected cycles.
    roots = set(range(len(nodes))) - parents.keys()
    pending = list(roots)
    visited = set()
    while pending:
        index = pending.pop()
        if index in visited:
            raise ValueError("Animation hierarchy contains a cycle")
        visited.add(index)
        pending.extend(nodes[index].get("children", []))
    if len(visited) != len(nodes):
        raise ValueError("Animation hierarchy contains a cycle")
    scenes = data.get("scenes", [])
    scene_roots = []
    for scene in scenes:
        indices = scene.get("nodes", [])
        for index in indices:
            _index(index, len(nodes))
        if len(set(indices)) != len(indices) or any(index not in roots for index in indices):
            raise ValueError("Invalid animation scene roots")
        scene_roots.append(sorted(names[index] for index in indices))
    if "scene" in data:
        _index(data["scene"], len(scenes))
    skins = data.get("skins", [])
    if not isinstance(skins, list) or len(skins) > 1:
        raise ValueError("Ambiguous animation skeleton")
    joints = skins[0]["joints"] if skins else list(range(len(nodes)))
    if not isinstance(joints, list) or not joints:
        raise ValueError("Invalid animation joints")
    for joint in joints:
        _index(joint, len(nodes))
    if len(set(joints)) != len(joints):
        raise ValueError("Repeated animation joints")
    for skin in skins:
        if "skeleton" in skin:
            _index(skin["skeleton"], len(nodes))
        if "inverseBindMatrices" in skin:
            accessor = data["accessors"][
                _index(skin["inverseBindMatrices"], len(data["accessors"]))
            ]
            if accessor["type"] != "MAT4" or accessor["count"] != len(joints):
                raise ValueError("Invalid animation inverse bind matrices")
    return (
        names,
        (hierarchy, scene_roots, data.get("scene"), sorted(names[j] for j in joints)),
        transforms,
    )


def _read_part(path):
    with path.open("rb") as source:
        magic, version, size, length, kind = struct.unpack("<4sIII4s", source.read(20))
        if (
            magic != b"glTF"
            or version != 2
            or size != os.fstat(source.fileno()).st_size
            or kind != b"JSON"
            or length % 4
            or length > size - 28
        ):
            raise ValueError("Invalid animation GLB header")
        data = json.loads(source.read(length), object_pairs_hook=_object)
        binary_length, binary_kind = struct.unpack("<I4s", source.read(8))
        if binary_kind != b"BIN\0" or binary_length % 4 or size != 28 + length + binary_length:
            raise ValueError("Invalid animation GLB binary chunk")
    if (
        data["asset"]["version"] != "2.0"
        or data.get("extensionsUsed")
        or data.get("extensionsRequired")
    ):
        raise ValueError("Unsupported animation GLB asset or extensions")
    _no_extensions(data)
    if any(data.get(key) for key in ("meshes", "materials", "images", "textures", "cameras")):
        raise ValueError("Animation GLB must contain skeleton nodes and clips only")
    buffers = data["buffers"]
    if len(buffers) != 1 or "uri" in buffers[0]:
        raise ValueError("Animation buffers must be embedded in one GLB buffer")
    byte_length = _integer(buffers[0]["byteLength"], 1)
    if not 0 <= binary_length - byte_length <= 3:
        raise ValueError("Invalid animation buffer padding")
    views = data["bufferViews"]
    for view in views:
        if type(view["buffer"]) is not int or view["buffer"] != 0:
            raise ValueError("Invalid animation buffer reference")
        start = _integer(view.get("byteOffset", 0))
        if start % 4 or start + _integer(view["byteLength"], 1) > byte_length:
            raise ValueError("Animation buffer view exceeds embedded data or is unaligned")
        if "byteStride" in view and not 4 <= _integer(view["byteStride"]) <= 252:
            raise ValueError("Invalid animation buffer stride")
    for accessor in data["accessors"]:
        view = views[_index(accessor["bufferView"], len(views))]
        width = {"SCALAR": 1, "VEC3": 3, "VEC4": 4, "MAT4": 16}[accessor["type"]] * 4
        if (
            type(accessor["componentType"]) is not int
            or accessor["componentType"] != 5126
            or "sparse" in accessor
            or accessor.get("normalized", False) is not False
        ):
            raise ValueError("Unsupported animation accessor")
        offset = _integer(accessor.get("byteOffset", 0))
        count = _integer(accessor["count"], 1)
        stride = view.get("byteStride", width)
        if (
            offset % 4
            or stride % 4
            or stride < width
            or offset + (count - 1) * stride + width > view["byteLength"]
        ):
            raise ValueError("Animation accessor exceeds buffer view or is unaligned")
    names, topology, transforms = _skeleton(data)
    for clip in data["animations"]:
        if not isinstance(clip["name"], str) or not clip["name"]:
            raise ValueError("Animation clip needs an authored name")
        for sampler in clip["samplers"]:
            for key in ("input", "output"):
                _index(sampler[key], len(data["accessors"]))
            input_accessor = data["accessors"][sampler["input"]]
            output_accessor = data["accessors"][sampler["output"]]
            interpolation = sampler.get("interpolation", "LINEAR")
            if (
                interpolation not in ("LINEAR", "STEP", "CUBICSPLINE")
                or input_accessor["type"] != "SCALAR"
                or output_accessor["type"] not in ("VEC3", "VEC4")
                or output_accessor["count"]
                != input_accessor["count"] * (3 if interpolation == "CUBICSPLINE" else 1)
            ):
                raise ValueError("Invalid animation sampler")
        for channel in clip["channels"]:
            _index(channel["sampler"], len(clip["samplers"]))
            _index(channel["target"]["node"], len(names))
            if "matrix" in data["nodes"][channel["target"]["node"]]:
                raise ValueError("Animated nodes must use TRS transforms")
    animation_summary(path)
    return data, 28 + length, byte_length, names, topology, transforms


def merge_animation_glbs(parts: list[Path], destination: Path) -> None:
    """Validate compatible animation batches, stream their BIN data, then atomically publish.

    JSON metadata is retained; binary validation is bounded to one input batch and
    copying uses at most 1 MiB. Inputs must remain unchanged during the merge.
    """
    if not parts:
        raise ValueError("No animation GLB parts")
    merged = None
    sources = []
    clip_names = set()
    binary_size = 0
    try:
        for path in parts:
            before = path.stat()
            data, offset, length, names, topology, transforms = _read_part(path)
            if merged is None:
                merged = {
                    key: data[key] for key in ("asset", "nodes", "scenes", "scene") if key in data
                }
                merged.update(animations=[], accessors=[], bufferViews=[])
                first_topology, first_transforms = topology, transforms
                node_indices = {name: index for index, name in enumerate(names)}
            elif topology != first_topology or any(
                not all(
                    math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-6)
                    for a, b in zip(first_transforms[name], transform, strict=True)
                )
                for name, transform in transforms.items()
            ):
                raise ValueError(
                    "Animation batches have differing skeleton topology or rest transforms"
                )
            view_base, accessor_base = len(merged["bufferViews"]), len(merged["accessors"])
            for view in data["bufferViews"]:
                view["byteOffset"] = view.get("byteOffset", 0) + binary_size
                merged["bufferViews"].append(view)
            for accessor in data["accessors"]:
                accessor["bufferView"] += view_base
                merged["accessors"].append(accessor)
            for clip in data["animations"]:
                if clip["name"] in clip_names:
                    raise ValueError(f"Duplicate animation clip name: {clip['name']}")
                clip_names.add(clip["name"])
                for sampler in clip["samplers"]:
                    sampler["input"] += accessor_base
                    sampler["output"] += accessor_base
                for channel in clip["channels"]:
                    target = channel["target"]
                    target["node"] = node_indices[names[target["node"]]]
                merged["animations"].append(clip)
            sources.append((path, offset, length, before))
            binary_size += length + (-length % 4)
        merged["buffers"] = [{"byteLength": binary_size}]
        payload = json.dumps(merged, allow_nan=False, separators=(",", ":")).encode("utf-8")
        payload += b" " * (-len(payload) % 4)
        size = 28 + len(payload) + binary_size
        if size > 0xFFFFFFFF:
            raise ValueError("Merged animation GLB exceeds the GLB size limit")
    except (
        KeyError,
        IndexError,
        TypeError,
        AttributeError,
        struct.error,
        UnicodeError,
        OverflowError,
    ) as error:
        raise ValueError("Invalid animation GLB structure") from error

    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp", delete=False
        ) as output:
            temporary = Path(output.name)
            output.write(struct.pack("<4sIII4s", b"glTF", 2, size, len(payload), b"JSON"))
            output.write(payload)
            output.write(struct.pack("<I4s", binary_size, b"BIN\0"))
            for path, offset, length, before in sources:
                with path.open("rb") as source:
                    current = os.fstat(source.fileno())
                    if _file_identity(current) != _file_identity(before):
                        raise ValueError("Animation input changed during merge")
                    source.seek(offset)
                    remaining = length
                    while remaining:
                        chunk = source.read(min(remaining, 1024 * 1024))
                        if not chunk:
                            raise ValueError("Truncated animation binary chunk")
                        output.write(chunk)
                        remaining -= len(chunk)
                    if _file_identity(os.fstat(source.fileno())) != _file_identity(current):
                        raise ValueError("Animation input changed during merge")
                output.write(b"\0" * (-length % 4))
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

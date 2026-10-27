"""Validate standalone, embedded glTF animation tracks before publication."""

import hashlib
import json
import math
import struct


def animation_summary(path, bone_names=None):
    """Read actual clips, node targets and binary samples, never exporter counts."""
    raw = path.read_bytes()
    try:
        magic, version, size, length, kind = struct.unpack_from("<4sIII4s", raw)
        if magic != b"glTF" or version != 2 or size != len(raw) or kind != b"JSON" or length % 4:
            raise ValueError("Invalid animation GLB header")
        data = json.loads(raw[20 : 20 + length])
        offset = 20 + length
        binary_length, binary_kind = struct.unpack_from("<I4s", raw, offset)
        binary = memoryview(raw)[offset + 8 :]
        if binary_kind != b"BIN\0" or binary_length != len(binary) or binary_length % 4:
            raise ValueError("Invalid animation GLB binary chunk")
        if any(data.get(key) for key in ("meshes", "materials", "textures", "images")):
            raise ValueError("Animation GLB must contain skeleton nodes and clips only")
        buffers = data.get("buffers", [])
        if (
            len(buffers) != 1
            or "uri" in buffers[0]
            or not 0 <= len(binary) - buffers[0]["byteLength"] <= 3
        ):
            raise ValueError("Animation buffers must be embedded")
        nodes = data["nodes"]
        names = [node["name"] for node in nodes]
        if any(not name for name in names) or len(names) != len(set(names)):
            raise ValueError("Animation nodes need unique names for bone binding")
        if any("mesh" in node or "skin" in node for node in nodes):
            raise ValueError("Animation nodes must not reference geometry")
        skins = data.get("skins", [])
        joints = skins[0]["joints"] if len(skins) == 1 else list(range(len(nodes)))
        if len(skins) > 1 or not joints or len(joints) != len(set(joints)):
            raise ValueError("Animation skeleton is empty or ambiguous")
        if any(type(index) is not int or not 0 <= index < len(nodes) for index in joints):
            raise ValueError("Invalid animation joint reference")

        def indexed(items, index):
            if type(index) is not int or not 0 <= index < len(items):
                raise ValueError("Invalid animation reference index")
            return items[index]

        def samples(index, shape):
            accessor = indexed(data["accessors"], index)
            if (
                accessor["componentType"] != 5126
                or accessor["type"] != shape
                or accessor["count"] < 1
                or "sparse" in accessor
                or accessor.get("normalized")
            ):
                raise ValueError("Invalid animation accessor")
            width = {"SCALAR": 1, "VEC3": 3, "VEC4": 4}[shape]
            view = indexed(data["bufferViews"], accessor["bufferView"])
            start = view.get("byteOffset", 0)
            end = start + view["byteLength"]
            if view["buffer"] != 0 or start < 0 or end > buffers[0]["byteLength"]:
                raise ValueError("Animation buffer view exceeds embedded data")
            relative = accessor.get("byteOffset", 0)
            stride = view.get("byteStride", width * 4)
            if (
                relative < 0
                or stride < width * 4
                or stride % 4
                or relative % 4
                or start + relative + (accessor["count"] - 1) * stride + width * 4 > end
            ):
                raise ValueError("Animation samples exceed their buffer view")
            values = [
                struct.unpack_from("<" + "f" * width, binary, start + relative + i * stride)
                for i in range(accessor["count"])
            ]
            if any(not math.isfinite(value) for row in values for value in row):
                raise ValueError("Animation contains non-finite samples")
            return values

        clips = []
        for clip in data.get("animations", []):
            targets = set()
            duration = 0
            if not clip.get("name") or not clip.get("channels"):
                raise ValueError("Animation clip is empty or unnamed")
            for channel in clip["channels"]:
                target = channel["target"]
                name = indexed(nodes, target["node"])["name"]
                if target["node"] not in joints:
                    raise ValueError("Animation target is not a skeleton joint")
                shape = {"translation": "VEC3", "rotation": "VEC4", "scale": "VEC3"}[target["path"]]
                if bone_names is not None and name not in bone_names:
                    raise ValueError(f"Animation targets a non-rig bone: {name}")
                identity = (name, target["path"])
                if identity in targets:
                    raise ValueError("Animation repeats a bone channel")
                targets.add(identity)
                sampler = indexed(clip["samplers"], channel["sampler"])
                times = [row[0] for row in samples(sampler["input"], "SCALAR")]
                values = samples(sampler["output"], shape)
                interpolation = sampler.get("interpolation", "LINEAR")
                if interpolation not in {"LINEAR", "STEP", "CUBICSPLINE"}:
                    raise ValueError("Unsupported animation interpolation")
                factor = 3 if interpolation == "CUBICSPLINE" else 1
                if (
                    len(values) != len(times) * factor
                    or times[0] < 0
                    or any(a >= b for a, b in zip(times, times[1:], strict=False))
                ):
                    raise ValueError("Invalid animation sample counts or times")
                duration = max(duration, times[-1])
            if duration <= 0:
                raise ValueError("Animation has no playable duration")
            clips.append({"name": clip["name"], "channels": len(targets), "seconds": duration})
        if not clips or len({clip["name"] for clip in clips}) != len(clips):
            raise ValueError("Animation GLB has no clips or duplicate clip names")
    except (KeyError, IndexError, TypeError, struct.error, json.JSONDecodeError) as error:
        raise ValueError("Invalid animation GLB structure") from error
    return {
        "bytes": len(raw),
        "animations": len(clips),
        "bones": len(joints),
        "textures": 0,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "clips": clips,
    }

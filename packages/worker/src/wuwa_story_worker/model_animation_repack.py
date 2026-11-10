"""Index and compact already-exported animation GLBs without rendering."""

import hashlib
import json
import math
import mmap
import os
import re
import shutil
import struct
import tempfile
from collections import Counter
from copy import deepcopy
from pathlib import Path

from .model_animation_merge import _file_identity, _read_part

_CHUNK = 1024 * 1024
_WIDTH = {"SCALAR": 4, "VEC3": 12, "VEC4": 16, "MAT4": 64}


class _MappedPath:
    """Let the existing validator inspect mapped bytes instead of reading the whole GLB."""

    def __init__(self, path):
        self.path = path
        self.mapping = None

    def open(self, *args, **kwargs):
        return self.path.open(*args, **kwargs)

    def read_bytes(self):
        with self.path.open("rb") as handle:
            self.mapping = mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ)
        return self.mapping


def _read(path):
    mapped = _MappedPath(path)
    try:
        return _read_part(mapped)
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
    finally:
        if mapped.mapping is not None:
            try:
                mapped.mapping.close()
            except BufferError:
                # The validator's exception traceback still owns its memoryview.
                # The mapping is released when that traceback is discarded.
                pass


def _digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    ).hexdigest()


def _source(clip):
    extras = clip.get("extras")
    metadata = extras.get("solaris_atlas") if isinstance(extras, dict) else None
    source = metadata.get("source") if isinstance(metadata, dict) else None
    if source is None:
        return clip["name"]
    if not isinstance(source, str) or not source.strip():
        raise ValueError("Invalid animation source")
    source = source.strip().replace("\\", "/").lower()
    return source[:-5] if source.endswith(".json") else source


def _layout(data, index, binary_offset):
    accessor = data["accessors"][index]
    view = data["bufferViews"][accessor["bufferView"]]
    width = _WIDTH[accessor["type"]]
    return (
        binary_offset + view.get("byteOffset", 0) + accessor.get("byteOffset", 0),
        accessor["count"],
        width,
        view.get("byteStride", width),
    )


def _copy_elements(handle, layout, consume):
    start, count, width, stride = layout
    handle.seek(start)
    if stride == width:
        remaining = count * width
        while remaining:
            block = handle.read(min(remaining, _CHUNK))
            if not block:
                raise ValueError("Truncated animation binary chunk")
            consume(block)
            remaining -= len(block)
    else:
        # Read bounded groups, discarding interleaved bytes and padding.
        per_chunk = max(1, _CHUNK // stride)
        while count:
            group = min(count, per_chunk)
            length = (group - 1) * stride + width
            block = handle.read(length)
            if len(block) != length:
                raise ValueError("Truncated animation binary chunk")
            for pos in range(0, length, stride):
                consume(block[pos : pos + width])
            count -= group
            if count:
                handle.seek(stride - width, os.SEEK_CUR)


def _check(handle, before):
    if _file_identity(os.fstat(handle.fileno())) != _file_identity(before):
        raise ValueError("Animation input changed during repack")


def _compatible_rigs(a, b, tolerance=1e-4):
    if a["hierarchy"] != b["hierarchy"] or a["joints"] != b["joints"]:
        return False
    if a["transforms"].keys() != b["transforms"].keys():
        return False
    if not all(
        abs(x - y) <= tolerance
        for name in a["transforms"]
        for x, y in zip(a["transforms"][name], b["transforms"][name], strict=True)
    ):
        return False

    def multiply(parent, local):
        # glTF matrices are column-major: world = parent world @ local.
        return [
            sum(parent[k * 4 + row] * local[column * 4 + k] for k in range(4))
            for column in range(4)
            for row in range(4)
        ]

    children = {child for values in a["hierarchy"].values() for child in values}
    pending = [(name, None, None) for name in a["hierarchy"] if name not in children]
    while pending:
        name, parent_a, parent_b = pending.pop()
        local_a, local_b = a["transforms"][name], b["transforms"][name]
        world_a = multiply(parent_a, local_a) if parent_a is not None else local_a
        world_b = multiply(parent_b, local_b) if parent_b is not None else local_b
        if any(
            not math.isfinite(x) or not math.isfinite(y) or abs(x - y) > tolerance
            for x, y in zip(world_a, world_b, strict=True)
        ):
            return False
        pending.extend((child, world_a, world_b) for child in a["hierarchy"][name])
    return True


def _element_blocks(handle, layout):
    start, count, width, stride = layout
    handle.seek(start)
    per_chunk = max(1, _CHUNK // stride)
    while count:
        group = min(count, per_chunk)
        length = (group - 1) * stride + width
        raw = handle.read(length)
        if len(raw) != length:
            raise ValueError("Truncated animation binary chunk")
        yield (
            raw
            if stride == width
            else b"".join(raw[pos : pos + width] for pos in range(0, length, stride))
        )
        count -= group
        if count:
            handle.seek(stride - width, os.SEEK_CUR)


def _element_block(handle, layout, start, count):
    position, _, width, stride = layout
    return next(_element_blocks(handle, (position + start * stride, count, width, stride)))


def _equivalent_animation(a, clip_a, b, clip_b, tolerance):
    def channels(data, clip):
        return {
            (data["nodes"][channel["target"]["node"]]["name"], channel["target"]["path"]): clip[
                "samplers"
            ][channel["sampler"]]
            for channel in clip["channels"]
        }

    data_a, offset_a, handle_a = a
    data_b, offset_b, handle_b = b
    channels_a, channels_b = channels(data_a, clip_a), channels(data_b, clip_b)
    if channels_a.keys() != channels_b.keys():
        return False
    for target, sampler_a in channels_a.items():
        sampler_b = channels_b[target]
        if sampler_a.get("interpolation", "LINEAR") != sampler_b.get("interpolation", "LINEAR"):
            return False
        for key in ("input", "output"):
            index_a, index_b = sampler_a[key], sampler_b[key]
            accessor_a, accessor_b = data_a["accessors"][index_a], data_b["accessors"][index_b]
            if (accessor_a["type"], accessor_a["count"]) != (
                accessor_b["type"],
                accessor_b["count"],
            ):
                return False
            # Both iterators use the same group size irrespective of interleaving.
            layout_a, layout_b = (
                _layout(data_a, index_a, offset_a),
                _layout(data_b, index_b, offset_b),
            )
            for start in range(0, accessor_a["count"], max(1, _CHUNK // 252)):
                count = min(accessor_a["count"] - start, max(1, _CHUNK // 252))
                left = _element_block(handle_a, layout_a, start, count)
                right = _element_block(handle_b, layout_b, start, count)
                if key == "input":
                    if left != right:
                        return False
                elif any(
                    abs(x[0] - y[0]) > tolerance
                    for x, y in zip(
                        struct.iter_unpack("<f", left), struct.iter_unpack("<f", right), strict=True
                    )
                ):
                    return False
    return True


def equivalent_animation(source_a, name_a, source_b, name_b, tolerance=1e-4):
    """Compare bindings and exact times, permitting bounded absolute value differences.

    This comparison alone does not prove common authored source or rig compatibility.
    """
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("Invalid animation tolerance")
    paths = [Path(source_a), Path(source_b)]
    before = [path.stat() for path in paths]
    parsed = [_read(path) for path in paths]
    clips = []
    for data, name in zip(parsed, (name_a, name_b), strict=True):
        selected = [clip for clip in data[0]["animations"] if clip["name"] == name]
        if len(selected) != 1:
            raise ValueError("Unknown animation clip")
        clips.append(selected[0])
    with paths[0].open("rb") as first, paths[1].open("rb") as second:
        _check(first, before[0])
        _check(second, before[1])
        result = _equivalent_animation(
            (parsed[0][0], parsed[0][1], first),
            clips[0],
            (parsed[1][0], parsed[1][1], second),
            clips[1],
            tolerance,
        )
        _check(first, before[0])
        _check(second, before[1])
    if any(
        _file_identity(path.stat()) != _file_identity(stat)
        for path, stat in zip(paths, before, strict=True)
    ):
        raise ValueError("Animation input changed during repack")
    return result


def _proof_source(value):
    """Normalize an authored package path without conflating different directories."""
    if not isinstance(value, str):
        return None
    value = value.strip().replace("\\", "/").casefold()
    if value.endswith(".json"):
        value = value[:-5]
    for prefix in ("/game/", "client/content/"):
        if value.startswith(prefix):
            value = value[len(prefix) :]
            break
    parts = value.split("/")
    if not parts or any(part in ("", ".", "..") for part in parts):
        return None
    if "." in parts[-1]:
        name, _, object_name = parts[-1].partition(".")
        if not name or object_name != name:
            return None
        parts[-1] = name
    return "/".join(parts)


def _raw_sources(provenance):

    files = provenance.get("source_files")
    if not isinstance(files, list):
        return {}
    hashes = {}
    for item in files:
        if not isinstance(item, dict):
            continue
        path, digest = item.get("path", item.get("package_path")), item.get("sha256")
        if (
            not isinstance(path, str)
            or not isinstance(digest, str)
            or not re.fullmatch(r"[a-fA-F0-9]{64}", digest)
        ):
            continue
        lower = path.replace("\\", "/").lower()
        for extension in (".uasset", ".uexp"):
            if lower.endswith(extension):
                source = _proof_source(lower[: -len(extension)])
                if source is not None:
                    hashes.setdefault((source, extension), set()).add(digest.lower())
    result = {}
    for source, _ in hashes:
        asset, export = hashes.get((source, ".uasset"), set()), hashes.get((source, ".uexp"), set())
        if len(asset) == len(export) == 1:
            result[source] = (next(iter(asset)), next(iter(export)))
    return result


def animation_pack_index(path):
    """Return stable rig and semantic clip fingerprints for an animation GLB."""
    path = Path(path)
    before = path.stat()
    data, offset, _, names, topology, transforms = _read(path)
    rig_signature = {
        "hierarchy": topology[0],
        "joints": topology[3],
        "transforms": {
            name: [float(value) if value else 0.0 for value in values]
            for name, values in transforms.items()
        },
    }
    rig = _digest(rig_signature)
    clips = []
    with path.open("rb") as handle:
        _check(handle, before)
        for clip in data["animations"]:
            source = _source(clip)
            channels = []
            for channel in clip["channels"]:
                target = channel["target"]
                sampler = clip["samplers"][channel["sampler"]]
                fingerprints = []
                for key in ("input", "output"):
                    index = sampler[key]
                    digest = hashlib.sha256()
                    _copy_elements(handle, _layout(data, index, offset), digest.update)
                    accessor = data["accessors"][index]
                    fingerprints.append([accessor["type"], accessor["count"], digest.hexdigest()])
                channels.append(
                    [
                        names[target["node"]],
                        target["path"],
                        sampler.get("interpolation", "LINEAR"),
                        fingerprints,
                    ]
                )
            channels.sort(key=lambda channel: (channel[0], channel[1]))
            clips.append(
                {"name": clip["name"], "source": source, "identity": _digest([source, channels])}
            )
        _check(handle, before)
    if _file_identity(path.stat()) != _file_identity(before):
        raise ValueError("Animation input changed during repack")
    return {"rig": rig, "rig_signature": rig_signature, "clips": clips}


def select_animation_pack(source, destination, names, *, renames=None):
    """Atomically write selected clips and their referenced, packed samples."""
    source, destination = Path(source), Path(destination)
    if source.resolve() == destination.resolve() or (
        destination.exists() and os.path.samefile(source, destination)
    ):
        raise ValueError("Animation source and destination must differ")
    selected = list(names)
    if (
        not selected
        or any(not isinstance(name, str) or not name for name in selected)
        or len(set(selected)) != len(selected)
    ):
        raise ValueError("Animation selection must contain unique clip names")
    before = source.stat()
    data, offset, _, _, _, _ = _read(source)
    clips = {clip["name"]: clip for clip in data["animations"]}
    if any(name not in clips for name in selected):
        raise ValueError("Unknown animation clip selection")
    renames = {} if renames is None else dict(renames)
    if set(renames) - set(selected):
        raise ValueError("Unknown animation rename")
    output_names = [renames.get(name, name) for name in selected]
    if any(not isinstance(name, str) or not name.strip() for name in output_names) or len(
        set(output_names)
    ) != len(output_names):
        raise ValueError("Invalid or duplicate animation output name")
    result = {
        key: deepcopy(data[key])
        for key in ("asset", "nodes", "scenes", "scene", "skins")
        if key in data
    }
    result.update(
        animations=[deepcopy(clips[name]) for name in selected], accessors=[], bufferViews=[]
    )
    for clip, name in zip(result["animations"], output_names, strict=True):
        clip["name"] = name
    indices = {}
    layouts = []
    size = 0

    def retain(index):
        nonlocal size
        if index not in indices:
            accessor = deepcopy(data["accessors"][index])
            view = deepcopy(data["bufferViews"][accessor["bufferView"]])
            layout = _layout(data, index, offset)
            length = layout[1] * layout[2]
            view.update(buffer=0, byteOffset=size, byteLength=length)
            view.pop("byteStride", None)
            accessor.update(bufferView=len(result["bufferViews"]), byteOffset=0)
            indices[index] = len(result["accessors"])
            result["accessors"].append(accessor)
            result["bufferViews"].append(view)
            layouts.append(layout)
            size += length
        return indices[index]

    for skin in result.get("skins", []):
        if "inverseBindMatrices" in skin:
            skin["inverseBindMatrices"] = retain(skin["inverseBindMatrices"])
    for clip in result["animations"]:
        # Discard unreferenced samplers as well as their accessors.
        used = sorted({channel["sampler"] for channel in clip["channels"]})
        remap = {old: new for new, old in enumerate(used)}
        clip["samplers"] = [clip["samplers"][i] for i in used]
        for channel in clip["channels"]:
            channel["sampler"] = remap[channel["sampler"]]
        for sampler in clip["samplers"]:
            for key in ("input", "output"):
                sampler[key] = retain(sampler[key])
    result["buffers"] = [{"byteLength": size}]
    payload = json.dumps(result, allow_nan=False, separators=(",", ":")).encode("utf-8")
    payload += b" " * (-len(payload) % 4)
    total = 28 + len(payload) + size
    if total > 0xFFFFFFFF:
        raise ValueError("Animation GLB exceeds the GLB size limit")
    temporary = None
    try:
        with (
            source.open("rb") as handle,
            tempfile.NamedTemporaryFile(
                dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp", delete=False
            ) as output,
        ):
            temporary = Path(output.name)
            _check(handle, before)
            output.write(struct.pack("<4sIII4s", b"glTF", 2, total, len(payload), b"JSON"))
            output.write(payload)
            output.write(struct.pack("<I4s", size, b"BIN\0"))
            for layout in layouts:
                _copy_elements(handle, layout, output.write)
            _check(handle, before)
            if _file_identity(source.stat()) != _file_identity(before):
                raise ValueError("Animation input changed during repack")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def repackage_bundles(bundles, destination):
    """Create a new bundle tree and return publication tuples with shared animation packs.

    ``destination`` must not exist. Inputs and metadata are never modified. Each output
    directory is named for its form ID; failures remove the newly created tree.
    """
    from wuwa_story.ingestion.model_animation_packs import validate_animation_packs

    from .model_animation_packs import owned_sources, ownership_for, write_manifest
    from .model_animations import animation_summary
    from .model_facial import validate_facial_pack

    bundles = list(bundles)
    ids = [form["id"] for form, _, _, _ in bundles]
    if (
        not ids
        or len(set(ids)) != len(ids)
        or any(
            not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_-]+", value) is None
            for value in ids
        )
    ):
        raise ValueError("Invalid or duplicate bundle form ID")
    destination = Path(destination)
    # Refuse inputs underneath the owned tree, even if the destination is missing.
    resolved = destination.resolve()
    if any(
        Path(output).resolve().is_relative_to(resolved)
        or resolved.is_relative_to(Path(output).resolve())
        for _, output, _, _ in bundles
    ):
        raise ValueError("Bundle destination overlaps an input")
    destination.mkdir()
    try:
        scope_forms = [
            {
                **form,
                "model": provenance.get("model"),
                "source": provenance.get("form_source", form.get("source") or {}),
            }
            for form, _, _, provenance in bundles
            if not form.get("component")
            and isinstance(provenance.get("model"), str)
            and provenance["model"]
        ]
        scopes = {form["id"]: form for form in scope_forms}
        states = []
        groups = {}
        for i, (form, output, summaries, provenance) in enumerate(bundles):
            output = Path(output)
            target = destination / ids[i]
            target.mkdir()
            for filename in ("preview.glb", "character.glb", "facial.json", "physics.json"):
                src = output / filename
                if src.is_file():
                    shutil.copyfile(src, target / filename)
                elif filename in ("preview.glb", "character.glb"):
                    raise ValueError(f"Missing bundle file: {filename}")
            info = {
                "form": deepcopy(form),
                "source": output,
                "target": target,
                "summaries": deepcopy(summaries),
                "provenance": deepcopy(provenance),
                "index": None,
                "animation_stat": None,
                "clips": {},
                "rows": [],
                "names": set(),
                "renames": {},
                "packs": [],
            }
            states.append(info)
            animation = output / "animations.glb"
            if not animation.is_file():
                if "animations" in summaries or provenance.get("animation_packs"):
                    raise ValueError("Missing legacy animation GLB")
                continue
            if provenance.get("animation_packs"):
                raise ValueError("Expected a legacy bundle without animation packs")
            info["animation_stat"] = animation.stat()
            index = animation_pack_index(animation)
            info["index"] = index
            rows = {row["name"]: row for row in index["clips"]}
            if form["id"] in scopes:
                rows = owned_sources(rows, ownership_for(scopes[form["id"]], scope_forms))
            identities = [row["identity"] for row in rows.values()]
            counts = Counter(identities)
            info["tokens"] = {
                row["name"]: (
                    row["identity"]
                    if counts[row["identity"]] == 1
                    else _digest(["ambiguous", row["identity"], row["name"]])
                )
                for row in rows.values()
            }
            info["rows"] = list(rows.values())
            info["source_counts"] = Counter(_proof_source(row["source"]) for row in index["clips"])
            info["raw_sources"] = _raw_sources(provenance)

        # Compare every rig against every existing member: tolerance is not transitive.
        rig_groups = []
        for i, state in enumerate(states):
            if state["index"] is None:
                continue
            signature = state["index"]["rig_signature"]
            group = next(
                (
                    g
                    for g in rig_groups
                    if all(
                        states[j]["index"]["rig"] == state["index"]["rig"]
                        or _compatible_rigs(states[j]["index"]["rig_signature"], signature)
                        for j in g
                    )
                ),
                None,
            )
            if group is None:
                group = []
                rig_groups.append(group)
            group.append(i)

        def parsed(state):
            if "parsed" not in state:
                data, offset, *_ = _read(state["source"] / "animations.glb")
                state["parsed"] = (
                    data,
                    offset,
                    {clip["name"]: clip for clip in data["animations"]},
                )
            return state["parsed"]

        def explicit(state, row):
            clip = parsed(state)[2][row["name"]]
            extras = clip.get("extras")
            meta = extras.get("solaris_atlas") if isinstance(extras, dict) else None
            return (
                isinstance(meta, dict)
                and isinstance(meta.get("source"), str)
                and bool(meta["source"].strip())
            )

        exact, candidates = {}, {}
        for rig, members in enumerate(rig_groups):
            for i in members:
                state = states[i]
                for row in state["rows"]:
                    identity = state["tokens"][row["name"]]
                    source = _proof_source(row["source"])
                    group_key = exact.get((rig, identity))
                    proof = state["raw_sources"].get(source)
                    if group_key is None and proof and state["source_counts"][source] == 1:
                        for candidate in candidates.get((rig, source), []):
                            representative = states[groups[candidate][0]]
                            other = representative["clips"][candidate[1]]
                            if (
                                i in groups[candidate]
                                or representative["source_counts"][source] != 1
                                or representative["raw_sources"].get(source) != proof
                                or not explicit(state, row)
                                or not explicit(representative, other)
                            ):
                                continue
                            data_a, offset_a, clips_a = parsed(representative)
                            data_b, offset_b, clips_b = parsed(state)
                            with (
                                (representative["source"] / "animations.glb").open("rb") as a,
                                (state["source"] / "animations.glb").open("rb") as b,
                            ):
                                _check(a, representative["animation_stat"])
                                _check(b, state["animation_stat"])
                                same = _equivalent_animation(
                                    (data_a, offset_a, a),
                                    clips_a[other["name"]],
                                    (data_b, offset_b, b),
                                    clips_b[row["name"]],
                                    1e-4,
                                )
                                _check(a, representative["animation_stat"])
                                _check(b, state["animation_stat"])
                            if same:
                                group_key = candidate
                                break
                    if group_key is None:
                        group_key = (rig, identity)
                        groups[group_key] = []
                        candidates.setdefault((rig, source), []).append(group_key)
                    if i in groups[group_key]:
                        raise ValueError("Ambiguous duplicate animation identity in bundle")
                    groups[group_key].append(i)
                    state["clips"][group_key[1]] = row
                    if group_key[1] == identity:
                        exact[(rig, identity)] = group_key

        # One pack for each set of forms sharing the same clips and rig. Shared
        # packs are named first so local collisions receive the stable suffix.
        packs = {}
        for (rig, identity), members in groups.items():
            packs.setdefault((rig, tuple(members)), []).append(identity)
        ordered = sorted(packs.items(), key=lambda item: (-len(item[0][1]), item[0]))
        for (_rig, members), identities in ordered:
            representative = states[members[0]]
            input_names, output_names = [], []
            for identity in sorted(identities):
                original = representative["clips"][identity]["name"]
                name = original
                if any(name in states[i]["names"] for i in members):
                    name = (
                        original.encode("utf-8")[:44].decode("utf-8", errors="ignore")
                        + "."
                        + identity[:12]
                    )
                if any(name in states[i]["names"] for i in members):
                    raise ValueError("Animation pack name collision")
                input_names.append(original)
                output_names.append(name)
                for i in members:
                    state = states[i]
                    state["names"].add(name)
                    state["renames"][state["clips"][identity]["name"]] = name
            work = representative["target"] / ".animation-pack.tmp"
            select_animation_pack(
                representative["source"] / "animations.glb",
                work,
                input_names,
                renames=dict(zip(input_names, output_names, strict=True)),
            )
            mapped = _MappedPath(work)
            try:
                summary = animation_summary(mapped)
            finally:
                if mapped.mapping is not None:
                    mapped.mapping.close()
            pack_id = summary["sha256"][:16]
            key = "animation_pack_" + pack_id
            first_path = None
            for i in members:
                state = states[i]
                pack_dir = state["target"] / "animation-packs"
                pack_dir.mkdir(exist_ok=True)
                target = pack_dir / (pack_id + ".glb")
                if target.exists():
                    raise ValueError("Animation pack ID collision")
                if first_path is None:
                    os.replace(work, target)
                    first_path = target
                else:
                    try:
                        os.link(first_path, target)
                    except OSError:
                        shutil.copyfile(first_path, target)
                state["packs"].append({"id": pack_id, "key": key, "clips": output_names[:]})
                state["summaries"][key] = deepcopy(summary)

        result = []
        for state in states:
            if state["index"] is not None:
                if _file_identity((state["source"] / "animations.glb").stat()) != _file_identity(
                    state["animation_stat"]
                ):
                    raise ValueError("Animation input changed during repack")
                state["summaries"].pop("animations", None)
                state["provenance"].pop("animations", None)
                facial_path = state["target"] / "facial.json"
                if facial_path.is_file():
                    facial = validate_facial_pack(
                        json.loads(facial_path.read_text(encoding="utf-8"))
                    )
                    existing = {row["name"] for row in state["index"]["clips"]}
                    reserved = {
                        clip["name"]
                        for clip in facial["clips"]
                        if clip.get("body_clip") is None or clip["name"] != clip["body_clip"]
                    }
                    clips = []
                    for clip in facial["clips"]:
                        old = clip.get("body_clip")
                        if old is not None:
                            if old not in existing:
                                raise ValueError("Facial clip refers to missing body animation")
                            if old not in state["renames"]:
                                continue
                            clip["body_clip"] = state["renames"][old]
                            if clip["name"] == old:
                                clip["name"] = clip["body_clip"]
                                if clip["name"] in reserved:
                                    clip["name"] += "." + _digest([old, clip["body_clip"]])[:12]
                                if clip["name"] in reserved:
                                    raise ValueError("Facial animation name collision")
                                reserved.add(clip["name"])
                        clips.append(clip)
                    facial["clips"] = clips
                    validate_facial_pack(facial)
                    facial_path.write_text(json.dumps(facial, ensure_ascii=False), encoding="utf-8")
                if state["packs"]:
                    validate_animation_packs(state["packs"])
                    state["provenance"]["animation_packs"] = state["packs"]
                    write_manifest(state["target"], state["packs"])
                else:
                    state["provenance"].pop("animation_packs", None)
            result.append((state["form"], state["target"], state["summaries"], state["provenance"]))
        return result
    except BaseException:
        shutil.rmtree(destination)
        raise

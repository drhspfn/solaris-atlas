"""Job-local reuse of verified body clips; appearances retain their own assets."""

import hashlib
import json
import math
import os
import shutil
from copy import deepcopy


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def package(value):
    value = value.replace("\\", "/").split(".", 1)[0].casefold()
    for prefix in ("/game/", "client/content/"):
        if value.startswith(prefix):
            return value[len(prefix):]
    return value


def variant_root(model):
    model = package(model)
    return model.split("/model/", 1)[0] + "/" if "/model/" in model else model.rsplit("/", 1)[0] + "/"


def ownership_for(form, forms):
    source = form.get("source") or {}
    role = source.get("role_id", source.get("id") if source.get("table") == "roleinfo" else None)
    inherited = []
    for base in forms:
        origin = base.get("source") or {}
        if (role is not None and origin.get("table") == "roleinfo" and origin.get("id") == role
                and origin.get("field", "MeshId") == source.get("field", "MeshId") and base.get("model")):
            inherited.append(variant_root(base["model"]))
    return {"root": variant_root(form["model"]),
            "roots": sorted({variant_root(item["model"]) for item in forms if item.get("model")}),
            "inherits": inherited, "explicit": form.get("montages", [])}


def owned_sources(sources, ownership):
    """Only explicit other-appearance folders are excluded; unknown paths remain shared."""
    if not ownership:
        return sources
    own = ownership["root"]
    allowed = {own, *ownership.get("inherits", [])}
    others = set(ownership["roots"]) - {own}
    explicit = {package(path) for path in ownership.get("explicit", [])}
    result = {}
    for key, source in sources.items():
        path = package(source["source"])
        matching = [root for root in (*others, own) if path.startswith(root)]
        closest = max(matching, key=len) if matching else None
        if closest is None or closest in allowed or path in explicit:
            result[key] = source
    return result


def clip_identity(clip):
    # One montage can export several equally-valued sequences. Preserve each
    # authored export instead of treating equal bytes as a duplicate action.
    export = clip.get("file")
    export = export.replace("\\", "/").casefold() if export else None
    return fingerprint([package(clip["source"]), export, clip["sha256"]])


def compatible_rigs(left, right):
    """Use the same 0.1 mm rest-pose tolerance as the model importer, never names alone."""
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    if len(left.get("bones", [])) != len(right.get("bones", [])) or not left.get("bones"):
        return False

    def close(a, b):
        return (len(a) == len(b) and all(type(x) in (int, float) and type(y) in (int, float)
                and math.isfinite(x) and math.isfinite(y) and math.isclose(x, y, rel_tol=0, abs_tol=1e-4)
                for x, y in zip(a, b, strict=True)))

    return close(left.get("object", []), right.get("object", [])) and all(
        a[:2] == b[:2] and close(a[2], b[2]) for a, b in zip(left["bones"], right["bones"], strict=True))


def prepare_packs(plan, cache, recipe):
    """Select reusable clips and leave only unseen clips in the Blender batch plan."""
    # Missing rest-rig evidence must never authorize sharing with another mesh.
    rig = fingerprint([plan["skeleton"], plan.get("rig_fingerprint", plan["model_sha256"]), recipe])
    namespace = fingerprint([plan["skeleton"], recipe])
    if plan.get("rig_signature"):
        rig = next((key for key, entries in cache.items() if entries
                    and all(entry.get("rig_namespace") == namespace
                            and compatible_rigs(entry.get("rig_signature"), plan["rig_signature"])
                            for entry in entries)), rig)
    entries = cache.setdefault(rig, [])
    reusable = {}
    for entry in entries:
        for identity, name in entry["identities"].items():
            reusable.setdefault(identity, (entry, name))
    old = deepcopy(plan["clips"])
    identities = [clip_identity(clip) for clip in old]
    if len(set(identities)) != len(identities):
        raise ValueError("Ambiguous duplicate animation source identity")
    selected, unseen, renames, names = {}, [], {}, set()
    for clip, identity in zip(old, identities, strict=True):
        if identity in reusable:
            entry, name = reusable[identity]
            if name in names:
                raise ValueError("Shared animation packs have colliding clip names")
            selected.setdefault(entry["id"], {"entry": entry, "clips": []})["clips"].append(name)
            renames[clip["name"]] = name
            names.add(name)
    for clip, identity in zip(old, identities, strict=True):
        if identity in reusable:
            continue
        name = clip["name"]
        if name in names:
            name = name.encode()[:44].decode(errors="ignore") + "." + identity[:12]
        if name in names:
            raise ValueError("Variant animation clip name collision")
        names.add(name)
        renames[clip["name"]] = name
        clip["name"] = clip["action"] = name
        unseen.append(clip)
    for row in plan["animations"]:
        if row.get("included_in_glb"):
            row["action"] = renames[row["action"]]
    for face in plan["facial"]["clips"]:
        if face.get("body_clip") is not None:
            before = face["body_clip"]
            face["body_clip"] = renames[before]
            if face["name"] == before:
                face["name"] = renames[before]
    plan["clips"] = unseen
    return rig, list(selected.values()), {clip_identity(clip): clip["name"] for clip in unseen}


def install_pack(output, entry, clips):
    target = output / "animation-packs" / (entry["id"] + ".glb")
    target.parent.mkdir(exist_ok=True)
    if target != entry["path"]:
        if target.exists():
            raise FileExistsError(target)
        try:
            os.link(entry["path"], target)
        except OSError:
            shutil.copyfile(entry["path"], target)
    return {"id": entry["id"], "key": "animation_pack_" + entry["id"], "clips": clips}


def write_manifest(output, packs):
    data = {"format": "solaris-animation-packs-v1", "packs": packs}
    (output / "animation-manifest.json").write_text(json.dumps(data, indent=2), encoding="utf-8")

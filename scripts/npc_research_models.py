"""Trace NPC models through exact client config references, without DB writes.

Inputs are local FModel exports. SQLite is opened read-only. No fuzzy names,
asset-name inference, network access, or application database access is used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import struct
from collections import defaultdict
from contextlib import closing
from pathlib import Path

NAMES = ("Rosemary", "Cristoforo", "Avidius", "Nyarla", "Zahira")
IRIS = "branch3.0_152_NPCIRIS"


class Row:
    """Read only the scalar/string fields specified by the shipped JS schema."""

    def __init__(self, blob):
        self.blob = blob
        self.pos = struct.unpack_from("<I", blob)[0]
        self.vtable = self.pos - struct.unpack_from("<i", blob, self.pos)[0]

    def field(self, index):
        entry = 4 + index * 2
        if entry >= struct.unpack_from("<H", self.blob, self.vtable)[0]:
            return None
        offset = struct.unpack_from("<H", self.blob, self.vtable + entry)[0]
        return self.pos + offset if offset else None

    def integer(self, index):
        at = self.field(index)
        return struct.unpack_from("<i", self.blob, at)[0] if at is not None else 0

    def text(self, index):
        at = self.field(index)
        if at is None:
            return ""
        start = at + struct.unpack_from("<I", self.blob, at)[0]
        size = struct.unpack_from("<I", self.blob, start)[0]
        if start + 4 + size > len(self.blob):
            raise ValueError("FlatBuffer string exceeds row bounds")
        return self.blob[start + 4:start + 4 + size].decode("utf-8")


def open_db(path):
    return closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True))


def walk(value, pointer=""):
    yield pointer, value
    if isinstance(value, dict):
        for key, child in value.items():
            escaped = str(key).replace("~", "~0").replace("/", "~1")
            yield from walk(child, pointer + "/" + escaped)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk(child, pointer + "/" + str(index))


def package(path):
    if not path.startswith("/Game/") or ".." in path.split("/"):
        raise ValueError(f"Invalid Unreal reference: {path}")
    return "Client/Content/" + path[6:].split(".")[0]


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def effective_base(template_components, instance_components):
    """Instance fields override template fields, including an explicit empty name."""
    return {**template_components.get("BaseInfoComponent", {}),
            **instance_components.get("BaseInfoComponent", {})}


def research(config, entities_path, locales, metadata, all_npcs=False, blueprints=()):
    sources = set()

    def rows(filename, query, parameters=()):
        path = config / filename
        sources.add(path)
        with open_db(path) as db:
            return db.execute(query, parameters).fetchall()

    localized = {}
    labels = []
    for path in sorted(locales.glob("lang_multi_text*.db")):
        sources.add(path)
        with open_db(path) as db:
            for key, name in db.execute(
                "SELECT Id, Content FROM MultiText WHERE Content IN (?,?,?,?,?)", NAMES
            ):
                labels.append({"file": str(path.resolve()), "key": key, "content": name})
                if key.startswith("Entity_"):
                    localized[key] = name

    templates = {}
    for identifier, blueprint, blob in rows(
        "db_template.db", "SELECT Id, BlueprintType, BinData FROM templateconfig"
    ):
        row = Row(blob)
        templates[blueprint] = {"id": identifier, "name": row.text(2),
                                "components": json.loads(row.text(3) or "{}")}

    sources.add(entities_path)
    entities = read_json(entities_path)
    entities_by_blueprint = defaultdict(list)
    for index, entity in enumerate(entities):
        entities_by_blueprint[entity["BlueprintType"]].append((index, entity))
    selected = {} if all_npcs else {IRIS: {"I.R.I.S."}}
    for blueprint in blueprints:
        selected.setdefault(blueprint, set())
    identity_links = []
    for blueprint, template in templates.items():
        key = template["components"].get("BaseInfoComponent", {}).get("TidName")
        if all_npcs and template["components"].get("BaseInfoComponent", {}).get("Category", {}).get("MainType") == "Npc":
            selected.setdefault(blueprint, set())
        if key in localized and not all_npcs:
            selected.setdefault(blueprint, set()).add(localized[key])
            identity_links.append({"blueprint": blueprint, "template_id": template["id"],
                                   "field": "ComponentsData.BaseInfoComponent.TidName", "key": key})
    for index, entity in enumerate(entities):
        blueprint = entity["BlueprintType"]
        components = entity.get("ComponentsData", {})
        base = effective_base(templates.get(blueprint, {}).get("components", {}), components)
        key = base.get("TidName")
        if all_npcs and base.get("Category", {}).get("MainType") == "Npc":
            selected.setdefault(blueprint, set())
        if key in localized and (not all_npcs or base.get("Category", {}).get("MainType") == "Npc"):
            selected.setdefault(blueprint, set()).add(localized[key])
            identity_links.append({"blueprint": blueprint, "entity_id": entity["EntityId"],
                                   "source": "instance" if "TidName" in components.get("BaseInfoComponent", {}) else "template",
                                   "key": key, "map_id": entity["MapId"]})

    # Resolve effective names independently of the six control labels. A shared
    # template name never makes all instances the same public person.
    name_keys = set()
    for blueprint in selected:
        key = templates.get(blueprint, {}).get("components", {}).get("BaseInfoComponent", {}).get("TidName")
        if key:
            name_keys.add(key)
    for entity in entities:
        blueprint = entity["BlueprintType"]
        if blueprint in selected:
            base = effective_base(templates.get(blueprint, {}).get("components", {}), entity.get("ComponentsData", {}))
            if base.get("TidName"):
                name_keys.add(base["TidName"])
    names_by_key = {}
    for path in sorted(locales.glob("lang_multi_text*.db")):
        with open_db(path) as db:
            for key in sorted(name_keys):
                found = db.execute("SELECT Content FROM MultiText WHERE Id=?", (key,)).fetchone()
                if found and found[0]:
                    names_by_key[key] = found[0]

    blueprint_rows = {bp: (identifier, blob) for identifier, bp, blob in rows(
        "db_blueprint.db", "SELECT Id, BlueprintType, BinData FROM blueprintconfig")}

    chains = []
    requests = set()
    for blueprint, names in sorted(selected.items()):
        template = templates.get(blueprint)
        if template is None:
            template = {"id": None, "name": None, "components": {}}
        bp = blueprint_rows.get(blueprint)
        bp_id, blob = bp if bp else (None, None)
        chain = {"names": sorted(names), "blueprint_type": blueprint,
                 "blueprint_config": {"file": "db_blueprint.db", "table": "blueprintconfig",
                                      "id": bp_id, "model_id": Row(blob).integer(4) if blob else None},
                 "template": {"file": "db_template.db", "table": "templateconfig", **template},
                 "models": [], "entities": [], "montage_references": [], "unresolved": []}
        template_key = template["components"].get("BaseInfoComponent", {}).get("TidName")
        chain["template_identity"] = {"key": template_key, "localized_name": names_by_key.get(template_key),
                                      "status": "localized" if template_key in names_by_key else "unresolved"}
        if not bp:
            chain["unresolved"].append("BlueprintConfig absent")
        if template["id"] is None:
            chain["unresolved"].append("TemplateConfig absent")
        model = template["components"].get("ModelComponent")
        if model:
            chain["models"].append({"source": "template", "pointer": "/ComponentsData/ModelComponent", "config": model})
        component_sources = [(f"template:{template['id']}", template["components"])]
        for index, entity in entities_by_blueprint[blueprint]:
            components = entity.get("ComponentsData", {})
            base = effective_base(template["components"], components)
            # All-NPC discovery excludes an instance explicitly overridden to
            # another category, even when its template category is Npc.
            if all_npcs and base.get("Category", {}).get("MainType") != "Npc":
                continue
            key = base.get("TidName")
            chain["entities"].append({"pointer": f"/{index}", "id": entity["Id"],
                                      "map_id": entity["MapId"], "entity_id": entity["EntityId"],
                                      "identity": {"key": key, "localized_name": names_by_key.get(key),
                                                   "source": "instance" if "TidName" in components.get("BaseInfoComponent", {}) else "template",
                                                   "status": "localized" if key in names_by_key else "unresolved"},
                                      "components": components})
            component_sources.append((f"entity:{entity['EntityId']}", components))
            if "ModelComponent" in components:
                # Preserve overrides explicitly; do not invent runtime merge semantics.
                chain["models"].append({"source": f"entity:{entity['EntityId']}",
                                         "pointer": f"/{index}/ComponentsData/ModelComponent",
                                         "config": components["ModelComponent"]})
        for source, components in component_sources:
            for pointer, value in walk(components):
                if isinstance(value, dict) and isinstance(value.get("MontageId"), int):
                    is_abp = value.get("IsAbp")
                    chain["montage_references"].append({"source": source, "pointer": pointer,
                                                         "id": value["MontageId"], "is_abp": is_abp})
                    if is_abp is not None:
                        requests.add((bool(is_abp), value["MontageId"]))
        if not chain["models"]:
            chain["unresolved"].append("No explicit ModelComponent; no asset-name inference attempted")
        chains.append(chain)

    montages = []
    for abp, identifier in sorted(requests):
        filename, table = ("db_AbpMontageData.db", "abpmontagedata") if abp else ("db_MontageData.db", "montagedata")
        found = rows(filename, f"SELECT BinData FROM {table} WHERE Id=?", (identifier,))
        record = {"id": identifier, "is_abp": abp, "file": filename, "table": table}
        if found:
            row = Row(found[0][0])
            fields = ("Montage", "MaleVariant", "InitState", "EndState") if abp else ("ActionMontage", "ExpressionMontage", "MouthSequence")
            record["fields"] = {name: row.text(index) for index, name in enumerate(fields, 1)}
        else:
            record["unresolved"] = "Exact montage ID absent from exported table"
        montages.append(record)

    paths = set()
    for chain in chains:
        for model in chain["models"]:
            paths.update(value for _, value in walk(model["config"]) if isinstance(value, str) and value.startswith("/Game/"))
    for record in montages:
        paths.update(value for value in record.get("fields", {}).values() if value.startswith("/Game/"))
    metadata_evidence = []
    pending = sorted(paths)
    visited = set()
    while pending:
        ref = pending.pop()
        pkg = package(ref)
        if pkg in visited:
            continue
        visited.add(pkg)
        path = metadata / (pkg + ".json")
        if not path.is_file():
            continue
        sources.add(path)
        data = read_json(path)
        refs = [{"pointer": pointer, "value": value} for pointer, value in walk(data)
                if isinstance(value, str) and value.startswith(("/Game/", "Client/Content/"))]
        skeletons = [entry for entry in refs if any(key in entry["pointer"].split("/") for key in ("Skeleton", "TargetSkeleton"))]
        animation_refs = [{"pointer": pointer + "/ObjectPath", "path": value["ObjectPath"],
                           "type": value["ObjectName"].split("'", 1)[0]}
                          for pointer, value in walk(data) if isinstance(value, dict)
                          and str(value.get("ObjectName", "")).startswith("AnimSequence'")
                          and str(value.get("ObjectPath", "")).startswith("/Game/")]
        metadata_evidence.append({"package": pkg, "file": str(path.resolve()),
                                  "exports": [{"name": row.get("Name"), "type": row.get("Type")}
                                              for row in data if isinstance(row, dict)], "references": refs,
                                  "skeletons": skeletons, "animation_references": animation_refs})
        # Follow only mesh/skeleton and montage animation references, not materials/textures.
        for entry in refs:
            if not any(key in entry["pointer"].split("/") for key in ("Skeleton", "TargetSkeleton", "SkeletalMesh", "AnimReference", "Sequence")):
                continue
            value = entry["value"]
            target = value if value.startswith("/Game/") else "/Game/" + value.removeprefix("Client/Content/")
            paths.add(target)
            pending.append(target)

    hashes = [{"file": str(path.resolve()), "bytes": path.stat().st_size,
               "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in sorted(sources)]
    matched = {name for chain in chains for name in chain["names"]}
    verified = {entry["package"]: entry for entry in metadata_evidence}
    for montage in montages:
        montage["metadata"] = []
        for field, path in montage.get("fields", {}).items():
            if not path.startswith("/Game/"):
                continue
            entry = verified.get(package(path), {})
            montage["metadata"].append({"field": field, "path": path,
                "montage_export_verified": any(row["type"] == "AnimMontage" for row in entry.get("exports", [])),
                "skeletons": entry.get("skeletons", []), "animation_references": entry.get("animation_references", [])})
    for chain in chains:
        chain["mesh_verification"] = []
        chain["abp_sequences"] = []
        for model in chain["models"]:
            model_type = model["config"].get("ModelType", {})
            mesh = model_type.get("NpcModel", {}).get("Mesh")
            if mesh:
                entry = verified.get(package(mesh), {})
                chain["mesh_verification"].append({"source": model["source"], "path": mesh,
                    "skeletal_mesh_export_verified": any(row["type"] == "SkeletalMesh" for row in entry.get("exports", [])),
                    "skeletons": entry.get("skeletons", []), "metadata_file": entry.get("file")})
            abp = model_type.get("Abp")
            if abp:
                entry = verified.get(package(abp), {})
                chain["abp_sequences"].append({"abp": abp, "references": entry.get("animation_references", []),
                    "skeletons": entry.get("skeletons", []),
                    "idle_status": "Explicit ABP sequence references; metadata does not establish runtime default state"})
    return {"scope": "Local NPC model and animation source chains only; read-only inputs",
            "mode": "all-npcs" if all_npcs else "controls",
            "instance_identity_rule": "Chain names are research anchors, not an identity assertion for every instance. Instance TidName overrides template TidName, including empty values.",
            "identity_basis": "Exact BlueprintType; other NPCs via exact localized Entity TidName, never asset names. Speaker labels alone do not prove model identity.",
            "localization": labels, "identity_links": identity_links,
            "unresolved_names": sorted(set(NAMES) - matched), "chains": chains, "montages": montages,
            "asset_paths": sorted(paths), "metadata": metadata_evidence,
            "missing_metadata": sorted({package(path) for path in paths} - verified.keys()), "sources": hashes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    base = Path("scratch/npc-research-20261106")
    parser.add_argument("--config", type=Path, default=base / "raw/Client/Content/Aki/ConfigDB")
    parser.add_argument("--entities", type=Path, default=Path("E:/Caches/solaris-model-lab/quest-story-proof/levelentityconfig.json"))
    parser.add_argument("--locales", type=Path, default=Path("scratch/event-catalog/en"))
    parser.add_argument("--metadata", type=Path, default=base / "metadata/assets")
    parser.add_argument("--all-npcs", action="store_true", help="Discover Npc-category templates/instances; never exports assets")
    parser.add_argument("--blueprint", action="append", default=[], help="Additional exact BlueprintType to trace (repeatable)")
    parser.add_argument("--output", type=Path, default=base / "models-evidence.json")
    args = parser.parse_args()
    result = research(args.config, args.entities, args.locales, args.metadata, args.all_npcs, args.blueprint)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    filters = sorted({package(path) + suffix for path in result["asset_paths"] for suffix in (".uasset", ".uexp")})
    args.output.with_suffix(".filters.txt").write_text("|".join(filters), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "chains": len(result["chains"]),
                      "montages": len(result["montages"]), "metadata": len(result["metadata"]),
                      "unresolved_names": result["unresolved_names"]}))


if __name__ == "__main__":
    main()

"""Command line interface for versioned, deterministic filesystem builds."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .core import Writer, canonical_json, git_commit, is_ignored_fs_entry, read_json, sha256_file
from .entities import compile_entities
from .flow import compile_flow
from .inventory import REQUIRED_NARRATIVE_TABLES, scan, strict_coverage_findings
from .localization import aggregate_locales, build_localization
from .media import compile_media
from .quests import compile_quests
from .references import compile_reference_tables

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = ROOT / "upstream" / "WutheringWaves_Data"
DEFAULT_DIST = ROOT / "dist"
BENCHMARKS = (119000000, 168800009, 139000039, 915700000,
              880000034, 880000036, 880000038)


def _version(data_root: Path) -> tuple[str, str | None]:
    readme = (data_root / "README.md").read_text(encoding="utf-8")
    game = re.search(r"Game Version:\s*([^<\n]+)", readme)
    resource = re.search(r"Resource Version:\s*([^<\n]+)", readme)
    if not game:
        raise ValueError(f"Game Version not found in {data_root / 'README.md'}")
    return game.group(1).strip(), resource.group(1).strip() if resource else None


def _action_schema(data_root: Path) -> tuple[list[str], list[str]]:
    names: set[str] = set()
    types: set[str] = set()
    for row in read_json(data_root / "BinData/flowState/flowstate.json"):
        for action in json.loads(row.get("Actions") or "[]"):
            if action.get("Name"):
                names.add(action["Name"])
            for talk in (action.get("Params") or {}).get("TalkItems") or []:
                types.add(talk.get("Type") or "")
    return sorted(names), sorted(types)


def _baseline(data_root: Path, inventory: dict[str, Any]) -> dict[str, Any]:
    names, types = _action_schema(data_root)
    return {"unsupported_candidate_schemas": {
        row["file"]: hashlib.sha256(canonical_json([row.get("field_paths", []), row.get("all_field_names", [])]).encode()).hexdigest()
        for row in inventory["tables"] if row["status"] in ("unknown", "partially_supported")},
        "supported_schemas": {
            row["file"]: hashlib.sha256(canonical_json([row.get("field_paths", []), row.get("all_field_names", [])]).encode()).hexdigest()
            for row in inventory["tables"] if row["status"] == "supported"},
        "action_names": names, "talk_item_types": types}


def _schema_diagnostics(current: dict[str, Any], baseline: dict[str, Any], writer: Writer) -> int:
    failures = 0
    for section in ("unsupported_candidate_schemas", "supported_schemas"):
        for path, fingerprint in current[section].items():
            previous = baseline.get(section, {}).get(path)
            if previous is None or previous != fingerprint:
                writer.diagnostic("schema_drift", "error", path, "$",
                                  {"kind": "added" if previous is None else "changed", "section": section})
                failures += 1
    for kind in ("action_names", "talk_item_types"):
        new = sorted(set(current[kind]) - set(baseline.get(kind, [])))
        if new:
            writer.diagnostic("schema_drift", "error", "BinData/flowState/flowstate.json", "$",
                              {"kind": kind, "new": new})
            failures += len(new)
    return failures


def _copy_raw_snapshot(data_root: Path, out: Path, inventory: dict[str, Any]) -> dict[str, Any]:
    evidence = []
    for row in inventory["tables"]:
        source = data_root / row["file"]
        if not source.is_file():
            continue
        target = out / "raw-evidence" / row["file"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        evidence.append({"file": row["file"], "sha256": sha256_file(target),
                         "status": row["status"], "bytes": target.stat().st_size})
    path = out / "raw-evidence" / "index.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"files": len(evidence), "bytes": sum(row["bytes"] for row in evidence)}


def _quest_graphs(out: Path, version: str) -> dict[str, int]:
    graph_file = out / "graphs/global.jsonl"
    adjacency: dict[str, list[dict[str, Any]]] = defaultdict(list)
    total = 0
    if graph_file.exists():
        with graph_file.open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                adjacency[row["from"]].append(row)
                total += 1
    quest_file = out / "entities/quest.jsonl"
    output_dir = out / "graphs/quests"
    output_dir.mkdir(parents=True, exist_ok=True)
    index_path = out / "indexes/quest_graphs.jsonl"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    blocked = {"requires_quest", "references_quest"}
    with quest_file.open(encoding="utf-8") as handle, index_path.open("w", encoding="utf-8") as index_handle:
        for line in handle:
            quest = json.loads(line)
            start = quest["id"]
            seen = {start}
            queue = deque([start])
            edges = []
            while queue:
                node = queue.popleft()
                for edge in adjacency.get(node, []):
                    if edge["type"] in blocked:
                        continue
                    edges.append(edge)
                    target = edge["to"]
                    if target not in seen:
                        seen.add(target)
                        queue.append(target)
            numeric = quest["quest_id"]
            graph = {"quest_id": numeric, "version": quest["version"],
                     "node_ids": sorted(seen), "edges": edges}
            (output_dir / f"{numeric}.json").write_text(
                json.dumps(graph, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8")
            index_handle.write(canonical_json({"quest_id": numeric, "nodes": len(seen),
                                               "edges": len(edges), "file": f"graphs/quests/{numeric}.json"}) + "\n")
    (out / "graphs/global.json").write_text(json.dumps({"version": version,
        "nodes": "entities/*.jsonl", "edges": "graphs/global.jsonl", "edge_count": total},
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"global_edges": total}


def build(data_root: Path, dist_root: Path, requested_version: str | None, strict: bool,
          strict_coverage: bool = False,
          baseline_path: Path | None = None) -> int:
    game_version, resource_version = _version(data_root)
    if requested_version and requested_version != game_version:
        raise ValueError(f"Requested {requested_version}, source README says {game_version}")
    final_out = dist_root / game_version
    out = dist_root / f".{game_version}.building"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    print("Scanning all BinData/Textmaps schemas...", flush=True)
    inventory = scan(data_root)
    (out / "coverage.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n",
                                       encoding="utf-8")
    source_commit = git_commit(data_root)
    writer = Writer(out, game_version, "Arikatsu/WutheringWaves_Data", source_commit)
    current_schema = _baseline(data_root, inventory)
    if baseline_path is None:
        baseline_path = Path(__file__).resolve().parent / "schemas" / "baseline-3.6.json"
    if baseline_path.exists():
        baseline = read_json(baseline_path)
        schema_failures = _schema_diagnostics(current_schema, baseline, writer)
    else:
        schema_failures = 1
        writer.diagnostic("missing_schema_baseline", "error", str(baseline_path), "$", None)
        baseline = {"action_names": []}
    for row in inventory["tables"]:
        if row["status"] in ("unknown", "partially_supported"):
            writer.diagnostic("unknown_table" if row["status"] == "unknown" else "partial_table",
                              "info", row["file"], "$",
                              {"rows": row.get("record_count"), "shape": row.get("shape"),
                               "raw_evidence": f"raw-evidence/{row['file']}"})
    print("Exporting all locales...", flush=True)
    english = build_localization(data_root, out / "localization",
        emit=lambda _kind, row: writer.emit("localization_file", {
            "id": f"locale:{row['locale']}", "locale": row["locale"],
            "path": f"localization/{row['locale']}.jsonl", "counts": row["counts"],
            "source": {"file": f"Textmaps/{row['locale']}"}, "raw": row["counts"]}),
        diagnostic=writer.diagnostic)
    localization_key_count = aggregate_locales(out / "localization")
    localize = lambda key: {"key": key, "en": english[key]["content"],
                            "source": english[key]["source"],
                            "resolution": english[key].get("resolution", "resolved_nonempty")} if key in english else {
                                "key": key, "en": None, "source": None, "resolution": "missing_key"}
    print("Compiling quests, entities, media and all FlowStates...", flush=True)
    paths = {name: data_root / path for name, path in {
        "quest_data": "BinData/QuestData/questdata.json",
        "quest_nodes": "BinData/QuestNodeData/questnodedata.json",
        "plot_handbook": "BinData/PlotHandBook/plothandbookconfig.json",
        "flow": "BinData/flow/flow.json", "flow_state": "BinData/flowState/flowstate.json"}.items()}
    compile_quests(paths, writer.emit, writer.edge, writer.diagnostic, localize)
    compile_entities(data_root, writer, english)
    compile_media(data_root, writer.emit, writer.edge, writer.diagnostic, localize)
    flow_counts = compile_flow(data_root, writer, english, set(baseline.get("action_names", [])))
    reference_counts = compile_reference_tables(data_root, writer, localize, inventory)
    talk_accounted = (flow_counts.get("talk_item", 0) + flow_counts.get("narration", 0) +
                      flow_counts.get("phone_message", 0) + flow_counts.get("player_choice", 0) -
                      flow_counts.get("inline_options", 0))
    if talk_accounted != flow_counts.get("raw_talk_items", 0):
        writer.diagnostic("talk_item_coverage_mismatch", "error", "BinData/flowState/flowstate.json", "$",
                          {"raw": flow_counts.get("raw_talk_items", 0), "accounted": talk_accounted})
    baseline_table_paths = set(baseline.get("unsupported_candidate_schemas", {})) | set(baseline.get("supported_schemas", {}))
    unsupported_talk = [item for item in writer.diagnostics if item["code"] in {
        "unknown_talk_item_type", "unsupported_talk_item", "unsupported_talk_items_schema",
        "talk_item_coverage_mismatch"}]
    coverage_findings = strict_coverage_findings(inventory["tables"], baseline_table_paths,
                                                 len(unsupported_talk), REQUIRED_NARRATIVE_TABLES)
    coverage_failures = bool(coverage_findings["new_tier1_tables"] or
                             coverage_findings["unclassified_tier1_tables"] or
                             coverage_findings["unknown_strong_reference_fields"] or
                             coverage_findings["unclassified_talk_diagnostics"] or
                             coverage_findings["missing_required_tables"])
    if strict_coverage:
        for file in coverage_findings["new_tier1_tables"]:
            row = next(row for row in inventory["tables"] if row["file"] == file)
            writer.diagnostic("strict_coverage_new_tier1_table", "error", file, "$",
                              {"fields": row.get("tier1_fields", [])})
        for file in coverage_findings["unclassified_tier1_tables"]:
            writer.diagnostic("strict_coverage_unclassified_tier1", "error", file, "$", None)
        for file, fields in coverage_findings["unknown_strong_reference_fields"].items():
            row = next(row for row in inventory["tables"] if row["file"] == file)
            writer.diagnostic("strict_coverage_unknown_reference_field", "error", row["file"], "$",
                              fields)
        for file in coverage_findings["missing_required_tables"]:
            writer.diagnostic("strict_coverage_missing_required_table", "error", file, "$", None)
    stats = writer.finish()
    graph_stats = _quest_graphs(out, game_version)
    evidence_stats = _copy_raw_snapshot(data_root, out, inventory)
    tier1_unknown = [row for row in inventory["tables"]
                     if row.get("narrative_tier") == "tier1" and row.get("status") == "unknown"]
    coverage_summary = {
        "tables": {"tier1": {"total": sum(row.get("narrative_tier") == "tier1" for row in inventory["tables"]),
                              "normalized": sum(row.get("narrative_tier") == "tier1" and row["status"] == "supported" for row in inventory["tables"]),
                              "partially_normalized": sum(row.get("narrative_tier") == "tier1" and row["status"] == "partially_supported" for row in inventory["tables"]),
                              "unknown": len(tier1_unknown)},
                    "tier2": {"total": sum(row.get("narrative_tier") == "tier2" for row in inventory["tables"]),
                              "unknown": inventory["summary"]["tier2_unknown"]},
                    "tier3": {"total": sum(row.get("narrative_tier") == "tier3" for row in inventory["tables"]),
                              "unknown": inventory["summary"]["tier3_unknown"]}},
        "talk_items": {"raw_total": flow_counts.get("raw_talk_items", 0),
                       "normalized_total": talk_accounted,
                       "unclassified": len(unsupported_talk)},
        "plot_audio": {"total": stats["entity_counts"].get("voice_reference", 0),
                       "referenced": flow_counts.get("voiced_talk_items", 0),
                       "unreferenced": max(0, stats["entity_counts"].get("voice_reference", 0) -
                                           flow_counts.get("referenced_voice_ids", 0))},
        "cutscenes": {"resolved": flow_counts.get("resolved_cutscenes", 0),
                      "unresolved": flow_counts.get("unresolved_cutscenes", 0)},
        "additional_sources": reference_counts,
        "strict_coverage": {"failures": int(coverage_failures),
                            **coverage_findings},
        "localization": {**(read_json(out / "localization/coverage.json")
                             if (out / "localization/coverage.json").exists() else {}),
                         "referenced_missing_keys": sorted({str(item["detail"]) for item in writer.diagnostics
                                                            if item["code"] == "unresolved_localization"})},
    }
    (out / "coverage-summary.json").write_text(
        json.dumps(coverage_summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    timestamp = datetime.fromtimestamp(int(epoch), timezone.utc) if epoch else datetime.now(timezone.utc)
    manifest = {"schema_version": "1", "game_version": game_version,
                "resource_version": resource_version, "source_repository": "Arikatsu/WutheringWaves_Data",
                "source_commit": source_commit,
                "extraction_timestamp": timestamp.isoformat(),
                "schema_inventory_hash": inventory["schema_inventory_hash"],
                "data_determinism": "All content files are stable for a pinned source commit; set SOURCE_DATE_EPOCH for a stable manifest timestamp.",
                "statistics": {**stats, **graph_stats, "localization_keys": localization_key_count,
                               "flow_coverage": flow_counts,
                               "additional_source_records": reference_counts,
                               "raw_evidence": evidence_stats},
                "strict_schema_failures": schema_failures,
                "strict_coverage_failures": int(coverage_failures)}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                      encoding="utf-8")
    print(f"Compiled {out}: {stats['entity_counts']} / {stats['diagnostic_count']} diagnostics", flush=True)
    if (strict and (schema_failures or any(d["severity"] == "error" for d in writer.diagnostics))) or (strict_coverage and coverage_failures):
        print(f"Strict build failed; artifacts and diagnostics remain at {out}.", file=sys.stderr)
        return 2
    previous = dist_root / f".{game_version}.previous"
    if previous.exists():
        shutil.rmtree(previous)
    if final_out.exists():
        final_out.rename(previous)
    try:
        out.rename(final_out)
    except OSError:
        if previous.exists() and not final_out.exists():
            previous.rename(final_out)
        raise
    if previous.exists():
        shutil.rmtree(previous)
    print(f"Published {final_out}", flush=True)
    return 0


def _diff(left: Path, right: Path, out: Path) -> dict[str, Any]:
    def entities(path: Path):
        result: dict[str, dict[str, Any]] = {}
        for file in sorted(file for file in (path / "entities").glob("*.jsonl")
                           if not is_ignored_fs_entry(file)):
            with file.open(encoding="utf-8") as handle:
                for line in handle:
                    row = json.loads(line)
                    source = row.get("source") or {}
                    comparable = {key: value for key, value in row.items()
                                  if key not in ("version", "source_repository", "source_commit")}
                    result[row["id"]] = {
                        "hash": hashlib.sha256(canonical_json(comparable).encode()).hexdigest(),
                        "source": (source.get("file"), source.get("raw_path")),
                        "localized": hashlib.sha256(canonical_json(
                            {k: row.get(k) for k in ("localization", "display_name", "name", "texts")}).encode()).hexdigest(),
                        "action_guid": row.get("action_guid"),
                        "kind": row.get("kind"),
                    }
        return result
    a, b = entities(left), entities(right)
    categories: dict[str, list[str]] = {k: [] for k in
        ("added", "removed", "changed", "moved", "localization_changed",
         "graph_changed", "references_changed", "asset_changed")}
    for identifier in sorted(set(a) | set(b)):
        if identifier not in a:
            categories["added"].append(identifier)
        elif identifier not in b:
            categories["removed"].append(identifier)
        elif a[identifier]["hash"] != b[identifier]["hash"]:
            categories["changed"].append(identifier)
            old, new = a[identifier], b[identifier]
            if old["source"] != new["source"]:
                categories["moved"].append(identifier)
            if old["localized"] != new["localized"]:
                categories["localization_changed"].append(identifier)
            if old["kind"] in ("asset_reference", "cutscene_variant", "voice_reference", "audio_event"):
                categories["asset_changed"].append(identifier)
    removed_ids = set(categories["removed"])
    old_guids = {v["action_guid"]: k for k, v in a.items() if v["action_guid"] and k in removed_ids}
    for identifier in categories["added"]:
        guid = b[identifier]["action_guid"]
        if guid and guid in old_guids:
            categories["moved"].append(f"{old_guids[guid]} -> {identifier} (ActionGuid={guid})")

    for locale_file in sorted(path for path in (left / "localization").glob("*.jsonl")
                              if path.name != "all-locales.jsonl" and not is_ignored_fs_entry(path)):
        other = right / "localization" / locale_file.name
        if not other.exists():
            categories["localization_changed"].append(locale_file.name + ":removed")
            continue
        if sha256_file(locale_file) == sha256_file(other):
            continue
        def localized_rows(path: Path):
            values = {}
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    row = json.loads(line)
                    values[(row["namespace"], str(row["key"]))] = row.get("content")
            return values
        old_locale, new_locale = localized_rows(locale_file), localized_rows(other)
        for key in sorted(set(old_locale) | set(new_locale)):
            if old_locale.get(key) != new_locale.get(key):
                categories["localization_changed"].append(f"{locale_file.stem}:{key[0]}:{key[1]}")
    for locale_file in sorted(path for path in (right / "localization").glob("*.jsonl")
                              if path.name != "all-locales.jsonl" and not is_ignored_fs_entry(path)):
        if not (left / "localization" / locale_file.name).exists():
            categories["localization_changed"].append(locale_file.name + ":added")
    def edge_hash(path: Path) -> str:
        file = path / "graphs/global.jsonl"
        if not file.exists():
            return ""
        digest = hashlib.sha256()
        with file.open(encoding="utf-8") as handle:
            for line in handle:
                edge = json.loads(line)
                comparable = {key: value for key, value in edge.items()
                              if key not in ("version", "source_repository", "source_commit")}
                digest.update(canonical_json(comparable).encode())
                digest.update(b"\n")
        return digest.hexdigest()
    if edge_hash(left) != edge_hash(right):
        categories["graph_changed"].append("graphs/global.jsonl")
        categories["references_changed"].append("graphs/global.jsonl")
    result = {"from": left.name, "to": right.name, "categories": categories,
              "counts": {k: len(v) for k, v in categories.items()},
              "identity_basis": "stable raw/source IDs; localized text is never primary identity"}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wuwa-narrative")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--dist", type=Path, default=DEFAULT_DIST)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("scan")
    build_parser = sub.add_parser("build")
    build_parser.add_argument("--version")
    build_parser.add_argument("--strict", action="store_true")
    build_parser.add_argument("--strict-coverage", action="store_true")
    quest_parser = sub.add_parser("quest")
    quest_parser.add_argument("quest_id", type=int)
    quest_parser.add_argument("--version")
    diff_parser = sub.add_parser("diff")
    diff_parser.add_argument("from_version")
    diff_parser.add_argument("to_version")
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--version")
    validate_parser.add_argument("--output-only", action="store_true",
                                 help="validate compiled entities and graph without the raw source snapshot")
    coverage_parser = sub.add_parser("coverage")
    coverage_parser.add_argument("--version")
    args = parser.parse_args(argv)
    data_root = args.data.resolve()
    game_version = getattr(args, "version", None) or _version(data_root)[0]
    out = args.dist.resolve() / game_version
    if args.command == "scan":
        inventory = scan(data_root)
        out.mkdir(parents=True, exist_ok=True)
        (out / "coverage.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n",
                                           encoding="utf-8")
        print(json.dumps(inventory["summary"], indent=2))
        return 0
    if args.command == "build":
        return build(data_root, args.dist.resolve(), args.version, args.strict,
                     args.strict_coverage)
    if args.command == "quest":
        path = out / "graphs/quests" / f"{args.quest_id}.json"
        if not path.exists():
            print(f"Quest graph not built: {path}", file=sys.stderr)
            return 1
        graph = read_json(path)
        print(json.dumps({"file": str(path), "quest_id": args.quest_id,
                          "nodes": len(graph["node_ids"]), "edges": len(graph["edges"])}, indent=2))
        return 0
    if args.command == "diff":
        result = _diff(args.dist.resolve() / args.from_version,
                       args.dist.resolve() / args.to_version,
                       args.dist.resolve() / "diffs" / f"{args.from_version}..{args.to_version}.json")
        print(json.dumps(result["counts"], indent=2))
        return 0
    if args.command == "validate":
        manifest = read_json(out / "manifest.json")
        diagnostics = read_json(out / "diagnostics.json")
        errors = [row for row in diagnostics["items"] if row["severity"] == "error"]
        identifiers: set[str] = set()
        duplicate_ids = 0
        for path in sorted(path for path in (out / "entities").glob("*.jsonl")
                            if not is_ignored_fs_entry(path)):
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    identifier = json.loads(line)["id"]
                    duplicate_ids += identifier in identifiers
                    identifiers.add(identifier)
        dangling = 0
        missing_provenance = 0
        edge_count = 0
        with (out / "graphs/global.jsonl").open(encoding="utf-8") as handle:
            for line in handle:
                edge = json.loads(line)
                edge_count += 1
                dangling += edge["from"] not in identifiers or edge["to"] not in identifiers
                missing_provenance += not all(edge.get(field) for field in
                                             ("type", "basis", "source", "raw_path", "version"))
        evidence_mismatch = 0
        if not args.output_only:
            for evidence in read_json(out / "raw-evidence/index.json"):
                path = out / "raw-evidence" / evidence["file"]
                evidence_mismatch += not path.exists() or sha256_file(path) != evidence["sha256"]
        result = {"version": manifest["game_version"], "diagnostics": diagnostics["count"],
                  "errors": len(errors), "schema_hash": manifest["schema_inventory_hash"],
                  "entities": len(identifiers), "edges": edge_count,
                  "duplicate_ids": duplicate_ids, "dangling_edges": dangling,
                  "edges_missing_provenance": missing_provenance,
                  "raw_evidence_hash_mismatches": evidence_mismatch,
                  "raw_evidence_check": "skipped" if args.output_only else "verified"}
        print(json.dumps(result, indent=2))
        return 2 if errors or duplicate_ids or dangling or missing_provenance or evidence_mismatch else 0
    if args.command == "coverage":
        print(json.dumps(read_json(out / "coverage.json")["summary"], indent=2))
        return 0
    return 1

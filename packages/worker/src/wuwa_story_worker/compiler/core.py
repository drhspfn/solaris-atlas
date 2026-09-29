"""Filesystem emitters and source provenance shared by compiler passes."""
from __future__ import annotations

import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any


def is_ignored_fs_entry(path: str | Path) -> bool:
    """Return true for macOS metadata files that are not dataset records."""
    name = Path(path).name
    return name == ".DS_Store" or name.startswith("._")


def read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_commit(path: Path) -> str | None:
    result = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                            capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


class Writer:
    """Write stable per-kind JSONL records and a typed reference graph."""

    def __init__(self, out: Path, version: str, repository: str | None = None,
                 commit: str | None = None):
        self.out = out
        self.version = version
        self.repository = repository
        self.commit = commit
        self.handles: dict[str, Any] = {}
        self.ids: set[str] = set()
        self.kinds: Counter[str] = Counter()
        self.edge_counts: Counter[str] = Counter()
        self.edges_for_validation: list[tuple[str, str, str, str]] = []
        self.diagnostics: list[dict[str, Any]] = []
        out.mkdir(parents=True, exist_ok=True)

    def _handle(self, kind: str):
        if kind not in self.handles:
            path = self.out / "entities" / f"{kind}.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            self.handles[kind] = path.open("w", encoding="utf-8")
        return self.handles[kind]

    def emit(self, kind: str, record: dict[str, Any]) -> None:
        identifier = record.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise ValueError(f"{kind} record has no stable id")
        if identifier in self.ids:
            self.diagnostic("duplicate_id", "error", str(record.get("source", "")),
                            str(record.get("raw_path", "")), identifier)
            return
        self.ids.add(identifier)
        self.kinds[kind] += 1
        row = {"kind": kind, "version": self.version,
               "source_repository": self.repository, "source_commit": self.commit, **record}
        self._handle(kind).write(canonical_json(row) + "\n")

    def edge(self, from_id: str, to_id: str, kind: str, basis: str,
             source: str, raw_path: str, extra: dict[str, Any] | None = None) -> None:
        if not from_id or not to_id:
            self.diagnostic("unresolved_reference", "warning", source, raw_path,
                            {"from": from_id, "to": to_id, "type": kind})
            return
        path = self.out / "graphs" / "global.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        if "_edge_handle" not in self.__dict__:
            self._edge_handle = path.open("w", encoding="utf-8")
        relation = ("source_array_adjacency" if kind == "next_source_action" else
                    "authored_order" if kind in ("next_authored_plot_step", "next_authored_talk") else
                    "runtime_condition" if kind in ("condition_slot", "checks_quest_node", "requires_quest",
                                                    "has_condition_branch") else
                    "explicit_reference")
        row = {"from": from_id, "to": to_id, "type": kind, "basis": basis,
               "source": source, "raw_path": raw_path, "version": self.version,
               "source_repository": self.repository, "source_commit": self.commit,
               "relation": relation}
        if extra:
            row.update(extra)
        self._edge_handle.write(canonical_json(row) + "\n")
        self.edges_for_validation.append((from_id, to_id, kind, raw_path))
        self.edge_counts[kind] += 1

    def diagnostic(self, code: str, severity: str, source: str,
                   raw_path: str, detail: Any) -> None:
        self.diagnostics.append({"code": code, "severity": severity,
                                 "source": source, "raw_path": raw_path,
                                 "detail": detail, "version": self.version})

    def finish(self) -> dict[str, Any]:
        for handle in self.handles.values():
            handle.close()
        if "_edge_handle" in self.__dict__:
            self._edge_handle.close()
        for source_id, target_id, edge_type, raw_path in self.edges_for_validation:
            if source_id not in self.ids or target_id not in self.ids:
                self.diagnostic("dangling_reference", "warning", "graphs/global.jsonl", raw_path,
                                {"from": source_id, "to": target_id, "type": edge_type,
                                 "missing": [v for v in (source_id, target_id) if v not in self.ids]})
        self.diagnostics.sort(key=lambda d: (d["code"], d["source"], d["raw_path"], canonical_json(d["detail"])))
        (self.out / "diagnostics.json").write_text(
            json.dumps({"count": len(self.diagnostics), "items": self.diagnostics},
                       ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"entity_counts": dict(sorted(self.kinds.items())),
                "edge_counts": dict(sorted(self.edge_counts.items())),
                "diagnostic_count": len(self.diagnostics)}

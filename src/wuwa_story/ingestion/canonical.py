from collections.abc import Iterator
from pathlib import Path
from typing import Any


def detect_compiled_dataset(source: Path) -> bool:
    """Recognize the deterministic compiler's versioned filesystem output."""
    manifest = source / "manifest.json"
    evidence_index = source / "raw-evidence" / "index.json"
    if not manifest.is_file() or not evidence_index.is_file():
        return False
    try:
        import json

        value: Any = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(
        value.get("game_version")
        and value.get("source_repository")
        and value.get("schema_inventory_hash")
    )


class CanonicalRecordAdapter:
    """Read-only adapter boundary for canonical nodes and source-backed edges.

    It intentionally leaves semantic event/claim generation to a later subsystem.
    """

    def __init__(self, dataset: Path) -> None:
        if not detect_compiled_dataset(dataset):
            raise ValueError(f"Not a compiled WuWa dataset: {dataset}")
        self.root = dataset

    def entity_files(self) -> list[Path]:
        return sorted((self.root / "entities").glob("*.jsonl"))

    def iter_entities(self, kind: str) -> Iterator[dict[str, Any]]:
        import json

        path = self.root / "entities" / f"{kind}.jsonl"
        if not path.exists():
            return
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid canonical JSONL at {path}:{line_number}") from exc
                if not isinstance(record, dict) or not isinstance(record.get("id"), str):
                    raise ValueError(f"Canonical record lacks string id at {path}:{line_number}")
                yield record

    def iter_edges(self) -> Iterator[dict[str, Any]]:
        import json

        path = self.root / "graphs" / "global.jsonl"
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid edge JSONL at {path}:{line_number}") from exc
                required = ("from", "to", "type", "basis", "source", "raw_path", "version")
                if not isinstance(record, dict) or any(key not in record for key in required):
                    raise ValueError(
                        f"Canonical edge lacks required provenance at {path}:{line_number}"
                    )
                yield record

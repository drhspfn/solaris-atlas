"""Private, disposable cache for independently discovered model components."""

import hashlib
import importlib.metadata
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

from wuwa_story_worker.client_assets import matches

logger = logging.getLogger(__name__)
MAX_RECORD_BYTES = 1024 * 1024
MAX_RECORDS = 128


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def input_key(plan, client, forms, include_animations, tools):
    """Verify actual mounted input, including unexpected extra archives, before reuse."""
    game = client / "game"
    expected = {item["path"] for item in plan["files"]}
    actual = {path.relative_to(game).as_posix() for path in game.rglob("*")
              if path.is_file() and path.suffix.lower() in {".pak", ".sig", ".utoc", ".ucas"}}
    if actual != expected:
        raise ValueError("Installed game archives differ from the trusted plan")
    for item in plan["files"]:
        path = game / item["path"]
        if path.is_symlink() or not matches(path, item):
            raise ValueError(f"Installed game archive does not match the trusted plan: {item['path']}")
    # Hash the complete tool directories: the executable loads native libraries and data beside it.
    tool_files = {}
    for tool in tools:
        for path in sorted(tool.parent.rglob("*")):
            if path.is_file():
                tool_files[str(path)] = file_hash(path)
    import wuwa_story

    import wuwa_story_worker

    source_files = {}
    for package in (wuwa_story, wuwa_story_worker):
        base = Path(package.__file__).parent
        for path in sorted(base.rglob("*.py")):
            source_files[package.__name__ + "/" + path.relative_to(base).as_posix()] = file_hash(path)
    distributions = sorted((dist.metadata.get("Name", ""), dist.version)
                           for dist in importlib.metadata.distributions())
    identity = {"schema": 1, "plan": plan, "forms": forms, "animations": include_animations,
                "keys": file_hash(client / "keys.txt"), "tools": tool_files, "source": source_files,
                "python": sys.version, "distributions": distributions}
    return hashlib.sha256(encoded(identity)).hexdigest()


def read_record(directory, key):
    try:
        path = directory / (key + ".json")
        if path.stat().st_size > MAX_RECORD_BYTES:
            return None
        record = json.loads(path.read_bytes())
        result = record["result"]
        if (record["key"] != key or record["sha256"] != hashlib.sha256(encoded(result)).hexdigest()
                or not isinstance(result, dict) or len(result) > 32
                or any(not isinstance(k, str) or not isinstance(v, dict)
                       or not isinstance(v.get("component"), dict) or not isinstance(v.get("model"), str)
                       for k, v in result.items())):
            return None
        return result
    except (OSError, ValueError, KeyError, TypeError):
        return None


def write_record(directory, key, result):
    record = encoded({"key": key, "result": result, "sha256": hashlib.sha256(encoded(result)).hexdigest()})
    if len(record) > MAX_RECORD_BYTES:
        return
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".discovery-", delete=False) as stream:
            name = stream.name
            stream.write(record)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, directory / (key + ".json"))
        # Cache is disposable. Concurrent pruning can only cause a harmless miss.
        entries = sorted(directory.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        for path in entries[MAX_RECORDS:]:
            path.unlink(missing_ok=True)
    finally:
        if name:
            Path(name).unlink(missing_ok=True)

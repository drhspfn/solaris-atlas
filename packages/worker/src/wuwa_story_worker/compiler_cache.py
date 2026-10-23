"""Compiled snapshots are reusable only with the compiler that produced them."""

import hashlib
import json
from pathlib import Path


def compiler_fingerprint(root: Path | None = None):
    root = root or Path(__file__).with_name("compiler")
    digest = hashlib.sha256()
    sources = sorted(
        (path for path in root.rglob("*") if path.is_file() and path.suffix in {".py", ".json"}),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    if not sources:
        raise ValueError("Compiler source identity is unavailable")
    for path in sources:
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def compiled_cache_valid(manifest: Path, marker: Path, fingerprint: str):
    try:
        return manifest.is_file() and json.loads(marker.read_text(encoding="utf-8")) == {
            "compiler_sha256": fingerprint}
    except (OSError, ValueError):
        return False

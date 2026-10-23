"""Stable game package identities and disposable standalone extraction workspaces."""

import hashlib
import os
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path

from wuwa_story.ingestion.media_provenance import authored_package_path

from wuwa_story_worker.transient_workspace import transient_workspace


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def package_path(path: Path) -> str:
    parts = path.parts
    for index in range(len(parts) - 1):
        if parts[index:index + 2] == ("Client", "Content"):
            return "/".join(parts[index:])
    # Numeric media extraction can omit the Client/Content prefix.
    if "Aki" in parts:
        return "Client/Content/" + "/".join(parts[parts.index("Aki"):])
    if path.suffix.casefold() in {".wem", ".bnk"}:
        return "Media/" + path.name
    raise ValueError(f"Export has no stable game package path: {path.name}")


@contextmanager
def temporary_entity_workspace(client: Path):
    """Share downloaded archives read-only; every extracted byte belongs to this run."""
    base = client.parent.parent if client.parent.name == "assets" else client.parent
    with transient_workspace(base / "transient-jobs") as root:
        for name in ("plan.json", "status.json", "keys.txt"):
            shutil.copyfile(client / name, root / name)
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "mklink", "/J", str(root / "game"),
                            str((client / "game").resolve())], check=True, capture_output=True)
        else:
            (root / "game").symlink_to((client / "game").resolve(), target_is_directory=True)
        yield root


def source_provenance(request, target, originals, tools, voice=None):
    """Accept MediaRequest or a pinned release result for any game media type.

    voice may include media/bank IDs, a recipe hash or other reconstruction data.
    Keep this metadata on both FileReference and FileVariant rows.
    """
    identity = request if isinstance(request, dict) else request.model_dump()
    return {
        "schema_version": 1, "asset_version": identity["asset_version"],
        "download_id": identity["download_id"], "tier": identity["tier"],
        "package_path": authored_package_path(target),
        "language": target.get("language"),
        "tools": {key: value for key, value in tools.items()
                  if key != "WUWA_VGMSTREAM_PATH" or target["kind"] != "image"},
        "source_files": [{"package_path": package_path(path), "sha256": sha256(path)}
                         for path in originals],
        **(voice or {}),
    }

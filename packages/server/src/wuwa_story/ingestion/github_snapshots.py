"""Fetch version branches from GitHub, compile each pinned commit, and import it."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RemoteSnapshot:
    branch: str
    commit: str


def parse_remote_heads(output: str, first: str, last: str | None) -> list[RemoteSnapshot]:
    """Select numeric major.minor release branches from `git ls-remote --heads`."""
    first_version = _parse_version(first)
    last_version = _parse_version(last) if last else None
    versions: dict[tuple[int, int], RemoteSnapshot] = {}
    for line in output.splitlines():
        fields = line.split()
        if len(fields) != 2 or not fields[1].startswith("refs/heads/"):
            continue
        branch = fields[1].removeprefix("refs/heads/")
        if not re.fullmatch(r"\d+\.\d+", branch):
            continue
        version = _parse_version(branch)
        if version < first_version or (last_version is not None and version > last_version):
            continue
        versions[version] = RemoteSnapshot(branch, fields[0])
    return [versions[key] for key in sorted(versions)]


def _parse_version(value: str) -> tuple[int, int]:
    if not re.fullmatch(r"\d+\.\d+", value):
        raise ValueError(f"Expected a major.minor version, got {value!r}")
    major, minor = value.split(".")
    return int(major), int(minor)


def _git(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *(str(arg) for arg in args)],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def discover_remote_snapshots(repo_url: str, first: str, last: str | None) -> list[RemoteSnapshot]:
    output = _git("ls-remote", "--heads", repo_url)
    snapshots = parse_remote_heads(output, first, last)
    if not snapshots:
        raise ValueError(f"No matching numeric release branches found in {repo_url}")
    return snapshots


def prepare_checkout(repo_url: str, cache: Path) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    if not (cache / ".git").exists():
        if any(cache.iterdir()):
            raise ValueError(f"Git cache directory must be empty before initialization: {cache}")
        _git("init", str(cache))
        _git("-C", str(cache), "remote", "add", "origin", repo_url)
        _git("-C", str(cache), "config", "core.autocrlf", "false")
    else:
        _git("-C", str(cache), "remote", "set-url", "origin", repo_url)


def fetch_snapshot(cache: Path, snapshot: RemoteSnapshot) -> None:
    # Jobs are pinned when enqueued. Fetch the advertised commit itself so a branch
    # advancing before the consumer runs cannot silently change the requested snapshot.
    _git("-C", str(cache), "fetch", "--depth=1", "origin", snapshot.commit)
    fetched = _git("-C", str(cache), "rev-parse", "FETCH_HEAD")
    if fetched != snapshot.commit:
        raise RuntimeError(
            f"Upstream branch {snapshot.branch} moved during fetch: "
            f"advertised {snapshot.commit}, fetched {fetched}"
        )
    _git("-C", str(cache), "checkout", "--force", "--detach", snapshot.commit)


def load_checkpoints(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in payload.items()
    ):
        raise ValueError(f"Checkpoint file must contain a string-to-string JSON object: {path}")
    return payload


def save_checkpoint(path: Path, branch: str, commit: str) -> None:
    checkpoints = load_checkpoints(path)
    checkpoints[branch] = commit
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(dict(sorted(checkpoints.items())), indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def compiler_environment(cache: Path) -> dict[str, str]:
    env = dict(os.environ)
    timestamp = _git("-C", str(cache), "show", "-s", "--format=%ct", "HEAD")
    env["SOURCE_DATE_EPOCH"] = timestamp
    return env

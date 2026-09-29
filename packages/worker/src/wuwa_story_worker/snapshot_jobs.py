"""Build a pinned GitHub datamine snapshot and import it into the archive database."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from wuwa_story.config.settings import get_settings
from wuwa_story.ingestion.compiler_importer import CompiledDatasetImporter
from wuwa_story.ingestion.github_snapshots import (
    RemoteSnapshot,
    compiler_environment,
    fetch_snapshot,
    prepare_checkout,
)

logger = logging.getLogger(__name__)
_cache_lock = asyncio.Lock()
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def _git(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *(str(arg) for arg in args)],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _version_from_readme(source: Path) -> str:
    match = re.search(
        r"Game Version:\s*([^<\n]+)", (source / "README.md").read_text(encoding="utf-8")
    )
    if not match:
        raise ValueError(f"Game Version is missing from {source / 'README.md'}")
    return match.group(1).strip()


def _validate_job(payload: dict[str, Any]) -> tuple[str, str, str]:
    if payload.get("schema_version") != 1 or payload.get("job_type") != "snapshot.build_import":
        raise ValueError("Unsupported snapshot job schema or job type")
    version = payload.get("version")
    repository = payload.get("repository_url")
    commit = payload.get("commit")
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+", version):
        raise ValueError("Snapshot job version must be major.minor, for example '1.0'")
    if not isinstance(repository, str) or not repository.startswith("https://"):
        raise ValueError("Snapshot job repository_url must be HTTPS")
    if not isinstance(commit, str) or not COMMIT_PATTERN.fullmatch(commit):
        raise ValueError("Snapshot job commit must be a full 40-character SHA")
    return version, repository, commit


async def _checkout_job(repository: str, version: str, commit: str, workspace: Path) -> Path:
    cache = workspace / "git-cache"
    job_root = workspace / "jobs" / f"{version}-{commit}"
    source = job_root / "source"
    async with _cache_lock:
        def checkout() -> None:
            prepare_checkout(repository, cache)
            if source.is_dir() and (source / ".git").exists():
                if _git("-C", str(source), "rev-parse", "HEAD") == commit:
                    return
                shutil.rmtree(job_root)
            job_root.mkdir(parents=True, exist_ok=True)
            fetch_snapshot(cache, RemoteSnapshot(version, commit))
            subprocess.run(
                ["git", "clone", "--shared", "--no-checkout", str(cache), str(source)],
                check=True,
                capture_output=True,
                text=True,
            )
            _git("-C", str(source), "checkout", "--detach", commit)

        await asyncio.to_thread(checkout)
    return source


async def build_and_import_snapshot(payload: dict[str, Any]) -> None:
    version, repository, commit = _validate_job(payload)
    workspace = Path(os.getenv("WUWA_WORKER_WORKSPACE", "/var/lib/wuwa-worker")).resolve()
    source = await _checkout_job(repository, version, commit, workspace)
    job_root = source.parent
    dist = job_root / "dist"
    build_output = dist / _version_from_readme(source)
    if not (build_output / "manifest.json").is_file():
        shutil.rmtree(dist, ignore_errors=True)
        env = compiler_environment(source)
        env["PYTHONPATH"] = os.pathsep.join(
            filter(None, [str(Path(__file__).resolve().parents[1]), env.get("PYTHONPATH", "")])
        )
        command = [
            sys.executable,
            "-m",
            "wuwa_story_worker.compiler.cli",
            "--data",
            str(source),
            "--dist",
            str(dist),
            "build",
        ]
        logger.info("Compiling WuWa %s at %s", version, commit)
        await asyncio.to_thread(subprocess.run, command, cwd=job_root, env=env, check=True)

    manifest_path = build_output / "manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError(f"Compiler did not publish a manifest for upstream version {version}")
    manifest: Any = json.loads(manifest_path.read_text(encoding="utf-8"))
    actual_version = manifest.get("game_version")
    if not isinstance(actual_version, str) or ".".join(actual_version.split(".")[:2]) != version:
        raise ValueError(f"Upstream branch {version} produced unexpected game version {actual_version!r}")
    if manifest.get("source_commit") != commit:
        raise ValueError("Compiled snapshot commit does not match the pinned job commit")

    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    try:
        async with factory() as session:
            result = await CompiledDatasetImporter().import_release(
                build_output, session, batch_size=int(os.getenv("WUWA_IMPORT_BATCH_SIZE", "500"))
            )
        logger.info("Imported WuWa %s at %s: %s", actual_version, commit, result)
    finally:
        await engine.dispose()

    # The DB import is the durable product. Keep the shared Git object cache and
    # discard the per-job checkout and large compiled raw-evidence snapshot.
    shutil.rmtree(job_root)

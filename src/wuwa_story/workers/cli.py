import argparse
import asyncio
import json
import logging
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from wuwa_story.config.settings import get_settings
from wuwa_story.ingestion.compiler_importer import CompiledDatasetImporter
from wuwa_story.ingestion.github_snapshots import (
    compiler_environment,
    discover_remote_snapshots,
    fetch_snapshot,
    load_checkpoints,
    prepare_checkout,
    save_checkpoint,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wuwa-story-worker")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run", help="Run the worker placeholder (no queue is configured)")
    importer = commands.add_parser("import-compiler", help="Import a compiler output directory")
    importer.add_argument("dataset", type=Path)
    importer.add_argument("--batch-size", type=int, default=1000)
    series = commands.add_parser(
        "import-series", help="Import compiled version snapshots in game-version order"
    )
    series.add_argument("root", type=Path, help="Directory containing one folder per snapshot")
    series.add_argument("--from-version", default="3.0", help="First major/minor version")
    series.add_argument("--to-version", default="3.6", help="Last major/minor version")
    series.add_argument("--batch-size", type=int, default=1000)
    series.add_argument(
        "--dry-run", action="store_true", help="List snapshots without connecting to the database"
    )
    sync = commands.add_parser(
        "sync-github", help="Fetch upstream release branches, compile snapshots, and import them"
    )
    sync.add_argument(
        "--repo-url",
        default="https://github.com/Arikatsu/WutheringWaves_Data.git",
    )
    sync.add_argument(
        "--compiler-root", type=Path, default=Path("../wuwa-story-investigation")
    )
    sync.add_argument("--workspace", type=Path, default=Path("var/upstream-sync"))
    sync.add_argument("--from-version", default="3.0")
    sync.add_argument("--to-version", help="Optional upper bound; omit to include future branches")
    sync.add_argument("--batch-size", type=int, default=500)
    sync.add_argument("--dry-run", action="store_true", help="Only list remote branch heads")
    sync.add_argument("--watch", action="store_true", help="Repeat polling for new branch commits")
    sync.add_argument("--interval-seconds", type=int, default=1800)
    return parser


def discover_versioned_datasets(root: Path, first: str, last: str) -> list[tuple[str, Path]]:
    """Find one or more compiled snapshots for every requested major/minor version."""
    try:
        first_parts = tuple(int(part) for part in first.split("."))
        last_parts = tuple(int(part) for part in last.split("."))
    except ValueError as exc:
        raise ValueError("Version bounds must be numeric major.minor values, for example 3.0") from exc
    if len(first_parts) != 2 or len(last_parts) != 2 or first_parts > last_parts:
        raise ValueError("Version bounds must be ordered major.minor values, for example 3.0 to 3.6")
    if not root.is_dir():
        raise FileNotFoundError(f"Snapshot root does not exist or is not a directory: {root}")

    snapshots: list[tuple[tuple[int, int, int], str, Path]] = []
    for manifest_path in root.glob("*/manifest.json"):
        try:
            manifest: Any = json.loads(manifest_path.read_text(encoding="utf-8"))
            raw_version = manifest["game_version"]
            if not isinstance(raw_version, str):
                raise ValueError("game_version must be a string")
            version = tuple(int(part) for part in raw_version.split("."))
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid compiled dataset manifest: {manifest_path}") from exc
        if len(version) < 2:
            raise ValueError(f"Invalid game_version in {manifest_path}: {raw_version!r}")
        major_minor = version[:2]
        if first_parts <= major_minor <= last_parts:
            normalized = (version + (0, 0, 0))[:3]
            snapshots.append((normalized, raw_version, manifest_path.parent))

    snapshots.sort(key=lambda item: (item[0], str(item[2])))
    available = {version[:2] for version, _, _ in snapshots}
    missing = []
    for major in range(first_parts[0], last_parts[0] + 1):
        for minor in range(first_parts[1] if major == first_parts[0] else 0,
                           last_parts[1] + 1 if major == last_parts[0] else 100):
            if (major, minor) not in available:
                missing.append(f"{major}.{minor}")
    if missing:
        raise FileNotFoundError(
            "No compiled snapshot found for required game versions: " + ", ".join(missing)
        )
    return [(version, path) for _, version, path in snapshots]


async def _import_dataset(path: Path, batch_size: int) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    try:
        async with factory() as session:
            result = await CompiledDatasetImporter().import_release(path, session, batch_size)
            logging.getLogger(__name__).info("Import completed: %s", result)
    finally:
        await engine.dispose()


async def _import_series(datasets: list[tuple[str, Path]], batch_size: int) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    try:
        importer = CompiledDatasetImporter()
        for version, path in datasets:
            logging.getLogger(__name__).info("Importing game %s from %s", version, path)
            async with factory() as session:
                result = await importer.import_release(path, session, batch_size)
            logging.getLogger(__name__).info("Import completed: %s", result)
    finally:
        await engine.dispose()


async def _sync_once(args: argparse.Namespace) -> None:
    snapshots = discover_remote_snapshots(args.repo_url, args.from_version, args.to_version)
    for snapshot in snapshots:
        print(f"{snapshot.branch}\t{snapshot.commit}")
    if args.dry_run:
        return

    compiler_root = args.compiler_root.resolve()
    if not (compiler_root / "wuwa_narrative" / "cli.py").is_file():
        raise FileNotFoundError(f"Narrative compiler source not found under {compiler_root}")
    workspace = args.workspace.resolve()
    cache = workspace / "source"
    dist = workspace / "dist"
    checkpoint_path = workspace / "checkpoints.json"
    prepare_checkout(args.repo_url, cache)
    checkpoints = load_checkpoints(checkpoint_path)
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    try:
        for snapshot in snapshots:
            if checkpoints.get(snapshot.branch) == snapshot.commit:
                logging.getLogger(__name__).info(
                    "Upstream %s is unchanged at %s", snapshot.branch, snapshot.commit
                )
                continue
            logging.getLogger(__name__).info(
                "Fetching %s at %s", snapshot.branch, snapshot.commit
            )
            fetch_snapshot(cache, snapshot)
            command = [
                sys.executable,
                "-m",
                "wuwa_narrative",
                "--data",
                str(cache),
                "--dist",
                str(dist),
                "build",
            ]
            subprocess.run(
                command,
                cwd=compiler_root,
                env=compiler_environment(cache),
                check=True,
            )
            built = dist / _version_from_readme(cache) / "manifest.json"
            if not built.is_file():
                raise RuntimeError(f"Compiler did not publish a snapshot for branch {snapshot.branch}")
            manifest: Any = json.loads(built.read_text(encoding="utf-8"))
            game_version = manifest.get("game_version")
            if not isinstance(game_version, str) or ".".join(game_version.split(".")[:2]) != snapshot.branch:
                raise ValueError(
                    f"Branch {snapshot.branch} produced unexpected game version {game_version!r}"
                )
            if manifest.get("source_commit") != snapshot.commit:
                raise ValueError(
                    f"Built snapshot commit does not match fetched branch {snapshot.branch}"
                )
            async with factory() as session:
                result = await CompiledDatasetImporter().import_release(
                    built.parent, session, args.batch_size
                )
            logging.getLogger(__name__).info("Imported %s: %s", game_version, result)
            save_checkpoint(checkpoint_path, snapshot.branch, snapshot.commit)
            checkpoints[snapshot.branch] = snapshot.commit
    finally:
        await engine.dispose()


def _version_from_readme(source: Path) -> str:
    match = re.search(
        r"Game Version:\s*([^<\n]+)", (source / "README.md").read_text(encoding="utf-8")
    )
    if not match:
        raise ValueError(f"Game Version is missing from {source / 'README.md'}")
    return match.group(1).strip()


def _sync(args: argparse.Namespace) -> None:
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be positive")
    if args.interval_seconds < 10:
        raise SystemExit("--interval-seconds must be at least 10")
    if not args.watch:
        asyncio.run(_sync_once(args))
        return
    while True:
        try:
            asyncio.run(_sync_once(args))
        except Exception:
            logging.getLogger(__name__).exception("Upstream sync failed; it will retry next poll")
        time.sleep(args.interval_seconds)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    args = _parser().parse_args()
    if args.command == "import-compiler":
        if args.batch_size < 1:
            raise SystemExit("--batch-size must be positive")
        asyncio.run(_import_dataset(args.dataset, args.batch_size))
    elif args.command == "import-series":
        if args.batch_size < 1:
            raise SystemExit("--batch-size must be positive")
        try:
            datasets = discover_versioned_datasets(args.root, args.from_version, args.to_version)
        except (ValueError, FileNotFoundError) as exc:
            raise SystemExit(str(exc)) from exc
        for version, path in datasets:
            print(f"{version}\t{path}")
        if not args.dry_run:
            asyncio.run(_import_series(datasets, args.batch_size))
    elif args.command == "sync-github":
        _sync(args)
    else:
        logging.getLogger(__name__).info("No queue or background handlers are configured.")


def import_main() -> None:
    sys.argv = [sys.argv[0], "import-compiler", *sys.argv[1:]]
    main()


def import_series_main() -> None:
    sys.argv = [sys.argv[0], "import-series", *sys.argv[1:]]
    main()


def sync_github_main() -> None:
    sys.argv = [sys.argv[0], "sync-github", *sys.argv[1:]]
    main()


if __name__ == "__main__":
    main()

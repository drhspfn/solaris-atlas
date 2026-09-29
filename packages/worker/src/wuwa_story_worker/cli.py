"""Command line entrypoints for snapshot build and import jobs."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from wuwa_story.config.settings import get_settings
from wuwa_story.ingestion.compiler_importer import CompiledDatasetImporter

from wuwa_story_worker.broker import consume_jobs, replay_failed_jobs
from wuwa_story_worker.queues import queue_concurrency
from wuwa_story_worker.scheduler import (
    DEFAULT_REPOSITORY,
    enqueue_snapshot,
    run_watch,
)
from wuwa_story_worker.snapshot_jobs import build_and_import_snapshot


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wuwa-story-worker")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run", help="Consume RabbitMQ snapshot build/import jobs")
    importer = commands.add_parser("import-compiler", help="Import a compiled output directory")
    importer.add_argument("dataset", type=Path)
    importer.add_argument("--batch-size", type=int, default=1000)
    series = commands.add_parser("import-series", help="Import compiled snapshots in version order")
    series.add_argument("root", type=Path)
    series.add_argument("--from-version", default="1.0")
    series.add_argument("--to-version", default="3.6")
    series.add_argument("--batch-size", type=int, default=1000)
    series.add_argument("--dry-run", action="store_true")
    enqueue = commands.add_parser("enqueue-snapshot", help="Queue one upstream version snapshot")
    enqueue.add_argument("--version", required=True, help="Upstream branch, for example 1.0")
    enqueue.add_argument("--commit", help="Pin an explicit full commit SHA; defaults to branch head")
    enqueue.add_argument("--repo-url", default=DEFAULT_REPOSITORY)
    watch = commands.add_parser("watch-upstream", help="Poll upstream branches and enqueue new commits")
    watch.add_argument("--repo-url", default=DEFAULT_REPOSITORY)
    watch.add_argument("--from-version", default="1.0")
    watch.add_argument("--to-version", help="Optional upper major.minor bound")
    watch.add_argument("--workspace", type=Path, default=Path("/var/lib/wuwa-worker"))
    watch.add_argument("--interval-seconds", type=int, default=1800)
    watch.add_argument("--once", action="store_true", help="Scan once and exit")
    replay = commands.add_parser("replay-failed", help="Requeue dead-lettered jobs")
    replay.add_argument("--queue", choices=("snapshot_build",), default="snapshot_build")
    replay.add_argument("--limit", type=int, default=100)
    return parser


def discover_versioned_datasets(root: Path, first: str, last: str) -> list[tuple[str, Path]]:
    try:
        first_parts = tuple(int(part) for part in first.split("."))
        last_parts = tuple(int(part) for part in last.split("."))
    except ValueError as exc:
        raise ValueError("Version bounds must be numeric major.minor values, for example 3.0") from exc
    if len(first_parts) != 2 or len(last_parts) != 2 or first_parts > last_parts:
        raise ValueError("Version bounds must be ordered major.minor values, for example 1.0 to 3.6")
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
        if first_parts <= version[:2] <= last_parts:
            snapshots.append(((version + (0, 0, 0))[:3], raw_version, manifest_path.parent))
    snapshots.sort(key=lambda item: (item[0], str(item[2])))
    available = {version[:2] for version, _, _ in snapshots}
    missing = [f"{major}.{minor}" for major in range(first_parts[0], last_parts[0] + 1)
               for minor in range(first_parts[1] if major == first_parts[0] else 0,
                                  last_parts[1] + 1 if major == last_parts[0] else 100)
               if (major, minor) not in available]
    if missing:
        raise FileNotFoundError("No compiled snapshot found for required game versions: " + ", ".join(missing))
    return [(version, path) for _, version, path in snapshots]


async def _import_datasets(datasets: list[tuple[str, Path]], batch_size: int) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    try:
        importer = CompiledDatasetImporter()
        for version, path in datasets:
            logging.info("Importing game %s from %s", version, path)
            async with factory() as session:
                result = await importer.import_release(path, session, batch_size)
            logging.info("Import completed: %s", result)
    finally:
        await engine.dispose()


async def _run() -> None:
    await consume_jobs({"snapshot_build": build_and_import_snapshot})


async def _enqueue(args: argparse.Namespace) -> None:
    await enqueue_snapshot(args.version, args.repo_url, args.commit)


async def _replay_failed(args: argparse.Namespace) -> None:
    count = await replay_failed_jobs(args.queue, args.limit)
    logging.info("Requeued %d failed job(s)", count)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = _parser().parse_args()
    if args.command == "run":
        queue_concurrency()
        asyncio.run(_run())
    elif args.command == "enqueue-snapshot":
        asyncio.run(_enqueue(args))
    elif args.command == "watch-upstream":
        asyncio.run(run_watch(args))
    elif args.command == "replay-failed":
        asyncio.run(_replay_failed(args))
    elif args.command == "import-compiler":
        if args.batch_size < 1:
            raise SystemExit("--batch-size must be positive")
        asyncio.run(_import_datasets([(args.dataset.name, args.dataset)], args.batch_size))
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
            asyncio.run(_import_datasets(datasets, args.batch_size))


def import_main() -> None:
    sys.argv = [sys.argv[0], "import-compiler", *sys.argv[1:]]
    main()


def import_series_main() -> None:
    sys.argv = [sys.argv[0], "import-series", *sys.argv[1:]]
    main()


if __name__ == "__main__":
    main()

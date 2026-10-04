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
from wuwa_story.agents.contracts import AnalysisRequest
from wuwa_story.agents.jobs import enqueue_analysis
from wuwa_story.agents.settings import get_agent_settings
from wuwa_story.config.settings import get_settings
from wuwa_story.ingestion.compiler_importer import CompiledDatasetImporter

from wuwa_story_worker.asset_export import export_assets
from wuwa_story_worker.asset_jobs import (
    download_client_assets,
    enqueue_assets,
    enqueue_maps,
    extract_client_assets,
)
from wuwa_story_worker.broker import consume_jobs, replay_failed_jobs
from wuwa_story_worker.client_assets import discover_plan, download_plan, save_json
from wuwa_story_worker.entity_media import process_entity_media
from wuwa_story_worker.map_assets import build_maps, refresh_map_sources
from wuwa_story_worker.queues import QUEUES, queue_concurrency
from wuwa_story_worker.scheduler import (
    DEFAULT_REPOSITORY,
    enqueue_snapshot,
    run_watch,
)
from wuwa_story_worker.snapshot_jobs import build_and_import_snapshot
from wuwa_story_worker.story_agent import dispatch_story_revisits, process_story_analysis
from wuwa_story_worker.voice_packages import discover_voice_plan, download_voice_plan


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wuwa-story-worker")
    commands = parser.add_subparsers(dest="command", required=True)
    agent = commands.add_parser("enqueue-analysis", help="Queue source-cited story analysis")
    agent.add_argument("--quest-id", type=int, required=True)
    agent.add_argument("--version", required=True)
    agent.add_argument("--locale", default="en", help="Explanation language; source reading uses all available translations")
    agent.add_argument("--generation", default="", help="Explicit new generation token; default deduplicates")
    runner = commands.add_parser("run", help="Consume selected RabbitMQ jobs")
    runner.add_argument("--queue", action="append", choices=tuple(QUEUES), default=None)
    assets = commands.add_parser("plan-assets", help="Inspect official client archives without downloading")
    assets.add_argument("--version", required=True)
    assets.add_argument("--tier", choices=("sd", "hd", "uhd"), default="hd")
    assets.add_argument("--output", type=Path)
    asset_enqueue = commands.add_parser("enqueue-assets", help="Queue a pinned official client download")
    asset_enqueue.add_argument("plan", type=Path)
    asset_download = commands.add_parser("download-assets", help="Download a pinned client plan locally")
    asset_download.add_argument("plan", type=Path)
    asset_download.add_argument("--workspace", type=Path, required=True)
    asset_download.add_argument("--concurrency", type=int, default=4)
    voice_plan = commands.add_parser(
        "plan-voices", help="Pin all four official voice packs and character packs")
    voice_plan.add_argument("--version", required=True)
    voice_plan.add_argument("--public-config", type=Path, required=True)
    voice_plan.add_argument("--config-crypto", type=Path, required=True)
    voice_plan.add_argument("--output", type=Path, required=True)
    voice_download = commands.add_parser(
        "download-voices", help="Download a pinned multilingual voice plan")
    voice_download.add_argument("plan", type=Path)
    voice_download.add_argument("--workspace", type=Path, required=True)
    voice_download.add_argument("--installed-game", type=Path)
    voice_download.add_argument("--concurrency", type=int, default=4)
    asset_export = commands.add_parser("extract-assets", help="Export a completed client download using FModelCLI")
    asset_export.add_argument("root", type=Path)
    asset_export.add_argument("--fmodel", type=Path, required=True)
    asset_export.add_argument("--filter", required=True)
    asset_export.add_argument("--upload", action="store_true", help="Publish raw exported files and manifest to S3")
    maps = commands.add_parser("extract-maps", help="Decode map tiles, assemble previews and extract positioned objects")
    maps.add_argument("root", type=Path)
    maps.add_argument("--fmodel", type=Path, required=True)
    maps.add_argument("--converter", type=Path, required=True, help="CUE4Parse.CLI executable")
    maps.add_argument("--publish", action="store_true", help="Register maps and content addressed files in PostgreSQL and S3")
    refresh_maps = commands.add_parser("refresh-map-sources", help="Refresh item acquisition evidence for published map markers")
    refresh_maps.add_argument("root", type=Path)
    map_enqueue = commands.add_parser("enqueue-maps", help="Queue map extraction for a downloaded client plan")
    map_enqueue.add_argument("plan", type=Path)
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
    replay.add_argument("--queue", choices=tuple(QUEUES), default="snapshot_build")
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
    # A major release ends at its last available minor, not at an invented .99.
    # Keep checking interior gaps and explicit range endpoints.
    missing = []
    for major in range(first_parts[0], last_parts[0] + 1):
        lower = first_parts[1] if major == first_parts[0] else 0
        upper = last_parts[1] if major == last_parts[0] else max(
            (minor for candidate, minor in available if candidate == major), default=lower
        )
        missing.extend(f"{major}.{minor}" for minor in range(lower, max(lower, upper) + 1)
                       if (major, minor) not in available)
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


async def _run(queues: list[str] | None = None) -> None:
    handlers = {"snapshot_build": build_and_import_snapshot, "asset_download": download_client_assets,
                "asset_extract": extract_client_assets, "entity_media": process_entity_media,
                "story_agent": process_story_analysis}
    # Existing workers keep their snapshot-only role unless explicitly configured.
    selected = {key: handlers[key] for key in (queues or ["snapshot_build"])}
    if "story_agent" in selected:
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(dispatch_story_revisits())
            tasks.create_task(consume_jobs(selected))
    else:
        await consume_jobs(selected)


async def _enqueue(args: argparse.Namespace) -> None:
    await enqueue_snapshot(args.version, args.repo_url, args.commit)


async def _replay_failed(args: argparse.Namespace) -> None:
    count = await replay_failed_jobs(args.queue, args.limit)
    logging.info("Requeued %d failed job(s)", count)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = _parser().parse_args()
    if args.command == "enqueue-analysis":
        from wuwa_story.db.session import SessionFactory

        async def enqueue():
            async with SessionFactory() as session:
                run = await enqueue_analysis(session, AnalysisRequest(quest_id=args.quest_id, game_version=args.version, locale=args.locale, generation=args.generation), get_agent_settings())
                print(json.dumps({"id": run.id, "status": run.status}))

        asyncio.run(enqueue())
    elif args.command == "run":
        queue_concurrency()
        asyncio.run(_run(args.queue))
    elif args.command == "plan-assets":
        plan = discover_plan(args.version, args.tier)
        if args.output:
            save_json(args.output, plan)
        print(json.dumps({"version": plan["version"], "tier": plan["tier"], "id": plan["id"],
                          "files": len(plan["files"]), "download_gib": round(sum(item["size"] for item in plan["files"]) / 1024 ** 3, 2),
                          "keys_commit": plan["keys_commit"]}, indent=2))
    elif args.command == "enqueue-assets":
        asyncio.run(enqueue_assets(json.loads(args.plan.read_text(encoding="utf-8"))))
    elif args.command == "download-assets":
        root = download_plan(json.loads(args.plan.read_text(encoding="utf-8")), args.workspace.resolve(), args.concurrency)
        print(root)
    elif args.command == "plan-voices":
        plan = discover_voice_plan(
            args.version, args.public_config, args.config_crypto)
        save_json(args.output, plan)
        print(json.dumps({"id": plan["id"], "languages": plan["languages"], "files": len(plan["files"]),
                          "download_gib": round(sum(item["size"] for item in plan["files"]) / 1024 ** 3, 2)}))
    elif args.command == "download-voices":
        print(download_voice_plan(json.loads(args.plan.read_text(encoding="utf-8")), args.workspace,
                                  args.installed_game, args.concurrency))
    elif args.command == "extract-assets":
        print(asyncio.run(export_assets(args.root.resolve(), args.fmodel, args.filter, args.upload)))
    elif args.command == "extract-maps":
        print(asyncio.run(build_maps(args.root.resolve(), args.fmodel, args.converter, args.publish)))
    elif args.command == "refresh-map-sources":
        print(json.dumps(asyncio.run(refresh_map_sources(args.root.resolve()))))
    elif args.command == "enqueue-maps":
        asyncio.run(enqueue_maps(json.loads(args.plan.read_text(encoding="utf-8"))))
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

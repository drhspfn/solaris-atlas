"""Import only explicit entity-reference edges from a compiled snapshot."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from wuwa_story.config.settings import get_settings
from wuwa_story.db.models.ops import GameRelease, ImportRun
from wuwa_story.ingestion.canonical import CanonicalRecordAdapter
from wuwa_story.ingestion.canonical_import import import_canonical_edges
from wuwa_story.ingestion.releases import start_import_run

RELATIONS = {
    "decomposes_into",
    "has_speaker",
    "references_area",
    "references_character",
    "references_item",
    "references_npc",
}


async def _run(dataset: Path, batch_size: int) -> None:
    adapter = CanonicalRecordAdapter(dataset)
    manifest = json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    try:
        async with factory() as session:
            release = await session.scalar(
                select(GameRelease).where(GameRelease.game_version == manifest["game_version"])
            )
            if release is None:
                raise ValueError(f"Release {manifest['game_version']!r} has not been imported")
            run = await start_import_run(
                session,
                release.id,
                "wuwa-entity-reference-importer",
                "0.1.0",
                {"source_path": str(dataset), "relations": sorted(RELATIONS)},
            )
            run.status = "running"
            run_id = run.id
            await session.commit()
            try:
                count = await import_canonical_edges(
                    adapter, session, release.id, batch_size, type_filter=RELATIONS
                )
                run = await session.get(ImportRun, run_id)
                assert run is not None
                run.status = "succeeded"
                run.finished_at = datetime.now(UTC)
                run.records_seen = count
                await session.commit()
                print(f"Processed {count} explicit entity-reference edges for {manifest['game_version']}")
            except Exception as exc:
                await session.rollback()
                run = await session.get(ImportRun, run_id)
                if run is not None:
                    run.status = "failed"
                    run.finished_at = datetime.now(UTC)
                    run.records_failed += 1
                    run.error = f"{type(exc).__name__}: {exc}"
                    await session.commit()
                raise
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--batch-size", type=int, default=1000)
    args = parser.parse_args()
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be positive")
    asyncio.run(_run(args.dataset, args.batch_size))


if __name__ == "__main__":
    main()

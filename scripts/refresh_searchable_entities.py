"""Refresh character, item, and area projections from a compiled snapshot."""

import argparse
import asyncio
import itertools
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from wuwa_story.config.settings import get_settings
from wuwa_story.db.models import Node
from wuwa_story.db.models.ops import GameRelease
from wuwa_story.ingestion.canonical import CanonicalRecordAdapter
from wuwa_story.ingestion.canonical_import import (
    _insert_typed_entities,
    _source_identity,
    _source_record_ids,
)


def _batches(records, size: int):
    iterator = iter(records)
    while batch := list(itertools.islice(iterator, size)):
        yield batch


async def _refresh(dataset: Path, batch_size: int) -> None:
    adapter = CanonicalRecordAdapter(dataset)
    manifest = json.loads(adapter.root.joinpath("manifest.json").read_text(encoding="utf-8"))
    game_version = manifest["game_version"]
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    totals = {"character": 0, "item": 0, "area": 0}
    try:
        async with factory() as session:
            release = await session.scalar(
                select(GameRelease).where(GameRelease.game_version == game_version)
            )
            if release is None:
                raise ValueError(f"Game release {game_version!r} is not imported")
            for kind in totals:
                for batch in _batches(adapter.iter_entities(kind), batch_size):
                    rows = await session.execute(
                        select(Node.id, Node.canonical_key, Node.type_id).where(
                            Node.canonical_key.in_([record["id"] for record in batch])
                        )
                    )
                    node_map = {key: (node_id, type_id) for node_id, key, type_id in rows}
                    refs = [_source_identity(record.get("source")) for record in batch]
                    source_ids = await _source_record_ids(
                        session, release.id, (ref for ref in refs if ref is not None)
                    )
                    existing = [record for record in batch if record["id"] in node_map]
                    await _insert_typed_entities(session, kind, existing, node_map, source_ids)
                    totals[kind] += len(existing)
                    await session.commit()
        print(f"Refreshed searchable entity projections for {game_version}: {totals}")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be positive")
    asyncio.run(_refresh(args.dataset, args.batch_size))


if __name__ == "__main__":
    main()

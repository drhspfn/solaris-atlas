"""Rebuild typed action/dialogue rows and links from an imported compiler snapshot."""

import argparse
import asyncio
import itertools
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from wuwa_story.config.settings import get_settings
from wuwa_story.db.models import DialogueLine, Node, QuestAction
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
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    try:
        async with factory() as session:
            import json

            game_version = json.loads(
                adapter.root.joinpath("manifest.json").read_text(encoding="utf-8")
            )["game_version"]
            release = await session.scalar(
                select(GameRelease).where(GameRelease.game_version == game_version)
            )
            if release is None:
                raise ValueError(f"Game release {game_version!r} is not imported")

            for kind, model in (("action", QuestAction), ("talk_item", DialogueLine)):
                node_ids = [record["id"] for record in adapter.iter_entities(kind)]
                for offset in range(0, len(node_ids), 1000):
                    await session.execute(
                        delete(model).where(
                            model.node_id.in_(
                                select(Node.id).where(
                                    Node.canonical_key.in_(node_ids[offset : offset + 1000])
                                )
                            )
                        )
                    )
                await session.commit()

            for kind in ("action", "talk_item"):
                for batch in _batches(adapter.iter_entities(kind), batch_size):
                    canonical_keys = [record["id"] for record in batch]
                    node_rows = await session.execute(
                        select(Node.id, Node.canonical_key, Node.type_id).where(
                            Node.canonical_key.in_(canonical_keys)
                        )
                    )
                    node_map = {
                        key: (node_id, type_id) for node_id, key, type_id in node_rows
                    }
                    refs = [_source_identity(record.get("source")) for record in batch]
                    source_ids = await _source_record_ids(
                        session, release.id, (ref for ref in refs if ref is not None)
                    )
                    await _insert_typed_entities(
                        session, kind, batch, node_map, source_ids
                    )
                    await session.commit()

            action_count = await session.scalar(select(func.count()).select_from(QuestAction))
            dialogue_count = await session.scalar(select(func.count()).select_from(DialogueLine))
            print(f"Rebuilt typed projection for {game_version}: actions={action_count}, dialogues={dialogue_count}")
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

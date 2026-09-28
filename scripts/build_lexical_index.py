"""Build deterministic lexical search documents for an imported game release."""

import argparse
import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from wuwa_story.config.settings import get_settings
from wuwa_story.search.indexer import build_lexical_index


async def _run(game_version: str, batch_size: int, categories: set[str] | None) -> None:
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    try:
        async with factory() as session:
            count = await build_lexical_index(session, game_version, batch_size, categories)
            print(f"Indexed {count} search documents for game version {game_version}")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("game_version")
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--category", action="append", dest="categories")
    args = parser.parse_args()
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be positive")
    asyncio.run(
        _run(
            args.game_version,
            args.batch_size,
            set(args.categories) if args.categories else None,
        )
    )


if __name__ == "__main__":
    main()

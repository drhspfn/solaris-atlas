import argparse
import asyncio
import logging
import sys
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from wuwa_story.config.settings import get_settings
from wuwa_story.ingestion.compiler_importer import CompiledDatasetImporter


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wuwa-story-worker")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run", help="Run the worker placeholder (no queue is configured)")
    importer = commands.add_parser("import-compiler", help="Import a compiler output directory")
    importer.add_argument("dataset", type=Path)
    importer.add_argument("--batch-size", type=int, default=1000)
    return parser


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


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    args = _parser().parse_args()
    if args.command == "import-compiler":
        if args.batch_size < 1:
            raise SystemExit("--batch-size must be positive")
        asyncio.run(_import_dataset(args.dataset, args.batch_size))
    else:
        logging.getLogger(__name__).info("No queue or background handlers are configured.")


def import_main() -> None:
    sys.argv = [sys.argv[0], "import-compiler", *sys.argv[1:]]
    main()


if __name__ == "__main__":
    main()

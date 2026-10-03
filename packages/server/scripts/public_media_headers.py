"""Backfill immutable headers on registered public media. Dry run unless --apply."""

import argparse
import asyncio

from sqlalchemy import select

from wuwa_story.config.settings import get_settings
from wuwa_story.db.models.storage import FileLocation
from wuwa_story.db.session import SessionFactory, engine
from wuwa_story.storage.s3 import S3Storage


async def run(apply: bool) -> None:
    storage = S3Storage(get_settings())
    cursor = ""
    checked = changed = 0
    try:
        while True:
            async with SessionFactory() as session:
                keys = list(await session.scalars(select(FileLocation.object_key).where(
                    FileLocation.backend == "s3", FileLocation.bucket == storage.bucket,
                    FileLocation.available.is_(True),
                    FileLocation.object_key.like("objects/%"),
                    FileLocation.object_key > cursor,
                ).distinct().order_by(FileLocation.object_key).limit(500)))
            if not keys:
                break
            for key in keys:
                changed += await storage.refresh_public_cache_header(key, apply=apply)
                checked += 1
            cursor = keys[-1]
            print(f"checked={checked} {'updated' if apply else 'pending'}={changed}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    asyncio.run(run(parser.parse_args().apply))

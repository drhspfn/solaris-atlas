"""Read-only runtime canary for the actual worker CLI and asyncpg JSONB codecs.

Run in a fresh worker process: python tests/worker_cli_postgres_check.py
Uses DATABASE_URL, or WUWA_TEST_DATABASE_URL when explicitly provided. Requires
ops.processing_run with at least one object metadata value. Never consumes jobs
or writes application data; the SQL transaction is explicitly read-only.
Set WUWA_CODEC_RUN_ID to check one specific processing run (for example 4787).
"""

import asyncio
import os

if os.getenv("WUWA_TEST_DATABASE_URL"):
    os.environ["DATABASE_URL"] = os.environ["WUWA_TEST_DATABASE_URL"]

# Import CLI FIRST, just like the installed entrypoint. Importing the DB session
# before CLI would miss the startup/configuration path this canary must exercise.
from wuwa_story_worker import cli  # noqa: E402


async def main():
    from sqlalchemy import bindparam, func, select, text
    from sqlalchemy.dialects.postgresql import JSONB
    from wuwa_story.db.models.ops import ProcessingRun
    from wuwa_story.db.session import SessionFactory, engine

    from wuwa_story_worker import entity_media, release_media

    assert entity_media.engine is engine
    assert release_media.engine is engine
    original_pool = engine.sync_engine.pool
    expected = {"worker_cli_codec": {"ok": True}, "values": [1, "two"]}
    run_id = int(os.environ["WUWA_CODEC_RUN_ID"]) if os.getenv("WUWA_CODEC_RUN_ID") else None

    async def check_codecs(handlers, **options):
        assert set(handlers) == {"pool_audio"}
        assert engine.sync_engine.pool is original_pool, "CLI replaced the dialect-configured pool"
        async with SessionFactory() as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            actual = await session.scalar(select(bindparam("payload", type_=JSONB)), {"payload": expected})
            assert isinstance(actual, dict) and actual == expected, "JSONB parameter codec is broken"
            query = select(ProcessingRun.metadata_json)
            query = query.where(ProcessingRun.id == run_id) if run_id is not None else query.where(
                func.jsonb_typeof(ProcessingRun.metadata_json) == "object")
            metadata = await session.scalar(query.limit(1))
            assert isinstance(metadata, dict), "Stored ProcessingRun metadata did not decode as dict"
        print("PASS: CLI keeps shared engine/pool; JSONB parameter and ProcessingRun metadata decode as dict; read-only")

    original_consumer = cli.consume_jobs
    cli.consume_jobs = check_codecs
    try:
        await cli._run(pool="audio")
    finally:
        cli.consume_jobs = original_consumer


if __name__ == "__main__":
    asyncio.run(main())

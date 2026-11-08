"""Backfill native previews from stored source bundles (dry run unless --apply).

Use --cursor with the reported next_cursor for another bounded batch. Failed or
locked references can be retried with --ref-id. Requires a direct PostgreSQL
connection: session advisory locks must survive commits.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path

from sqlalchemy import and_, case, func, or_, select, text
from wuwa_story.db.models.storage import FileLocation, FileObject, FileReference
from wuwa_story.storage.service import FileRegistrationService

REFERENCE_TYPES = ("story_spine", "character_trial_spine", "event_spine")
RECIPE = "riglyph-native-v2"
logger = logging.getLogger(__name__)


def current(metadata, source_id):
    preview = (metadata or {}).get("animation_preview") or {}
    if (not isinstance(preview, dict) or preview.get("recipe") != RECIPE
            or type(preview.get("source_file_id")) is not int
            or preview["source_file_id"] != source_id or source_id is None):
        return False
    variants = preview.get("variants")
    return preview.get("status") == "static" or (
        preview.get("status") == "ready" and isinstance(variants, list) and bool(variants))


def candidates(*, limit, cursor=0, ref_ids=(), rebuild=False):
    query = select(FileReference.id).where(
        FileReference.reference_type.in_(REFERENCE_TYPES),
        FileReference.file_id.is_not(None),
        FileReference.id > cursor,
    )
    if ref_ids:
        query = query.where(FileReference.id.in_(ref_ids))
    if not rebuild:
        preview = FileReference.metadata_json["animation_preview"]
        # CASE avoids jsonb_array_length errors on old/malformed scalar values.
        variant_count = case((func.jsonb_typeof(preview["variants"]) == "array",
                              func.jsonb_array_length(preview["variants"])), else_=0)
        query = query.where(~func.coalesce(and_(
            preview["recipe"].astext == RECIPE,
            preview["source_file_id"] == func.to_jsonb(FileReference.file_id),
            or_(preview["status"].astext == "static",
                and_(preview["status"].astext == "ready", variant_count > 0)),
        ), False))
    return query.order_by(FileReference.id).limit(limit)


async def download(storage, key, bundle, published, max_bytes):
    if published.size_bytes is not None and published.size_bytes > max_bytes:
        raise ValueError("Source bundle exceeds download bound")
    digest = hashlib.sha256()
    size = 0
    with bundle.open("wb") as output:
        async for chunk in storage.get(key):
            size += len(chunk)
            if size > max_bytes:
                raise ValueError("Source bundle exceeds download bound")
            output.write(chunk)
            digest.update(chunk)
    if published.size_bytes is not None and size != published.size_bytes:
        raise ValueError("Source bundle size mismatch")
    if published.sha256 is not None and digest.digest() != published.sha256:
        raise ValueError("Source bundle checksum mismatch")


async def backfill_reference(session_factory, storage, ref_id, *, engine, rebuild=False,
                             max_bytes=512 * 1024**2):
    # A namespaced bigint avoids colliding with owner/run locks in other workers.
    lock_key = int.from_bytes(hashlib.sha256(f"riglyph-backfill:{ref_id}".encode()).digest()[:8],
                              "big", signed=True)
    async with engine.connect() as lock:
        locked = await lock.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_key})
        await lock.commit()
        if not locked:
            return "locked"
        try:
            async with session_factory() as session:
                reference = await session.get(FileReference, ref_id)
                if reference is None or reference.reference_type not in REFERENCE_TYPES or reference.file_id is None:
                    return "missing"
                if not rebuild and current(reference.metadata_json, reference.file_id):
                    return "current"
                source_id = reference.file_id
                published = await session.get(FileObject, source_id)
                key = await session.scalar(select(FileLocation.object_key).where(
                    FileLocation.file_id == source_id,
                    FileLocation.backend == storage.backend,
                    FileLocation.bucket == storage.bucket,
                    FileLocation.available.is_(True),
                ).order_by(FileLocation.is_primary.desc(), FileLocation.id).limit(1))
                if published is None or key is None:
                    raise ValueError(
                        "Source bundle is unavailable in configured storage")
                # Keep the source detached, with no read transaction during download/render.
                session.expunge(published)
            with tempfile.TemporaryDirectory(prefix="riglyph-backfill-") as scratch:
                bundle = Path(scratch) / "source.zip"
                await download(storage, key, bundle, published, max_bytes)
                from wuwa_story_worker.riglyph_render import build_animation, publish_animation

                artifacts = await asyncio.to_thread(build_animation, bundle)
                async with session_factory() as session:
                    # Rendering finishes before this session exists. The publisher
                    # must not commit: registration and metadata are atomic here.
                    metadata = await publish_animation(
                        session, bundle, published, FileRegistrationService(storage), None,
                        artifacts=artifacts)
                    reference = await session.scalar(select(FileReference).where(
                        FileReference.id == ref_id).with_for_update())
                    if reference is None or reference.file_id != source_id or reference.reference_type not in REFERENCE_TYPES:
                        await session.rollback()
                        return "changed"
                    if not rebuild and current(reference.metadata_json, source_id):
                        await session.rollback()
                        return "current"
                    if isinstance(metadata.get("animation_preview"), dict):
                        metadata = {**metadata, "animation_preview": {
                            **metadata["animation_preview"], "source_file_id": source_id}}
                    reference.metadata_json = {
                        **(reference.metadata_json or {}), **metadata}
                    # storage.file_reference's public_cache_changed trigger invalidates
                    # API caches in the same commit as files, variants and metadata.
                    await session.commit()
                    if (metadata.get("animation_preview") or {}).get("status") == "failed":
                        # Conversion succeeded: retain its archive and failure metadata
                        # before reporting a retryable native render failure.
                        logger.warning("riglyph.backfill_render_failed reference_id=%s source_file_id=%s riglyph_file_id=%s",
                                       ref_id, source_id, metadata.get("riglyph_file_id"))
                        return "failed"
                    return "updated"
        finally:
            await lock.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key})
            await lock.commit()


async def run(args):
    if args.apply and os.getenv("WUWA_DB_TRANSACTION_POOLING") == "1":
        raise ValueError(
            "Backfill requires a direct database connection for session locks")
    from wuwa_story.config.settings import get_settings
    from wuwa_story.db.session import SessionFactory, engine
    from wuwa_story.storage.s3 import S3Storage

    try:
        async with SessionFactory() as session:
            ids = list(await session.scalars(candidates(
                limit=args.limit, cursor=args.cursor, ref_ids=args.ref_id, rebuild=args.rebuild)))
        results = []
        storage = S3Storage(get_settings()) if args.apply else None
        for ref_id in ids:
            try:
                status = await backfill_reference(
                    SessionFactory, storage, ref_id, engine=engine, rebuild=args.rebuild,
                    max_bytes=args.max_bundle_bytes) if args.apply else "dry_run"
            except Exception as error:
                # Retain diagnostic stack locations without exception messages or
                # chained remote bodies, which may contain payloads/credentials.
                safe_error = RuntimeError(f"Backfill failed ({type(error).__name__})")
                logger.exception("riglyph.backfill_failed reference_id=%s error_type=%s",
                                 ref_id, type(error).__name__,
                                 exc_info=(RuntimeError, safe_error, error.__traceback__))
                results.append(
                    {"reference_id": ref_id, "status": "failed", "error_type": type(error).__name__,
                     "log_event": "riglyph.backfill_failed"})
            else:
                results.append({"reference_id": ref_id, "status": status})
        report = {"apply": args.apply, "next_cursor": ids[-1] if ids else args.cursor,
                  "results": results}
        print(json.dumps(report), flush=True)
        return int(any(result["status"] == "failed" for result in results))
    finally:
        await engine.dispose()


def positive(value):
    result = int(value)
    if not 0 < result < 2**63:
        raise argparse.ArgumentTypeError("must be a positive bigint")
    return result


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    mode = cli.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true",
                     help="publish previews; default only lists reference IDs")
    mode.add_argument("--dry-run", dest="apply", action="store_false",
                      help="list eligible IDs without downloads or writes")
    cli.set_defaults(apply=False)
    cli.add_argument("--rebuild", action="store_true",
                     help="also reevaluate current ready/static previews")
    cli.add_argument("--limit", type=positive, default=100,
                     help="maximum references scanned (1–10000)")
    cli.add_argument("--cursor", type=int, default=0,
                     help="exclusive reference ID cursor")
    cli.add_argument("--ref-id", type=positive, action="append",
                     default=[], help="restrict to an ID (repeatable)")
    cli.add_argument("--max-bundle-bytes", type=positive,
                     default=512 * 1024**2)
    return cli


def main():
    cli = parser()
    args = cli.parse_args()
    if args.limit > 10000 or not 0 <= args.cursor < 2**63:
        cli.error("limit must be 1–10000 and cursor a nonnegative bigint")
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()

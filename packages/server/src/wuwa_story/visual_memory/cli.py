"""Inspect local models and import explicitly selected game assets for review.

`visual-memory bootstrap NODE_ID ...` imports at most one image per node. Each
node must already have exactly one visual identity linked to it; no labels are
created or inferred. All newly imported references remain pending review.
"""

import argparse
import asyncio
import importlib.util
import json
import os
from pathlib import Path

from wuwa_story.visual_memory.images import MAX_IMAGE_BYTES, load_image
from wuwa_story.visual_memory.inference import VisualMemoryUnavailable, get_engine


def model_status():
    dino = os.environ.get("VISUAL_MEMORY_DINO_PATH", "")
    detector = os.environ.get("VISUAL_MEMORY_DETECTOR_PATH", "")
    directory = Path(dino)
    files_present = (
        bool(dino)
        and all(
            (directory / name).is_file() for name in ("config.json", "preprocessor_config.json")
        )
        and any((directory / name).is_file() for name in ("model.safetensors", "pytorch_model.bin"))
    )
    return {
        "dino": {
            "path": dino or None,
            "files_present": files_present,
            "dependencies_present": all(
                importlib.util.find_spec(name) is not None for name in ("torch", "transformers")
            ),
        },
        "detector": {
            "path": detector or None,
            "file_present": bool(detector) and Path(detector).is_file(),
            "dependencies_present": all(
                importlib.util.find_spec(name) is not None
                for name in ("numpy", "cv2", "onnxruntime")
            ),
        },
    }


def smoke(path):
    with Path(path).open("rb") as source:
        image = load_image(source.read(MAX_IMAGE_BYTES + 1))
    engine = get_engine()
    embedding = engine.embed(image)
    detections = engine.detect(image)
    return {
        "width": image.width,
        "height": image.height,
        "model_key": embedding.model_key,
        "dimensions": len(embedding.vector),
        "detections": detections,
    }


async def bootstrap(session, storage, service, node_ids):
    from sqlalchemy import select

    from wuwa_story.db.models.storage import FileLocation, FileObject, FileReference
    from wuwa_story.db.models.visual_memory import VisualIdentityNode

    if (
        not 1 <= len(node_ids) <= 20
        or any(isinstance(x, bool) or not isinstance(x, int) or x < 1 for x in node_ids)
        or len(set(node_ids)) != len(node_ids)
    ):
        raise ValueError("Provide 1–20 distinct positive node IDs")
    results = []
    for node_id in node_ids:
        identity_ids = (
            await session.scalars(
                select(VisualIdentityNode.identity_id)
                .where(VisualIdentityNode.node_id == node_id)
                .order_by(VisualIdentityNode.identity_id)
            )
        ).all()
        if len(identity_ids) != 1:
            results.append({"node_id": node_id, "status": "identity_missing_or_ambiguous"})
            continue
        statement = (
            select(FileReference, FileObject, FileLocation)
            .join(FileObject, FileObject.id == FileReference.file_id)
            .join(FileLocation, FileLocation.file_id == FileObject.id)
            .where(
                FileReference.owner_node_id == node_id,
                FileObject.mime_type.in_(
                    ("image/jpeg", "image/png", "image/webp", "image/gif", "image/bmp")
                ),
                FileLocation.available.is_(True),
                FileLocation.backend == storage.backend,
                FileLocation.bucket == storage.bucket,
            )
            .order_by(FileReference.id.desc(), FileLocation.is_primary.desc(), FileLocation.id)
            .limit(1)
        )
        row = (await session.execute(statement)).first()
        if row is None:
            results.append({"node_id": node_id, "status": "image_missing"})
            continue
        reference, file, location = row
        if file.size_bytes is not None and file.size_bytes > MAX_IMAGE_BYTES:
            results.append(
                {"node_id": node_id, "status": "image_too_large", "file_reference_id": reference.id}
            )
            continue
        data = bytearray()
        async for chunk in storage.get(location.object_key):
            data.extend(chunk)
            if len(data) > MAX_IMAGE_BYTES:
                raise ValueError(f"File reference {reference.id} exceeds 8 MiB")
        result = await service.add_reference(
            bytes(data),
            links=[{"identity_id": identity_ids[0], "appearance_id": None}],
            source_type="game_asset",
            status="pending",
            rights=f"Game asset; rights not verified. node_id={node_id}; file_reference_id={reference.id}; file_id={file.id}; reference_type={reference.reference_type}",
        )
        results.append(
            {
                "node_id": node_id,
                "file_reference_id": reference.id,
                "reference_id": result["id"],
                "status": "duplicate" if result.get("duplicate") else "pending",
            }
        )
    return {"items": results}


async def _run_bootstrap(node_ids):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from wuwa_story.config.settings import get_settings
    from wuwa_story.storage.s3 import S3Storage
    from wuwa_story.visual_memory.service import VisualMemoryService

    settings = get_settings()
    db_engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        async with async_sessionmaker(db_engine, expire_on_commit=False)() as session:
            storage = S3Storage(settings)
            result = await bootstrap(
                session, storage, VisualMemoryService(session, storage=storage), node_ids
            )
            await session.commit()
            return result
    finally:
        await db_engine.dispose()


async def _run_enrich_web(args):
    from wuwa_story.visual_memory.web_enrichment import (
        EnrichmentTarget,
        collect_web_images_for_target,
        export_candidates_to_dir,
        load_enrichment_targets,
        persist_candidates_to_visual_memory,
    )

    names = [n.strip() for n in args.names.split(",") if n.strip()] if args.names else None
    suffixes = [s.strip() for s in args.suffixes.split(",") if s.strip()] if args.suffixes else None
    role_types = [r.strip() for r in args.role_types.split(",") if r.strip()] if args.role_types else None
    export_dir = Path(args.export_dir) if args.export_dir else None

    if args.offline:
        if not names:
            raise ValueError("--offline requires --names (comma-separated character/NPC names)")
        offline_kind = "npc" if args.kind == "npc" else ("playable" if args.kind == "playable" else "custom")
        targets = [
            EnrichmentTarget(entity_id=None, name=name, kind=offline_kind)
            for name in (names[: args.limit] if args.limit else names)
        ]
        items = []
        for target in targets:
            collected = await collect_web_images_for_target(
                target,
                suffixes=suffixes,
                per_query=args.per_query,
                max_images=args.max_per_entity,
                min_size=args.min_size,
                delay_seconds=args.delay,
            )
            saved_dir = (
                export_candidates_to_dir(export_dir, target, collected["candidates"])
                if export_dir
                else None
            )
            items.append(
                {
                    "name": target.name,
                    "kind": target.kind,
                    "found": len(collected["candidates"]),
                    "skipped": collected["skipped"],
                    "errors": collected["errors"],
                    "export_dir": str(saved_dir) if saved_dir else None,
                }
            )
        return {"mode": "offline", "targets": len(items), "items": items}

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from wuwa_story.config.settings import get_settings
    from wuwa_story.storage.s3 import S3Storage
    from wuwa_story.visual_memory.service import VisualMemoryService

    settings = get_settings()
    db_engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        async with async_sessionmaker(db_engine, expire_on_commit=False)() as session:
            targets = await load_enrichment_targets(
                session,
                kind=args.kind,
                names=names,
                limit=args.limit,
                role_types=role_types,
            )
            storage = S3Storage(settings)
            service = VisualMemoryService(session, storage=storage)
            items = []
            for target in targets:
                collected = await collect_web_images_for_target(
                    target,
                    suffixes=suffixes,
                    per_query=args.per_query,
                    max_images=args.max_per_entity,
                    min_size=args.min_size,
                    delay_seconds=args.delay,
                )
                saved_dir = (
                    export_candidates_to_dir(export_dir, target, collected["candidates"])
                    if export_dir
                    else None
                )
                db_stats = None
                if not args.dry_run:
                    db_stats = await persist_candidates_to_visual_memory(
                        session,
                        service,
                        target,
                        collected["candidates"],
                        storage=storage,
                        include_db_images=args.include_db_images,
                    )
                    await session.commit()
                items.append(
                    {
                        "entity_id": target.entity_id,
                        "name": target.name,
                        "kind": target.kind,
                        "role_type": target.role_type,
                        "existing_db_images": target.existing_image_count,
                        "found_web": len(collected["candidates"]),
                        "skipped": collected["skipped"],
                        "errors": collected["errors"],
                        "export_dir": str(saved_dir) if saved_dir else None,
                        "db": db_stats,
                    }
                )
            if not args.dry_run:
                await session.commit()
            return {"mode": "dry_run" if args.dry_run else "db", "targets": len(items), "items": items}
    finally:
        await db_engine.dispose()


def main():
    parser = argparse.ArgumentParser(prog="visual-memory")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "status", help="Check local model files and dependencies without loading models"
    )
    smoke_parser = commands.add_parser("smoke", help="Run both models against one local image")
    smoke_parser.add_argument("image", help="Local image file")
    bootstrap_parser = commands.add_parser(
        "bootstrap", help="Import one stored image per explicitly selected node"
    )
    bootstrap_parser.add_argument("node_ids", type=int, nargs="+", metavar="NODE_ID")
    enrich_parser = commands.add_parser(
        "enrich-web",
        help="Discover and import character/NPC reference images from DuckDuckGo",
    )
    enrich_parser.add_argument("--kind", choices=("playable", "npc", "all"), default="playable")
    enrich_parser.add_argument("--names", help="Comma-separated entity names to filter or run offline")
    enrich_parser.add_argument("--role-types", help="Comma-separated role_type values (e.g. 2,4,5)")
    enrich_parser.add_argument("--suffixes", help="Comma-separated search suffixes")
    enrich_parser.add_argument("--limit", type=int, default=None, help="Max entities to process")
    enrich_parser.add_argument("--per-query", type=int, default=5, help="Max DDG results per query")
    enrich_parser.add_argument("--max-per-entity", type=int, default=12, help="Max images per entity")
    enrich_parser.add_argument("--min-size", type=int, default=200, help="Min width and height in px")
    enrich_parser.add_argument("--delay", type=float, default=0.4, help="Delay between DDG queries (s)")
    enrich_parser.add_argument("--export-dir", help="Optional local folder to save images + manifest.json")
    enrich_parser.add_argument("--include-db-images", action="store_true", help="Also import DB images")
    enrich_parser.add_argument("--dry-run", action="store_true", help="Do not write to DB")
    enrich_parser.add_argument("--offline", action="store_true", help="Run without DB using --names")
    args = parser.parse_args()
    try:
        if args.command == "status":
            result = model_status()
        elif args.command == "smoke":
            result = smoke(args.image)
        elif args.command == "enrich-web":
            result = asyncio.run(_run_enrich_web(args))
        else:
            result = asyncio.run(_run_bootstrap(args.node_ids))
    except (ValueError, VisualMemoryUnavailable, OSError) as exc:
        parser.exit(1, f"visual-memory: {exc}\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()

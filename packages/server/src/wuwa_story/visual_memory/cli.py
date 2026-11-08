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
    args = parser.parse_args()
    try:
        if args.command == "status":
            result = model_status()
        elif args.command == "smoke":
            result = smoke(args.image)
        else:
            result = asyncio.run(_run_bootstrap(args.node_ids))
    except (ValueError, VisualMemoryUnavailable, OSError) as exc:
        parser.exit(1, f"visual-memory: {exc}\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()

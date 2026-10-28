"""Serve viewer and model from one CDN origin; never proxy GLB through the API."""

import asyncio
import hashlib
import mimetypes
import re
from pathlib import Path


async def publish_viewer(storage, root=Path("/app/model-viewer")):
    files = sorted(path for path in root.rglob("*") if path.is_file())
    if not (root / "index.html").is_file() or not files:
        raise ValueError("Static model viewer is missing from the model worker image")
    identity = hashlib.sha256()
    for path in files:
        identity.update(path.relative_to(root).as_posix().encode() + b"\0")
        identity.update(hashlib.sha256(path.read_bytes()).digest())
    prefix = f"model-viewer/{identity.hexdigest()}"
    index_key = prefix + "/index.html"
    if await storage.stat(index_key) is None:
        for path in sorted(files, key=lambda value: value.name == "index.html"):
            mime = mimetypes.guess_type(path.name)[0] or "text/plain"
            await storage.put_file(path, prefix + "/" + path.relative_to(root).as_posix(), mime)
    return index_key


async def activate_viewer(storage, root=Path("/app/model-viewer")):
    """Switch the stable entry point only after the immutable viewer is complete."""
    index_key = await publish_viewer(storage, root)
    if not re.fullmatch(r"model-viewer/[0-9a-f]{64}/index\.html", index_key):
        raise ValueError("Invalid immutable model viewer key")
    current_key = "model-viewer/current/index.html"
    body = (
        '<!doctype html><meta charset="utf-8"><title>Model viewer</title>'
        f'<script>location.replace("../{index_key.split("/")[1]}/index.html" + location.search + location.hash);</script>'
    ).encode()
    await asyncio.to_thread(
        storage.client.put_object,
        Bucket=storage.bucket,
        Key=current_key,
        Body=body,
        CacheControl="no-cache",
        ContentType="text/html",
    )
    return current_key


async def ensure_glb_download(storage, key, digest):
    """Set a usable extension without duplicating hash-addressed object bytes."""
    disposition = f'attachment; filename="model-{digest[:16]}.glb"'
    head = await asyncio.to_thread(storage.client.head_object, Bucket=storage.bucket, Key=key)
    if head.get("ContentDisposition") == disposition:
        return
    preserved = {
        name: head[name]
        for name in (
            "ContentType",
            "CacheControl",
            "ContentEncoding",
            "ContentLanguage",
            "Expires",
            "StorageClass",
        )
        if name in head
    }
    await asyncio.to_thread(
        storage.client.copy_object,
        Bucket=storage.bucket,
        Key=key,
        CopySource={"Bucket": storage.bucket, "Key": key},
        MetadataDirective="REPLACE",
        Metadata=head.get("Metadata", {}),
        ContentDisposition=disposition,
        **preserved,
    )


def main():
    import argparse

    from wuwa_story.config.settings import get_settings
    from wuwa_story.storage.s3 import S3Storage

    parser = argparse.ArgumentParser(description="Activate the static model viewer")
    parser.add_argument("--activate", action="store_true", required=True)
    parser.parse_args()
    print(asyncio.run(activate_viewer(S3Storage(get_settings()))))


if __name__ == "__main__":
    main()

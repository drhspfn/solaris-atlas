"""Download metadata for published model assets; the web owns the viewer runtime."""

import asyncio


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

"""Import public launcher Notices and archive their official images in object storage."""

import asyncio
import tempfile
from pathlib import Path

import httpx
from sqlalchemy import select
from wuwa_story.config.settings import get_settings
from wuwa_story.db.models.notices import GameNotice, GameNoticeRevision
from wuwa_story.db.models.storage import FileLocation, FileReference
from wuwa_story.ingestion.notices import fetch_notice_feed, safe_notice_url, store_notices
from wuwa_story.storage.s3 import S3Storage
from wuwa_story.storage.service import FileRegistrationService

from wuwa_story_worker.asset_export import export_assets
from wuwa_story_worker.tooling import tool_path


async def import_notices(root, session):
    receipt = await export_assets(root, tool_path("WUWA_FMODEL_PATH"), "JavaScript/Launcher/Notice")
    definitions = list((receipt.parent / "files").rglob("LauncherNoticeDefine.js"))
    if len(definitions) != 1:
        raise ValueError("Expected one client launcher Notice definition")
    feed, page, document = await fetch_notice_feed(definitions[0].read_text(encoding="utf-8"))
    report = await store_notices(session, feed, page, document)
    await session.commit()  # Text/history remains available if individual media fails.
    notices = list(
        await session.scalars(
            select(GameNotice).where(GameNotice.feed == feed, GameNotice.locale == "en")
        )
    )
    await session.commit()  # Release archive read locks before downloading images.
    storage = S3Storage(get_settings())
    await storage.ensure_bucket()
    registration = FileRegistrationService(storage)
    urls = {
        url
        for notice in notices
        for url in (
            [
                notice.content.get(key)
                for key in ("banner_url", "fold_banner_url")
                if notice.content.get(key)
                in (notice.content["raw"].get("tabBanner"), notice.content["raw"].get("foldBanner"))
            ]
            + [
                block["source_url"]
                for block in notice.content["blocks"]
                if block["type"] == "image" and block["url"] == block["source_url"]
            ]
        )
        if url and safe_notice_url(url, image=True)
    }
    semaphore = asyncio.Semaphore(4)
    failures, downloaded = [], {}
    with tempfile.TemporaryDirectory(prefix="wuwa-notices-") as directory:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:

            async def download(url, index):
                async with semaphore:
                    try:
                        async with client.stream("GET", url) as response:
                            response.raise_for_status()
                            mime = response.headers.get("content-type", "").split(";")[0]
                            if mime not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
                                raise ValueError("Unsupported Notice image format")
                            path = Path(directory) / f"image-{index}"
                            size = 0
                            with path.open("wb") as output:
                                async for chunk in response.aiter_bytes():
                                    size += len(chunk)
                                    if size > 20 * 1024 * 1024:
                                        raise ValueError("Notice image exceeds 20 MiB")
                                    output.write(chunk)
                            downloaded[url] = (path, mime)
                    except (httpx.HTTPError, ValueError) as error:
                        failures.append(
                            {
                                "expected": url,
                                "status": "notice_image_failed",
                                "error": type(error).__name__,
                            }
                        )

            await asyncio.gather(*(download(url, index) for index, url in enumerate(sorted(urls))))
        published = {}
        # One session is used sequentially; network downloads alone run concurrently.
        for url, (path, mime) in downloaded.items():
            file = await registration.register_file(session, path, "image", mime_type=mime)
            reference = await session.scalar(
                select(FileReference).where(
                    FileReference.reference_type == "notice_image",
                    FileReference.source_path == url,
                    FileReference.file_id == file.id,
                )
            )
            if reference is None:
                await registration.register_reference(
                    session, file_id=file.id, reference_type="notice_image", source_path=url
                )
            # register_file uses the canonical content-addressed object key.
            location = await session.scalar(
                select(FileLocation).where(
                    FileLocation.file_id == file.id,
                    FileLocation.backend == "s3",
                    FileLocation.bucket == storage.bucket,
                    FileLocation.available.is_(True),
                    FileLocation.is_primary.is_(True),
                )
            )
            if location:
                published[url] = storage.public_url(location.object_key)
            # Publish each reusable file independently; another importer may
            # need the same content hash while later images are still uploading.
            await session.commit()
        for notice in notices:
            content = dict(notice.content)
            content["blocks"] = [
                {**block, "url": published.get(block.get("source_url"), block.get("url"))}
                if block["type"] == "image"
                else block
                for block in content["blocks"]
            ]
            for key in ("banner_url", "fold_banner_url"):
                content[key] = published.get(content.get(key), content.get(key))
            notice.content = content
            snapshot = await session.get(GameNoticeRevision, (notice.id, notice.revision))
            if snapshot:
                snapshot.content = content
        await session.commit()
    return {
        **report,
        "images": len(published),
        "missing": len(failures),
        "media_report": {"entries": failures},
    }, "partial" if failures else "completed"

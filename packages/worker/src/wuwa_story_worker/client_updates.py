"""Keep installed client tiers current through the existing durable download queue."""

import asyncio
import logging
import os
from datetime import UTC, datetime

from wuwa_story.db.session import SessionFactory
from wuwa_story.ingestion.client_assets import inspect_installed_clients
from wuwa_story.ingestion.client_updates import check_client_update

from wuwa_story_worker.client_assets import save_json
from wuwa_story_worker.tooling import asset_workspace

logger = logging.getLogger(__name__)


async def poll_client_updates(workspace):
    installed = await asyncio.to_thread(inspect_installed_clients, workspace)
    tiers = sorted({item["tier"] for item in installed
                    if item.get("state") == "downloaded" and item.get("tier") in {"hd", "sd", "uhd"}})
    results = []
    for tier in tiers:
        async with SessionFactory() as session:
            results.append(await check_client_update(session, tier))
    return results


async def watch_client_updates():
    interval = int(os.getenv("WUWA_CLIENT_UPDATE_INTERVAL_SECONDS", "3600"))
    if interval == 0:
        return
    if interval < 60:
        raise ValueError("Client update interval must be at least 60 seconds, or 0 to disable")
    workspace = asset_workspace()
    while True:
        status = {"checked_at": datetime.now(UTC).isoformat(), "interval_seconds": interval}
        try:
            results = await poll_client_updates(workspace)
            status["results"] = [{k: v for k, v in result.items() if k != "queued_at"}
                                 for result in results]
            logger.info("Official client update check: %s", results)
        except Exception as error:
            status["error"] = str(error)[:1000]
            logger.exception("Client update check failed; retrying next interval")
        try:
            await asyncio.to_thread(save_json, workspace / "client-update-status.json", status)
        except OSError:
            logger.exception("Could not save client update check status")
        await asyncio.sleep(interval)

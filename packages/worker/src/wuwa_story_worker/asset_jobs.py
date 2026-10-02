"""Independent asset download jobs; snapshot imports never download a game client."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from wuwa_story_worker.asset_export import export_assets
from wuwa_story_worker.broker import publish_job
from wuwa_story_worker.client_assets import download_plan, validate_plan

logger = logging.getLogger(__name__)
_download_lock = asyncio.Lock()


async def enqueue_assets(plan: dict[str, Any]) -> None:
    validate_plan(plan)
    await publish_job("asset_download", plan, plan["id"])
    logger.info("Queued asset job %s (%s %s)", plan["id"], plan["version"], plan["tier"])


async def download_client_assets(plan: dict[str, Any]) -> None:
    workspace = Path(os.getenv("WUWA_ASSET_WORKSPACE", "/var/lib/wuwa-assets")).resolve()
    concurrency = int(os.getenv("WUWA_ASSET_DOWNLOAD_CONCURRENCY", "4"))
    async with _download_lock:
        root = await asyncio.to_thread(download_plan, plan, workspace, concurrency)
    logger.info("Asset job %s ready for extraction at %s", plan["id"], root)
    for asset_filter in os.getenv("WUWA_ASSET_EXPORT_FILTERS", "ConfigDB").split(","):
        asset_filter = asset_filter.strip()
        if not re.fullmatch(r"[a-zA-Z0-9_./-]+", asset_filter):
            raise ValueError("Invalid asset export filter")
        tag = hashlib.sha256(asset_filter.encode()).hexdigest()[:16]
        await publish_job("asset_extract", {"schema_version": 1, "job_type": "assets.extract",
                          "download_id": plan["id"], "version": plan["version"], "tier": plan["tier"],
                          "filter": asset_filter}, plan["id"] + "-" + tag)


async def extract_client_assets(payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != 1 or payload.get("job_type") != "assets.extract":
        raise ValueError("Unsupported extraction job")
    for key, pattern in {"download_id": r"[0-9a-f]{64}", "version": r"\d+\.\d+\.\d+",
                         "tier": r"sd|hd|uhd", "filter": r"[a-zA-Z0-9_./-]+"}.items():
        if not isinstance(payload.get(key), str) or not re.fullmatch(pattern, payload[key]):
            raise ValueError(f"Invalid extraction {key}")
    workspace = Path(os.environ["WUWA_ASSET_WORKSPACE"]).resolve()
    root = workspace / "assets" / f"{payload['version']}-{payload['tier']}-{payload['download_id'][:16]}"
    plan = json.loads((root / "plan.json").read_text(encoding="utf-8"))
    if plan["id"] != payload["download_id"]:
        raise ValueError("Extraction job does not match downloaded plan")
    receipt = await export_assets(root, Path(os.environ["WUWA_FMODEL_PATH"]), payload["filter"], upload=True)
    logger.info("Exported and published %s: %s", payload["filter"], receipt)

"""Independent asset download jobs; snapshot imports never download a game client."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from typing import Any

from wuwa_story_worker.asset_export import export_assets
from wuwa_story_worker.broker import publish_job
from wuwa_story_worker.client_assets import download_plan, validate_plan
from wuwa_story_worker.job_tracking import update_admin_run
from wuwa_story_worker.map_assets import build_maps
from wuwa_story_worker.tooling import asset_workspace, tool_path

logger = logging.getLogger(__name__)
_download_lock = asyncio.Lock()


async def enqueue_assets(plan: dict[str, Any]) -> None:
    validate_plan(plan)
    await publish_job("asset_download", plan, plan["id"])
    logger.info("Queued asset job %s (%s %s)", plan["id"], plan["version"], plan["tier"])


async def enqueue_maps(plan: dict[str, Any], run_id: int | None = None) -> None:
    validate_plan(plan)
    payload = {
        "schema_version": 1,
        "job_type": "assets.maps",
        "download_id": plan["id"],
        "version": plan["version"],
        "tier": plan["tier"],
    }
    if run_id is not None:
        payload["run_id"] = run_id
    await publish_job("asset_extract", payload, plan["id"] + "-maps-v1")


async def download_client_assets(plan: dict[str, Any]) -> None:
    run_id = plan.get("run_id")
    if run_id is not None:
        if type(run_id) is not int or run_id < 1:
            raise ValueError("Invalid asset download run ID")
        await update_admin_run(run_id, "asset_download", "running")
    try:
        workspace = asset_workspace()
        concurrency = int(os.getenv("WUWA_ASSET_DOWNLOAD_CONCURRENCY", "4"))
        async with _download_lock:
            root = await asyncio.to_thread(download_plan, plan, workspace, concurrency)
        logger.info("Asset job %s ready for extraction at %s", plan["id"], root)
        for asset_filter in os.getenv("WUWA_ASSET_EXPORT_FILTERS", "ConfigDB").split(","):
            asset_filter = asset_filter.strip()
            if not re.fullmatch(r"[a-zA-Z0-9_./-]+", asset_filter):
                raise ValueError("Invalid asset export filter")
            tag = hashlib.sha256(asset_filter.encode()).hexdigest()[:16]
            await publish_job(
                "asset_extract",
                {
                    "schema_version": 1,
                    "job_type": "assets.extract",
                    "download_id": plan["id"],
                    "version": plan["version"],
                    "tier": plan["tier"],
                    "filter": asset_filter,
                },
                plan["id"] + "-" + tag,
            )
        if os.getenv("WUWA_ASSET_BUILD_MAPS", "0") == "1":
            await enqueue_maps(plan)
    except Exception as error:
        if run_id is not None:
            try:
                await update_admin_run(
                    run_id, "asset_download", "failed", error=f"{type(error).__name__}: {error}"
                )
            except Exception:
                logger.exception("Failed to record asset download failure run_id=%s", run_id)
        raise
    if run_id is not None:
        await update_admin_run(run_id, "asset_download", "completed")


async def extract_client_assets(payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != 1 or payload.get("job_type") not in ("assets.extract", "assets.maps"):
        raise ValueError("Unsupported extraction job")
    patterns = {"download_id": r"[0-9a-f]{64}", "version": r"\d+\.\d+\.\d+", "tier": r"sd|hd|uhd"}
    if payload["job_type"] == "assets.extract":
        patterns["filter"] = r"[a-zA-Z0-9_./-]+"
    for key, pattern in patterns.items():
        if not isinstance(payload.get(key), str) or not re.fullmatch(pattern, payload[key]):
            raise ValueError(f"Invalid extraction {key}")
    run_id = payload.get("run_id")
    processor_key = "map_build" if payload["job_type"] == "assets.maps" else "asset_extract"
    if run_id is not None:
        if type(run_id) is not int or run_id < 1:
            raise ValueError("Invalid extraction run ID")
        await update_admin_run(run_id, processor_key, "running")
    try:
        workspace = asset_workspace()
        root = workspace / "assets" / f"{payload['version']}-{payload['tier']}-{payload['download_id'][:16]}"
        plan = json.loads((root / "plan.json").read_text(encoding="utf-8"))
        if plan["id"] != payload["download_id"]:
            raise ValueError("Extraction job does not match downloaded plan")
        if payload["job_type"] == "assets.maps":
            receipt = await build_maps(
                root,
                tool_path("WUWA_FMODEL_PATH"),
                tool_path("WUWA_TEXTURE_CONVERTER_PATH"),
                publish=True,
            )
            logger.info("Built and published map assets: %s", receipt)
        else:
            receipt = await export_assets(
                root, tool_path("WUWA_FMODEL_PATH"), payload["filter"], upload=True
            )
            logger.info("Exported and published %s: %s", payload["filter"], receipt)
    except Exception as error:
        if run_id is not None:
            try:
                await update_admin_run(
                    run_id, processor_key, "failed", error=f"{type(error).__name__}: {error}"
                )
            except Exception:
                logger.exception("Failed to record extraction failure run_id=%s", run_id)
        raise
    if run_id is not None:
        await update_admin_run(run_id, processor_key, "completed")

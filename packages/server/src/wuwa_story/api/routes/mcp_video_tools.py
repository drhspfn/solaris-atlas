"""Capability-separated adapters for registered cutscene video review."""

import base64
from typing import Any

from mcp.types import ImageContent, Tool
from pydantic import Field

from wuwa_story.api.routes.visual_memory_video import ScanInput, service_for
from wuwa_story.visual_memory.tools import Input


class SourceInput(Input):
    cutscene_key: str = Field(min_length=1, max_length=512)
    game_version: str | None = Field(default=None, max_length=64)
    locale: str = Field(default="en", min_length=1, max_length=32)


class ScanIdInput(Input):
    scan_id: int = Field(gt=0, strict=True)
    frame_offset: int = Field(default=0, ge=0, strict=True)
    frame_limit: int = Field(default=4, ge=1, le=4, strict=True)


class InternalScanInput(Input):
    scan_id: int = Field(gt=0, strict=True)
    frame_offset: int = Field(default=0, ge=0, strict=True)
    frame_limit: int = Field(default=12, ge=1, le=12, strict=True)


TOOLS = [
    Tool(name="visual_inspect_cutscene", description="Inspect published cutscene video sources and their review status. No scan is started.",
         inputSchema=SourceInput.model_json_schema()),
    Tool(name="visual_get_video_scan", description="View up to four sampled frames from a reviewed video scan. Observations and private moderation are excluded.",
         inputSchema=ScanIdInput.model_json_schema()),
]


async def dispatch(name: str, arguments: dict[str, Any], session: Any) -> dict[str, Any]:
    """Public output is explicitly projected, never the administrator scan manifest."""
    try:
        if name == "visual_inspect_cutscene":
            data = SourceInput.model_validate(arguments)
            result = await service_for(session).sources(
                cg_name=data.cutscene_key, game_version=data.game_version, locale=data.locale,
            )
            sources = []
            for source in result.get("sources", []):
                scan = source.get("scan") or {}
                sources.append({
                    "asset_node_id": source.get("asset_node_id"),
                    "asset_version": source.get("asset_version"),
                    "url": source.get("url"),
                    "duration": source.get("duration"),
                    "reviewed": scan.get("reviewed") is True,
                })
            return {"name": result.get("name"), "game_version": result.get("game_version"),
                    "sources": sources}
        if name == "visual_get_video_scan":
            data = ScanIdInput.model_validate(arguments)
            scan = await service_for(session).get_scan(data.scan_id, frame_offset=data.frame_offset,
                                                       frame_limit=data.frame_limit)
            if scan.get("reviewed") is not True:
                raise ValueError("Scan unavailable")
            # Do not expose unvalidated historical source URLs, crops, labels or decisions.
            progress = scan.get("progress") or {}
            return {"id": scan["id"], "status": scan["status"], "reviewed": True,
                    "progress": {"completed": progress.get("completed"), "total": progress.get("total")},
                    "frames": [{"index": frame["index"], "time": frame["time"],
                                "image_url": frame.get("image_url")} for frame in scan.get("frames", [])[:data.frame_limit]],
                    "frame_count": scan.get("frame_count"),
                    "next_frame_offset": scan.get("next_frame_offset")}
        raise ValueError("Unknown video tool")
    except ValueError:
        return {"error": {"code": "invalid_input", "message": "Input or reviewed scan unavailable."}}


async def frame_contents(name: str, arguments: dict[str, Any], session: Any) -> list[ImageContent]:
    """Load only registered frames from a still-reviewed scan, never an arbitrary URL."""
    if name != "visual_get_video_scan":
        return []
    data = ScanIdInput.model_validate(arguments)
    service = service_for(session)
    scan = await service.get_scan(data.scan_id, frame_offset=data.frame_offset,
                                  frame_limit=data.frame_limit)
    if scan.get("reviewed") is not True:
        raise ValueError("Scan unavailable")
    images = []
    for frame in scan.get("frames", [])[:data.frame_limit]:
        content = await service.frame_bytes(data.scan_id, frame["index"])
        if not content.startswith(b"\xff\xd8\xff") or len(content) > 5 * 1024 * 1024:
            raise ValueError("Invalid stored frame")
        images.append(ImageContent(type="image", data=base64.b64encode(content).decode("ascii"),
                                   mimeType="image/jpeg"))
    return images


async def read_internal(arguments: dict[str, Any], session: Any) -> dict[str, Any]:
    data = InternalScanInput.model_validate(arguments)
    return await service_for(session).get_scan(**data.model_dump())


async def enqueue_internal(arguments: dict[str, Any], session: Any) -> dict[str, Any]:
    """Only the internal/worker capability registry may call this mutating adapter."""
    data = ScanInput.model_validate(arguments)
    values = data.model_dump()
    if values["cutscene_key"] is None:
        values.pop("cutscene_key")
    return await service_for(session).enqueue(**values)

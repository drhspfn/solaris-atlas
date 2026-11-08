"""Bounded, local-only frame extraction for visual-memory video jobs.

The caller is responsible for downloading and verifying a registered object from
configured storage. These functions must not receive user-supplied paths or URLs.
"""

import asyncio
import copy
import hashlib
import json
import math
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert

from wuwa_story.db.models import (
    FileLocation,
    FileObject,
    FileReference,
    MediaAsset,
    Node,
    NodeType,
    ProcessingRun,
    Processor,
)
from wuwa_story.storage.service import FileRegistrationService
from wuwa_story.story.cutscene_publications import publication_order, publication_types
from wuwa_story.visual_memory.service import VisualMemoryService

PROCESSOR = "visual_memory_video"
PROTOCOL = "visual-video-v1"


def sample_times(duration: float, interval: float = 3) -> list[float]:
    """Keep the requested interval across the whole clip, including its ending."""
    if (
        not math.isfinite(duration)
        or not math.isfinite(interval)
        or duration <= 0
        or not 0.25 <= interval <= 60
    ):
        raise ValueError("Invalid bounded sampling plan")
    last = duration - min(0.2, duration / 2)
    times = [round(index * interval, 4) for index in range(math.floor(last / interval) + 1)]
    if last - times[-1] > 0.01:
        times.append(round(last, 4))
    return times


def probe_duration(video: Path) -> float:
    """Probe a trusted local MP4 without allowing network or playlist protocols."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-protocol_whitelist",
            "file,pipe",
            "-f",
            "mov",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(video.resolve()),
        ],
        capture_output=True,
        check=True,
        timeout=30,
    )
    try:
        duration = float(json.loads(result.stdout)["format"]["duration"])
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError("Video duration is unavailable") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Video duration is unavailable")
    return duration


def extract_frame(video: Path, timestamp: float) -> bytes:
    """Extract one bounded JPEG from a verified local MP4."""
    if not math.isfinite(timestamp) or timestamp < 0:
        raise ValueError("Invalid frame timestamp")
    result = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-threads",
            "1",
            "-filter_threads",
            "1",
            "-protocol_whitelist",
            "file,pipe",
            "-ss",
            str(timestamp),
            "-f",
            "mov",
            "-i",
            str(video.resolve()),
            "-map",
            "0:v:0",
            "-frames:v",
            "1",
            "-vf",
            "scale=1280:720:force_original_aspect_ratio=decrease,format=yuvj420p",
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
        timeout=60,
    )
    if not result.stdout or len(result.stdout) > 5 * 1024 * 1024:
        raise ValueError("Frame extraction failed or exceeded the image limit")
    return result.stdout


class VideoReviewService:
    """Durable source-bound sampling. The caller owns commit/rollback and job execution."""

    def __init__(self, session, storage=None, engine=None):
        self.session = session
        self.memory = VisualMemoryService(session, storage, engine)
        self.storage = self.memory.storage

    async def catalog(self, q="", game_version=None, locale="en", limit=12, offset=0):
        from wuwa_story.api.routes.cutscenes import catalog

        result = await catalog(
            game_version=game_version,
            locale=locale,
            q=q,
            playable=True,
            limit=limit,
            offset=offset,
            cursor=None,
            session=self.session,
        )
        return {
            **result,
            "items": [
                {**row, "cutscene_key": row.get("name") or row.get("cg_name")}
                for row in result["items"]
            ],
        }

    async def _published(self, asset_node_id, version=None, cutscene_key=None):
        node_type = await self.session.scalar(
            select(NodeType.key).join(Node, Node.type_id == NodeType.id).where(Node.id == asset_node_id)
        )
        if node_type != "asset_reference" and not (
            node_type == "media_asset" and await self.session.get(MediaAsset, asset_node_id) is not None
        ):
            raise ValueError("A registered game media asset is required")
        statement = select(FileReference).where(
            FileReference.owner_node_id == asset_node_id,
            FileReference.reference_type.in_(publication_types("cutscene_video")),
        )
        if version:
            statement = statement.where(
                FileReference.metadata_json["asset_version"].astext == version
            )
        if cutscene_key is not None:
            statement = statement.where(
                or_(
                    FileReference.reference_type == "cutscene_video",
                    FileReference.metadata_json["cutscene_key"].astext == cutscene_key,
                )
            )
        reference = await self.session.scalar(
            statement.order_by(
                publication_order(FileReference.reference_type),
                FileReference.id.desc(),
            ).limit(1)
        )
        if reference is None:
            raise ValueError("No published video is available for this asset and version")
        file = await self.session.get(FileObject, reference.file_id)
        if not file or not file.sha256 or file.mime_type != "video/mp4":
            raise ValueError("A content-addressed published MP4 is required")
        if not file.size_bytes:
            raise ValueError("Published video must have a registered non-zero size")
        location = await self._location(file.id)
        version = reference.metadata_json.get("asset_version")
        url = (
            self.storage.public_url(location.object_key)
            if hasattr(self.storage, "public_url")
            else None
        )
        return {
            "asset_node_id": asset_node_id,
            "asset_version": version,
            "game_version": None,
            "source_reference_id": reference.id,
            "file_reference_id": reference.id,
            "file_id": file.id,
            "source_sha256": file.sha256.hex(),
            "sha256": file.sha256.hex(),
            "url": url,
            "video_url": url,
            "size_bytes": file.size_bytes,
            "duration": reference.metadata_json.get("duration_seconds")
            or reference.metadata_json.get("duration"),
            "object_key": location.object_key,
        }

    async def _location(self, file_id):
        location = await self.session.scalar(
            select(FileLocation)
            .where(
                FileLocation.file_id == file_id,
                FileLocation.backend == self.storage.backend,
                FileLocation.bucket == self.storage.bucket,
                FileLocation.available.is_(True),
            )
            .order_by(FileLocation.is_primary.desc(), FileLocation.id)
        )
        if location is None:
            raise ValueError("The registered object is not available in configured storage")
        return location

    async def source_by_reference(
        self, reference_id, game_version=None, asset_version=None, cutscene_key=None
    ):
        reference = await self.session.get(FileReference, reference_id)
        if reference is None or reference.reference_type not in publication_types("cutscene_video"):
            raise ValueError("Published video reference not found")
        if cutscene_key is not None:
            await self._validate_context(reference.owner_node_id, cutscene_key, game_version)
        source = await self._published(
            reference.owner_node_id,
            asset_version or reference.metadata_json.get("asset_version"),
            cutscene_key,
        )
        if source["source_reference_id"] != reference_id:
            raise ValueError("Video publication changed; reload the source list")
        source.update(game_version=game_version, cutscene_key=cutscene_key)
        return source

    async def _validate_context(self, asset_node_id, cutscene_key, game_version):
        from wuwa_story.api.routes.cutscenes import detail

        result = await detail(
            cutscene_key.removeprefix("cutscene:"), game_version, "en", self.session
        )
        media = ((result.get("event") or {}).get("playback") or {}).get("media") or {}
        if asset_node_id not in {item.get("asset_node_id") for item in media.values()}:
            raise ValueError("This published asset does not belong to the selected cutscene")

    async def sources(self, cg_name, game_version=None, locale="en"):
        from wuwa_story.api.routes.cutscenes import detail
        from wuwa_story.db.models import Node

        result = await detail(cg_name, game_version, locale, self.session)
        node = await self.session.get(Node, result["node_id"])
        cutscene_key = node.canonical_key
        playback = (result.get("event") or {}).get("playback") or {}
        sources = []
        seen = set()
        for label, media in (playback.get("media") or {}).items():
            asset_id = media.get("asset_node_id")
            if not asset_id or asset_id in seen:
                continue
            seen.add(asset_id)
            try:
                source = await self._published(
                    asset_id, playback.get("asset_version"), cutscene_key
                )
            except ValueError:
                continue
            source.update(
                label=label,
                cutscene_key=cutscene_key,
                cutscene_name=cg_name,
                game_version=result.get("game_version"),
            )
            scan = await self.session.scalar(
                select(ProcessingRun)
                .join(Processor)
                .where(
                    Processor.key == PROCESSOR,
                    ProcessingRun.metadata_json["source"]["source_sha256"].astext
                    == source["source_sha256"],
                )
                .order_by(ProcessingRun.id.desc())
                .limit(1)
            )
            source["scan"] = await self.get_scan(scan.id) if scan else None
            source.pop("object_key", None)
            sources.append(source)
        return {
            "name": cg_name,
            "game_version": result.get("game_version"),
            "sources": sources,
            "items": sources,
        }

    async def enqueue(
        self,
        asset_node_id,
        asset_version=None,
        game_version=None,
        source_reference_id=None,
        source_sha256=None,
        cutscene_key=None,
        interval_seconds=3,
        frame_batch_size=12,
        force=False,
    ):
        sample_times(1, interval_seconds)
        if not 1 <= frame_batch_size <= 48:
            raise ValueError("Frame page size must be between 1 and 48")
        if cutscene_key is not None:
            await self._validate_context(asset_node_id, cutscene_key, game_version)
        source = await self._published(
            asset_node_id,
            asset_version or (game_version if cutscene_key is None else None),
            cutscene_key,
        )
        source.update(game_version=game_version, cutscene_key=cutscene_key)
        if source_reference_id is not None and source["source_reference_id"] != source_reference_id:
            raise ValueError("Selected source was replaced; reload before starting a scan")
        if source_sha256 is not None and source["source_sha256"] != source_sha256:
            raise ValueError("Selected video bytes changed; reload before starting a scan")
        models = await asyncio.to_thread(self.memory._engine().model_identity)
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "sha256": source["source_sha256"],
                    "models": models,
                    "interval": interval_seconds,
                    "protocol": PROTOCOL,
                },
                sort_keys=True,
            ).encode()
        ).digest()
        await self.session.execute(
            select(func.pg_advisory_xact_lock(814, int.from_bytes(fingerprint[:3], "big")))
        )
        await self.session.execute(
            insert(Processor)
            .values(
                key=PROCESSOR,
                version=PROTOCOL,
                description="Human-reviewed cutscene frames for visual memory",
            )
            .on_conflict_do_nothing(index_elements=[Processor.key])
        )
        processor_id = await self.session.scalar(
            select(Processor.id).where(Processor.key == PROCESSOR)
        )
        existing = await self.session.scalar(
            select(ProcessingRun)
            .where(
                ProcessingRun.processor_id == processor_id,
                ProcessingRun.input_hash == fingerprint,
            )
            .order_by(ProcessingRun.id.desc())
            .limit(1)
        )
        if existing and (not force or existing.status in ("pending", "running")):
            return {**await self.get_scan(existing.id), "duplicate": True}
        if existing and existing.status == "failed":
            existing.status, existing.error = "pending", None
            await self.session.flush()
            return await self.get_scan(existing.id)
        run = ProcessingRun(
            processor_id=processor_id,
            target_node_id=asset_node_id,
            prompt_version=PROTOCOL,
            input_hash=fingerprint,
            status="pending",
            metadata_json={
                "source": source,
                "models": models,
                "interval_seconds": interval_seconds,
                "frame_batch_size": frame_batch_size,
                "reviewed": False,
            },
            raw_output={
                "completed_frames": 0,
                "total_frames": None,
                "total_detections": 0,
                "unresolved_count": 0,
            },
        )
        self.session.add(run)
        await self.session.flush()
        return await self.get_scan(run.id)

    async def _run(self, scan_id, lock=False):
        statement = (
            select(ProcessingRun)
            .join(Processor)
            .where(
                ProcessingRun.id == scan_id,
                Processor.key == PROCESSOR,
            )
            .execution_options(populate_existing=True)
        )
        if lock:
            statement = statement.with_for_update(of=ProcessingRun)
        run = await self.session.scalar(statement)
        if run is None:
            raise ValueError("Visual video scan not found")
        return run

    async def _frame(self, scan_id, frame_index, lock=False):
        from wuwa_story.db.models import VisualVideoFrame

        statement = (
            select(VisualVideoFrame)
            .where(
                VisualVideoFrame.scan_id == scan_id,
                VisualVideoFrame.frame_index == frame_index,
            )
            .execution_options(populate_existing=True)
        )
        if lock:
            statement = statement.with_for_update()
        frame = await self.session.scalar(statement)
        if frame is None:
            raise ValueError("Frame does not exist")
        return frame

    async def get_scan(self, scan_id, frame_offset=0, frame_limit=12):
        from wuwa_story.db.models import VisualVideoFrame

        if frame_offset < 0 or not 1 <= frame_limit <= 48:
            raise ValueError("Invalid frame page")
        run = await self._run(scan_id)
        source = {
            key: value for key, value in run.metadata_json["source"].items() if key != "object_key"
        }
        progress = run.raw_output or {}
        frames = []
        rows = (
            await self.session.scalars(
                select(VisualVideoFrame)
                .where(
                    VisualVideoFrame.scan_id == scan_id,
                )
                .order_by(VisualVideoFrame.frame_index)
                .offset(frame_offset)
                .limit(frame_limit)
            )
        ).all()
        for row in rows:
            location = await self._location(row.file_id)
            url = (
                self.storage.public_url(location.object_key)
                if hasattr(self.storage, "public_url")
                else f"/admin/visual-memory/videos/scans/{scan_id}/frames/{row.frame_index}/image"
            )
            frames.append(
                {
                    "index": row.frame_index,
                    "time": row.time,
                    "image_url": url,
                    "width": row.width,
                    "height": row.height,
                    "detections": row.detections,
                }
            )
        count = progress.get("completed_frames", 0)
        return {
            "id": run.id,
            "status": run.status,
            "reviewed": bool(run.metadata_json.get("reviewed")),
            "reviewed_at": run.metadata_json.get("reviewed_at"),
            "source": source,
            "models": run.metadata_json.get("models"),
            "frames": frames,
            "frame_count": count,
            "frame_offset": frame_offset,
            "frame_limit": frame_limit,
            "next_frame_offset": frame_offset + len(frames)
            if frame_offset + len(frames) < count
            else None,
            "total_detections": progress.get("total_detections", 0),
            "unresolved_count": progress.get("unresolved_count", 0),
            "interval_seconds": run.metadata_json.get("interval_seconds"),
            "frame_batch_size": run.metadata_json.get("frame_batch_size", 12),
            "progress": {"completed": count, "total": progress.get("total_frames")},
            "error": run.error,
            "sampling_only": True,
        }

    async def resolve(
        self, scan_id, frame_index, detection_index, decision="approved", links=None, actor_id=None
    ):
        if actor_id is None:
            raise ValueError("A human administrator must resolve video observations")
        decision = "approved" if decision == "approve" else decision
        if decision not in ("approved", "pending", "reject"):
            raise ValueError("Invalid observation decision")
        run = await self._run(scan_id, lock=True)
        if run.status != "succeeded" or run.metadata_json.get("reviewed"):
            raise ValueError("Only completed, unreviewed scans can be labelled")
        frame = await self._frame(scan_id, frame_index, lock=True)
        detections = copy.deepcopy(frame.detections)
        detection = next((d for d in detections if d["index"] == detection_index), None)
        if detection is None:
            raise ValueError("Detection does not exist")
        previous = detection.get("decision", "unresolved")
        if decision != "reject":
            image = await self.frame_bytes(scan_id, frame_index)
            source = run.metadata_json["source"]
            reference = await self.memory.add_reference(
                image,
                bbox=detection["bbox"],
                links=links or [],
                source_type="cutscene",
                source_url=source.get("url"),
                source_page=(source["url"] + "#t=" + str(frame.time))
                if source.get("url")
                else None,
                rights="Wuthering Waves game frame; rights retained by the original publisher",
                source_group="video:" + source["source_sha256"],
                actor_id=actor_id,
            )
            actual = {(x["identity_id"], x.get("appearance_id")) for x in reference["links"]}
            wanted = {(x["identity_id"], x.get("appearance_id")) for x in (links or [])}
            if actual != wanted:
                raise ValueError(
                    f"Crop already has different labels; review reference #{reference['id']} in Library"
                )
            if decision == "approved" or reference["status"] != "approved":
                reference = await self.memory.review_reference(
                    reference["id"],
                    decision,
                    actor_id=actor_id,
                    reason=f"Video scan {scan_id}, frame {frame_index} at {frame.time}s",
                )
            detection.update(reference_id=reference["id"], links=reference["links"])
        else:
            if detection.get("reference_id"):
                await self.memory.review_reference(
                    detection["reference_id"],
                    "rejected",
                    actor_id=actor_id,
                    reason=f"Rejected in video scan {scan_id}, frame {frame_index}",
                )
            detection["links"] = []
        detection.update(
            decision=decision,
            status=decision,
            reviewed_by=actor_id,
            reviewed_at=datetime.now(UTC).isoformat(),
        )
        frame.detections = detections
        run.raw_output = {
            **run.raw_output,
            "unresolved_count": max(
                0, run.raw_output.get("unresolved_count", 0) - int(previous == "unresolved")
            ),
        }
        await self.session.flush()
        return await self.get_scan(scan_id)

    async def edit_bbox(
        self, scan_id, frame_index, bbox, detection_index=None, frame_offset=None, frame_limit=12
    ):
        from wuwa_story.db.models import VisualVideoFrame
        from wuwa_story.visual_memory.images import crop_image, encode_png, load_image

        run = await self._run(scan_id, lock=True)
        if run.status != "succeeded" or run.metadata_json.get("reviewed"):
            raise ValueError("Only completed, unreviewed scans can be labelled")
        frame = await self._frame(scan_id, frame_index, lock=True)
        detections = copy.deepcopy(frame.detections)
        detection = None
        if detection_index is not None:
            detection = next((d for d in detections if d["index"] == detection_index), None)
            if detection is None:
                raise ValueError("Detection does not exist")
            if detection.get("decision", "unresolved") != "unresolved" or detection.get("reference_id"):
                raise ValueError("Only unresolved observations without a saved reference can be edited")

        file = await self.session.get(FileObject, frame.file_id)
        data = await self.frame_bytes(scan_id, frame_index)
        if not file or file.mime_type != "image/jpeg" or not data.startswith(b"\xff\xd8\xff"):
            raise ValueError("Stored frame is not a registered JPEG image")
        image = await asyncio.to_thread(load_image, data)
        crop = crop_image(image, bbox)
        box = [math.floor(bbox[0]), math.floor(bbox[1]), math.ceil(bbox[2]), math.ceil(bbox[3])]

        position = await self.session.scalar(
            select(func.count()).select_from(VisualVideoFrame).where(
                VisualVideoFrame.scan_id == scan_id, VisualVideoFrame.frame_index < frame_index
            )
        )
        if not 1 <= frame_limit <= 48:
            raise ValueError("Invalid frame page")
        if frame_offset is None:
            frame_offset = position // frame_limit * frame_limit
        if frame_offset < 0 or not frame_offset <= position < frame_offset + frame_limit:
            raise ValueError("Frame page must contain the edited frame")

        if detection is None:
            duplicate = next((d for d in detections if d.get("decision", "unresolved") != "reject"
                              and [math.floor(d["bbox"][0]), math.floor(d["bbox"][1]),
                                   math.ceil(d["bbox"][2]), math.ceil(d["bbox"][3])] == box), None)
            if duplicate is not None:
                return await self.get_scan(scan_id, frame_offset, frame_limit)

        found = await self.memory.search(await asyncio.to_thread(encode_png, crop))
        candidates = copy.deepcopy(found["candidates"])
        for candidate in candidates:
            candidate["evidence"] = candidate.get("evidence", [])[:2]
        if detection is None:
            detection = {"index": max((d["index"] for d in detections), default=-1) + 1,
                         "detection": {"score": None, "status": "manual"},
                         "decision": "unresolved", "status": "unresolved"}
            detections.append(detection)
            run.raw_output = {**run.raw_output,
                              "total_detections": run.raw_output.get("total_detections", 0) + 1,
                              "unresolved_count": run.raw_output.get("unresolved_count", 0) + 1}
        detection.update(bbox=box, normalized_bbox=[box[0] / image.width, box[1] / image.height,
                                                    box[2] / image.width, box[3] / image.height],
                         candidates=candidates, links=[])
        frame.detections = detections
        await self.session.flush()
        return await self.get_scan(scan_id, frame_offset, frame_limit)

    async def reject_all(self, scan_id, actor_id=None):
        from wuwa_story.db.models import VisualVideoFrame

        if actor_id is None:
            raise ValueError("A human administrator must review observations")
        run = await self._run(scan_id, lock=True)
        if run.status != "succeeded" or run.metadata_json.get("reviewed"):
            raise ValueError("Only completed, unreviewed scans can be reviewed")
        offset = 0
        while True:
            rows = (
                await self.session.scalars(
                    select(VisualVideoFrame)
                    .where(
                        VisualVideoFrame.scan_id == scan_id,
                    )
                    .order_by(VisualVideoFrame.frame_index)
                    .offset(offset)
                    .limit(24)
                )
            ).all()
            if not rows:
                break
            for frame in rows:
                detections = copy.deepcopy(frame.detections)
                for detection in detections:
                    if detection.get("decision", "unresolved") == "unresolved":
                        detection.update(
                            decision="reject",
                            status="reject",
                            reviewed_by=actor_id,
                            reviewed_at=datetime.now(UTC).isoformat(),
                        )
                frame.detections = detections
            await self.session.flush()
            offset += len(rows)
        run.raw_output = {**run.raw_output, "unresolved_count": 0}
        await self.session.flush()
        return await self.get_scan(scan_id)

    async def mark_reviewed(self, scan_id, actor_id=None):
        if actor_id is None:
            raise ValueError("A human administrator must finish review")
        run = await self._run(scan_id, lock=True)
        if run.status != "succeeded":
            raise ValueError("Sampling must complete before review can finish")
        if (run.raw_output or {}).get("unresolved_count", 0):
            raise ValueError("Resolve or skip every proposal before marking the video reviewed")
        run.metadata_json = {
            **run.metadata_json,
            "reviewed": True,
            "reviewed_by": actor_id,
            "reviewed_at": datetime.now(UTC).isoformat(),
        }
        await self.session.flush()
        return await self.get_scan(scan_id)

    async def frame_bytes(self, scan_id, frame_index):
        await self._run(scan_id)
        frame = await self._frame(scan_id, frame_index)
        location = await self._location(frame.file_id)
        data = bytearray()
        async for chunk in self.storage.get(location.object_key):
            data.extend(chunk)
            if len(data) > 5 * 1024 * 1024:
                raise ValueError("Stored frame exceeds the image limit")
        return bytes(data)

    async def process(self, scan_id):
        """Run only while holding the worker's session advisory lock for this scan."""
        from wuwa_story.db.models import VisualVideoFrame

        run = await self._run(scan_id)
        if run.status not in ("pending", "running"):
            return await self.get_scan(scan_id)
        spec = copy.deepcopy(run.metadata_json)
        if await asyncio.to_thread(self.memory._engine().model_identity) != spec["models"]:
            raise ValueError("Configured inference models changed; explicitly start a new scan")
        run.status, run.error = "running", None
        await self.session.commit()
        source = spec["source"]
        location = await self._location(source["file_id"])
        file = await self.session.get(FileObject, source["file_id"])
        if file is None or file.sha256.hex() != source["source_sha256"]:
            raise ValueError("Source video bytes changed")
        with tempfile.TemporaryDirectory(prefix="visual-video-") as directory:
            reserve = max(512 * 1024**2, source["size_bytes"] // 20)
            if shutil.disk_usage(directory).free < source["size_bytes"] + reserve:
                raise ValueError(
                    "Insufficient temporary disk space for this source video; free space and retry"
                )
            video = Path(directory) / "source.mp4"
            digest, size = hashlib.sha256(), 0
            with video.open("wb") as target:
                async for chunk in self.storage.get(location.object_key):
                    size += len(chunk)
                    if size > source["size_bytes"]:
                        raise ValueError("Stored video exceeds its registered size")
                    digest.update(chunk)
                    await asyncio.to_thread(target.write, chunk)
            if digest.hexdigest() != source["source_sha256"] or size != source["size_bytes"]:
                raise ValueError("Stored video failed source hash or length verification")
            duration = await asyncio.to_thread(probe_duration, video)
            times = sample_times(duration, spec["interval_seconds"])
            run = await self._run(scan_id)
            data = dict(run.raw_output or {})
            data.update(total_frames=len(times), duration=duration)
            run.raw_output = data
            await self.session.commit()
            for index, timestamp in enumerate(times):
                if (
                    await self.session.scalar(
                        select(VisualVideoFrame.id).where(
                            VisualVideoFrame.scan_id == scan_id,
                            VisualVideoFrame.frame_index == index,
                        )
                    )
                    is not None
                ):
                    continue
                jpeg = await asyncio.to_thread(extract_frame, video, timestamp)
                result = await self.memory.identify(jpeg, detect=True)
                path = Path(directory) / "frame.jpg"
                await asyncio.to_thread(path.write_bytes, jpeg)
                file = await FileRegistrationService(self.storage).register_file(
                    self.session, path, "image", mime_type="image/jpeg"
                )
                detections = []
                for i, detection in enumerate(result["detections"]):
                    candidates = copy.deepcopy(detection["identification"]["candidates"])
                    for candidate in candidates:
                        candidate["evidence"] = candidate.get("evidence", [])[:2]
                    detections.append(
                        {
                            "index": i,
                            "bbox": detection["bbox"],
                            "detection": detection["detection"],
                            "candidates": candidates,
                            "decision": "unresolved",
                            "status": "unresolved",
                            "links": [],
                        }
                    )
                self.session.add(
                    VisualVideoFrame(
                        scan_id=scan_id,
                        frame_index=index,
                        time=timestamp,
                        file_id=file.id,
                        width=result["width"],
                        height=result["height"],
                        detections=detections,
                    )
                )
                run = await self._run(scan_id)
                data = {
                    **run.raw_output,
                    "completed_frames": run.raw_output.get("completed_frames", 0) + 1,
                    "total_detections": run.raw_output.get("total_detections", 0) + len(detections),
                    "unresolved_count": run.raw_output.get("unresolved_count", 0) + len(detections),
                }
                run.raw_output = data
                await self.session.commit()
            run = await self._run(scan_id)
            run.status, run.finished_at, run.error = "succeeded", datetime.now(UTC), None
            await self.session.commit()
        return await self.get_scan(scan_id)

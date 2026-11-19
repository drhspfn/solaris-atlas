"""Tool-driven video/cutscene analysis agent with global atlas numbering and local workspace."""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import logging
import math
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError
from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from wuwa_story.agents.budget import reserve, settle
from wuwa_story.agents.capabilities import analysis_prompt, video_analysis_definitions
from wuwa_story.agents.cutscene_vision import (
    VISION_VERSION,
    VideoAnalysisResult,
    dynamic_sampling_plan,
    sample_times,
    validate_resample_request,
)
from wuwa_story.agents.providers import (
    Provider,
    ProviderFailure,
    ProviderRejected,
    ToolCall,
    token_usage,
)
from wuwa_story.agents.settings import AgentSettings
from wuwa_story.db.models.agents import AgentCall
from wuwa_story.db.models.core import DialogueLine, Speaker
from wuwa_story.db.models.graph import Edge, Node, NodeRevision, NodeType
from wuwa_story.db.models.i18n import Locale, LocalizationValue
from wuwa_story.db.models.lore import LoreChunk
from wuwa_story.db.models.ontology import RelationType
from wuwa_story.db.models.ops import GameRelease, ProcessingRun
from wuwa_story.db.models.storage import FileReference
from wuwa_story.db.repositories.search import lexical_search
from wuwa_story.search.lore import LoreSearchService
from wuwa_story.storage.service import FileRegistrationService

from wuwa_story_worker.cutscene_import import run_tool, video_duration
from wuwa_story_worker.video_atlas import write_atlas

logger = logging.getLogger(__name__)

MAX_AGENT_TURNS = 14
MAX_FINISH_CORRECTIONS = 2
MAX_REFERENCE_IMAGES = 6
MAX_SPATIAL_INSPECTIONS = 4


def _order_evidence_frames(arguments: dict[str, Any], workspace: VideoWorkspace, *, order_events: bool = False,
                           order_story_events: bool = False, normalize_intervals: bool = False,
                           draft: VideoAnalysisResult | None = None) -> dict[str, Any]:
    """Sort known frame lists by scene time; never invent or discard evidence."""
    result = copy.deepcopy(arguments)
    if not isinstance(result, dict):
        return result
    events = result.get("events")
    if order_events and isinstance(events, list) and all(
        isinstance(item, dict) and type(item.get("time")) in (int, float) and math.isfinite(item["time"])
        for item in events
    ):
        order = sorted(range(len(events)), key=lambda index: events[index]["time"])
        remap = {old: new for new, old in enumerate(order)}
        result["events"] = [events[index] for index in order]
        if isinstance(result.get("event_review"), list):
            for decision in result["event_review"]:
                if isinstance(decision, dict) and isinstance(decision.get("final_indices"), list):
                    decision["final_indices"] = [remap.get(index, index) if type(index) is int else index
                                                 for index in decision["final_indices"]]
    if normalize_intervals and draft is not None:
        for review_key, before, after_key in (
            ("event_review", draft.events, "events"),
            ("identity_review", draft.identity_tracks, "identity_tracks"),
        ):
            decisions = result.get(review_key)
            after = result.get(after_key)
            if not isinstance(decisions, list) or not isinstance(after, list) or not before:
                continue
            if review_key == "event_review":
                for d in decisions:
                    if not isinstance(d, dict) or d.get("status") != "retained":
                        continue
                    d_idx = d.get("draft_index")
                    f_indices = d.get("final_indices")
                    if (
                        type(d_idx) is int
                        and 0 <= d_idx < len(before)
                        and isinstance(f_indices, list)
                        and f_indices
                        and all(type(i) is int and 0 <= i < len(after) for i in f_indices)
                    ):
                        draft_obs = " ".join(before[d_idx].observation.split()).casefold()
                        if not any(
                            draft_obs in " ".join(str(after[i].get("observation", "")).split()).casefold()
                            for i in f_indices
                        ):
                            frames = [
                                frame
                                for i in f_indices
                                for frame in (after[i].get("evidence_frames") or [])
                                if type(frame) is int
                            ]
                            if not frames:
                                frames = [
                                    after[i]["global_frame"]
                                    for i in f_indices
                                    if type(after[i].get("global_frame")) is int
                                ]
                            if frames:
                                d["status"] = "revised"
                                if not str(d.get("reason") or "").strip():
                                    d["reason"] = "Refined observation wording while preserving cited visual evidence"
                                if not d.get("evidence_frames"):
                                    d["evidence_frames"] = frames
            elif review_key == "identity_review":
                for d in decisions:
                    if not isinstance(d, dict) or d.get("status") != "retained":
                        continue
                    d_idx = d.get("draft_index")
                    f_indices = d.get("final_indices")
                    if (
                        type(d_idx) is int
                        and 0 <= d_idx < len(before)
                        and isinstance(f_indices, list)
                        and f_indices
                        and all(type(i) is int and 0 <= i < len(after) for i in f_indices)
                    ):
                        draft_item = before[d_idx]
                        if any(
                            str(after[i].get("identity", "")).strip().casefold() != draft_item.identity.strip().casefold()
                            or str(after[i].get("appearance", "")).strip().casefold() != draft_item.appearance.strip().casefold()
                            or after[i].get("confidence") != draft_item.confidence
                            or after[i].get("basis") != draft_item.basis
                            for i in f_indices
                        ):
                            frames = [
                                frame
                                for i in f_indices
                                for frame in (after[i].get("evidence_frames") or [])
                                if type(frame) is int
                            ]
                            if frames:
                                d["status"] = "revised"
                                if not str(d.get("reason") or "").strip():
                                    d["reason"] = "Updated identity track attributes while preserving cited evidence"
                                if not d.get("evidence_frames"):
                                    d["evidence_frames"] = frames
            present = {d.get("draft_index") for d in decisions if isinstance(d, dict)}
            missing = [idx for idx in range(len(before)) if idx not in present]
            if (
                len(missing) == 1
                and missing[0] == len(before) - 1
                and len(decisions) == len(before) - 1
                and len(after) == len(before)
                and all(
                    isinstance(d, dict)
                    and d.get("draft_index") == idx
                    and d.get("final_indices") == [idx]
                    for idx, d in enumerate(decisions)
                )
            ):
                tail_idx = missing[0]
                draft_tail = before[tail_idx]
                final_tail = after[tail_idx]
                if isinstance(final_tail, dict):
                    if review_key == "event_review":
                        f_time = final_tail.get("time")
                        if type(f_time) in (int, float) and abs(float(f_time) - draft_tail.time) <= 3.0:
                            d_obs = " ".join(draft_tail.observation.split()).casefold()
                            f_obs = " ".join(str(final_tail.get("observation", "")).split()).casefold()
                            if d_obs and d_obs in f_obs:
                                decisions.append({"draft_index": tail_idx, "status": "retained", "final_indices": [tail_idx]})
                            else:
                                frames = list(final_tail.get("evidence_frames") or [])
                                if not frames and type(final_tail.get("global_frame")) is int:
                                    frames = [final_tail["global_frame"]]
                                decisions.append({
                                    "draft_index": tail_idx,
                                    "status": "revised",
                                    "final_indices": [tail_idx],
                                    "reason": "Retained final event with minor wording refinement",
                                    "evidence_frames": frames,
                                })
                continue
            if len(missing) == 1 and len(decisions) == len(before) - 1:
                miss_idx = missing[0]
                draft_item = before[miss_idx]
                claimed = {
                    f_idx
                    for d in decisions
                    if isinstance(d, dict) and isinstance(d.get("final_indices"), list)
                    for f_idx in d["final_indices"]
                    if type(f_idx) is int
                }
                for f_idx, final_item in enumerate(after):
                    if f_idx in claimed or not isinstance(final_item, dict):
                        continue
                    if review_key == "event_review":
                        d_obs = " ".join(draft_item.observation.split()).casefold()
                        f_obs = " ".join(str(final_item.get("observation", "")).split()).casefold()
                        f_time = final_item.get("time")
                        if type(f_time) in (int, float) and abs(float(f_time) - draft_item.time) <= 3.0 and d_obs and d_obs in f_obs:
                            decisions.append({"draft_index": miss_idx, "status": "retained", "final_indices": [f_idx]})
                            break
                    else:
                        d_id = draft_item.identity.strip().casefold()
                        f_id = str(final_item.get("identity", "")).strip().casefold()
                        if d_id and d_id == f_id:
                            decisions.append({"draft_index": miss_idx, "status": "retained", "final_indices": [f_idx]})
                            break
    story_events = result.get("story_events")
    if order_story_events and isinstance(story_events, list) and all(
        isinstance(item, dict) and type(item.get("time_start")) in (int, float)
        and math.isfinite(item["time_start"]) for item in story_events
    ):
        result["story_events"] = sorted(story_events, key=lambda item: item["time_start"])
    for collection in ("events", "story_events", "identity_tracks"):
        items = result.get(collection, [])
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            if normalize_intervals:
                kind_key = "basis" if collection == "identity_tracks" else "evidence_kind"
                if item.get(kind_key) == "contextual":
                    sources = item.get("context_sources")
                    if not isinstance(sources, list) or not any(str(s).strip() for s in sources):
                        item[kind_key] = "inferred"
            frames = item.get("evidence_frames")
            if isinstance(frames, list) and all(type(frame) is int and frame in workspace.frame_times for frame in frames):
                item["evidence_frames"] = sorted(frames, key=lambda frame: (workspace.frame_times[frame], frame))
                known_times = [workspace.frame_times[frame] for frame in item["evidence_frames"]]
                if normalize_intervals and known_times:
                    if collection == "events":
                        gf = item.get("global_frame")
                        if type(gf) is int and gf in workspace.frame_times:
                            known_times.append(workspace.frame_times[gf])
                        end = item.get("time_end")
                        if type(end) in (int, float) and math.isfinite(end):
                            item["time_end"] = min(float(workspace.duration), max(float(end), max(known_times)))
                    else:
                        start, end = item.get("time_start"), item.get("time_end")
                        if type(start) in (int, float) and math.isfinite(start):
                            item["time_start"] = max(0.0, min(float(start), min(known_times)))
                        if type(end) in (int, float) and math.isfinite(end):
                            item["time_end"] = min(float(workspace.duration), max(float(end), max(known_times)))
    return result


def _review_evidence_errors(draft: VideoAnalysisResult, final: VideoAnalysisResult, workspace: VideoWorkspace) -> list[str]:
    """Require an explicit, evidence-backed disposition for each provisional entry."""
    errors = []
    for name, before, after, decisions in (
        ("event", draft.events, final.events, final.event_review),
        ("identity", draft.identity_tracks, final.identity_tracks, final.identity_review),
    ):
        indices = [decision.draft_index for decision in decisions]
        expected = list(range(len(before)))
        if sorted(indices) != expected:
            missing = [idx for idx in expected if idx not in indices]
            extra = [idx for idx in indices if idx not in expected or indices.count(idx) > 1]
            errors.append(
                f"{name}_review must cover each draft index 0..{len(before) - 1} exactly once "
                f"(draft has {len(before)} items; missing draft_index={missing}, extra/duplicate={sorted(set(extra))})"
            )
            continue
        for decision in decisions:
            label = f"{name}_review {decision.draft_index}"
            if any(index < 0 or index >= len(after) for index in decision.final_indices):
                errors.append(f"{label}: invalid final index")
                continue
            if decision.status in ("retained", "revised") and not decision.final_indices:
                errors.append(f"{label}: retained or revised entries need final indices")
            if decision.status in ("disputed", "unresolved") and decision.final_indices:
                errors.append(f"{label}: disputed or unresolved entries must not claim final indices")
            if decision.status != "retained" and not decision.reason.strip():
                errors.append(f"{label}: explain changes or uncertainty")
            if decision.status in ("revised", "disputed"):
                item = before[decision.draft_index]
                start = item.time if name == "event" else item.time_start
                end = (item.time_end if item.time_end is not None else start) if name == "event" else item.time_end
                cited_frames = list(decision.evidence_frames)
                if not cited_frames and decision.status == "revised":
                    for index in decision.final_indices:
                        if 0 <= index < len(after):
                            target = after[index]
                            cited_frames.extend(target.evidence_frames)
                            if name == "event" and target.global_frame is not None:
                                cited_frames.append(target.global_frame)
                valid_frames = [frame for frame in cited_frames if frame in workspace.frame_times]
                if not valid_frames or len(valid_frames) != len(cited_frames) or not any(
                    start - 3 <= workspace.frame_times[frame] <= end + 3
                    for frame in valid_frames
                ):
                    errors.append(f"{label}: cite known nearby frames to revise or dispute")
            if name == "identity" and decision.status == "retained" and any(
                after[index].identity.strip().casefold() != before[decision.draft_index].identity.strip().casefold()
                or after[index].appearance.strip().casefold() != before[decision.draft_index].appearance.strip().casefold()
                or after[index].confidence != before[decision.draft_index].confidence
                or after[index].basis != before[decision.draft_index].basis
                or after[index].time_start > before[decision.draft_index].time_end + 3
                or after[index].time_end < before[decision.draft_index].time_start - 3
                for index in decision.final_indices
            ):
                errors.append(f"{label}: a changed identity requires revision and evidence")
            if name == "event" and decision.status == "retained" and decision.final_indices:
                item = before[decision.draft_index]
                end = item.time_end if item.time_end is not None else item.time
                if not any(after[index].time <= end + 3 and
                           (after[index].time_end if after[index].time_end is not None else after[index].time) >= item.time - 3
                           for index in decision.final_indices):
                    errors.append(f"{label}: retained observation must overlap the draft time")
                if not any(" ".join(item.observation.split()).casefold() in
                           " ".join(after[index].observation.split()).casefold()
                           for index in decision.final_indices):
                    errors.append(f"{label}: changed wording requires revision and evidence")
    return errors[:20]


async def _previous_scene_evidence(db: AsyncSession, run: ProcessingRun) -> dict[str, Any]:
    """Only reuse an explicitly matching, completed analysis as fallible prior evidence."""
    from wuwa_story.agents.cutscene_vision import visual_reference
    from wuwa_story.agents.scene_analysis_reader import current_scene_report

    metadata = run.metadata_json or {}
    plan, version = metadata.get("analysis_plan"), metadata.get("asset_version")
    if run.target_node_id is None or not version:
        return {"available": False}
    if plan:
        report = await current_scene_report(db, run.target_node_id, version, plan)
    else:
        report = await visual_reference(db, run.target_node_id, version)
    if report is None:
        return {"available": False}
    previous = report.metadata_json or {}
    previous_run_id = previous.get("run_id")
    if (type(previous_run_id) is not int or previous_run_id == run.id or
            previous.get("source_sha256") != metadata.get("source_sha256") or
            previous.get("source_file_id") != metadata.get("file_id")):
        return {"available": False}
    previous_run = await db.get(ProcessingRun, previous_run_id)
    if (previous_run is None or previous_run.status != "completed"
            or (previous_run.raw_output or {}).get("report_reference_id") != report.id
            or (previous_run.metadata_json or {}).get("text_context") != metadata.get("text_context")):
        return {"available": False}
    events = previous.get("events") or []
    tracks = previous.get("identity_tracks") or []
    def spread(items, limit):
        indices = range(len(items)) if len(items) <= limit else (
            index * (len(items) - 1) // (limit - 1) for index in range(limit))
        return [{"prior_index": index, **items[index]} for index in indices]

    return {"available": True, "report_id": report.id, "run_id": previous_run_id,
            "meaning": "Earlier AI observations of the same selected video and source context; provisional, not verified facts.",
            "events": spread(events, 30), "identity_tracks": spread(tracks, 20),
            "events_omitted": max(0, len(events) - 30), "identity_tracks_omitted": max(0, len(tracks) - 20)}


@dataclass
class AtlasEntry:
    atlas_index: int
    path: Path
    image_bytes: bytes
    layout: dict[str, Any]
    sample_times: list[float]
    frame_number_start: int
    frame_number_end: int
    reason: str = "overview"


@dataclass
class VideoWorkspace:
    """Holds source video, extracted frames, and globally numbered atlases for one run."""

    root: Path
    ffmpeg: Path
    movie: Path
    duration: float
    settings: AgentSettings
    limits: dict[str, float | int]
    atlases: list[AtlasEntry] = field(default_factory=list)
    frame_paths: dict[int, Path] = field(default_factory=dict)
    frame_times: dict[int, float] = field(default_factory=dict)
    next_atlas_index: int = 1
    next_frame_index: int = 1

    async def inspect_region(self, global_frame: int, bbox: list[float]) -> tuple[dict[str, Any], bytes]:
        if (type(global_frame) is not int or global_frame not in self.frame_times or not isinstance(bbox, list)
                or len(bbox) != 4 or any(type(value) not in (int, float) or not math.isfinite(value) for value in bbox)):
            raise ValueError("Choose a known frame and four finite normalized coordinates")
        left, top, right, bottom = bbox
        if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1 and right - left >= 0.02 and bottom - top >= 0.02):
            raise ValueError("Crop must be inside the frame and at least 2% of its width and height")
        timestamp = self.frame_times[global_frame]
        movie, local_time, duration, source = self.source_at(timestamp)
        safe_ts = (source["decode_source_time"] if source else
                   min(max(0.0, float(local_time)), max(0.0, duration - 0.1)))
        region_key = hashlib.sha256(json.dumps([global_frame, bbox]).encode()).hexdigest()[:16]
        path = self.root / f"region_{region_key}.jpg"
        crop = (f"crop=trunc(iw*{right - left:.8f}/2)*2:trunc(ih*{bottom - top:.8f}/2)*2:"
                f"trunc(iw*{left:.8f}/2)*2:trunc(ih*{top:.8f}/2)*2,scale=1024:-2:flags=lanczos,format=yuvj420p")
        await run_tool([str(self.ffmpeg), "-v", "error", "-y", "-ss", str(safe_ts), "-i", str(movie),
                        "-frames:v", "1", "-vf", crop, "-threads", "1", "-q:v", "3", str(path)], 60)
        data = path.read_bytes()
        if not data or len(data) > 2_000_000:
            raise ValueError("Region image is empty or exceeds the size limit")
        return {"global_frame": global_frame, "scene_time": timestamp, "bbox": bbox,
                "sha256": hashlib.sha256(data).hexdigest(), "mime_type": "image/jpeg"}, data

    def source_at(self, timestamp: float) -> tuple[Path, float, float, dict]:
        return self.movie, timestamp, self.duration, {}

    def overview_times(self) -> list[float]:
        return sample_times(self.duration, float(self.limits["overview_interval"]),
                            int(self.limits["max_overview_frames"]))

    async def extract_frame(self, timestamp: float, global_index: int) -> bytes:
        out = self.root / f"frame_{global_index:04d}.jpg"
        out.unlink(missing_ok=True)
        movie, local_time, duration, source = self.source_at(timestamp)
        safe_ts = (source["decode_source_time"] if source else
                   min(max(0.0, float(local_time)), max(0.0, duration - 0.1))
                   if duration > 0 else max(0.0, float(local_time)))
        await run_tool(
            [
                str(self.ffmpeg),
                "-v",
                "error",
                "-y",
                "-ss",
                str(safe_ts),
                "-i",
                str(movie),
                "-frames:v",
                "1",
                "-vf",
                "scale=640:-2,format=yuvj420p",
                "-threads",
                "1",
                "-q:v",
                "4",
                str(out),
            ],
            60,
        )
        data = out.read_bytes()
        if len(data) > 1_000_000:
            raise ValueError("Frame exceeds bounded JPEG size")
        self.frame_paths[global_index] = out
        self.frame_times[global_index] = timestamp
        return data

    async def build_atlases(self, times: list[float], *, reason: str = "overview") -> list[AtlasEntry]:
        created: list[AtlasEntry] = []
        per_atlas = max(1, min(24, self.settings.vision_atlas_frames))
        for offset in range(0, len(times), per_atlas):
            selected = times[offset : offset + per_atlas]
            start_frame = self.next_frame_index
            frames: list[bytes] = []
            for t in selected:
                frames.append(await self.extract_frame(t, self.next_frame_index))
                self.next_frame_index += 1
            atlas_idx = self.next_atlas_index
            self.next_atlas_index += 1
            atlas_path = self.root / f"atlas_{atlas_idx:03d}.jpg"
            layout = await asyncio.to_thread(
                write_atlas,
                frames,
                selected,
                atlas_path,
                tile_width=self.settings.vision_atlas_tile_width,
                quality=self.settings.vision_atlas_quality,
                atlas_index=atlas_idx,
                frame_number_start=start_frame,
            )
            entry = AtlasEntry(
                atlas_index=atlas_idx,
                path=atlas_path,
                image_bytes=atlas_path.read_bytes(),
                layout=layout,
                sample_times=list(selected),
                frame_number_start=start_frame,
                frame_number_end=start_frame + len(selected) - 1,
                reason=reason,
            )
            self.atlases.append(entry)
            created.append(entry)
        return created

    def manifest(self, entries: list[AtlasEntry] | None = None) -> list[dict[str, Any]]:
        target = self.atlases if entries is None else entries
        return [
            {
                "atlas_index": entry.atlas_index,
                "reason": entry.reason,
                "frame_number_start": entry.frame_number_start,
                "frame_number_end": entry.frame_number_end,
                "tiles": [
                    {
                        "global_frame": tile["global_frame_index"],
                        "label": f"A{entry.atlas_index:02d} #{tile['global_frame_index']:03d}",
                        "time": tile["time"],
                    }
                    for tile in entry.layout["tiles"]
                ],
            }
            for entry in target
        ]


def _multimodal_user_message(provider: Provider, text: str, images: list[bytes]) -> dict[str, Any]:
    encoded = [base64.b64encode(img).decode("ascii") for img in images]
    if provider.settings.provider == "gemini":
        return {
            "role": "user",
            "parts": [
                {"text": text},
                *[{"inlineData": {"mimeType": "image/jpeg", "data": val}} for val in encoded],
            ],
        }
    if provider.settings.provider == "chat":
        return {
            "role": "user",
            "content": [
                {"type": "text", "text": text},
                *[
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{val}", "detail": "high"}}
                    for val in encoded
                ],
            ],
        }
    return {
        "role": "user",
        "content": [
            {"type": "input_text", "text": text},
            *[
                {"type": "input_image", "image_url": f"data:image/jpeg;base64,{val}", "detail": "high"}
                for val in encoded
            ],
        ],
    }


async def _identify_atlas_characters(
    db: AsyncSession | None,
    workspace: VideoWorkspace,
    atlas_indices: list[int] | None = None,
    global_frame_indices: list[int] | None = None,
) -> dict[str, Any]:
    """Use the shared identification path and preserve its confidence and evidence."""
    from wuwa_story_worker.video_identity import identify_atlas_characters

    return await identify_atlas_characters(db, workspace, atlas_indices, global_frame_indices)


async def _execute_video_tool(
    db: AsyncSession | None,
    workspace: VideoWorkspace,
    call: ToolCall,
) -> tuple[dict[str, Any], list[AtlasEntry]]:
    args = call.arguments
    if call.name == "resample_video_window":
        check = validate_resample_request(
            workspace.duration,
            float(args["start_seconds"]),
            float(args["end_seconds"]),
            float(args["interval_seconds"]),
            soft_max_frames=int(workspace.limits["soft_zoom_frames"]),
            hard_max_frames=int(workspace.limits["hard_zoom_frames"]),
            bypass_limit=bool(args.get("bypass_limit", False)),
            bypass_justification=args.get("bypass_justification"),
        )
        if not check["ok"]:
            return check, []
        try:
            created = await workspace.build_atlases(
                list(check["times"]),  # type: ignore[arg-type]
                reason=str(args.get("reason") or "zoom_window"),
            )
        except ValueError as error:
            return {"ok": False, "error": str(error)}, []
        return (
            {
                "ok": True,
                "bypassed": check["bypassed"],
                "reason": args.get("reason"),
                "new_atlases": workspace.manifest(created),
            },
            created,
        )

    if call.name == "identify_atlas_characters":
        result = await _identify_atlas_characters(
            db,
            workspace,
            atlas_indices=args.get("atlas_indices"),
            global_frame_indices=args.get("global_frame_indices"),
        )
        return result, []

    if db is None:
        return {"error": "Archive database is not connected in local simulation mode"}, []

    latest_release = await db.scalar(
        select(GameRelease.id)
        .where(select(NodeRevision.id).where(NodeRevision.release_id == GameRelease.id).exists())
        .order_by(GameRelease.sequence.desc())
        .limit(1)
    )
    if call.name == "search_entities":
        query = str(args.get("query") or "").strip()
        category = args.get("category")
        limit = max(1, min(int(args.get("limit") or 10), 25))
        if not query or latest_release is None:
            return {"results": []}, []
        en_id = await db.scalar(select(Locale.id).where(Locale.code == "en"))
        rows = await lexical_search(
            db,
            query,
            locale_id=en_id or 1,
            categories=[str(category)] if category else None,
            limit=limit,
        )
        return {"results": rows}, []

    if call.name == "search_dialogue":
        query = str(args.get("query") or "").strip()
        limit = max(1, min(int(args.get("limit") or 10), 25))
        if not query or latest_release is None:
            return {"results": []}, []
        en_id = await db.scalar(select(Locale.id).where(Locale.code == "en"))
        line_val = aliased(LocalizationValue, name="line_val")
        speaker_val = aliased(LocalizationValue, name="speaker_val")
        stmt = (
            select(
                DialogueLine.node_id,
                speaker_val.content.label("speaker"),
                line_val.content.label("text"),
            )
            .outerjoin(Speaker, Speaker.node_id == DialogueLine.speaker_node_id)
            .outerjoin(
                speaker_val,
                (speaker_val.key_id == Speaker.name_key_id)
                & (speaker_val.locale_id == (en_id or 1))
                & (speaker_val.release_id == latest_release)
                & (speaker_val.status == "resolved_nonempty"),
            )
            .join(
                line_val,
                (line_val.key_id == DialogueLine.localization_key_id)
                & (line_val.locale_id == (en_id or 1))
                & (line_val.release_id == latest_release)
                & (line_val.status == "resolved_nonempty"),
            )
            .where(line_val.content.icontains(query, autoescape=True))
            .limit(limit)
        )
        rows = [dict(r) for r in (await db.execute(stmt)).mappings()]
        return {"results": rows}, []

    if call.name == "search_lore":
        query = str(args.get("query") or "").strip()
        limit = max(1, min(int(args.get("limit") or 8), 20))
        if not query:
            return {"results": []}, []
        service = LoreSearchService(db)
        results = await service.hybrid_search(query, None, limit=limit)
        return {
            "results": [
                {
                    "source_type": r.source_type,
                    "source_id": r.source_id,
                    "quest_id": r.quest_id,
                    "chunk_type": r.chunk_type,
                    "content": r.content,
                }
                for r in results
            ]
        }, []

    if call.name == "read_node":
        node_id = int(args["node_id"])
        node = await db.get(Node, node_id)
        if node is None:
            return {"error": f"Node {node_id} not found"}, []
        nt = await db.get(NodeType, node.type_id)
        return {
            "id": node.id,
            "canonical_key": node.canonical_key,
            "slug": node.slug,
            "type": nt.key if nt else None,
        }, []

    if call.name == "graph_neighbors":
        node_id = int(args["node_id"])
        limit = max(1, min(int(args.get("limit") or 20), 50))
        rows = (
            await db.execute(
                select(Edge.from_node_id, Edge.to_node_id, RelationType.key, Edge.layer)
                .join(RelationType, RelationType.id == Edge.relation_type_id)
                .where((Edge.from_node_id == node_id) | (Edge.to_node_id == node_id))
                .limit(limit)
            )
        ).all()
        return {
            "edges": [
                {"from_node_id": f, "to_node_id": t, "relation": r, "layer": layer}
                for f, t, r, layer in rows
            ]
        }, []

    return {"error": f"Unknown tool: {call.name}"}, []


async def _publish_cutscene_knowledge(
    db: AsyncSession,
    run: ProcessingRun,
    result: VideoAnalysisResult,
) -> None:
    """Persist cutscene lore chunks and agent-layer edges so quest analysis finds them."""
    if run.target_node_id is None:
        return
    asset_node_id = run.target_node_id
    asset_version = run.metadata_json.get("asset_version")

    await db.execute(
        delete(LoreChunk).where(
            LoreChunk.source_type == "cutscene_vision",
            LoreChunk.source_id == str(asset_node_id),
        )
    )
    participants = sorted(
        {
            *(link.target_name for link in result.entity_links if link.kind == "character"),
            *(actor for ev in result.events for actor in ev.actors),
            *(actor for sev in result.story_events for actor in sev.participants),
        }
    )
    lines = [
        f"Cutscene: {result.title or f'Asset {asset_node_id}'}",
        f"Summary: {result.summary}",
    ]
    if result.lore_topics:
        lines.append("Lore topics: " + ", ".join(result.lore_topics))
    if result.story_events:
        lines.append(
            "Events: "
            + " | ".join(
                f"[{sev.time_start:.1f}s-{sev.time_end:.1f}s] {sev.title}: {sev.summary}"
                for sev in result.story_events
            )
        )
    if result.entity_links:
        lines.append(
            "Linked entities: "
            + ", ".join(f"{link.target_name} ({link.kind}, {link.relation})" for link in result.entity_links)
        )
    content = "\n".join(lines)
    db.add(
        LoreChunk(
            source_type="cutscene_vision",
            source_id=str(asset_node_id),
            quest_id=None,
            game_version=str(asset_version)[:20] if asset_version else None,
            characters=participants,
            chunk_type="cutscene_lore",
            language="en",
            content=content,
            search_vector=func.to_tsvector("simple", content),
        )
    )

    relation_map = {
        "appears_in": "INVOLVES",
        "uses_item": "USES_ITEM",
        "located_at": "LOCATED_AT",
        "related_to_event": "RELATED_TO",
        "mentions": "MENTIONS",
    }
    seen_edges: set[tuple[int, int]] = set()
    for link in result.entity_links:
        if not link.target_node_id or link.target_node_id == asset_node_id:
            continue
        target_exists = await db.scalar(select(Node.id).where(Node.id == link.target_node_id))
        if target_exists is None:
            continue
        rel_key = relation_map.get(link.relation, "MENTIONS")
        rel_id = await db.scalar(select(RelationType.id).where(RelationType.key == rel_key))
        if rel_id is None:
            # Seeded relation IDs are explicit, so their sequence may lag behind MAX(id).
            # Serialize allocation with other ontology writers before selecting the next ID.
            await db.execute(text("LOCK TABLE ontology.relation_type IN SHARE ROW EXCLUSIVE MODE"))
            await db.execute(
                insert(RelationType)
                .values(id=select(func.coalesce(func.max(RelationType.id), 0) + 1).scalar_subquery(),
                        key=rel_key, label=rel_key.replace("_", " ").title(),
                        description=f"Generated video-analysis relation {rel_key}")
                .on_conflict_do_nothing(index_elements=[RelationType.key])
            )
            rel_id = await db.scalar(select(RelationType.id).where(RelationType.key == rel_key))
        if rel_id is None or (link.target_node_id, rel_id) in seen_edges:
            continue
        seen_edges.add((link.target_node_id, rel_id))
        existing_edge = await db.scalar(
            select(Edge.id).where(
                Edge.from_node_id == asset_node_id,
                Edge.to_node_id == link.target_node_id,
                Edge.relation_type_id == rel_id,
                Edge.layer == "semantic",
            )
        )
        if existing_edge is None:
            db.add(
                Edge(
                    from_node_id=asset_node_id,
                    to_node_id=link.target_node_id,
                    relation_type_id=rel_id,
                    layer="semantic",
                    basis="inference",
                    confidence=0.85 if link.confidence == "observed" else 0.7,
                    processor_run_id=run.id,
                    metadata_json={
                        "run_id": run.id,
                        "kind": link.kind,
                        "evidence": link.evidence,
                    },
                )
            )


def _estimate_input_tokens(payload: Any, atlases: list[AtlasEntry], *, extra_images: int = 0) -> int:
    """Estimate text tokens without counting raw base64 image bytes, plus per-atlas image tokens."""
    def _strip_images(obj: Any) -> Any:
        if isinstance(obj, str):
            if obj.startswith("data:image/"):
                return "<image>"
            return obj
        if isinstance(obj, list):
            return [_strip_images(item) for item in obj]
        if isinstance(obj, dict):
            if obj.get("type") == "image" and "source" in obj:
                return {"type": "image", "source": "<image>"}
            return {k: _strip_images(v) for k, v in obj.items()}
        return obj

    text_len = len(json.dumps(_strip_images(payload), ensure_ascii=False, default=str))
    image_tokens = sum(
        max(
            2048,
            math.ceil(int(entry.layout.get("width", 1280)) / 16)
            * math.ceil(int(entry.layout.get("height", 720)) / 16)
            * 2,
        )
        for entry in atlases
    )
    # Reference and region images are bounded to 1024x1024 pixels.
    return max(4096, text_len // 4 + image_tokens + extra_images * 8192 + 1024)


def _temporal_evidence_errors(result: VideoAnalysisResult, workspace: VideoWorkspace,
                              context: dict[str, Any] | None = None) -> list[str]:
    errors = []

    def crosses_gap(start, end):
        if not hasattr(workspace, "sources"):
            return False
        cursor = start
        for source in workspace.sources:
            segment = source.segment
            if segment["scene_end"] <= cursor:
                continue
            if segment["scene_start"] > cursor:
                break
            cursor = max(cursor, segment["scene_end"])
            if cursor >= end:
                break
        return cursor < end

    def check_span(label, start, end, frames, kind, sources):
        if not 0 <= start <= end <= workspace.duration:
            errors.append(f"{label}: invalid scene interval")
        times = [workspace.frame_times.get(frame) for frame in frames]
        if not frames or any(time is None for time in times):
            errors.append(f"{label}: cite known evidence_frames")
        elif any(time < start - 0.1 or time > end + 0.1 for time in times):
            errors.append(
                f"{label}: evidence frames must be inside the interval "
                f"[{round(start, 3)}, {round(end, 3)}] (cited frame times: {[round(t, 3) for t in times]})"
            )
        elif times != sorted(times):
            errors.append(f"{label}: evidence frames must be chronological")
        if kind is None:
            errors.append(f"{label}: specify evidence basis")
        if kind == "contextual" and not any(source.strip() for source in sources):
            errors.append(f"{label}: cite contextual source identifiers")

    for index, event in enumerate(result.events):
        if event.time > workspace.duration or (event.time_end is not None and event.time_end > workspace.duration):
            errors.append(f"event {index}: interval exceeds scene duration")
        if event.global_frame is None:
            errors.append(f"event {index}: cite a global_frame")
        elif event.global_frame not in workspace.frame_times:
            errors.append(f"event {index}: unknown global_frame {event.global_frame}")
        elif abs(workspace.frame_times[event.global_frame] - event.time) > 0.1:
            errors.append(
                f"event {index}: time does not match global_frame {event.global_frame} "
                f"(event.time={round(event.time, 3)}, frame_time={round(workspace.frame_times[event.global_frame], 3)})"
            )
        if any(frame not in workspace.frame_times for frame in event.evidence_frames):
            errors.append(f"event {index}: evidence_frames contains an unknown frame")
        elif event.evidence_frames:
            times = [workspace.frame_times[frame] for frame in event.evidence_frames]
            if times != sorted(times):
                errors.append(f"event {index}: evidence frames must be chronological")
            if event.time_end is not None and any(time < event.time - 0.1 or time > event.time_end + 0.1 for time in times):
                errors.append(
                    f"event {index}: evidence frames must be inside the interval "
                    f"[{round(event.time, 3)}, {round(event.time_end, 3)}] (cited frame times: {[round(t, 3) for t in times]})"
                )
        if event.time_end is not None and event.evidence_kind == "observed" and crosses_gap(event.time, event.time_end):
            errors.append(f"event {index}: observed action crosses a scene gap")
        if event.evidence_kind is None:
            errors.append(f"event {index}: specify observed, inferred or contextual evidence_kind")
        if event.evidence_kind == "contextual" and not any(source.strip() for source in event.context_sources):
            errors.append(f"event {index}: cite contextual source identifiers")
        if index > 0:
            prev = result.events[index - 1]
            prev_obs = " ".join(prev.observation.split()).casefold()
            curr_obs = " ".join(event.observation.split()).casefold()
            if prev_obs and prev_obs == curr_obs and (
                event.global_frame == prev.global_frame or abs(event.time - prev.time) <= 1.0
            ):
                errors.append(f"event {index}: duplicate adjacent observation; merge into one event with evidence_frames")
    if [event.time_start for event in result.story_events] != sorted(event.time_start for event in result.story_events):
        errors.append("story events must be chronological")
    for index, event in enumerate(result.story_events):
        label = f"story event {index}"
        check_span(label, event.time_start, event.time_end, event.evidence_frames,
                   event.evidence_kind, event.context_sources)
        if event.time_end - event.time_start > 1 and len(set(event.evidence_frames)) < 2:
            errors.append(f"{label}: cite at least two frames for a multi-second action")
        if event.evidence_kind == "observed" and crosses_gap(event.time_start, event.time_end):
            errors.append(f"{label}: observed action crosses a scene gap")
    for index, track in enumerate(result.identity_tracks):
        check_span(f"identity track {index}", track.time_start, track.time_end,
                   track.evidence_frames, track.basis, track.context_sources)
    if workspace.duration >= 60.0 and result.events:
        tail_threshold = workspace.duration - 22.0
        tail_frames = [frame for frame, f_time in workspace.frame_times.items() if f_time >= tail_threshold]
        if tail_frames and max(event.time for event in result.events) < tail_threshold:
            errors.append(
                f"timeline ending coverage: last event starts at {round(max(event.time for event in result.events), 2)}s "
                f"in a {round(workspace.duration, 2)}s scene; add separate event(s) for the final visual beats before fade-out"
            )
    dialogue_lines = (context or {}).get("dialogue_lines") if isinstance(context, dict) else None
    if isinstance(dialogue_lines, list) and any(
        isinstance(line, dict) and "{playername}" in str(line.get("speaker", "")).casefold()
        for line in dialogue_lines
    ):
        has_rover_track = any("rover" in track.identity.casefold() for track in result.identity_tracks)
        if not has_rover_track:
            errors.append(
                "identity_tracks: dialogue_lines include '{PlayerName}' (Rover); identify Rover in identity_tracks "
                "and events.actors for shots where the protagonist appears (basis='contextual' or 'inferred')"
            )
    return errors[:20]


TEMPORAL_REVIEW_INSTRUCTION = (
    "Draft received. Perform a separate consistency review before submitting the final result: "
    "check identities and simultaneous appearances, short events between samples, actions and "
    "physical contact, emotional shifts, chronology, gaps, ending coverage before final fade-out, "
    "and unsupported interpretations. In source_text_context.dialogue_lines, speaker '{PlayerName}' "
    "is Rover: ensure Rover is explicitly identified in identity_tracks and events.actors across all "
    "shots where the dark-haired protagonist appears (such as arriving, clasping hands, embracing, "
    "or supporting a companion at sunset), and never misattribute Rover's back/side shots to another "
    "dark-clad character like Lily (Wartime). Do NOT compress distinct visual beats of a climax or "
    "ending montage into one omnibus event: split multi-cut sequences so the final visual beats in "
    "the last 20 seconds before fade-out (e.g., cosmic doorway/portal and characters walking/supporting "
    "each other in the flower field at sunset) have their own chronological events. Persist confirmed "
    "character identities across all shots of the same appearance, and preserve specific observable "
    "visual nouns (such as 'white cat' or 'feline creature' rather than generalizing to 'animal-like "
    "figure'). Merge redundant 1-second duplicate angles of the same continuing action into one event "
    "with multiple evidence_frames, while keeping distinct sequential micro-actions and interactions "
    "separate. Use the resample and identity tools for consequential uncertainty within the existing "
    "budget. Revise earlier observations only when supported. Cite actual frame numbers and matching "
    "scene timestamps, set evidence_kind for every event, and call finish_video_analysis again."
)

EVIDENCE_REVIEW_INSTRUCTION = (
    "The draft is provisional evidence, not confirmed identity. Review every draft event and identity track "
    "by its zero-based index. In event_review and identity_review return exactly one decision for each: "
    "retained, revised, disputed, or unresolved. Retained/revised decisions need final_indices; disputed/"
    "unresolved have none. For revised/disputed cite nearby numbered evidence_frames (you may also include "
    "confirming frames from elsewhere in the scene) and explain why; "
    "for unresolved explain the uncertainty. A retained identity must keep the same label; revise it with "
    "evidence if its name, appearance, confidence, or basis changes. Retained observations must keep their "
    "original wording; mark paraphrases or merged duplicate shots as revised with nearby evidence. Keep "
    "distinct simultaneous appearances separate, and never dilute specific visual details into generic "
    "placeholders during revision. Do not silently drop an earlier observation, guess a name, or treat a "
    "draft claim as confirmed. Include the review decisions in your final finish_video_analysis result."
)


async def run_video_analysis_agent(
    db: AsyncSession,
    run: ProcessingRun,
    settings: AgentSettings,
    workspace: VideoWorkspace,
    storage: Any,
    context: dict[str, Any] | None,
) -> VideoAnalysisResult:
    """Execute the multi-turn video analysis agent and persist outputs."""
    overview_times = workspace.overview_times()
    overview_atlases = await workspace.build_atlases(overview_times, reason="overview")

    tools = video_analysis_definitions()
    system_prompt = analysis_prompt("video_analysis")
    intro = {
        "duration_seconds": workspace.duration,
        "limits": workspace.limits,
        "atlases": workspace.manifest(overview_atlases),
        "source_text_context": context or {},
    }
    if run.metadata_json.get("review_evidence_version") == 1:
        previous = (run.raw_output or {}).get("previous_scene_evidence")
        if previous is None:
            previous = await _previous_scene_evidence(db, run)
            run.raw_output = {**(run.raw_output or {}), "previous_scene_evidence": previous}
            await db.commit()
        intro["previous_scene_evidence"] = previous
        if previous.get("available"):
            intro["previous_evidence_instruction"] = (
                "Compare prior observations and identity tracks with current frames and context; "
                "reinspect consequential differences. Prior AI output is not proof and may be incomplete or wrong."
            )
    if run.metadata_json.get("analysis_plan"):
        intro["analysis_plan"] = run.metadata_json["analysis_plan"]
        intro["timeline_rules"] = (
            "Analyze ALL selected segments as ONE ordered scene, with ONE shared screenshot pool. "
            "All frame labels, tool windows and result times use scene seconds. "
            "Source times and offsets are in the manifest. Gaps contain no video evidence. "
            "The selection already resolves mutually exclusive alternatives; do not concatenate them."
        )
    history: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
    ]

    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
        provider = Provider(settings, client)
        history.append(
            _multimodal_user_message(
                provider,
                "Analyze this video using the globally numbered atlases and tools.\n"
                + json.dumps(intro, ensure_ascii=False),
                [entry.image_bytes for entry in overview_atlases],
            )
        )

        final_result: VideoAnalysisResult | None = None
        review_required = run.metadata_json.get("temporal_review_version") == 1
        evidence_review_required = run.metadata_json.get("review_evidence_version") == 1
        if evidence_review_required and not review_required:
            raise ValueError("Evidence review requires temporal review")
        review_started = False
        review_started_at = -1
        review_draft: VideoAnalysisResult | None = None
        finish_corrections = 0
        last_rejected_finish_digest: str | None = None
        reference_images_used = 0
        spatial_inspections_used = 0
        usage_steps = []

        async def record_finish(key: str, result: dict[str, Any]) -> None:
            diagnostics = dict((run.raw_output or {}).get("finish_diagnostics", {}))
            diagnostics[key] = result
            run.raw_output = {**(run.raw_output or {}), "finish_diagnostics": diagnostics}
            await db.commit()

        def remember_draft(candidate: VideoAnalysisResult) -> None:
            nonlocal review_draft
            review_draft = candidate
            if evidence_review_required:
                saved = candidate.model_dump()
                previous = (run.raw_output or {}).get("review_draft")
                if previous is not None and previous != saved:
                    raise ProviderFailure("video_agent_review_draft_changed_on_replay")
                run.raw_output = {**(run.raw_output or {}), "review_draft": saved}

        def review_errors(candidate: VideoAnalysisResult) -> list[str]:
            errors = _temporal_evidence_errors(candidate, workspace, context) if review_required else []
            if evidence_review_required:
                errors.extend(_review_evidence_errors(review_draft, candidate, workspace) if review_draft is not None
                              else ["A saved draft is required before final review"])
            return errors[:20]

        accounted = set((run.raw_output or {}).get(
            "accounted_steps", range(int((run.raw_output or {}).get("step", 0)))))
        for turn_index in range(MAX_AGENT_TURNS):
            step = turn_index
            route, payload = provider.request(history, tools)
            input_bound = _estimate_input_tokens(payload, workspace.atlases,
                                                  extra_images=reference_images_used + spatial_inspections_used)
            call = await db.scalar(
                select(AgentCall).where(AgentCall.run_id == run.id, AgentCall.step == step)
            )
            if call is not None:
                if call.status != "completed" or not call.response:
                    raise ValueError("Uncertain prior call; automatic retry prohibited")
                raw = call.response
                if raw.get("_rejected"):
                    raise ProviderFailure("recorded_provider_rejection_requires_new_run")
                source_tokens, output_tokens = token_usage(raw, settings.provider)
            else:
                call = await reserve(
                    db, settings, run_id=run.id, step=step, input_bound=input_bound,
                    output_bound=settings.max_output_tokens, kind="cutscene_visual")
                await db.commit()
                try:
                    raw = await provider.post(route, payload)
                except ProviderRejected as error:
                    await settle(db, call.id, settings, 0, 0, {"_rejected": error.diagnostic()})
                    await db.commit()
                    raise
                except ProviderFailure:
                    await db.commit()
                    raise
                source_tokens, output_tokens = token_usage(raw, settings.provider)
                within = await settle(db, call.id, settings, source_tokens, output_tokens, raw)
                # Commit recorded usage even when the response exceeds the reservation.
                await db.commit()
                if within is False:
                    raise ValueError("Visual usage exceeded reservation")
            usage = raw.get("usage") or {}
            input_details = usage.get("input_tokens_details") or {}
            usage_steps.append({"step": step, "input_tokens": source_tokens, "output_tokens": output_tokens,
                                "cached_input_tokens": input_details.get("cached_tokens")})
            if hasattr(db, "flush"):
                await db.flush()
            if hasattr(db, "refresh"):
                await db.refresh(call)
            if step not in accounted:
                run.tokens_input = (run.tokens_input or 0) + source_tokens
                run.tokens_output = (run.tokens_output or 0) + output_tokens
                run.cost = float(getattr(run, "cost", 0) or 0) + float(getattr(call, "cost_usd", 0) or 0)
                accounted.add(step)
            run.raw_output = {
                **(run.raw_output or {}),
                "mode": "video_agent",
                "step": step + 1,
                "atlases": len(workspace.atlases),
                "accounted_steps": sorted(accounted),
            }
            await db.commit()

            if provider.output_limited(raw):
                raise ProviderFailure("video_agent_output_limit")
            turn = provider.parse(raw)
            history.extend(turn.items)
            if not turn.calls:
                if turn.text:
                    try:
                        candidate = VideoAnalysisResult.model_validate(
                            _order_evidence_frames(json.loads(turn.text), workspace, order_events=evidence_review_required,
                                                   order_story_events=evidence_review_required and review_started,
                                                   normalize_intervals=review_started, draft=review_draft))
                    except (ValidationError, ValueError, TypeError) as exc:
                        if call is None:
                            finish_corrections += 1
                        outcome = {"error": "invalid_finish_text", "details": str(exc)[:2000]}
                        await record_finish(f"{step}:text", outcome)
                        if call is None and finish_corrections > MAX_FINISH_CORRECTIONS:
                            raise ProviderFailure("video_agent_finish_correction_limit") from exc
                        history.append(_multimodal_user_message(provider, json.dumps(outcome), []))
                        continue
                    if review_required and not review_started:
                        review_started = True
                        review_started_at = step
                        finish_corrections = 0
                        remember_draft(candidate)
                        await record_finish(f"{step}:text", {"status": "draft"})
                        history.append(_multimodal_user_message(provider, TEMPORAL_REVIEW_INSTRUCTION +
                            (" " + EVIDENCE_REVIEW_INSTRUCTION if evidence_review_required else ""), []))
                        continue
                    errors = review_errors(candidate)
                    if errors:
                        if call is None:
                            finish_corrections += 1
                        await record_finish(f"{step}:text", {"error": "invalid_temporal_evidence", "details": errors})
                        if call is None and finish_corrections > MAX_FINISH_CORRECTIONS:
                            raise ProviderFailure("video_agent_finish_correction_limit")
                        history.append(_multimodal_user_message(provider, "Correct the final evidence: " + "; ".join(errors), []))
                        continue
                    await record_finish(f"{step}:text", {"status": "accepted"})
                    final_result = candidate
                    break
                raise ProviderFailure("video_agent_stopped_without_result")

            new_images: list[tuple[AtlasEntry, dict[str, Any]]] = []
            reference_images: list[bytes] = []
            for tool_index, tc in enumerate(turn.calls):
                if tc.name == "finish_video_analysis":
                    finish_key = f"{step}:{tool_index}"
                    previous_finish = (run.raw_output or {}).get("finish_diagnostics", {}).get(finish_key)
                    if call is not None and step in accounted and previous_finish and previous_finish.get("error"):
                        history.append(provider.tool_result(tc, previous_finish))
                        continue
                    try:
                        candidate = VideoAnalysisResult.model_validate(
                            _order_evidence_frames(tc.arguments, workspace, order_events=evidence_review_required,
                                                   order_story_events=evidence_review_required and review_started,
                                                   normalize_intervals=review_started, draft=review_draft))
                    except ValidationError as exc:
                        finish_corrections += 1
                        digest = hashlib.sha256(
                            json.dumps(tc.arguments, sort_keys=True, default=str).encode("utf-8")
                        ).hexdigest()
                        duplicate = digest == last_rejected_finish_digest
                        last_rejected_finish_digest = digest
                        details = [{"field": list(error["loc"]), "type": error["type"], "message": error["msg"]}
                                   for error in exc.errors(include_input=False)][:20]
                        outcome = {
                            "error": "invalid_finish_arguments",
                            "details": details,
                            **({"duplicate_payload": True,
                                "hint": "Identical invalid payload resent; fix the listed fields before retrying."}
                               if duplicate else {}),
                        }
                        await record_finish(finish_key, outcome)
                        if finish_corrections > MAX_FINISH_CORRECTIONS:
                            raise ProviderFailure("video_agent_finish_correction_limit") from exc
                        history.append(provider.tool_result(tc, outcome))
                        continue
                    if review_required and not review_started:
                        review_started = True
                        review_started_at = step
                        finish_corrections = 0
                        last_rejected_finish_digest = None
                        remember_draft(candidate)
                        outcome = {"status": "draft", "instruction": TEMPORAL_REVIEW_INSTRUCTION +
                                   (" " + EVIDENCE_REVIEW_INSTRUCTION if evidence_review_required else "")}
                        await record_finish(finish_key, outcome)
                        history.append(provider.tool_result(tc, outcome))
                        continue
                    if review_required and step == review_started_at:
                        outcome = {"error": "review_requires_a_separate_turn"}
                        await record_finish(finish_key, outcome)
                        history.append(provider.tool_result(tc, outcome))
                        continue
                    errors = review_errors(candidate)
                    if errors:
                        finish_corrections += 1
                        digest = hashlib.sha256(
                            json.dumps(tc.arguments, sort_keys=True, default=str).encode("utf-8")
                        ).hexdigest()
                        duplicate = digest == last_rejected_finish_digest
                        last_rejected_finish_digest = digest
                        outcome = {
                            "error": "invalid_temporal_evidence",
                            "details": errors,
                            **({"duplicate_payload": True,
                                "hint": "Identical payload resent; fix the listed review/temporal errors before retrying."}
                               if duplicate else {}),
                        }
                        await record_finish(finish_key, outcome)
                        if finish_corrections > MAX_FINISH_CORRECTIONS:
                            raise ProviderFailure("video_agent_finish_correction_limit")
                        history.append(provider.tool_result(tc, outcome))
                        continue
                    final_result = candidate
                    outcome = {"status": "accepted"}
                    await record_finish(finish_key, outcome)
                    history.append(provider.tool_result(tc, outcome))
                    break
                tool_key = f"{step}:{tool_index}"
                cached = (run.raw_output or {}).get("tool_results", {}).get(tool_key)
                if tc.name == "inspect_video_region":
                    if spatial_inspections_used >= MAX_SPATIAL_INSPECTIONS:
                        tool_out, image = {"error": "spatial_inspection_limit"}, None
                    else:
                        try:
                            tool_out, image = await workspace.inspect_region(tc.arguments.get("global_frame"),
                                                                             tc.arguments.get("bbox"))
                        except (ValueError, OSError) as exc:
                            tool_out, image = {"error": "invalid_or_unavailable_region", "detail": str(exc)[:200]}, None
                        except RuntimeError:
                            tool_out, image = {"error": "region_extraction_failed"}, None
                    if cached is not None and cached["output"] != tool_out:
                        raise ProviderFailure("video_agent_region_changed_on_replay")
                    if cached is None:
                        tool_results = dict((run.raw_output or {}).get("tool_results", {}))
                        tool_results[tool_key] = {"output": tool_out, "atlases": []}
                        run.raw_output = {**(run.raw_output or {}), "tool_results": tool_results}
                        await db.commit()
                    history.append(provider.tool_result(tc, tool_out))
                    if image is not None:
                        spatial_inspections_used += 1
                        reference_images.append(image)
                    continue
                if tc.name == "get_character_reference_image":
                    from wuwa_story_worker.video_identity import character_reference_image

                    if reference_images_used >= MAX_REFERENCE_IMAGES:
                        tool_out, image = {"error": "reference_image_limit"}, None
                    else:
                        tool_out, image = await character_reference_image(db, tc.arguments)
                    if cached is not None and cached["output"] != tool_out:
                        raise ProviderFailure("video_agent_reference_changed_on_replay")
                    if cached is None:
                        tool_results = dict((run.raw_output or {}).get("tool_results", {}))
                        tool_results[tool_key] = {"output": tool_out, "atlases": []}
                        run.raw_output = {**(run.raw_output or {}), "tool_results": tool_results}
                        await db.commit()
                    history.append(provider.tool_result(tc, tool_out))
                    if image is not None:
                        reference_images_used += 1
                        reference_images.append(image)
                    continue
                if cached is not None:
                    tool_out = cached["output"]
                    created = []
                    for atlas in cached["atlases"]:
                        created.extend(await workspace.build_atlases(atlas["times"], reason=atlas["reason"]))
                else:
                    tool_out, created = await _execute_video_tool(db, workspace, tc)
                    tool_results = dict((run.raw_output or {}).get("tool_results", {}))
                    tool_results[tool_key] = {
                        "output": tool_out,
                        "atlases": [{"times": e.sample_times, "reason": e.reason} for e in created],
                    }
                    run.raw_output = {**(run.raw_output or {}), "tool_results": tool_results}
                    await db.commit()
                history.append(provider.tool_result(tc, tool_out))
                for entry in created:
                    new_images.append((entry, tool_out))
            if final_result is not None:
                break
            if new_images:
                history.append(
                    _multimodal_user_message(
                        provider,
                        "Newly extracted atlas images from resample_video_window:\n"
                        + json.dumps(workspace.manifest([e for e, _ in new_images]), ensure_ascii=False),
                        [e.image_bytes for e, _ in new_images],
                    )
                )
            if reference_images:
                history.append(_multimodal_user_message(
                    provider, "Requested character reference images and video region crops, in tool-call order; compare with scene frames.",
                    reference_images,
                ))

    if final_result is None:
        raise ProviderFailure("video_agent_exceeded_max_turns")

    provenance = (workspace.report_provenance(final_result)
                  if hasattr(workspace, "report_provenance") else {})
    service = FileRegistrationService(storage)
    atlas_refs = []
    for entry in workspace.atlases:
        source = entry.layout.get("source_segment", run.metadata_json)
        manifest = {
            "protocol": VISION_VERSION,
            "source_file_id": source["file_id"],
            "source_sha256": source["source_sha256"],
            **entry.layout,
        }
        if provenance:
            manifest.update(run_id=run.id, cutscene_node_id=run.target_node_id,
                            asset_version=source["asset_version"])
        identity = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
        source_path = f"video:{source['file_id']}:atlas:{identity}"
        file = await service.register_file(db, entry.path, "image", mime_type="image/jpeg")
        await service.register_variant(db, source["file_id"], file.id, "video_frame_atlas", run.id)
        reference = await db.scalar(
            select(FileReference).where(
                FileReference.reference_type == "video_frame_atlas",
                FileReference.source_path == source_path,
                FileReference.file_id == file.id,
                FileReference.owner_node_id == run.target_node_id,
            )
        )
        if reference is None:
            reference = await service.register_reference(
                db,
                file_id=file.id,
                reference_type="video_frame_atlas",
                owner_node_id=run.target_node_id,
                source_path=source_path,
            )
            reference.metadata_json = manifest
        atlas_refs.append({"reference_id": reference.id, "file_id": file.id, "atlas_index": entry.atlas_index})

    preview_ids = []
    preview_path = workspace.root / "preview.jpg"
    max_preview_time = max(0.0, workspace.duration - 0.1)
    for candidate in sorted(final_result.preview_candidates, key=lambda c: c.score, reverse=True)[:5]:
        preview_path.unlink(missing_ok=True)
        logical_scene = "analysis_plan" in run.metadata_json
        seek_time = (float(candidate.time) if logical_scene
                     else min(max(0.0, float(candidate.time)), max_preview_time))
        try:
            movie, seek_time, _, preview_source = workspace.source_at(seek_time)
            if logical_scene:
                seek_time = preview_source["decode_source_time"]
            await run_tool(
                [
                    str(workspace.ffmpeg),
                    "-v",
                    "error",
                    "-y",
                    "-ss",
                    str(seek_time),
                    "-i",
                    str(movie),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale='min(1280,iw)':-2,format=yuvj420p",
                    "-threads",
                    "1",
                    "-q:v",
                    "2",
                    str(preview_path),
                ],
                60,
            )
            if not preview_path.is_file() or preview_path.stat().st_size == 0:
                continue
        except Exception as exc:
            logger.warning("video_agent.preview_extract_failed time=%s error_type=%s", seek_time, type(exc).__name__)
            continue
        file = await service.register_file(db, preview_path, "image", mime_type="image/jpeg")
        preview_file_id = preview_source.get("file_id", run.metadata_json["file_id"])
        await service.register_variant(db, preview_file_id, file.id, "cutscene_preview", run.id)
        source_path = f"video:{preview_file_id}:preview:run:{run.id}:time:{candidate.time}"
        ref = await db.scalar(
            select(FileReference).where(
                FileReference.reference_type == "cutscene_preview",
                FileReference.source_path == source_path,
                FileReference.owner_node_id == run.target_node_id,
                FileReference.file_id == file.id,
            )
        )
        if ref is None:
            ref = await service.register_reference(
                db,
                file_id=file.id,
                reference_type="cutscene_preview",
                owner_node_id=run.target_node_id,
                source_path=source_path,
            )
            ref.metadata_json = {
                "source_file_id": preview_file_id,
                "source_sha256": preview_source.get("source_sha256", run.metadata_json["source_sha256"]),
                "asset_version": preview_source.get("asset_version", run.metadata_json["asset_version"]),
                "timestamp": candidate.time,
                "score": candidate.score,
                "run_id": run.id,
                **({"source": preview_source} if preview_source else {}),
            }
        preview_ids.append(file.id)

    report = {
        "evidence_type": "ai_visual_observation",
        "protocol": VISION_VERSION,
        "complete": True,
        "source_file_id": run.metadata_json["file_id"],
        "source_sha256": run.metadata_json["source_sha256"],
        "asset_version": run.metadata_json["asset_version"],
        "duration": workspace.duration,
        "title": final_result.title,
        "summary": final_result.summary,
        "events": [ev.model_dump() for ev in final_result.events],
        "story_events": [sev.model_dump() for sev in final_result.story_events],
        "entity_links": [link.model_dump() for link in final_result.entity_links],
        "lore_topics": final_result.lore_topics,
        "identity_tracks": [track.model_dump() for track in final_result.identity_tracks],
        "spatial_evidence": [entry["output"] for entry in (run.raw_output or {}).get("tool_results", {}).values()
                             if "global_frame" in entry.get("output", {}) and "bbox" in entry.get("output", {})],
        **({"review_evidence_version": 1, "review_draft": review_draft.model_dump(),
            "event_review": [decision.model_dump() for decision in final_result.event_review],
            "identity_review": [decision.model_dump() for decision in final_result.identity_review]}
           if evidence_review_required and review_draft is not None else {}),
        "atlases": workspace.manifest(),
        "atlas_references": atlas_refs,
        "preview_file_ids": preview_ids,
        "run_id": run.id,
        "tokens_input": run.tokens_input,
        "tokens_output": run.tokens_output,
        "cost_usd": float(run.cost or 0),
        "analysis_metrics": {
            "unique_frames": len(set(workspace.frame_times.values())),
            "revisited_frames": max(0, len(workspace.frame_times) - len(set(workspace.frame_times.values()))),
            "resampled_atlases": sum(entry.reason != "overview" for entry in workspace.atlases),
            "resample_requests": sum("new_atlases" in item.get("output", {})
                                     for item in (run.raw_output or {}).get("tool_results", {}).values()),
            "reference_images": reference_images_used,
            "spatial_inspections": spatial_inspections_used,
            "finish_attempts": len((run.raw_output or {}).get("finish_diagnostics", {})),
            "finish_rejections": sum("error" in item for item in (run.raw_output or {}).get("finish_diagnostics", {}).values()),
            "tool_output_chars": sum(len(json.dumps(item.get("output", {}), ensure_ascii=False, default=str))
                                     for item in (run.raw_output or {}).get("tool_results", {}).values()),
            "usage_steps": usage_steps,
        },
        **({"temporal_review_version": 1,
            "reviewed_atlases": [entry.atlas_index for entry in workspace.atlases if entry.reason != "overview"]}
           if review_required else {}),
        **provenance,
    }
    report_path = workspace.root / "visual-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    report_file = await service.register_file(db, report_path, "raw_json", mime_type="application/json")
    source_path = f"video:{run.metadata_json['file_id']}:visual:{run.input_hash.hex()}"
    report_ref = await db.scalar(
        select(FileReference).where(
            FileReference.reference_type == "cutscene_visual_description",
            FileReference.source_path == source_path,
            FileReference.owner_node_id == run.target_node_id,
        )
    )
    if report_ref is None:
        report_ref = await service.register_reference(
            db,
            file_id=report_file.id,
            reference_type="cutscene_visual_description",
            owner_node_id=run.target_node_id,
            source_path=source_path,
        )
    report_ref.metadata_json = report

    await _publish_cutscene_knowledge(db, run, final_result)
    run.raw_output = {
        **(run.raw_output or {}),
        "mode": "video_agent",
        "report_reference_id": report_ref.id,
        "atlases": len(atlas_refs),
    }
    run.status = "completed"
    return final_result


async def simulate_video_analysis(
    video_path: Path,
    output_dir: Path,
    *,
    interval: float | None = None,
    window_start: float | None = None,
    window_end: float | None = None,
    window_interval: float = 0.5,
) -> dict[str, Any]:
    """Local CLI simulation: builds globally numbered atlases, tests zoom resampling, and writes outputs."""
    ffmpeg_name = shutil.which("ffmpeg")
    if not ffmpeg_name:
        raise RuntimeError("ffmpeg not found on PATH")
    ffmpeg = Path(ffmpeg_name).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    duration, _ = await asyncio.to_thread(video_duration, ffmpeg, video_path)
    settings = AgentSettings(_env_file=None)
    if interval is not None:
        settings.vision_frame_interval = max(1, min(30, int(round(interval))))
    limits = dynamic_sampling_plan(duration, settings)

    with tempfile.TemporaryDirectory() as tmp:
        workspace = VideoWorkspace(
            root=Path(tmp),
            ffmpeg=ffmpeg,
            movie=video_path.resolve(),
            duration=duration,
            settings=settings,
            limits=limits,
        )
        overview_times = sample_times(
            duration,
            float(limits["overview_interval"]),
            int(limits["max_overview_frames"]),
        )
        await workspace.build_atlases(overview_times, reason="overview")

        resample_result: dict[str, Any] | None = None
        if window_start is not None and window_end is not None:
            check = validate_resample_request(
                duration,
                window_start,
                window_end,
                window_interval,
                soft_max_frames=int(limits["soft_zoom_frames"]),
                hard_max_frames=int(limits["hard_zoom_frames"]),
            )
            if check["ok"]:
                created = await workspace.build_atlases(
                    list(check["times"]),  # type: ignore[arg-type]
                    reason="cli_zoom_window",
                )
                resample_result = {"ok": True, "new_atlases": workspace.manifest(created)}
            else:
                resample_result = check

        for entry in workspace.atlases:
            shutil.copy2(entry.path, output_dir / entry.path.name)
        summary = {
            "video": str(video_path),
            "duration_seconds": duration,
            "limits": limits,
            "atlases": workspace.manifest(),
            "resample_result": resample_result,
        }
        (output_dir / "simulation_manifest.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return summary

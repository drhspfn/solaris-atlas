"""Read a pinned logical scene without splicing mutually exclusive source movies."""

from __future__ import annotations

import asyncio
import hashlib
import math
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from wuwa_story.agents.cutscene_vision import dynamic_sampling_plan, sample_times
from wuwa_story.db.models.storage import FileLocation

from wuwa_story_worker.cutscene_import import video_duration
from wuwa_story_worker.video_analysis_agent import VideoWorkspace


@dataclass
class SceneSource:
    segment: dict
    movie: Path
    duration: float


class LogicalVideoWorkspace(VideoWorkspace):
    """One numbering space and frame allowance for all selected scene segments."""

    def __init__(self, *, plan: dict, sources: list[SceneSource], **kwargs):
        super().__init__(**kwargs)
        self.plan = plan
        self.sources = sources

    def source_at(self, timestamp: float) -> tuple[Path, float, float, dict]:
        if not math.isfinite(timestamp):
            raise ValueError("Scene time must be finite")
        for index, source in enumerate(self.sources):
            segment = source.segment
            if segment["scene_start"] <= timestamp < segment["scene_end"]:
                local = segment["source_start"] + timestamp - segment["scene_start"]
                return source.movie, local, source.duration, {
                    **segment, "segment_index": index, "source_time": local,
                    "scene_time": timestamp,
                    "scene_offset": segment["scene_start"] - segment["source_start"],
                    # FFmpeg needs a small EOF margin; retain the authored coordinate too.
                    "decode_source_time": min(local, max(segment["source_start"], source.duration - 0.1)),
                }
        raise ValueError("Scene time has no selected video evidence (gap or outside scene)")

    def overview_times(self) -> list[float]:
        # Reserve both boundaries of EVERY part before spending the remaining pool.
        cap = int(self.limits["max_overview_frames"])
        if 2 * len(self.sources) > cap:
            raise ValueError("Overview allowance cannot cover every selected segment")
        times = []
        remaining = cap
        for index, source in enumerate(self.sources):
            segment = source.segment
            length = segment["scene_end"] - segment["scene_start"]
            allowance = remaining - 2 * (len(self.sources) - index - 1)
            # Distribute scarce frames across parts, rather than starving the ending.
            remaining_length = sum(s.segment["scene_end"] - s.segment["scene_start"]
                                   for s in self.sources[index:])
            count = min(allowance, max(2, round(remaining * length / remaining_length)))
            selected = sample_times(length, float(self.limits["overview_interval"]), count)
            times.extend(segment["scene_start"] + t for t in selected)
            remaining -= len(selected)
        return times

    async def build_atlases(self, times: list[float], *, reason: str = "overview"):
        if len(self.frame_paths) + len(times) > self.settings.vision_max_frames:
            raise ValueError("Shared scene screenshot allowance exhausted")
        # Validate the whole window before extracting anything; gaps are never clamped.
        selected = [(t, self.source_at(t)[3]["segment_index"]) for t in times]
        created = []
        for index, source in enumerate(self.sources):
            group = [t for t, segment_index in selected if segment_index == index]
            if not group:
                continue
            entries = await super().build_atlases(group, reason=reason)
            for entry in entries:
                entry.layout["source_segment"] = {**source.segment, "segment_index": index}
                for tile in entry.layout["tiles"]:
                    tile["source"] = self.source_at(tile["time"])[3]
            created.extend(entries)
        return created

    def manifest(self, entries=None):
        target = self.atlases if entries is None else entries
        manifests = super().manifest(target)
        for manifest, entry in zip(manifests, target, strict=True):
            manifest["source_segment"] = entry.layout["source_segment"]
            for tile, layout_tile in zip(manifest["tiles"], entry.layout["tiles"], strict=True):
                tile["source"] = layout_tile["source"]
        return manifests

    def report_provenance(self, result) -> dict:
        events = []
        for event in result.events:
            source = self.source_at(event.time)[3]
            if event.global_frame is not None:
                frame_time = self.frame_times.get(event.global_frame)
                if frame_time is None or abs(frame_time - event.time) > 0.1:
                    raise ValueError("Observation frame does not match its scene timestamp")
            events.append({**event.model_dump(), "source": source})
        story_events = []
        for event in result.story_events:
            if not 0 <= event.time_start < event.time_end <= self.duration:
                raise ValueError("Story event is outside the scene timeline")
            spans = []
            for index, source in enumerate(self.sources):
                segment = source.segment
                start = max(event.time_start, segment["scene_start"])
                end = min(event.time_end, segment["scene_end"])
                if start < end:
                    spans.append({**segment, "segment_index": index,
                                  "scene_start": start, "scene_end": end,
                                  "source_start": segment["source_start"] + start - segment["scene_start"],
                                  "source_end": segment["source_start"] + end - segment["scene_start"]})
            if not spans:
                raise ValueError("Story event has no selected video evidence")
            story_events.append({**event.model_dump(), "source_spans": spans})
        gaps = []
        end = 0.0
        for source in self.sources:
            if source.segment["scene_start"] > end:
                gaps.append({"scene_start": end, "scene_end": source.segment["scene_start"]})
            end = source.segment["scene_end"]
        return {"analysis_plan": self.plan, "cutscene_node_id": self.plan["cutscene_node_id"],
                "events": events, "story_events": story_events, "gaps": gaps,
                "coverage": "sampled_selected_segments; gaps contain no visual evidence"}


async def load_logical_workspace(db, run, settings, storage, config, root, ffmpeg):
    plan = run.metadata_json["analysis_plan"]
    if (not isinstance(plan, dict) or type(plan.get("version")) is not int
            or plan["version"] != 1 or plan.get("cutscene_node_id") != run.target_node_id):
        raise ValueError("Unsupported or mismatched logical scene plan")
    segments = plan.get("segments")
    if not isinstance(segments, list) or not segments or len(segments) > settings.vision_max_frames // 2:
        raise ValueError("Logical scene must fit the shared screenshot allowance")
    previous_end = 0.0
    for segment in segments:
        if not isinstance(segment, dict):
            raise ValueError("Logical scene requires source segment objects")
        for key in ("source_start", "source_end", "scene_start", "scene_end"):
            value = segment.get(key)
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError("Logical scene requires finite nonnegative source/scene bounds")
        source_length = segment["source_end"] - segment["source_start"]
        scene_length = segment["scene_end"] - segment["scene_start"]
        if (source_length <= 0 or abs(source_length - scene_length) > 0.001
                or segment["scene_start"] < previous_end):
            raise ValueError("Selected scene intervals must be ordered, nonoverlapping and unscaled")
        previous_end = segment["scene_end"]
        for key in ("asset_node_id", "file_id"):
            if type(segment.get(key)) is not int or segment[key] <= 0:
                raise ValueError("Logical segment requires source identifiers")
        if not isinstance(segment.get("provenance"), dict) or not isinstance(segment.get("asset_version"), str):
            raise ValueError("Logical segment requires version and source provenance")
        digest = segment.get("source_sha256")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("Logical segment requires a source content hash")
    sources = []
    downloaded = {}
    for segment in segments:
        identity = (segment["file_id"], segment["source_sha256"])
        if identity not in downloaded:
            location = await db.scalar(select(FileLocation).where(
                FileLocation.file_id == segment["file_id"], FileLocation.backend == "s3",
                FileLocation.bucket == config.s3_bucket, FileLocation.available.is_(True)).limit(1))
            if location is None:
                raise ValueError("Selected scene source storage unavailable")
            movie = root / f"source_{len(downloaded)}.mp4"
            digest = hashlib.sha256()
            with movie.open("wb") as output:
                async for data in storage.get(location.object_key):
                    digest.update(data)
                    output.write(data)
            if digest.hexdigest() != segment["source_sha256"]:
                raise ValueError("Selected scene source content changed")
            duration, _ = await asyncio.to_thread(video_duration, ffmpeg, movie)
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError("Selected scene source duration unavailable")
            downloaded[identity] = movie, duration
        movie, duration = downloaded[identity]
        if segment["source_start"] >= duration or segment["source_end"] > duration + 0.1:
            raise ValueError("Selected scene interval exceeds source duration")
        sources.append(SceneSource(segment, movie, duration))
    limits = dynamic_sampling_plan(previous_end, settings)
    # More tiny sequential clips may need more boundaries than a single clip of equal length.
    limits["max_overview_frames"] = max(2 * len(sources), int(limits["max_overview_frames"]))
    return LogicalVideoWorkspace(plan=plan, sources=sources, root=root, ffmpeg=ffmpeg,
                                 movie=sources[0].movie, duration=previous_end,
                                 settings=settings, limits=limits)

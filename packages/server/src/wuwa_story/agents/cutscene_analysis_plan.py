"""Plan a single authored playback path without guessing variant equivalence."""

import hashlib
import json
import math
from typing import Any

from wuwa_story.ingestion.cutscenes import Choice, Clip, PlaybackFlow


async def enqueue_logical_visual_job(session, plan: dict, reference, settings):
    """Pin the complete plan and context under the same transaction lock as legacy jobs."""
    from sqlalchemy import select, text
    from sqlalchemy.dialects.postgresql import insert

    from wuwa_story.agents.cutscene_vision import VISION_VERSION
    from wuwa_story.agents.video_context import video_context_snapshot
    from wuwa_story.db.models.ops import ProcessingRun, Processor

    config = settings.public_config()
    context = await video_context_snapshot(session, reference)
    identity = hashlib.sha256(json.dumps({"analysis_plan": plan, "protocol": VISION_VERSION,
        "config": config, "text_context": context}, sort_keys=True).encode()).digest()
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"),
                          {"key": int.from_bytes(identity[:7], "big")})
    await session.execute(insert(Processor).values(key="cutscene_vision", version=VISION_VERSION)
                          .on_conflict_do_nothing(index_elements=[Processor.key]))
    processor = await session.scalar(select(Processor.id).where(Processor.key == "cutscene_vision"))
    existing = await session.scalar(select(ProcessingRun).where(
        ProcessingRun.processor_id == processor, ProcessingRun.input_hash == identity))
    if existing:
        return existing
    first = plan["segments"][0]
    run = ProcessingRun(processor_id=processor, target_node_id=plan["cutscene_node_id"],
        prompt_version=VISION_VERSION, input_hash=identity, status="pending", metadata_json={
            "reference_id": reference.id, "file_id": first["file_id"],
            "source_sha256": first["source_sha256"], "asset_version": first["asset_version"],
            "analysis_plan": plan, "settings": config, "text_context": context,
            "cutscene_node_id": plan["cutscene_node_id"],
            "selected_asset_node_ids": plan["selection"]["selected_asset_node_ids"],
            "ignored_alternative_asset_node_ids": plan["selection"]["ignored_alternative_asset_node_ids"],
        }, raw_output={"batches": [], "step": 0})
    session.add(run)
    await session.flush()
    return run


def build_analysis_plan(
    cutscene_node_id: int, asset_version: str, flow_data: dict, sources: dict[str, dict],
    *, flow_reference_id: int,
) -> dict[str, Any]:
    flow = PlaybackFlow.model_validate(flow_data)
    nodes = {node.id: node for node in flow.nodes}
    selected, decisions = [], []
    cursor, elapsed = flow.entry, 0.0
    while cursor:
        node = nodes[cursor]
        if isinstance(node, Choice):
            genders = {option.rover for option in node.options}
            if len(node.options) != 2 or genders != {"male", "female"}:
                raise ValueError("Playback choice is not a proven pair of Rover alternatives")
            female = next(option for option in node.options if option.rover == "female")
            decisions.append({"choice_id": node.id, "rover": "female", "selected": female.next,
                              "excluded": [option.next for option in node.options if option is not female]})
            cursor = female.next
            continue
        assert isinstance(node, Clip)
        source = sources.get(node.asset)
        if not source:
            raise ValueError("A selected playback asset is not published for this cutscene and version")
        duration = source.get("duration_seconds")
        if not isinstance(duration, (int, float)) or isinstance(duration, bool) or not math.isfinite(duration) or duration <= 0:
            raise ValueError("A selected playback asset has no verified duration")
        start, end = float(node.start), float(node.end if node.end is not None else duration)
        if end > duration + 0.001 or start >= end:
            raise ValueError("Playback clip exceeds its published source duration")
        segment_end = elapsed + end - start
        selected.append({"asset_node_id": source["asset_node_id"], "file_id": source["file_id"],
                         "source_sha256": source["source_sha256"], "asset_version": asset_version,
                         "source_start": start, "source_end": end, "scene_start": elapsed,
                         "scene_end": segment_end,
                         "provenance": {"flow_reference_id": flow_reference_id, "flow_node_id": node.id,
                                        "asset_key": node.asset, "segment": node.segment}})
        elapsed, cursor = segment_end, node.next
    if not selected:
        raise ValueError("Playback flow has no selected clips")
    selected_ids = sorted({segment["asset_node_id"] for segment in selected})
    ignored_ids = sorted({source["asset_node_id"] for source in sources.values()} - set(selected_ids))
    # Only a whole, single-clip Rover branch proves that an old asset-level
    # report covers the entire logical scene. Never reuse a partial path.
    reusable = []
    entry = nodes[flow.entry]
    if isinstance(entry, Choice) and len(decisions) == 1 and len(selected) == 1:
        branches = [nodes[option.next] for option in entry.options]
        if all(isinstance(branch, Clip) and branch.next is None and branch.start == 0
               and branch.end is None and branch.segment is None for branch in branches):
            reusable = selected_ids + [sources[branch.asset]["asset_node_id"] for branch in branches
                                      if branch.asset in sources and sources[branch.asset]["asset_node_id"] not in selected_ids]
    return {"version": 1, "cutscene_node_id": cutscene_node_id, "segments": selected,
            "selection": {"rover": "female", "choices": decisions,
                          "selected_asset_node_ids": selected_ids,
                          "ignored_alternative_asset_node_ids": ignored_ids,
                          "reusable_whole_scene_asset_ids": reusable,
                          "flow_reference_id": flow_reference_id}}

"""Exact game-resource references for quest media extraction."""

from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.api.routes.story.captions import caption_texts, caption_tracks
from wuwa_story.api.routes.story.shared import _quest_state_ids, _release_id
from wuwa_story.config.settings import get_settings
from wuwa_story.db.models.core import Quest, QuestAction, QuestState, VoiceReference
from wuwa_story.db.models.graph import Edge, EdgeEvidence, Node, NodeRevision
from wuwa_story.db.models.ontology import RelationType
from wuwa_story.db.models.ops import GameRelease
from wuwa_story.db.models.raw import SourceRecord
from wuwa_story.db.models.storage import FileLocation, FileReference
from wuwa_story.db.session import get_session
from wuwa_story.ingestion.cutscenes import Clip, PlaybackFlow
from wuwa_story.storage.s3 import S3Storage

router = APIRouter(tags=["story browsing"])


async def _links(
    session: AsyncSession, from_ids: list[int], relations: tuple[str, ...], release_id: int
) -> dict[int, list[dict[str, Any]]]:
    if not from_ids:
        return {}
    rows = await session.execute(
        select(Edge.from_node_id, Edge.to_node_id, Node.canonical_key,
               RelationType.key, Edge.basis, EdgeEvidence.source_file_path,
               EdgeEvidence.source_raw_path)
        .join(RelationType, RelationType.id == Edge.relation_type_id)
        .join(Node, Node.id == Edge.to_node_id)
        .join(EdgeEvidence, EdgeEvidence.edge_id == Edge.id)
        .where(Edge.from_node_id.in_(from_ids), RelationType.key.in_(relations),
               Edge.layer == "source", EdgeEvidence.release_id == release_id)
        .order_by(Edge.from_node_id, Edge.id, EdgeEvidence.id)
    )
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for owner_id, target_id, target_key, relation, basis, source_file, raw_path in rows:
        grouped[owner_id].append({
            "node_id": target_id, "canonical_key": target_key, "relation": relation,
            "basis": basis, "source": {"file": source_file, "raw_path": raw_path},
        })
    return grouped


async def dialogue_media(
    session: AsyncSession, line_ids: list[int], release_id: int | None
) -> dict[int, dict[str, Any]]:
    """Return only voice/AK references explicitly linked to these talk items."""
    if release_id is None or not line_ids:
        return {}
    links = await _links(
        session, line_ids, ("has_voice_reference", "posts_audio_event"), release_id
    )
    voice_ids = [link["node_id"] for entries in links.values() for link in entries
                 if link["relation"] == "has_voice_reference"]
    voices = {voice.node_id: voice for voice in await session.scalars(
        select(VoiceReference).where(VoiceReference.node_id.in_(voice_ids))
    )} if voice_ids else {}
    tracks: dict[int, dict[str, dict]] = defaultdict(dict)
    if voice_ids:
        settings = get_settings()
        storage = S3Storage(settings)
        rows = await session.execute(
            select(FileReference, FileLocation.object_key)
            .join(FileLocation, FileLocation.file_id == FileReference.file_id)
            .where(FileReference.owner_node_id.in_(voice_ids),
                   FileReference.reference_type == "voice_audio",
                   FileLocation.backend == "s3", FileLocation.bucket == settings.s3_bucket,
                   FileLocation.available.is_(True), FileLocation.is_primary.is_(True))
            .order_by(FileReference.id.desc())
        )
        for reference, object_key in rows:
            language = reference.metadata_json.get("language")
            if language in ("en", "ja", "ko", "zh"):
                tracks[reference.owner_node_id].setdefault(language, {
                    "language": language, "url": storage.public_url(object_key),
                    "asset_version": reference.metadata_json.get("asset_version"),
                    "duration_seconds": reference.metadata_json.get("duration_seconds"),
                })
    result = {}
    for line_id, entries in links.items():
        result[line_id] = {
            "voice_references": [
                {"canonical_key": link["canonical_key"], "plot_audio_id": voice.plot_audio_id,
                 "file_name": voice.file_name, "reference_kind": "plot_audio_filename",
                 "engine_path": None, "media_asset_linked": voice.media_asset_node_id is not None,
                 "basis": link["basis"], "source": link["source"],
                 "tracks": list(tracks.get(voice.node_id, {}).values())}
                for link in entries if link["relation"] == "has_voice_reference"
                if (voice := voices.get(link["node_id"])) is not None
            ],
            "audio_event_paths": [
                {"engine_path": link["canonical_key"].removeprefix("asset:ue:"),
                 "reference_kind": "unreal_asset_path", "basis": link["basis"],
                 "source": link["source"]}
                for link in entries if link["relation"] == "posts_audio_event"
                and link["canonical_key"].startswith("asset:ue:")
            ],
        }
    return result


async def cutscene_videos(session: AsyncSession, asset_ids: list[int], asset_version: str | None = None) -> dict[int, dict]:
    """Published playable files for exact authored asset identities, across text snapshots."""
    if not asset_ids:
        return {}
    settings = get_settings()
    storage = S3Storage(settings)
    rows = await session.execute(
        select(FileReference, FileLocation.object_key)
        .join(FileLocation, FileLocation.file_id == FileReference.file_id)
        .where(FileReference.owner_node_id.in_(asset_ids),
               FileReference.reference_type == "cutscene_video",
               FileLocation.backend == "s3", FileLocation.bucket == settings.s3_bucket,
               FileLocation.available.is_(True), FileLocation.is_primary.is_(True))
        .where(FileReference.metadata_json["asset_version"].astext == asset_version if asset_version else True)
        .order_by(FileReference.id.desc())
    )
    videos = {}
    for reference, object_key in rows:
        videos.setdefault(reference.owner_node_id, {
            "url": storage.public_url(object_key),
            "asset_version": reference.metadata_json.get("asset_version"),
            "has_audio": reference.metadata_json.get("has_audio", False),
            "soundtrack": reference.metadata_json.get("soundtrack"),
            "subtitles_included": reference.metadata_json.get("subtitles_included", False),
        })
    return videos


async def cutscene_flows(session: AsyncSession, cutscene_ids: list[int]) -> dict[int, dict]:
    if not cutscene_ids:
        return {}
    rows = await session.scalars(select(FileReference).where(
        FileReference.owner_node_id.in_(cutscene_ids), FileReference.reference_type == "cutscene_flow")
        .order_by(FileReference.id.desc()))
    selected = {}
    for reference in rows:
        selected.setdefault(reference.owner_node_id, reference)
    result = {}
    for owner_id, reference in selected.items():
        flow = PlaybackFlow.model_validate(reference.metadata_json["flow"])
        keys = {node.asset for node in flow.nodes if isinstance(node, Clip)}
        nodes = list(await session.scalars(select(Node).where(Node.canonical_key.in_(keys))))
        videos = await cutscene_videos(session, [node.id for node in nodes], reference.metadata_json["asset_version"])
        media = {node.canonical_key: videos[node.id] for node in nodes if node.id in videos}
        segment_nodes = [node for node in flow.nodes if isinstance(node, Clip) and node.segment]
        if segment_nodes:
            settings = get_settings()
            storage = S3Storage(settings)
            segment_rows = await session.execute(select(FileReference, FileLocation.object_key)
                .join(FileLocation, FileLocation.file_id == FileReference.file_id)
                .where(FileReference.owner_node_id.in_([node.id for node in nodes]),
                    FileReference.reference_type == "cutscene_segment",
                    FileReference.metadata_json["asset_version"].astext == reference.metadata_json["asset_version"],
                    FileReference.metadata_json["segment_id"].astext.in_([node.segment for node in segment_nodes]),
                    FileLocation.backend == "s3", FileLocation.bucket == settings.s3_bucket,
                    FileLocation.available.is_(True), FileLocation.is_primary.is_(True))
                .order_by(FileReference.id.desc()))
            for segment_reference, object_key in segment_rows:
                key = segment_reference.metadata_json["segment_id"]
                media.setdefault(key, {"url": storage.public_url(object_key),
                    "asset_version": segment_reference.metadata_json["asset_version"],
                    "has_audio": segment_reference.metadata_json.get("has_audio", False),
                    "soundtrack": segment_reference.metadata_json.get("soundtrack"), "subtitles_included": False})
        required = {node.segment or node.asset for node in flow.nodes if isinstance(node, Clip)}
        # Publish the whole path or none of it; a broken branch is not a playable flow.
        if required.issubset(media):
            result[owner_id] = {**flow.model_dump(), "media": {key: media[key] for key in required},
                                "asset_version": reference.metadata_json["asset_version"]}
    return result


@router.get("/quests/{game_quest_id}/media")
async def quest_media(
    game_quest_id: int,
    game_version: str | None = None,
    locale: str = "en",
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Authored media references with published playable files when available."""
    quest = await session.scalar(select(Quest).where(Quest.game_quest_id == game_quest_id))
    if quest is None:
        raise HTTPException(status_code=404, detail="quest not found")
    release_id = await _release_id(session, game_version)
    if release_id is None:
        raise HTTPException(status_code=404, detail="game version not imported")
    state_ids = await _quest_state_ids(session, quest.node_id)
    action_rows = list((await session.execute(
        select(QuestAction, QuestState, Node.canonical_key)
        .join(QuestState, QuestState.node_id == QuestAction.quest_state_node_id)
        .join(Node, Node.id == QuestAction.node_id)
        .where(QuestAction.quest_state_node_id.in_(state_ids))
        .order_by(QuestState.state_key, QuestAction.action_index)
    )).all())
    action_links = await _links(
        session, [action.node_id for action, _, _ in action_rows],
        ("plays_cutscene", "plays_sequence_asset", "posts_audio_event"), release_id,
    )
    cutscene_ids = [link["node_id"] for entries in action_links.values() for link in entries
                    if link["relation"] == "plays_cutscene"]
    flows = await cutscene_flows(session, cutscene_ids)
    variants = await _links(session, cutscene_ids, ("has_variant", "uses_audio_event", "uses_audio_event_normalized", "uses_caption"), release_id)
    child_ids = [link["node_id"] for entries in variants.values() for link in entries]
    assets = await _links(session, child_ids, ("references_asset",), release_id)
    playable = await cutscene_videos(session, [asset["node_id"] for entries in assets.values() for asset in entries])
    resource_rows = await session.execute(
        select(NodeRevision.node_id, SourceRecord.data)
        .join(SourceRecord, SourceRecord.id == NodeRevision.source_record_id)
        .where(NodeRevision.node_id.in_(child_ids), NodeRevision.release_id == release_id)
    ) if child_ids else []
    resource_raw = {node_id: raw for node_id, raw in resource_rows}
    captions = [raw for raw in resource_raw.values() if "CaptionText" in raw]
    texts = await caption_texts(session, captions, locale, release_id)
    transcript_states: dict[int, list[str]] = defaultdict(list)
    if cutscene_ids:
        for cutscene_id, state_key in await session.execute(
            select(Edge.from_node_id, QuestState.state_key)
            .join(RelationType, RelationType.id == Edge.relation_type_id)
            .join(QuestState, QuestState.node_id == Edge.to_node_id)
            .join(EdgeEvidence, EdgeEvidence.edge_id == Edge.id)
            .where(Edge.from_node_id.in_(cutscene_ids), RelationType.key == "has_transcript_state",
                   Edge.layer == "source", EdgeEvidence.release_id == release_id)
        ):
            transcript_states[cutscene_id].append(state_key)
    events = []
    for action, state, action_key in action_rows:
        for link in action_links.get(action.node_id, []):
            entry = {
                "kind": {"plays_cutscene": "cutscene", "plays_sequence_asset": "sequence",
                         "posts_audio_event": "audio_event"}[link["relation"]],
                "action": action_key, "flow_state": state.state_key,
                "action_index": action.action_index, "reference": link["canonical_key"],
                "basis": link["basis"], "source": link["source"],
                "engine_path": link["canonical_key"].removeprefix("asset:ue:")
                if link["canonical_key"].startswith("asset:ue:") else None,
                "reference_kind": "unreal_asset_path"
                if link["canonical_key"].startswith("asset:ue:") else "cg_name",
            }
            if link["relation"] == "plays_cutscene":
                entry["playback"] = flows.get(link["node_id"])
                entry["transcript_states"] = sorted(set(transcript_states[link["node_id"]]))
                # Timing schema is verified against the current client, not assumed for old builds.
                entry["captions"] = caption_tracks([
                    resource_raw[child["node_id"]]
                    for child in variants.get(link["node_id"], [])
                    if child["relation"] == "uses_caption" and child["node_id"] in resource_raw
                ], texts) if entry["playback"] and entry["playback"]["asset_version"] == "3.7.0" else {}
                entry["resources"] = [
                    {"kind": child["relation"], "reference": child["canonical_key"],
                     "basis": child["basis"], "source": child["source"],
                     "variant": {
                         "cg_id": resource_raw[child["node_id"]].get("CgId"),
                         "girl_or_boy": resource_raw[child["node_id"]].get("GirlOrBoy"),
                         "belong_branch": resource_raw[child["node_id"]].get("BelongBranch"),
                     } if child["relation"] == "has_variant" and child["node_id"] in resource_raw else None,
                     "caption": {
                         "localization_key": resource_raw[child["node_id"]].get("CaptionText"),
                         "show_moment": resource_raw[child["node_id"]].get("ShowMoment"),
                         "duration": resource_raw[child["node_id"]].get("Duration"),
                         "timing_unit": "unverified",
                     } if child["relation"] == "uses_caption" and child["node_id"] in resource_raw else None,
                     "assets": [
                         {"reference": asset["canonical_key"],
                          "engine_path": asset["canonical_key"].removeprefix("asset:ue:")
                          if asset["canonical_key"].startswith("asset:ue:") else None,
                          "reference_kind": "unreal_asset_path", "basis": asset["basis"],
                          "source": asset["source"], "video": playable.get(asset["node_id"])}
                         for asset in assets.get(child["node_id"], [])
                     ]}
                    for child in variants.get(link["node_id"], [])
                ]
            events.append(entry)
    package_links = await _links(
        session, [quest.node_id], ("references_video_package",), release_id
    )
    package_assets = await _links(
        session, [link["node_id"] for link in package_links.get(quest.node_id, [])],
        ("references_asset",), release_id,
    )
    return {
        "quest": f"quest:{game_quest_id}",
        "game_version": (await session.get(GameRelease, release_id)).game_version,
        "availability": "partial" if playable else "references_only",
        "events": events,
        "video_packages": [
            {"reference": link["canonical_key"], "basis": link["basis"],
             "source": link["source"],
             "packages": [{"reference": asset["canonical_key"],
                           "reference_kind": "video_package_identity",
                           "basis": asset["basis"], "source": asset["source"]}
                          for asset in package_assets.get(link["node_id"], [])]}
            for link in package_links.get(quest.node_id, [])
        ],
    }

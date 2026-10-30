"""Exact game-resource references for quest media extraction."""

from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.agents.cutscene_vision import visual_reference
from wuwa_story.agents.evidence import quest_fingerprint
from wuwa_story.api import read_projections
from wuwa_story.api.routes.story.captions import caption_texts, caption_tracks
from wuwa_story.api.routes.story.cutscene_audio import audio_bundles
from wuwa_story.api.routes.story.shared import _release_id
from wuwa_story.config.settings import get_settings
from wuwa_story.db.models.content import Document, DocumentHead
from wuwa_story.db.models.core import QuestAction, QuestState
from wuwa_story.db.models.graph import Edge, EdgeEvidence, Node, NodeRevision
from wuwa_story.db.models.i18n import Locale
from wuwa_story.db.models.ontology import RelationType
from wuwa_story.db.models.ops import GameRelease
from wuwa_story.db.models.raw import SourceRecord
from wuwa_story.db.models.storage import FileLocation, FileReference
from wuwa_story.db.repositories.nodes import get_quest
from wuwa_story.db.session import get_session
from wuwa_story.ingestion.cutscenes import Clip, PlaybackFlow
from wuwa_story.storage.s3 import S3Storage
from wuwa_story.story import media as story_media
from wuwa_story.story.service import _quest_page_state_ids
from wuwa_story.story.tree_order import authored_order

router = APIRouter(tags=["story browsing"])


async def _links(
    session: AsyncSession, from_ids: list[int], relations: tuple[str, ...], release_id: int
) -> dict[int, list[dict[str, Any]]]:
    return await story_media.media_links(session, from_ids, relations, release_id)

async def dialogue_media(
    session: AsyncSession, line_ids: list[int], release_id: int | None
) -> dict[int, dict[str, Any]]:
    """Compatibility entry point for existing media callers."""
    return await story_media.dialogue_media(
        session, line_ids, release_id, link_loader=_links,
        settings_factory=get_settings, storage_factory=S3Storage,
    )

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
            "asset_node_id": reference.owner_node_id,
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
    rows = await session.scalars(
        select(FileReference)
        .where(
            FileReference.owner_node_id.in_(cutscene_ids),
            FileReference.reference_type == "cutscene_flow",
        )
        .order_by(FileReference.id.desc())
    )
    selected = {}
    for reference in rows:
        selected.setdefault(reference.owner_node_id, reference)
    result = {}
    for owner_id, reference in selected.items():
        flow = PlaybackFlow.model_validate(reference.metadata_json["flow"])
        keys = {node.asset for node in flow.nodes if isinstance(node, Clip)}
        nodes = list(await session.scalars(select(Node).where(Node.canonical_key.in_(keys))))
        videos = await cutscene_videos(
            session, [node.id for node in nodes], reference.metadata_json["asset_version"]
        )
        media = {node.canonical_key: {**videos[node.id], "asset_node_id": node.id}
                 for node in nodes if node.id in videos}
        settings = get_settings()
        storage = S3Storage(settings)
        bundles = await audio_bundles(
            session,
            [node.id for node in nodes],
            reference.metadata_json["asset_version"],
            settings,
            storage,
        )
        for asset in nodes:
            if asset.canonical_key in media and asset.id in bundles:
                media[asset.canonical_key] = {
                    **media[asset.canonical_key],
                    "url": bundles[asset.id]["videos"]["full"],
                    "audio_tracks": bundles[asset.id]["tracks"],
                    "timeline_offset": 0,
                }
        segment_nodes = [node for node in flow.nodes if isinstance(node, Clip) and node.segment]
        if segment_nodes:
            settings = get_settings()
            storage = S3Storage(settings)
            segment_rows = await session.execute(
                select(FileReference, FileLocation.object_key)
                .join(FileLocation, FileLocation.file_id == FileReference.file_id)
                .where(
                    FileReference.owner_node_id.in_([node.id for node in nodes]),
                    FileReference.reference_type == "cutscene_segment",
                    FileReference.metadata_json["asset_version"].astext
                    == reference.metadata_json["asset_version"],
                    FileReference.metadata_json["segment_id"].astext.in_(
                        [node.segment for node in segment_nodes]
                    ),
                    FileLocation.backend == "s3",
                    FileLocation.bucket == settings.s3_bucket,
                    FileLocation.available.is_(True),
                    FileLocation.is_primary.is_(True),
                )
                .order_by(FileReference.id.desc())
            )
            for segment_reference, object_key in segment_rows:
                key = segment_reference.metadata_json["segment_id"]
                bundle = bundles.get(segment_reference.owner_node_id)
                media.setdefault(
                    key,
                    {
                        "url": storage.public_url(object_key),
                        "asset_node_id": segment_reference.owner_node_id,
                        "asset_version": segment_reference.metadata_json["asset_version"],
                        "has_audio": segment_reference.metadata_json.get("has_audio", False),
                        "soundtrack": segment_reference.metadata_json.get("soundtrack"),
                        "subtitles_included": False,
                        "timeline_offset": segment_reference.metadata_json["start_frame"]
                        / segment_reference.metadata_json["fps"],
                        "audio_tracks": None,
                    },
                )
                if bundle and key in bundle["videos"]:
                    media[key] = {
                        **media[key],
                        "url": bundle["videos"][key],
                        "audio_tracks": bundle["tracks"],
                    }
        required = {node.segment or node.asset for node in flow.nodes if isinstance(node, Clip)}
        # Publish the whole path or none of it; a broken branch is not a playable flow.
        if required.issubset(media):
            result[owner_id] = {
                **flow.model_dump(),
                "media": {key: media[key] for key in required},
                "asset_version": reference.metadata_json["asset_version"],
            }
    return result


@router.get("/quests/{game_quest_id}/media")
async def quest_media(
    game_quest_id: str,
    game_version: str | None = None,
    locale: str = "en",
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Authored media references with published playable files when available."""
    if read_projections.should_route_to_projection("quest", game_version):
        return await read_projections.quest_section(session, game_quest_id, locale, game_version, "media")
    quest = await get_quest(session, game_quest_id)
    if quest is None:
        raise HTTPException(status_code=404, detail="quest not found")
    game_quest_id = quest.game_quest_id
    release_id = await _release_id(session, game_version)
    if release_id is None:
        raise HTTPException(status_code=404, detail="game version not imported")
    tree_order = await authored_order(session, game_quest_id, release_id)
    state_ids = await _quest_page_state_ids(session, quest.node_id, release_id)
    if tree_order:
        state_ids = list(set(state_ids) | set(tree_order['state_ids']))
    contextual_state_ids = set(await session.scalars(
        select(Edge.to_node_id)
        .join(RelationType, RelationType.id == Edge.relation_type_id)
        .join(EdgeEvidence, EdgeEvidence.edge_id == Edge.id)
        .where(Edge.from_node_id == quest.node_id, Edge.layer == "source",
               RelationType.key == "references_scenario_flow_state",
               EdgeEvidence.release_id == release_id)
    )) - set(state_ids)
    state_ids = list(set(state_ids) | contextual_state_ids)
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
    runtime_refs = [(block, ref) for block in (tree_order or {}).get('blocks', [])
                    for ref in block['media']]
    runtime_refs.extend(({**block, 'source': item['source'],
                          'interaction_id': block['id'] + ':' + item['id']}, ref)
                        for block in (tree_order or {}).get('blocks', [])
                        for item in block.get('interactions', []) for ref in item.get('media', []))
    runtime_nodes = dict(await session.execute(select(Node.canonical_key, Node.id).where(
        Node.canonical_key.in_({ref['reference'] for _, ref in runtime_refs}),
        select(NodeRevision.id).where(NodeRevision.node_id == Node.id,
                                      NodeRevision.release_id == release_id).exists()))) if runtime_refs else {}
    cutscene_ids = [link["node_id"] for entries in action_links.values() for link in entries
                    if link["relation"] == "plays_cutscene"]
    cutscene_ids = list(set(cutscene_ids) | {runtime_nodes[ref['reference']] for _, ref in runtime_refs
                                          if ref['kind'] == 'cutscene' and ref['reference'] in runtime_nodes})
    flows = await cutscene_flows(session, cutscene_ids)
    explanation = await session.scalar(
        select(Document)
        .join(DocumentHead, DocumentHead.document_id == Document.id)
        .join(Locale, Locale.id == Document.locale_id)
        .where(
            Document.node_id == quest.node_id,
            Document.document_type == f"story-explanation:{release_id}",
        )
        .order_by(
            (Locale.code == locale).desc(), (Locale.code == "en").desc(), Document.revision.desc()
        )
        .limit(1)
    )
    if explanation and explanation.metadata_json.get("cutscene_descriptions") and (
        explanation.source_hash == await quest_fingerprint(
            session, quest, release_id, include_visual=True)
    ):
        for description in explanation.metadata_json.get("cutscene_descriptions", []):
            for flow in flows.values():
                for media in flow["media"].values():
                    if media.get("asset_node_id") == description["asset_node_id"]:
                        report = await visual_reference(
                            session, description["asset_node_id"], media["asset_version"]
                        )
                        if report and report.id == description["visual_reference_id"]:
                            media["description"] = description
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
    sources = []
    for action, state, action_key in action_rows:
        for link in action_links.get(action.node_id, []):
            if state.node_id in contextual_state_ids and link["relation"] not in ("plays_cutscene", "plays_sequence_asset"):
                continue
            entry = {
                "kind": {"plays_cutscene": "cutscene", "plays_sequence_asset": "sequence",
                         "posts_audio_event": "audio_event"}[link["relation"]],
                "action": action_key, "flow_state": state.state_key,
                "action_index": action.action_index, "reference": link["canonical_key"],
                "context_inferred": state.node_id in contextual_state_ids,
                "story_order_mapped": bool(tree_order and state.state_key in tree_order['mapped_flow_states']),
                "basis": link["basis"], "source": link["source"],
                "engine_path": link["canonical_key"].removeprefix("asset:ue:")
                if link["canonical_key"].startswith("asset:ue:") else None,
                "reference_kind": "unreal_asset_path"
                if link["canonical_key"].startswith("asset:ue:") else "cg_name",
            }
            position = (tree_order or {}).get('positions', {}).get(state.state_key)
            if position:
                entry['story_position'] = [*position, action.action_index, -1]
            sources.append((entry, link['node_id']))
    for block, ref in runtime_refs:
        entry = {
            'kind': ref['kind'], 'action': block['id'] + ref['raw_path'],
            'flow_state': block['owner'], 'action_index': ref.get('position', [0])[-1],
            'reference': ref['reference'], 'story_block_id': block['id'],
            'story_order_mapped': 'position' in ref,
            'context_inferred': False, 'branches': block['branches'],
            'transition': ref.get('transition', False), 'basis': ref['basis'],
            'source': {**block['source'], 'raw_path': ref['raw_path']},
            'engine_path': ref['reference'].removeprefix('asset:ue:') if ref['kind'] == 'sequence' else None,
            'reference_kind': 'unreal_asset_path' if ref['kind'] == 'sequence' else 'cg_name',
        }
        if 'position' in ref:
            entry['story_position'] = ref['position']
        if block.get('interaction_id'):
            entry['interaction_id'] = block['interaction_id']
            entry['action'] += ':' + block['interaction_id']
        sources.append((entry, runtime_nodes.get(ref['reference'])))
    events = []
    for entry, target_id in sources:
        if entry["kind"] == "cutscene":
            entry["playback"] = flows.get(target_id)
            entry["transcript_states"] = sorted(set(transcript_states[target_id]))
            # Timing schema is verified against the current client, not assumed for old builds.
            entry["captions"] = caption_tracks([
                resource_raw[child["node_id"]]
                for child in variants.get(target_id, [])
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
                for child in variants.get(target_id, [])
            ]
        events.append(entry)
    package_links = await _links(
        session, [quest.node_id], ("references_video_package",), release_id
    )
    package_assets = await _links(
        session, [link["node_id"] for link in package_links.get(quest.node_id, [])],
        ("references_asset",), release_id,
    )
    package_scenes = await _links(session, [quest.node_id], ("references_packaged_cutscene",), release_id)
    related = {link["node_id"]: link for link in package_scenes.get(quest.node_id, [])}
    package_flows = await cutscene_flows(session, list(related)) if related else {}
    return {
        "quest": f"quest:{game_quest_id}",
        "game_version": (await session.get(GameRelease, release_id)).game_version,
        "availability": "partial" if playable else "references_only",
        "events": events,
        "related_cutscenes": [{"name": link["canonical_key"].removeprefix("cutscene:"),
                               "playable": node_id in package_flows,
                               "playback": package_flows.get(node_id),
                               "context_inferred": True}
                              for node_id, link in related.items()
                              if not any(event['reference'] == link['canonical_key'] for event in events)],
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

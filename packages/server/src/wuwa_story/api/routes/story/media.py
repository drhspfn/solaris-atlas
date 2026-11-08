"""Exact game-resource references for quest media extraction."""

import re
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select
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
from wuwa_story.ingestion.spine_presentation import flow_presentation
from wuwa_story.storage.s3 import S3Storage
from wuwa_story.story import media as story_media
from wuwa_story.story import runtime_service
from wuwa_story.story.cutscene_descriptions import attach_public_descriptions
from wuwa_story.story.cutscene_occurrences import group_cutscene_occurrences
from wuwa_story.story.cutscene_publications import publication_order, publication_types
from wuwa_story.story.scene_continuations import scene_continuations
from wuwa_story.story.service import _quest_page_state_ids
from wuwa_story.story.tree_order import authored_order

router = APIRouter(tags=["story browsing"])


def verified_flow_music(media, presentation, owner, release_id):
    """Expose only published music bound to this exact authored ShowTalk."""
    client = media.get('game_source') or {}
    actions = (presentation.get('source') or {}).get('Actions') or []
    matches = []
    for section in media.get('sections', []):
        proof = section.get('source_proof') if isinstance(section, dict) else None
        if (not isinstance(proof, dict) or section.get('kind') != 'audio_event'
                or section.get('scope') != 'flow' or section.get('reconstruction_kind') not in {'flow_authored_music', 'state_consumer_fixed_start'}
                or proof.get('flow_owner') != owner or proof.get('release_id') != release_id
                or proof.get('presentation_sha256') != presentation.get('sha256')
                or proof.get('flow_state') != (presentation.get('source') or {}).get('StateKey')
                or any(not isinstance(client.get(k), str) or not client[k] or proof.get(k) != client[k]
                       for k in ('asset_version', 'download_id'))):
            continue
        if section.get('reconstruction_kind') == 'state_consumer_fixed_start' and (
                section.get('confidence') != 'reconstructed' or proof.get('confidence') != 'reconstructed'
                or proof.get('runtime_start_confirmed') is not False
                or proof.get('state_event') != section.get('reference')
                or not isinstance(proof.get('consumer_event'), str)):
            continue
        paths = [re.fullmatch(r'\$\.Actions\[(\d+)\]', str(proof.get(key, '')))
                 for key in ('play_path', 'talk_path', 'stop_path')]
        if not all(paths):
            continue
        start, talk, stop = [int(path[1]) for path in paths]
        if not 0 <= start < talk < stop < len(actions):
            continue
        if ([i for i, action in enumerate(actions) if isinstance(action, dict) and action.get('Name') == 'PostAkEvent']
                != [start, stop] or not any(cue.get('kind') == 'background'
                    and cue.get('background', {}).get('kind') in {'spine', 'image'}
                    and cue.get('source_path', '').startswith(f'$.Actions[{talk}].Params.TalkItems[')
                    for cue in presentation.get('cues', []))):
            continue
        play_action, talk_action, stop_action = (actions[i] for i in (start, talk, stop))
        if any(not isinstance(action, dict) for action in (play_action, talk_action, stop_action)):
            continue
        def event(action):
            config = (action.get('Params') or {}).get('EventConfig') or {}
            return config.get('AkEvent') if action.get('Name') == 'PostAkEvent' and config.get('Type') == 'Global' else None
        items = (talk_action.get('Params') or {}).get('TalkItems') or []
        keys = [item.get('TidTalk') for item in items if isinstance(item, dict)]
        if (talk_action.get('Name') != 'ShowTalk' or event(play_action) != section.get('reference')
                or not event(stop_action) or not keys or keys != proof.get('talk_text_keys')
                or any(not isinstance(key, str) or not key for key in keys) or len(set(keys)) != len(keys)):
            continue
        tracks = [track for track in section.get('tracks', []) if isinstance(track, dict)
                  and track.get('role') == 'music' and track.get('status') == 'published'
                  and isinstance(track.get('url'), str) and track['url']]
        if tracks:
            matches.append(({**section, 'tracks': tracks}, keys))
    return matches[0] if len(matches) == 1 else None


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


async def cutscene_videos(
    session: AsyncSession, asset_ids: list[int], asset_version: str | None = None,
    *, cutscene_key: str | None = None,
) -> dict[int, dict]:
    """Published playable files for exact authored asset identities, across text snapshots."""
    if not asset_ids:
        return {}
    settings = get_settings()
    storage = S3Storage(settings)
    rows = await session.execute(
        select(FileReference, FileLocation.object_key)
        .join(FileLocation, FileLocation.file_id == FileReference.file_id)
        .where(FileReference.owner_node_id.in_(asset_ids),
               FileReference.reference_type.in_(
                   publication_types("cutscene_video")),
               FileLocation.backend == "s3", FileLocation.bucket == settings.s3_bucket,
               FileLocation.available.is_(True), FileLocation.is_primary.is_(True))
        .where(FileReference.metadata_json["asset_version"].astext == asset_version if asset_version else True)
        .where(or_(FileReference.reference_type == "cutscene_video",
                   FileReference.metadata_json["cutscene_key"].astext == cutscene_key)
               if cutscene_key is not None else True)
        .order_by(publication_order(FileReference.reference_type), FileReference.id.desc())
    )
    videos = {}
    for reference, object_key in rows:
        videos.setdefault(reference.owner_node_id, {
            "asset_node_id": reference.owner_node_id,
            "url": storage.public_url(object_key),
            "asset_version": reference.metadata_json.get("asset_version"),
            "has_audio": reference.metadata_json.get("has_audio", False),
            "duration_seconds": reference.metadata_json.get("duration_seconds"),
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
            FileReference.reference_type.in_(
                publication_types("cutscene_flow")),
        )
        .order_by(publication_order(FileReference.reference_type), FileReference.id.desc())
    )
    selected = {}
    for reference in rows:
        previous = selected.get(reference.owner_node_id)
        # An incomplete newer rebuild must not hide an intact published flow.
        if previous is None or (reference.metadata_json.get("publication_complete") is True
                                and previous.metadata_json.get("publication_complete") is not True):
            selected[reference.owner_node_id] = reference
    result = {}
    for owner_id, reference in selected.items():
        cutscene_key = reference.metadata_json.get(
            "cutscene_key") or getattr(reference, "source_path", None)
        flow = PlaybackFlow.model_validate(reference.metadata_json["flow"])
        keys = {node.asset for node in flow.nodes if isinstance(node, Clip)}
        nodes = list(await session.scalars(select(Node).where(Node.canonical_key.in_(keys))))
        videos = await cutscene_videos(
            session, [
                node.id for node in nodes], reference.metadata_json["asset_version"],
            cutscene_key=cutscene_key,
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
            cutscene_key=cutscene_key,
        )
        for asset in nodes:
            if asset.canonical_key in media and asset.id in bundles:
                media[asset.canonical_key] = {
                    **media[asset.canonical_key],
                    "url": bundles[asset.id]["videos"]["full"],
                    "audio_tracks": bundles[asset.id]["tracks"],
                    "timeline_offset": 0,
                }
        segment_nodes = [node for node in flow.nodes if isinstance(
            node, Clip) and node.segment]
        if segment_nodes:
            settings = get_settings()
            storage = S3Storage(settings)
            segment_rows = await session.execute(
                select(FileReference, FileLocation.object_key)
                .join(FileLocation, FileLocation.file_id == FileReference.file_id)
                .where(
                    FileReference.owner_node_id.in_(
                        [node.id for node in nodes]),
                    FileReference.reference_type.in_(
                        publication_types("cutscene_segment")),
                    FileReference.reference_type == "cutscene_segment"
                    if reference.reference_type == "cutscene_flow"
                    and reference.metadata_json.get("publication_complete") is True else True,
                    or_(FileReference.reference_type == "cutscene_segment",
                        FileReference.metadata_json["cutscene_key"].astext == cutscene_key)
                    if cutscene_key is not None else True,
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
                .order_by(publication_order(FileReference.reference_type), FileReference.id.desc())
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
        required = {
            node.segment or node.asset for node in flow.nodes if isinstance(node, Clip)}
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
    if read_projections.should_route_to_projection("quest", game_version, locale=locale):
        from copy import deepcopy

        manifest = deepcopy(group_cutscene_occurrences(await read_projections.quest_section(
            session, game_quest_id, locale, game_version, "media")))
        release_id = await _release_id(session, manifest.get('game_version') or game_version)
        flows = {i: event.get('playback') for i, event in enumerate(manifest.get('events', []))}
        flows.update({f'related-{i}': item.get('playback') for i, item in enumerate(manifest.get('related_cutscenes', []))})
        await attach_public_descriptions(session, flows, release_id, locale)
        return manifest
    quest = await get_quest(session, game_quest_id)
    if quest is None:
        raise HTTPException(status_code=404, detail="quest not found")
    game_quest_id = quest.game_quest_id
    release_id = await _release_id(session, game_version)
    if release_id is None:
        raise HTTPException(
            status_code=404, detail="game version not imported")
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
    runtime_nodes = dict((await session.execute(select(Node.canonical_key, Node.id).where(
        Node.canonical_key.in_({ref['reference'] for _, ref in runtime_refs}),
        select(NodeRevision.id).where(NodeRevision.node_id == Node.id,
                                      NodeRevision.release_id == release_id).exists()))).all()) if runtime_refs else {}
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
            (Locale.code == locale).desc(), (Locale.code ==
                                             "en").desc(), Document.revision.desc()
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
    await attach_public_descriptions(session, flows, release_id, locale)
    variants = await _links(session, cutscene_ids, ("has_variant", "uses_audio_event", "uses_audio_event_normalized", "uses_caption"), release_id)
    child_ids = [link["node_id"] for entries in variants.values()
                 for link in entries]
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
                "_sequence_owner": state.node_id,
                "action_index": action.action_index, "reference": link["canonical_key"],
                "context_inferred": state.node_id in contextual_state_ids,
                "story_order_mapped": bool(tree_order and state.state_key in tree_order['mapped_flow_states']),
                "basis": link["basis"], "source": link["source"],
                "engine_path": link["canonical_key"].removeprefix("asset:ue:")
                if link["canonical_key"].startswith("asset:ue:") else None,
                "reference_kind": "unreal_asset_path"
                if link["canonical_key"].startswith("asset:ue:") else "cg_name",
            }
            position = (tree_order or {}).get(
                'positions', {}).get(state.state_key)
            if position:
                entry['story_position'] = [*position, action.action_index, -1]
            review_position = (tree_order or {}).get(
                'review_positions', {}).get(state.state_key)
            if review_position is not None:
                entry['review_position'] = [
                    *review_position, action.action_index, -1]
                entry['review_order_source'] = tree_order['review_source']
            sources.append((entry, link['node_id']))
    state_owners = defaultdict(set)
    for _, state, _ in action_rows:
        state_owners[state.state_key].add(state.node_id)
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
        source = block.get('source') or {}
        owners = state_owners.get(source.get('key'), set())
        if (ref['kind'] == 'sequence' and source.get('file') == 'BinData/flowState/flowstate.json'
                and len(owners) == 1):
            entry['_sequence_owner'] = next(iter(owners))
            entry['_sequence_cue_path'] = ref['raw_path']
        sources.append((entry, runtime_nodes.get(ref['reference'])))
    sequence_owners = {entry['_sequence_owner'] for entry, _ in sources
                       if entry['kind'] in {'sequence', 'audio_event'} and '_sequence_owner' in entry}
    presentations = {}
    if sequence_owners:
        rows = await session.execute(
            select(NodeRevision.node_id, SourceRecord.data)
            .join(SourceRecord, SourceRecord.id == NodeRevision.source_record_id)
            .where(NodeRevision.node_id.in_(sequence_owners), NodeRevision.release_id == release_id,
                   SourceRecord.release_id == release_id)
            .order_by(NodeRevision.revision.desc()))
        for owner, raw in rows:
            if owner not in presentations and isinstance(raw, dict):
                presentations[owner] = flow_presentation(raw)
    sequence_media = await runtime_service.sequence_playback(
        session, [], release_id, {owner: value['sha256'] for owner, value in presentations.items()}) if presentations else {}
    events = []
    for entry, target_id in sources:
        owner = entry.pop('_sequence_owner', None)
        cue_path = entry.pop('_sequence_cue_path', None)
        if entry['kind'] == 'sequence' and owner in sequence_media:
            media = sequence_media[owner]
            asset = entry.get('engine_path')
            cues = [cue for cue in presentations[owner]['cues'] if cue['kind'] == 'sequence'
                    and cue['asset'] == asset and (cue_path is None or cue.get('source_path') == cue_path)]
            asset_bindings = media.get('sequence_assets')
            bindings = [item for item in (asset_bindings if isinstance(asset_bindings, list) else []) if isinstance(item, dict)
                        and item.get('asset') == asset and isinstance(item.get('scenes'), list)
                        and item['scenes'] and all(isinstance(scene, str) and scene for scene in item['scenes'])]
            media_scenes = media.get('scenes')
            source_scenes = [scene['source'] for scene in (media_scenes if isinstance(media_scenes, list) else [])
                             if isinstance(scene, dict) and isinstance(scene.get('source'), str) and scene['source']]
            all_cues = [cue for cue in presentations[owner]
                        ['cues'] if cue['kind'] == 'sequence']
            if not bindings and len(all_cues) == 1 and len(cues) == 1 and len(source_scenes) == 1:
                bindings = [{'asset': asset, 'scenes': source_scenes}]

            def flow_music(section, flow_owner=owner, digest=presentations[owner]['sha256'],
                           client=media.get('game_source') or {}):
                proof = section.get('source_proof')
                return (section.get('kind') == 'audio_event' and section.get('scope') == 'flow'
                        and section.get('reconstruction_kind') == 'levelplay_fixed_start'
                        and isinstance(proof, dict) and proof.get('flow_owner') == flow_owner
                        and proof.get('presentation_sha256') == digest and proof.get('release_id') == release_id
                        and all(isinstance(client.get(key), str) and client[key] and proof.get(key) == client[key]
                                for key in ('asset_version', 'download_id')))
            if len(cues) == 1 and len(bindings) == 1:
                scenes = set(bindings[0]['scenes'])
                entry['sequence_media'] = {**media, 'sequence_assets': bindings,
                                           'sections': [section for section in media['sections']
                                 if (flow_music(section) if section.get('scope') == 'flow'
                                     else section.get('scene_source') in scenes)],
                                           'scenes': [scene for scene in (media_scenes if isinstance(media_scenes, list) else [])
                                                      if isinstance(scene, dict) and scene.get('source') in scenes]}
            elif cues or entry.get('story_block_id'):
                composition = media.get('composition') or {
                    'schema_version': 1, 'reasons': []}
                entry['sequence_media'] = {**media, 'sequence_assets': [],
                                           'sections': [section for section in media['sections'] if cues and flow_music(section)], 'scenes': [],
                                           'composition': {**composition, 'state': 'unsupported',
                                                           'reasons': [*composition.get('reasons', []), {'code': 'scene_binding_unavailable'}]}}
        elif entry['kind'] == 'sequence' and entry.get('story_block_id'):
            # Runtime actions are sourced from quest/level nodes, not a flow
            # state. Asset equality alone does not prove a flow ownership link.
            entry['sequence_media'] = {'schema_version': 1, 'complete_video': False, 'sections': [],
                                       'composition': {'schema_version': 1, 'state': 'unsupported',
                                                       'reasons': [{'code': 'flow_owner_unavailable'}]}}
        if entry["kind"] == "cutscene":
            entry["playback"] = flows.get(target_id)
            entry["transcript_states"] = sorted(
                set(transcript_states[target_id]))
            # Timing schema is verified against the current client, not assumed for old builds.
            entry["captions"] = caption_tracks([
                resource_raw[child["node_id"]]
                for child in variants.get(target_id, [])
                if child["relation"] == "uses_caption" and child["node_id"] in resource_raw
            ], texts) if entry["playback"] and entry["playback"]["asset_version"] in {"3.7.0", "3.7.1"} else {}
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
        if entry.get('kind') == 'audio_event' and owner in sequence_media:
            media = sequence_media[owner]
            verified = verified_flow_music(media, presentations[owner], owner, release_id)
            if verified:
                section, keys = verified
                if (section['source_proof']['play_path'] == f"$.Actions[{entry['action_index']}]"
                        and section.get('reference') == entry.get('engine_path')):
                    events.append({'kind': 'flow_music', 'flow_state': entry['flow_state'],
                                   'covered_text_keys': keys,
                                   'sequence_media': {**media, 'sections': [section]}})
    package_links = await _links(
        session, [quest.node_id], ("references_video_package",), release_id
    )
    package_assets = await _links(
        session, [link["node_id"]
                  for link in package_links.get(quest.node_id, [])],
        ("references_asset",), release_id,
    )
    package_scenes = await _links(session, [quest.node_id], ("references_packaged_cutscene",), release_id)
    related = {link["node_id"]
        : link for link in package_scenes.get(quest.node_id, [])}
    package_flows = await cutscene_flows(session, list(related)) if related else {}
    continuations = await scene_continuations(session, list(related), quest.node_id, release_id, locale)
    return group_cutscene_occurrences({
        "quest": f"quest:{game_quest_id}",
        "game_version": (await session.get(GameRelease, release_id)).game_version,
        "availability": "partial" if playable else "references_only",
        "events": events,
        "related_cutscenes": [{"name": link["canonical_key"].removeprefix("cutscene:"),
                               "playable": node_id in package_flows,
                               "playback": package_flows.get(node_id),
                               "continuations": continuations.get(node_id, []),
                               "context_inferred": True}
                              for node_id, link in related.items()
                              if not any(event.get('reference') == link['canonical_key'] for event in events)],
        "video_packages": [
            {"reference": link["canonical_key"], "basis": link["basis"],
             "source": link["source"],
             "packages": [{"reference": asset["canonical_key"],
                           "reference_kind": "video_package_identity",
                           "basis": asset["basis"], "source": asset["source"]}
                          for asset in package_assets.get(link["node_id"], [])]}
            for link in package_links.get(quest.node_id, [])
        ],
    })

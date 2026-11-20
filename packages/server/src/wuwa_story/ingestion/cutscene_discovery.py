"""Discover and register non-VideoData cutscenes (Activity MP4s and hybrid LevelA sequences)."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterable
from types import SimpleNamespace

from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert

from wuwa_story.db.models.core import Cutscene, DialogueLine, QuestAction, QuestState
from wuwa_story.db.models.graph import Edge, EdgeEvidence, Node, NodeRevision, NodeType
from wuwa_story.db.models.ontology import RelationType
from wuwa_story.db.models.ops import GameRelease
from wuwa_story.db.models.raw import SourceFile, SourceRecord
from wuwa_story.db.models.storage import FileLocation, FileObject, FileReference
from wuwa_story.db.session import SessionFactory
from wuwa_story.ingestion.canonical_import import import_canonical_edges
from wuwa_story.ingestion.cutscene_order import rebuild as rebuild_cutscene_order
from wuwa_story.ingestion.dungeon_backfill import invalidate_repaired_quests

ROLETOWER_SOURCE = "BinData/RoleTower/roletowerlevel.json"
_LEVELA_PREFIX = "/Game/Aki/Sequence/LevelA_Seq/"
_SAFE_CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
WRITE_BATCH_SIZE = 1000


def levela_sequence_code(engine_path: str) -> str | None:
    """Extract the scene code from a LevelA SequenceDataAsset UE path."""
    if not isinstance(engine_path, str):
        return None
    path = engine_path.removeprefix("asset:ue:")
    if not path.startswith(_LEVELA_PREFIX):
        return None
    leaf = path.rsplit("/", 1)[-1].split(".", 1)[0]
    code = leaf[3:] if leaf.startswith("DA_") else leaf
    if not code or not _SAFE_CODE.fullmatch(code):
        return None
    return code


async def authored_cutscene_assets(
    session, release_id: int, names: Iterable[str]
) -> dict[str, list[str]]:
    """Return `cutscene:<name> -> has_variant -> references_asset` asset keys in one snapshot."""
    target_keys = sorted({f"cutscene:{name}" for name in names if isinstance(name, str) and name})
    if not target_keys:
        return {}
    rows = await session.execute(
        select(Node.canonical_key, Edge.to_node_id)
        .join(Edge, Edge.from_node_id == Node.id)
        .join(RelationType, RelationType.id == Edge.relation_type_id)
        .join(EdgeEvidence, EdgeEvidence.edge_id == Edge.id)
        .where(
            Node.canonical_key.in_(target_keys),
            RelationType.key == "has_variant",
            Edge.layer == "source",
            EdgeEvidence.release_id == release_id,
        )
    )
    variants_by_scene: dict[str, set[int]] = defaultdict(set)
    variant_ids: set[int] = set()
    for scene_key, variant_id in rows:
        variants_by_scene[scene_key.removeprefix("cutscene:")].add(variant_id)
        variant_ids.add(variant_id)
    if not variant_ids:
        return {}
    asset_rows = await session.execute(
        select(Edge.from_node_id, Node.canonical_key)
        .join(Node, Node.id == Edge.to_node_id)
        .join(RelationType, RelationType.id == Edge.relation_type_id)
        .join(EdgeEvidence, EdgeEvidence.edge_id == Edge.id)
        .where(
            Edge.from_node_id.in_(variant_ids),
            RelationType.key == "references_asset",
            Edge.layer == "source",
            EdgeEvidence.release_id == release_id,
        )
    )
    assets_by_variant: dict[int, list[str]] = defaultdict(list)
    for variant_id, asset_key in asset_rows:
        assets_by_variant[variant_id].append(asset_key)
    result: dict[str, list[str]] = {}
    for name, v_ids in variants_by_scene.items():
        collected = sorted({key for v_id in v_ids for key in assets_by_variant.get(v_id, [])})
        if collected:
            result[name] = collected
    return result


async def levela_sequence_candidates(session, release_id: int) -> dict[str, list[dict]]:
    """Return LevelA_Seq assets referenced in a snapshot keyed by exact scene code."""
    seq_nodes = list(
        await session.execute(
            select(Node.id, Node.canonical_key)
            .join(NodeRevision, NodeRevision.node_id == Node.id)
            .where(
                NodeRevision.release_id == release_id,
                Node.canonical_key.startswith(f"asset:ue:{_LEVELA_PREFIX}"),
            )
            .distinct()
        )
    )
    if not seq_nodes:
        return {}
    node_ids = [node_id for node_id, _ in seq_nodes]
    edge_rows = list(
        await session.execute(
            select(
                Edge.to_node_id,
                Node.id,
                Node.canonical_key,
                EdgeEvidence.source_file_path,
                EdgeEvidence.source_raw_path,
            )
            .join(RelationType, RelationType.id == Edge.relation_type_id)
            .join(Node, Node.id == Edge.from_node_id)
            .join(EdgeEvidence, EdgeEvidence.edge_id == Edge.id)
            .where(
                Edge.to_node_id.in_(node_ids),
                RelationType.key == "plays_sequence_asset",
                Edge.layer == "source",
                EdgeEvidence.release_id == release_id,
            )
        )
    )
    refs_by_target: dict[int, list[dict]] = defaultdict(list)
    for target_id, owner_id, owner_key, source_file, raw_path in edge_rows:
        refs_by_target[target_id].append(
            {
                "owner_id": owner_id,
                "owner_key": owner_key,
                "source_file": source_file,
                "raw_path": raw_path,
            }
        )
    candidates: dict[str, list[dict]] = defaultdict(list)
    for node_id, key in seq_nodes:
        code = levela_sequence_code(key)
        if not code:
            continue
        for ref in refs_by_target.get(node_id, []):
            candidates[code].append({"sequence_asset": key, **ref})
        if not refs_by_target.get(node_id):
            candidates.setdefault(code, []).append({"sequence_asset": key})
    return dict(candidates)


async def _ensure_nodes_and_revisions(
    session,
    release_id: int,
    node_specs: list[dict],
    node_types: dict[str, int],
) -> dict[str, int]:
    """Idempotently create missing graph.node, graph.node_revision, and core.cutscene rows."""
    if not node_specs:
        return {}
    by_key = {spec["canonical_key"]: spec for spec in node_specs}
    insert_nodes = [
        {
            "canonical_key": key,
            "type_id": node_types[spec["type"]],
            "created_release_id": release_id,
            "metadata": spec.get("node_metadata") or {},
        }
        for key, spec in sorted(by_key.items())
    ]
    for start in range(0, len(insert_nodes), WRITE_BATCH_SIZE):
        await session.execute(
            insert(Node.__table__)
            .values(insert_nodes[start : start + WRITE_BATCH_SIZE])
            .on_conflict_do_nothing(index_elements=[Node.canonical_key])
        )
    existing = list(
        await session.execute(
            select(Node.canonical_key, Node.id)
            .where(Node.canonical_key.in_(by_key))
            .order_by(Node.canonical_key)
            .with_for_update()
        )
    )
    ids = {key: node_id for key, node_id in existing}
    revised = set(
        await session.scalars(
            select(NodeRevision.node_id).where(
                NodeRevision.release_id == release_id,
                NodeRevision.node_id.in_(ids.values()),
            )
        )
    )
    max_revs = {
        node_id: rev
        for node_id, rev in await session.execute(
            select(NodeRevision.node_id, func.max(NodeRevision.revision))
            .where(NodeRevision.node_id.in_(ids.values()))
            .group_by(NodeRevision.node_id)
        )
    }
    rev_values = [
        {
            "node_id": ids[key],
            "release_id": release_id,
            "revision": max_revs.get(ids[key], 0) + 1,
            "source_record_id": spec.get("source_record_id"),
            "content_hash": hashlib.sha256(key.encode("utf-8")).digest(),
            "metadata": spec.get("revision_metadata") or {},
        }
        for key, spec in sorted(by_key.items())
        if ids[key] not in revised
    ]
    for start in range(0, len(rev_values), WRITE_BATCH_SIZE):
        await session.execute(
            insert(NodeRevision.__table__).values(rev_values[start : start + WRITE_BATCH_SIZE])
        )
    cutscene_values = [
        {
            "node_id": ids[key],
            "cg_name": spec["cg_name"],
            "metadata": spec.get("cutscene_metadata") or {},
        }
        for key, spec in sorted(by_key.items())
        if spec["type"] == "cutscene"
    ]
    for start in range(0, len(cutscene_values), WRITE_BATCH_SIZE):
        await session.execute(
            insert(Cutscene.__table__)
            .values(cutscene_values[start : start + WRITE_BATCH_SIZE])
            .on_conflict_do_nothing(index_elements=[Cutscene.node_id])
        )
    return ids


async def _node_types(session) -> dict[str, int]:
    keys = ("cutscene", "cutscene_variant", "asset_reference")
    found = {
        key: type_id
        for key, type_id in await session.execute(
            select(NodeType.key, NodeType.id).where(NodeType.key.in_(keys))
        )
    }
    missing = set(keys) - found.keys()
    if missing:
        raise ValueError(f"Graph node types not seeded: {sorted(missing)}")
    return found


async def _register_records(
    session,
    release: GameRelease,
    node_specs: list[dict],
    edge_records: list[dict],
) -> dict[str, int]:
    types = await _node_types(session)
    ids = await _ensure_nodes_and_revisions(session, release.id, node_specs, types)
    full_edges = [
        {
            **record,
            "version": release.game_version,
            "source_repository": release.upstream_name,
            "source_commit": release.upstream_commit,
        }
        for record in edge_records
    ]
    await invalidate_repaired_quests(session, release, full_edges)
    for start in range(0, len(full_edges), WRITE_BATCH_SIZE):
        batch = full_edges[start : start + WRITE_BATCH_SIZE]
        if start:
            await invalidate_repaired_quests(session, release, batch)
        await import_canonical_edges(
            SimpleNamespace(iter_edges=lambda b=batch: iter(b)),
            session,
            release.id,
            batch_size=WRITE_BATCH_SIZE,
        )
    await rebuild_cutscene_order(session, release.id)
    await session.commit()
    return ids


async def roletower_showcase_videos(
    session, release_id: int, *, max_rows: int = 1000
) -> list[dict]:
    """Return character showcase video entries from BinData/RoleTower/roletowerlevel.json."""
    if type(release_id) is not int or release_id <= 0:
        raise ValueError("A positive imported release ID is required")
    if type(max_rows) is not int or not 1 <= max_rows <= 10000:
        raise ValueError("max_rows must be between 1 and 10000")
    rows = list(
        await session.execute(
            select(SourceRecord.id, SourceRecord.row_index, SourceRecord.data)
            .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
            .where(
                SourceRecord.release_id == release_id,
                SourceFile.release_id == release_id,
                SourceFile.logical_source_path == ROLETOWER_SOURCE,
            )
            .order_by(SourceRecord.row_index)
            .limit(max_rows + 1)
        )
    )
    if len(rows) > max_rows:
        raise ValueError("RoleTower rows exceed max_rows bound")
    entries: list[dict] = []
    for source_id, row_index, raw in rows:
        if not isinstance(raw, dict):
            continue
        video_path = raw.get("VideoPath")
        main_role_id = raw.get("MainRoleId")
        if not isinstance(video_path, str) or not video_path.startswith("/Game/"):
            continue
        if not isinstance(main_role_id, int) or isinstance(main_role_id, bool):
            continue
        name = video_path.rsplit("/", 1)[-1].split(".", 1)[0]
        if not name:
            continue
        entries.append(
            {
                "character_key": f"character:{main_role_id}",
                "main_role_id": main_role_id,
                "roletower_level_id": raw.get("Id"),
                "name": name,
                "video_path": video_path,
                "video_name_key": raw.get("VideoName"),
                "source_record_id": source_id,
                "row_index": row_index,
            }
        )
    return entries


async def discover_bindata_cutscenes(
    session, release_id: int, *, apply: bool = False, max_rows: int = 1000
) -> dict:
    """RoleTower videos are character showcase media, not story cutscenes."""
    if type(release_id) is not int or release_id <= 0:
        raise ValueError("A positive imported release ID is required")
    if type(max_rows) is not int or not 1 <= max_rows <= 10000:
        raise ValueError("max_rows must be between 1 and 10000")
    release = await session.get(GameRelease, release_id)
    if release is None:
        raise ValueError("Unknown imported release")
    return {
        "release_id": release_id,
        "game_version": release.game_version,
        "apply": apply,
        "source_rows": 0,
        "cutscene_names": [],
        "missing_nodes": 0,
        "edge_candidates": 0,
        "written_edges": 0,
    }


async def cleanup_roletower_cutscenes(
    session, *, apply: bool = False, max_rows: int = 1000
) -> dict:
    """Remove RoleTower Charact_* cutscenes and attach showcase MP4s to their characters."""
    if type(max_rows) is not int or not 1 <= max_rows <= 10000:
        raise ValueError("max_rows must be between 1 and 10000")
    await session.execute(text("SET LOCAL lock_timeout = '2s'"))
    await session.execute(text("SET LOCAL statement_timeout = '30s'"))
    if apply:
        await session.execute(text("SELECT pg_advisory_xact_lock(708, 0)"))

    raw_rows = list(
        await session.execute(
            select(
                SourceRecord.id,
                SourceRecord.release_id,
                SourceRecord.row_index,
                SourceRecord.data,
            )
            .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
            .where(SourceFile.logical_source_path == ROLETOWER_SOURCE)
            .order_by(SourceFile.release_id.desc(), SourceRecord.row_index.asc())
            .limit(max_rows + 1)
        )
    )
    if len(raw_rows) > max_rows:
        raise ValueError("RoleTower rows exceed max_rows bound")

    entries_by_pair: dict[tuple[int, str], dict] = {}
    roletower_names: set[str] = set()
    for source_id, rel_id, row_index, raw in raw_rows:
        if not isinstance(raw, dict):
            continue
        video_path = raw.get("VideoPath")
        main_role_id = raw.get("MainRoleId")
        if not isinstance(video_path, str) or not video_path.startswith("/Game/"):
            continue
        name = video_path.rsplit("/", 1)[-1].split(".", 1)[0]
        if name:
            roletower_names.add(name)
        if not isinstance(main_role_id, int) or isinstance(main_role_id, bool) or not name:
            continue
        entries_by_pair.setdefault(
            (main_role_id, video_path),
            {
                "character_key": f"character:{main_role_id}",
                "main_role_id": main_role_id,
                "roletower_level_id": raw.get("Id"),
                "name": name,
                "video_path": video_path,
                "video_name_key": raw.get("VideoName"),
                "source_record_id": source_id,
                "release_id": rel_id,
                "row_index": row_index,
            },
        )

    attached_count = 0
    already_attached_count = 0
    missing_video_files: list[str] = []
    dirty_char_ids: set[int] = set()
    dirty_release_ids: set[int] = set()

    for entry in entries_by_pair.values():
        char_node_id = await session.scalar(
            select(Node.id).where(Node.canonical_key == entry["character_key"])
        )
        if char_node_id is None:
            continue
        existing_ref = await session.scalar(
            select(FileReference.id)
            .where(
                FileReference.owner_node_id == char_node_id,
                FileReference.reference_type == "character_showcase_video",
                FileReference.source_path == entry["video_path"],
            )
            .limit(1)
        )
        if existing_ref is not None:
            already_attached_count += 1
            continue
        file_id = await session.scalar(
            select(FileReference.file_id)
            .join(FileObject, FileObject.id == FileReference.file_id)
            .join(FileLocation, FileLocation.file_id == FileObject.id)
            .where(
                FileReference.source_path == entry["video_path"],
                FileObject.mime_type == "video/mp4",
                FileLocation.available.is_(True),
            )
            .order_by(FileReference.id.desc())
            .limit(1)
        )
        if file_id is None:
            missing_video_files.append(entry["video_path"])
            continue
        attached_count += 1
        if apply:
            dirty_char_ids.add(char_node_id)
            dirty_release_ids.add(entry["release_id"])
            session.add(
                FileReference(
                    release_id=entry["release_id"],
                    owner_node_id=char_node_id,
                    file_id=file_id,
                    reference_type="character_showcase_video",
                    source_name=entry["video_name_key"],
                    source_path=entry["video_path"],
                    source_record_id=entry["source_record_id"],
                    metadata_json={
                        "roletower_level_id": entry["roletower_level_id"],
                        "video_name_key": entry["video_name_key"],
                        "name": entry["name"],
                    },
                )
            )

    cutscene_filter = Cutscene.metadata_json["resolution_basis"].astext == "RoleTowerLevel.VideoPath"
    if roletower_names:
        cutscene_filter = or_(
            cutscene_filter,
            Node.canonical_key.in_(sorted(f"cutscene:{name}" for name in roletower_names)),
        )
    cutscene_rows = list(
        await session.execute(
            select(Node.id, Node.canonical_key)
            .join(Cutscene, Cutscene.node_id == Node.id)
            .where(cutscene_filter)
            .order_by(Node.id)
        )
    )
    variant_rows = list(
        await session.execute(
            select(Node.id, Node.canonical_key)
            .where(Node.canonical_key.like("cutscene_variant:roletower:%"))
            .order_by(Node.id)
        )
    )

    cutscene_ids = [node_id for node_id, _ in cutscene_rows]
    variant_ids = [node_id for node_id, _ in variant_rows]
    remove_ids = sorted(set(cutscene_ids) | set(variant_ids))

    if apply and remove_ids:
        affected_releases = list(
            await session.scalars(
                select(NodeRevision.release_id)
                .where(NodeRevision.node_id.in_(cutscene_ids))
                .distinct()
            )
        ) if cutscene_ids else []
        await session.execute(
            delete(FileReference).where(FileReference.owner_node_id.in_(remove_ids))
        )
        await session.execute(
            delete(Edge).where(
                or_(Edge.from_node_id.in_(remove_ids), Edge.to_node_id.in_(remove_ids))
            )
        )
        if cutscene_ids:
            await session.execute(delete(Cutscene).where(Cutscene.node_id.in_(cutscene_ids)))
        await session.execute(delete(Node).where(Node.id.in_(remove_ids)))
        for rel_id in affected_releases:
            await rebuild_cutscene_order(session, rel_id)
    if apply and dirty_char_ids:
        from wuwa_story.read_models.refresh import mark_dirty

        char_ids = sorted(dirty_char_ids)
        await mark_dirty(session, "character", char_ids)
        if dirty_release_ids:
            versions = list(
                await session.execute(
                    select(GameRelease.game_version).where(
                        GameRelease.id.in_(sorted(dirty_release_ids))
                    )
                )
            )
            for (version,) in versions:
                if isinstance(version, str) and version:
                    await mark_dirty(session, "character", char_ids, version)
    if apply and (remove_ids or dirty_char_ids):
        await session.commit()

    return {
        "apply": apply,
        "roletower_entries": len(entries_by_pair),
        "attached_to_characters": attached_count,
        "already_attached": already_attached_count,
        "missing_video_files": sorted(set(missing_video_files)),
        "removed_cutscenes": [key for _, key in cutscene_rows],
        "removed_variants": [key for _, key in variant_rows],
    }


async def register_discovered_cutscenes(
    session,
    release_id: int,
    scenes: list[dict],
    *,
    apply: bool = True,
) -> dict:
    """Register archive-discovered LevelA/Activity cutscenes, variants, and quest transition links."""
    if not scenes:
        return {"release_id": release_id, "registered": [], "edges": 0}
    await session.execute(text("SET LOCAL lock_timeout = '2s'"))
    await session.execute(text("SET LOCAL statement_timeout = '30s'"))
    if apply:
        await session.execute(text("SELECT pg_advisory_xact_lock(708, :id)"), {"id": release_id})
    release = await session.get(GameRelease, release_id)
    if release is None:
        raise ValueError("Unknown imported release")

    seq_assets = sorted(
        {
            scene["sequence_asset"]
            for scene in scenes
            if isinstance(scene.get("sequence_asset"), str) and scene["sequence_asset"]
        }
    )
    seq_links_by_asset: dict[str, list[dict]] = defaultdict(list)
    if seq_assets:
        from sqlalchemy.orm import aliased

        target_node = aliased(Node)
        owner_node = aliased(Node)
        action_edges = list(
            await session.execute(
                select(
                    target_node.canonical_key,
                    owner_node.id,
                    owner_node.canonical_key,
                    EdgeEvidence.source_file_path,
                    EdgeEvidence.source_raw_path,
                )
                .select_from(Edge)
                .join(RelationType, RelationType.id == Edge.relation_type_id)
                .join(target_node, target_node.id == Edge.to_node_id)
                .join(owner_node, owner_node.id == Edge.from_node_id)
                .join(EdgeEvidence, EdgeEvidence.edge_id == Edge.id)
                .where(
                    Edge.layer == "source",
                    RelationType.key == "plays_sequence_asset",
                    EdgeEvidence.release_id == release_id,
                    target_node.canonical_key.in_(seq_assets),
                )
            )
        )
        owner_ids = {owner_id for _, owner_id, _, _, _ in action_edges}
        state_by_action: dict[int, tuple[str, int]] = {}
        if owner_ids:
            for action_id, state_id, state_key in await session.execute(
                select(QuestAction.node_id, QuestState.node_id, QuestState.state_key)
                .join(QuestState, QuestState.node_id == QuestAction.quest_state_node_id)
                .where(QuestAction.node_id.in_(owner_ids))
            ):
                state_by_action[action_id] = (state_key, state_id)
            nested_parents = list(
                await session.execute(
                    select(Edge.to_node_id, QuestState.state_key, QuestState.node_id)
                    .join(RelationType, RelationType.id == Edge.relation_type_id)
                    .join(QuestAction, QuestAction.node_id == Edge.from_node_id)
                    .join(QuestState, QuestState.node_id == QuestAction.quest_state_node_id)
                    .where(
                        Edge.layer == "source",
                        RelationType.key == "contains_nested_action",
                        Edge.to_node_id.in_(owner_ids),
                    )
                )
            )
            for nested_id, state_key, state_id in nested_parents:
                state_by_action.setdefault(nested_id, (state_key, state_id))
        state_ids = {state_id for _, state_id in state_by_action.values()}
        states_with_lines = set(
            await session.scalars(
                select(QuestAction.quest_state_node_id)
                .join(DialogueLine, DialogueLine.action_node_id == QuestAction.node_id)
                .where(QuestAction.quest_state_node_id.in_(state_ids))
                .distinct()
            )
        ) if state_ids else set()
        for seq_key, owner_id, owner_key, source_file, raw_path in action_edges:
            state_info = state_by_action.get(owner_id)
            seq_links_by_asset[seq_key].append(
                {
                    "owner_key": owner_key,
                    "source_file": source_file or "BinData/flowState/flowstate.json",
                    "raw_path": raw_path or "$",
                    "state_key": state_info[0] if state_info else None,
                    "has_lines": bool(state_info and state_info[1] in states_with_lines),
                }
            )

    node_specs: list[dict] = []
    edge_records: list[dict] = []
    seen_nodes: set[str] = set()
    registered_names: list[str] = []

    for scene in scenes:
        name = scene["cg_name"]
        variants = scene.get("variants") or []
        if not name or not variants:
            continue
        registered_names.append(name)
        scene_key = f"cutscene:{name}"
        if scene_key not in seen_nodes:
            seen_nodes.add(scene_key)
            node_specs.append(
                {
                    "canonical_key": scene_key,
                    "type": "cutscene",
                    "cg_name": name,
                    "cutscene_metadata": {
                        "resolution": "exact",
                        "resolution_basis": scene.get(
                            "resolution_basis", "LevelA SequenceDataAsset + client archive"
                        ),
                    },
                }
            )
        has_embedded = any(v.get("embedded_fragment") for v in variants)
        for index, variant in enumerate(variants):
            asset_key = variant["asset"]
            source_file = variant.get("source_file") or variant.get("movie_path") or "archive-index"
            raw_path = variant.get("raw_path") or "$"
            variant_key = f"cutscene_variant:discovered:{name}:{index}"
            if variant_key not in seen_nodes:
                seen_nodes.add(variant_key)
                node_specs.append(
                    {
                        "canonical_key": variant_key,
                        "type": "cutscene_variant",
                        "node_metadata": {
                            "cg_name": name,
                            "girl_or_boy": variant.get("girl_or_boy", 2),
                            "embedded_fragment": bool(variant.get("embedded_fragment")),
                            "discovered_from": variant.get("basis", "archive discovery"),
                        },
                    }
                )
            if asset_key not in seen_nodes:
                seen_nodes.add(asset_key)
                node_specs.append(
                    {
                        "canonical_key": asset_key,
                        "type": "asset_reference",
                        "node_metadata": {"source_file": source_file, "source_raw_path": raw_path},
                    }
                )
            edge_records.append(
                {
                    "from": scene_key,
                    "to": variant_key,
                    "type": "has_variant",
                    "relation": "explicit_reference",
                    "source": source_file,
                    "raw_path": raw_path,
                    "basis": variant.get("basis", "LevelA archive discovery"),
                }
            )
            edge_records.append(
                {
                    "from": variant_key,
                    "to": asset_key,
                    "type": "references_asset",
                    "relation": "explicit_reference",
                    "source": source_file,
                    "raw_path": raw_path,
                    "basis": variant.get("basis", "LevelA archive discovery"),
                }
            )
        seq_asset = scene.get("sequence_asset")
        if seq_asset:
            basis = (
                "LevelA SequenceDataAsset -> embedded movie fragment"
                if has_embedded
                else "LevelA SequenceDataAsset -> client archive movie"
            )
            seen_states: set[str] = set()
            for owner in seq_links_by_asset.get(seq_asset, []):
                edge_records.append(
                    {
                        "from": owner["owner_key"],
                        "to": scene_key,
                        "type": "plays_cutscene",
                        "relation": "explicit_reference",
                        "source": owner["source_file"],
                        "raw_path": owner["raw_path"],
                        "basis": basis,
                        **({"state_key": owner["state_key"]} if owner.get("state_key") else {}),
                    }
                )
                state_key = owner.get("state_key")
                if state_key and owner.get("has_lines") and state_key not in seen_states:
                    seen_states.add(state_key)
                    edge_records.append(
                        {
                            "from": scene_key,
                            "to": f"flow_state:{state_key}",
                            "type": "has_transcript_state",
                            "relation": "exact_join",
                            "source": owner["source_file"],
                            "raw_path": owner["raw_path"],
                            "basis": "ShowTalk LevelA SequenceDataAsset owns flow_state transcript lines",
                            "state_key": state_key,
                        }
                    )

    if apply and (node_specs or edge_records):
        await _register_records(session, release, node_specs, edge_records)
    return {
        "release_id": release_id,
        "registered": sorted(set(registered_names)),
        "edges": len(edge_records),
    }


async def main(args) -> None:
    async with SessionFactory() as session:
        if args.cleanup_roletower:
            report = await cleanup_roletower_cutscenes(
                session, apply=args.apply, max_rows=args.max_rows
            )
        else:
            if not args.release_id:
                raise SystemExit("--release-id is required unless --cleanup-roletower is set")
            report = await discover_bindata_cutscenes(
                session, args.release_id, apply=args.apply, max_rows=args.max_rows
            )
        print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-id", type=int)
    parser.add_argument("--max-rows", type=int, default=1000)
    parser.add_argument("--cleanup-roletower", action="store_true")
    parser.add_argument("--apply", action="store_true")
    asyncio.run(main(parser.parse_args()))

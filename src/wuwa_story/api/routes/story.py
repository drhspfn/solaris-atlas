"""Browse endpoints for source-backed character and quest exploration."""

from collections import Counter
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.api.source_labels import source_family_label
from wuwa_story.db.models.core import (
    Character,
    DialogueLine,
    Item,
    Location,
    PlayerChoice,
    Quest,
    QuestAction,
    QuestNode,
    QuestState,
    Speaker,
    SpeakerEntityLink,
)
from wuwa_story.db.models.graph import Edge, Node, NodeRevision, NodeType
from wuwa_story.db.models.i18n import Locale, LocalizationKey, LocalizationValue
from wuwa_story.db.models.ontology import RelationType
from wuwa_story.db.models.ops import GameRelease
from wuwa_story.db.models.raw import SourceFile, SourceRecord
from wuwa_story.db.models.search import EntityAlias, SearchDocument
from wuwa_story.db.models.story import Scene
from wuwa_story.db.session import get_session

router = APIRouter(tags=["story browsing"])


@router.get("/categories")
async def browse_categories(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    rows = (await session.execute(
        select(NodeType.key, func.count(Node.id))
        .outerjoin(Node, Node.type_id == NodeType.id)
        .group_by(NodeType.key)
        .order_by(NodeType.key)
    )).all()
    counts = dict(rows)
    dialogue_count = await session.scalar(select(func.count()).select_from(DialogueLine)) or 0
    browse_categories = [
        {"key": "character", "count": counts.get("character", 0), "node_types": ["character"]},
        {"key": "item", "count": counts.get("item", 0), "node_types": ["item"]},
        {
            "key": "location",
            "count": counts.get("area", 0) + counts.get("location", 0),
            "node_types": ["area", "location"],
        },
        {"key": "quest", "count": counts.get("quest", 0), "node_types": ["quest"]},
        {"key": "speaker", "count": counts.get("speaker", 0), "node_types": ["speaker"]},
        {
            "key": "dialogue",
            "count": dialogue_count,
            "node_types": ["dialogue_line", "talk_item"],
        },
    ]
    return {
        "categories": [{"key": key, "count": count} for key, count in rows],
        "browse_categories": browse_categories,
    }


async def _release_id(session: AsyncSession, game_version: str | None) -> int | None:
    statement = select(GameRelease.id).order_by(GameRelease.sequence.desc())
    if game_version:
        statement = statement.where(GameRelease.game_version == game_version)
    return await session.scalar(statement.limit(1))


async def _localized(
    session: AsyncSession,
    key_id: int | None,
    locale_code: str,
    release_id: int | None,
) -> dict[str, Any] | None:
    if key_id is None:
        return None
    key = await session.get(LocalizationKey, key_id)
    locale = await session.scalar(select(Locale).where(Locale.code == locale_code))
    if key is None or locale is None or release_id is None:
        return {
            "key": key.key if key else None,
            "locale": locale_code,
            "content": None,
            "resolution": "unavailable",
        }
    value = await session.scalar(
        select(LocalizationValue).where(
            LocalizationValue.key_id == key_id,
            LocalizationValue.locale_id == locale.id,
            LocalizationValue.release_id == release_id,
        )
    )
    return {
        "key": key.key,
        "locale": locale_code,
        "content": value.content if value else None,
        "resolution": value.status if value else "missing_key",
    }


async def _node_label(session: AsyncSession, node_id: int, locale_code: str) -> dict[str, Any]:
    node = await session.get(Node, node_id)
    if node is None:
        return {"id": node_id, "canonical_key": None, "type": None, "label": None}
    node_type = await session.get(NodeType, node.type_id)
    locale = await session.scalar(select(Locale).where(Locale.code == locale_code))
    alias = None
    if locale:
        alias = await session.scalar(
            select(EntityAlias.alias)
            .where(EntityAlias.node_id == node_id, EntityAlias.locale_id == locale.id)
            .order_by(EntityAlias.id)
            .limit(1)
        )
        if alias is None:
            alias = await session.scalar(
                select(SearchDocument.title)
                .where(
                    SearchDocument.target_node_id == node_id, SearchDocument.locale_id == locale.id
                )
                .order_by(SearchDocument.id)
                .limit(1)
            )
    return {
        "id": node.id,
        "canonical_key": node.canonical_key,
        "type": node_type.key if node_type else None,
        "label": alias or node.slug or node.canonical_key,
        "status": node.status,
    }


async def _explicit_entity_links(
    session: AsyncSession, node_id: int, locale_code: str, limit: int = 200
) -> list[dict[str, Any]]:
    neighbor_id = case((Edge.from_node_id == node_id, Edge.to_node_id), else_=Edge.from_node_id)
    rows = await session.execute(
        select(Edge, RelationType.key, Node, NodeType.key)
        .join(RelationType, RelationType.id == Edge.relation_type_id)
        .join(Node, Node.id == neighbor_id)
        .join(NodeType, NodeType.id == Node.type_id)
        .where(
            or_(Edge.from_node_id == node_id, Edge.to_node_id == node_id),
            neighbor_id != node_id,
            Edge.layer.in_(["source", "canonical"]),
            NodeType.key != "source_reference",
        )
        .order_by(RelationType.key, Node.canonical_key, Edge.id)
        .limit(limit)
    )
    return [
        {
            "relation": relation,
            "direction": "outgoing" if edge.from_node_id == node_id else "incoming",
            "node": await _node_label(session, neighbor.id, locale_code),
            "basis": edge.basis,
            "provenance": edge.metadata_json,
        }
        for edge, relation, neighbor, _node_type in rows
    ]


def _provenance(
    record: SourceRecord | None, source_file: SourceFile | None
) -> dict[str, Any] | None:
    if record is None:
        return None
    return {
        "source_file": source_file.logical_source_path if source_file else None,
        "source_row": record.row_index,
        "source_key": record.source_key,
        "raw_record": record.data,
    }


async def _dialogue_payload(
    session: AsyncSession,
    row: tuple[Any, ...],
    *,
    locale_code: str,
    release_id: int | None,
) -> dict[str, Any]:
    line, action, state, node, record, source_file = row
    speaker = (
        await _node_label(session, line.speaker_node_id, locale_code)
        if line.speaker_node_id
        else None
    )
    text = await _localized(session, line.localization_key_id, locale_code, release_id)
    choice_targets = (
        select(Edge.to_node_id)
        .join(RelationType, RelationType.id == Edge.relation_type_id)
        .where(
            Edge.from_node_id == line.node_id,
            RelationType.key == "presents_choice",
        )
    )
    choices = await session.scalars(
        select(PlayerChoice).where(PlayerChoice.node_id.in_(choice_targets))
    )
    choice_payloads = []
    for choice in choices:
        choice_node = await session.get(Node, choice.node_id)
        choice_source = (
            await session.get(SourceRecord, choice.source_record_id)
            if choice.source_record_id
            else None
        )
        choice_file = (
            await session.get(SourceFile, choice_source.source_file_id)
            if choice_source is not None
            else None
        )
        choice_payloads.append(
            {
                "id": choice_node.canonical_key if choice_node else None,
                "text": await _localized(
                    session, choice.localization_key_id, locale_code, release_id
                ),
                "provenance": _provenance(choice_source, choice_file),
            }
        )
    return {
        "id": node.canonical_key,
        "node_type": "dialogue_line",
        "speaker": speaker,
        "text": text or {"inline_text": line.inline_text, "resolution": "inline"},
        "player_choices": choice_payloads,
        "game_ids": {
            "talk_item_id": line.game_talk_item_id,
            "text_id": line.game_text_id,
            "speaker_id": speaker["canonical_key"] if speaker else None,
        },
        "source_type": line.source_type,
        "source_index": line.source_index,
        "action": {
            "id": action.action_id,
            "name": action.action_name,
            "index": action.action_index,
            "params": action.params,
        }
        if action
        else None,
        "flow_state": state.state_key if state else None,
        "ordering": {
            "basis": "action_index_then_talk_source_index",
            "scope": "authored order; runtime branch traversal is not implied",
        },
        "provenance": _provenance(record, source_file),
    }


async def _quest_state_ids(session: AsyncSession, quest_node_id: int) -> list[int]:
    relation_ids = select(RelationType.id).where(RelationType.key == "references_flow_state")
    child_quest_nodes = (
        select(Edge.to_node_id)
        .join(RelationType, Edge.relation_type_id == RelationType.id)
        .where(
            Edge.from_node_id == quest_node_id,
            RelationType.key == "has_quest_node",
            Edge.layer == "source",
        )
    )
    ids = await session.scalars(
        select(Edge.to_node_id).where(
            or_(Edge.from_node_id == quest_node_id, Edge.from_node_id.in_(child_quest_nodes)),
            Edge.relation_type_id.in_(relation_ids),
            Edge.layer == "source",
        )
    )
    return list(ids)


async def _quest_info(
    session: AsyncSession,
    quest: Quest,
    locale_code: str,
    game_version: str | None = None,
) -> dict[str, Any]:
    node = await session.get(Node, quest.node_id)
    release_id = await _release_id(session, game_version)
    name = await _localized(session, quest.name_key_id, locale_code, release_id)
    quest_nodes = (
        select(Edge.to_node_id)
        .join(RelationType, RelationType.id == Edge.relation_type_id)
        .where(Edge.from_node_id == quest.node_id, RelationType.key == "has_quest_node")
    )
    area_rows = await session.execute(
        select(Edge, Node)
        .join(RelationType, RelationType.id == Edge.relation_type_id)
        .join(Node, Node.id == Edge.to_node_id)
        .where(
            Edge.from_node_id.in_(quest_nodes),
            RelationType.key == "references_area",
            Edge.layer == "source",
        )
        .order_by(Node.canonical_key, Edge.id)
    )
    return {
        "game_quest_id": quest.game_quest_id,
        "canonical_key": node.canonical_key if node else f"quest:{quest.game_quest_id}",
        "name": name,
        "quest_type": quest.quest_type,
        "region_id": quest.region_id,
        "explicit_area_references": [
            {
                "area": await _node_label(session, area.id, locale_code),
                "basis": edge.basis,
                "provenance": edge.metadata_json,
            }
            for edge, area in area_rows
        ],
    }


async def _latest_source_record(session: AsyncSession, node_id: int) -> dict[str, Any] | None:
    result = await session.execute(
        select(NodeRevision, SourceRecord, SourceFile, GameRelease)
        .join(SourceRecord, SourceRecord.id == NodeRevision.source_record_id)
        .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
        .outerjoin(GameRelease, GameRelease.id == NodeRevision.release_id)
        .where(NodeRevision.node_id == node_id)
        .order_by(NodeRevision.revision.desc(), NodeRevision.id.desc())
        .limit(1)
    )
    row = result.first()
    if row is None:
        return None
    revision, record, source_file, release = row
    return {
        "source_file": source_file.logical_source_path,
        "source_row": record.row_index,
        "version": release.game_version if release else None,
        "raw_path": revision.metadata_json.get("source_raw_path"),
        "raw_record": record.data,
    }


async def _quest_references_from_links(
    session: AsyncSession,
    links: list[dict[str, Any]],
    locale_code: str,
    game_version: str | None = None,
) -> list[dict[str, Any]]:
    quest_node_ids = {
        link["node"]["id"]
        for link in links
        if link["node"]["type"] == "quest_node"
    }
    if not quest_node_ids:
        return []
    quest_nodes = list(
        await session.scalars(
            select(QuestNode).where(QuestNode.node_id.in_(quest_node_ids))
        )
    )
    game_quest_ids = {row.game_quest_id for row in quest_nodes if row.game_quest_id is not None}
    quests = {
        quest.game_quest_id: quest
        for quest in await session.scalars(select(Quest).where(Quest.game_quest_id.in_(game_quest_ids or {-1})))
    }
    links_by_node = {link["node"]["id"]: link for link in links}
    grouped: dict[int, dict[str, Any]] = {}
    for quest_node in quest_nodes:
        if quest_node.game_quest_id not in quests:
            continue
        node = await session.get(Node, quest_node.node_id)
        link = links_by_node[quest_node.node_id]
        entry = grouped.setdefault(
            quest_node.game_quest_id,
            {
                "quest": None,
                "references": [],
                "meaning": "These quest nodes explicitly reference this entity; this alone does not indicate a reward or character presence.",
            },
        )
        if entry["quest"] is None:
            entry["quest"] = await _quest_info(
                session, quests[quest_node.game_quest_id], locale_code, game_version
            )
        entry["references"].append(
            {
                "quest_node": {
                    "canonical_key": node.canonical_key if node else None,
                    "game_node_id": quest_node.game_node_id,
                    "node_type": quest_node.node_type,
                },
                "direction": link["direction"],
                "relation": link["relation"],
                "basis": link["basis"],
                "provenance": link["provenance"],
            }
        )
    return [grouped[key] for key in sorted(grouped)]


def _logical_image_references(raw: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"field": key, "asset_path": value}
        for key, value in raw.items()
        if isinstance(value, str)
        and value.startswith("/Game/")
        and any(token in key.casefold() for token in ("icon", "card", "portrait"))
    ]


@router.get("/items/{canonical_key:path}/profile")
async def item_profile(
    canonical_key: str,
    locale: str = "en",
    game_version: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    node = await session.scalar(select(Node).where(Node.canonical_key == canonical_key))
    if node is None:
        raise HTTPException(status_code=404, detail="item not found")
    item = await session.get(Item, node.id)
    if item is None:
        raise HTTPException(status_code=404, detail="node is not an item")
    release_id = await _release_id(session, game_version)
    raw_source = await _latest_source_record(session, node.id)
    raw = raw_source["raw_record"] if raw_source else {}
    links = await _explicit_entity_links(session, node.id, locale)
    location_links = [
        link
        for link in links
        if link["node"]["type"] in {"area", "location"}
        and link["relation"] in {"references_area", "within_area", "occurs_at"}
    ]
    quest_references = await _quest_references_from_links(
        session, links, locale, game_version
    )
    access_ids = raw.get("ItemAccess", [])
    access_rows = await session.execute(
        select(SourceRecord, SourceFile)
        .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
        .where(
            SourceFile.logical_source_path == "BinData/accesspath/accesspath.json",
            SourceRecord.data["Id"].as_integer().in_(access_ids or [-1]),
        )
        .order_by(SourceRecord.data["SortIndex"].as_integer(), SourceRecord.row_index)
    )
    access_paths = []
    for access_record, access_file in access_rows:
        access_raw = access_record.data
        desc_key = await session.scalar(
            select(LocalizationKey).where(LocalizationKey.key == access_raw.get("Description"))
        )
        access_paths.append(
            {
                "id": access_raw.get("Id"),
                "description": await _localized(
                    session, desc_key.id if desc_key else None, locale, release_id
                ),
                "type_id": access_raw.get("Type"),
                "parameters": {
                    "val1": access_raw.get("Val1"),
                    "val2": access_raw.get("Val2"),
                    "val3": access_raw.get("Val3"),
                    "client_conditions": access_raw.get("ClientCondition", []),
                },
                "source": {
                    "file": access_file.logical_source_path,
                    "row": access_record.row_index,
                    "basis": "exact ItemInfo.ItemAccess[] to AccessPath.Id join",
                },
            }
        )
    enrichment_rows = await session.execute(
        select(SourceRecord, SourceFile)
        .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
        .where(
            SourceFile.logical_source_path == "BinData/enrichment/enrichmentareaconfig.json",
            SourceRecord.data["ItemId"].as_integer() == item.game_item_id,
        )
        .order_by(SourceRecord.row_index)
    )
    enrichment_sources = [
        {
            "level_id": record.data.get("LevelId"),
            "enrichment_id": record.data.get("EnrichmentId"),
            "entity_ids": record.data.get("EntityIds", []),
            "source": {
                "file": source_file.logical_source_path,
                "row": record.row_index,
                "raw_path": f"$[{record.row_index}]",
                "basis": "exact EnrichmentAreaConfig.ItemId join",
            },
            "location_resolution": "unresolved_level_or_entity_mapping",
        }
        for record, source_file in enrichment_rows
    ]
    harvest_entity_ids = sorted(
        {entity_id for row in enrichment_sources for entity_id in row["entity_ids"]}
    )
    voxel_rows = await session.execute(
        select(SourceRecord, SourceFile)
        .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
        .where(
            SourceFile.logical_source_path == "BinData/EntityVoxelInfo/entityvoxelinfo.json",
            SourceRecord.data["EntityId"].as_integer().in_(harvest_entity_ids or [-1]),
        )
        .order_by(SourceRecord.row_index)
    )
    world_maps: dict[int, dict[str, Any]] = {}
    for voxel_record, voxel_file in voxel_rows:
        voxel = voxel_record.data
        map_id = voxel.get("MapId")
        entity_id = voxel.get("EntityId")
        if not isinstance(map_id, int):
            continue
        map_data = world_maps.setdefault(
            map_id,
            {"map_id": map_id, "entity_ids": set(), "evidence": []},
        )
        map_data["entity_ids"].add(entity_id)
        map_data["evidence"].append(
            {
                "entity_id": entity_id,
                "source_file": voxel_file.logical_source_path,
                "source_row": voxel_record.row_index,
                "raw_path": f"$[{voxel_record.row_index}]",
            }
        )
    world_map_payloads = [
        {
            **map_data,
            "entity_ids": sorted(map_data["entity_ids"]),
            "basis": "exact EnrichmentAreaConfig.EntityIds[] to EntityVoxelInfo.EntityId join",
            "named_area_resolution": "unresolved_map_has_multiple_area_or_floor_regions",
        }
        for _, map_data in sorted(world_maps.items())
    ]
    return {
        "item": {
            "canonical_key": node.canonical_key,
            "name": await _localized(session, item.name_key_id, locale, release_id),
            "description": await _localized(session, item.description_key_id, locale, release_id),
            "game_item_id": item.game_item_id,
            "status": node.status,
        },
        "media": {
            "status": "asset_not_extracted",
            "icon_asset_path": raw.get("Icon"),
            "small_icon_asset_path": raw.get("IconSmall"),
            "medium_icon_asset_path": raw.get("IconMiddle"),
            "note": "These are upstream Unreal asset identifiers; image bytes or browser URLs are not currently served.",
        },
        "gameplay_metadata": {
            "item_type": raw.get("ItemType"),
            "main_type_id": raw.get("MainTypeId"),
            "quality_id": raw.get("QualityId"),
            "show_types": raw.get("ShowTypes", []),
            "item_access_ids": raw.get("ItemAccess", []),
        },
        "acquisition_paths": access_paths,
        "harvest_sources": enrichment_sources,
        "harvest_world_maps": world_map_payloads,
        "locations": location_links,
        "quest_references": quest_references,
        "explicit_links": links,
        "source": raw_source,
        "unresolved": {
            "item_access_ids": {
                "values": raw.get("ItemAccess", []),
                "reason": "Access IDs resolve to localized AccessPath descriptions, but their opaque parameters are not treated as locations.",
            }
        },
    }


@router.get("/locations/{canonical_key:path}/profile")
async def location_profile(
    canonical_key: str,
    locale: str = "en",
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    node = await session.scalar(select(Node).where(Node.canonical_key == canonical_key))
    if node is None:
        raise HTTPException(status_code=404, detail="location not found")
    location = await session.get(Location, node.id)
    if location is None:
        raise HTTPException(status_code=404, detail="node is not a location")
    locale_id = await session.scalar(select(Locale.id).where(Locale.code == locale))
    release_id = await _release_id(session, None)
    parent = (
        await _node_label(session, location.parent_location_node_id, locale)
        if location.parent_location_node_id
        else None
    )
    child_ids = await session.scalars(
        select(Location.node_id)
        .where(Location.parent_location_node_id == node.id)
        .order_by(Location.game_location_id, Location.node_id)
    )
    children = [await _node_label(session, child_id, locale) for child_id in child_ids]
    related = await _explicit_entity_links(session, node.id, locale)
    quest_references = await _quest_references_from_links(session, related, locale)
    raw_source = await _latest_source_record(session, node.id)
    return {
        "location": {
            "canonical_key": node.canonical_key,
            "name": await _localized(session, location.name_key_id, locale, release_id),
            "game_location_id": location.game_location_id,
            "status": node.status,
        },
        "hierarchy": {
            "parent": parent,
            "children": children,
            "basis": "raw area Father field" if parent else None,
        },
        "explicit_links": related,
        "quest_references": quest_references,
        "locale_available": locale_id is not None,
        "media": {
            "status": "asset_not_extracted",
            "asset_references": _logical_image_references(
                (raw_source or {}).get("raw_record", {})
            ),
        },
        "source": raw_source,
    }


@router.get("/quests/{game_quest_id}/profile")
async def quest_profile(
    game_quest_id: int,
    locale: str = "en",
    game_version: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    quest = await session.scalar(select(Quest).where(Quest.game_quest_id == game_quest_id))
    if quest is None:
        raise HTTPException(status_code=404, detail="quest not found")
    quest_nodes = await session.execute(
        select(QuestNode, Node, SourceRecord, SourceFile)
        .join(Node, Node.id == QuestNode.node_id)
        .outerjoin(SourceRecord, SourceRecord.id == QuestNode.source_record_id)
        .outerjoin(SourceFile, SourceFile.id == SourceRecord.source_file_id)
        .where(QuestNode.game_quest_id == game_quest_id)
        .order_by(SourceFile.logical_source_path, SourceRecord.row_index, QuestNode.game_node_id)
        .limit(2000)
    )
    qnode_rows = list(quest_nodes)
    qnode_ids = {entry[0].node_id for entry in qnode_rows}
    nodes_by_game_id = {entry[0].game_node_id: entry[1].canonical_key for entry in qnode_rows}
    raw_parent_links = []
    for quest_node, node, source_record, source_file in qnode_rows:
        data = quest_node.metadata_json.get("data", {})
        parent_id = data.get("ParentNodeId") if isinstance(data, dict) else None
        parent_key = nodes_by_game_id.get(str(parent_id)) if parent_id not in (None, 0, "0") else None
        if parent_key:
            raw_parent_links.append(
                {
                    "from": parent_key,
                    "to": node.canonical_key,
                    "relation": "parent_of",
                    "basis": "explicit_reference",
                    "raw_field": "Data.ParentNodeId",
                    "ordering": "hierarchy only; sibling traversal order is not implied",
                    "source": {
                        "file": source_file.logical_source_path if source_file else None,
                        "row": source_record.row_index if source_record else None,
                    },
                }
            )
    hierarchy_rows = []
    if qnode_ids:
        hierarchy_rows = list(
            await session.execute(
                select(Edge, RelationType.key, Node, NodeType.key)
                .join(RelationType, RelationType.id == Edge.relation_type_id)
                .join(Node, Node.id == Edge.to_node_id)
                .join(NodeType, NodeType.id == Node.type_id)
                .where(
                    Edge.from_node_id.in_(qnode_ids),
                    Edge.to_node_id.in_(qnode_ids),
                    RelationType.key.in_(["parent_of", "requires_quest", "checks_quest_node"]),
                    Edge.layer == "source",
                )
                .order_by(RelationType.key, Edge.from_node_id, Edge.to_node_id)
            )
        )
    states = await session.scalars(
        select(QuestState)
        .where(QuestState.node_id.in_(await _quest_state_ids(session, quest.node_id)))
        .order_by(QuestState.flow_id, QuestState.state_id, QuestState.state_key)
    )
    state_payloads = []
    for state in states:
        actions = await session.scalars(
            select(QuestAction).where(QuestAction.quest_state_node_id == state.node_id)
            .order_by(QuestAction.action_index)
        )
        state_payloads.append(
            {
                "state_key": state.state_key,
                "flow_id": state.flow_id,
                "state_id": state.state_id,
                "actions": [
                    {
                        "canonical_key": (await session.get(Node, action.node_id)).canonical_key,
                        "action_id": action.action_id,
                        "action_guid": action.action_guid,
                        "name": action.action_name,
                        "action_index": action.action_index,
                        "params": action.params,
                        "source_record_id": action.source_record_id,
                    }
                    for action in actions
                ],
            }
        )
    plot_steps = (
        select(Edge.to_node_id)
        .join(RelationType, RelationType.id == Edge.relation_type_id)
        .where(Edge.from_node_id == quest.node_id, RelationType.key == "has_plot_step")
    )
    scene_ids = (
        select(Edge.to_node_id)
        .join(RelationType, RelationType.id == Edge.relation_type_id)
        .where(Edge.from_node_id.in_(plot_steps), RelationType.key == "presents_scene")
    )
    scenes = await session.scalars(
        select(Scene).where(Scene.node_id.in_(scene_ids)).order_by(Scene.authored_order, Scene.node_id)
    )
    scene_payloads = []
    for scene in scenes:
        scene_node = await session.get(Node, scene.node_id)
        scene_payloads.append(
            {
                "canonical_key": scene_node.canonical_key if scene_node else None,
                "title": scene.title,
                "authored_order": scene.authored_order,
                "ordering_basis": scene.source_basis,
            }
        )
    quest_links = await _explicit_entity_links(session, quest.node_id, locale)
    return {
        "quest": await _quest_info(session, quest, locale, game_version),
        "tree": [
            {
                "canonical_key": node.canonical_key,
                "game_node_id": quest_node.game_node_id,
                "node_type": quest_node.node_type,
                "data": quest_node.metadata_json.get("data", {}),
                "source_array_order": source_record.row_index if source_record else None,
                "source": {
                    "file": source_file.logical_source_path if source_file else None,
                    "row": source_record.row_index if source_record else None,
                },
            }
            for quest_node, node, source_record, source_file in qnode_rows
        ],
        "tree_edges": [
            {
                "from": source.canonical_key,
                "relation": relation,
                "to": target.canonical_key,
                "basis": edge.basis,
                "provenance": edge.metadata_json,
            }
            for edge, relation, target, _target_type in hierarchy_rows
            if (source := await session.get(Node, edge.from_node_id)) is not None
        ],
        "raw_parent_links": raw_parent_links,
        "flow_states": state_payloads,
        "scenes": scene_payloads,
        "quest_prerequisites": [
            {
                **link,
                "meaning": "A raw PreQuest/runtime condition references this quest. It is a prerequisite relation, not an immediate next-quest order.",
            }
            for link in quest_links
            if link["relation"] == "requires_quest" and link["direction"] == "outgoing"
        ],
        "related_quest_references": [
            link for link in quest_links if link["relation"] == "references_quest"
        ],
        "ordering": {
            "tree": "source file/row order; this is not a guaranteed traversal",
            "actions": "authored action_index within each flow state",
            "states": "flow/state IDs then state key; no linear runtime chronology is implied",
        },
        "transcript_endpoint": f"/quests/{game_quest_id}/transcript?locale={locale}&game_version={game_version or ''}",
        "explicit_links": quest_links,
        "source": await _latest_source_record(session, quest.node_id),
    }


@router.get("/characters/{canonical_key:path}/profile")
async def character_profile(
    canonical_key: str,
    locale: str = "en",
    game_version: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    node = await session.scalar(select(Node).where(Node.canonical_key == canonical_key))
    if node is None:
        raise HTTPException(status_code=404, detail="character not found")
    character = await session.get(Character, node.id)
    if character is None:
        raise HTTPException(status_code=404, detail="node is not a character")

    link_rows = await session.execute(
        select(SpeakerEntityLink, Speaker, Node)
        .join(Speaker, Speaker.node_id == SpeakerEntityLink.speaker_node_id)
        .join(Node, Node.id == Speaker.node_id)
        .where(SpeakerEntityLink.entity_node_id == node.id)
        .order_by(Speaker.game_speaker_id)
    )
    speakers = []
    own_speaker_ids: set[int] = set()
    state_ids: set[int] = set()
    for link, speaker, speaker_node in link_rows:
        own_speaker_ids.add(speaker.node_id)
        speakers.append(
            {
                "canonical_key": speaker_node.canonical_key,
                "game_speaker_id": speaker.game_speaker_id,
                "resolution": link.resolution_type,
                "evidence_source_record_id": link.source_record_id,
            }
        )
        speaker_states = (
            select(QuestAction.quest_state_node_id)
            .join(DialogueLine, DialogueLine.action_node_id == QuestAction.node_id)
            .where(DialogueLine.speaker_node_id == speaker.node_id)
        )
        state_ids.update(await session.scalars(speaker_states))

    co_present: dict[int, set[int]] = {}
    co_present_speakers: dict[int, set[int]] = {}
    if state_ids:
        speaker_rows = await session.execute(
            select(DialogueLine.speaker_node_id, QuestAction.quest_state_node_id)
            .join(QuestAction, QuestAction.node_id == DialogueLine.action_node_id)
            .where(
                QuestAction.quest_state_node_id.in_(state_ids),
                DialogueLine.speaker_node_id.is_not(None),
                DialogueLine.speaker_node_id.not_in(own_speaker_ids),
            )
            .distinct()
        )
        for speaker_node_id, state_node_id in speaker_rows:
            co_present_speakers.setdefault(speaker_node_id, set()).add(state_node_id)
        co_rows = await session.execute(
            select(SpeakerEntityLink.entity_node_id, QuestAction.quest_state_node_id)
            .join(Speaker, Speaker.node_id == SpeakerEntityLink.speaker_node_id)
            .join(DialogueLine, DialogueLine.speaker_node_id == Speaker.node_id)
            .join(QuestAction, QuestAction.node_id == DialogueLine.action_node_id)
            .where(
                QuestAction.quest_state_node_id.in_(state_ids),
                SpeakerEntityLink.entity_node_id != node.id,
            )
            .distinct()
        )
        for entity_node_id, state_node_id in co_rows:
            co_present.setdefault(entity_node_id, set()).add(state_node_id)
    co_present_characters = []
    for entity_node_id, co_states in sorted(co_present.items()):
        co_present_characters.append(
            {
                "character": await _node_label(session, entity_node_id, locale),
                "basis": "shared_flow_state_dialogue",
                "flow_states": [
                    state.state_key
                    for state in await session.scalars(
                        select(QuestState)
                        .where(QuestState.node_id.in_(co_states))
                        .order_by(QuestState.state_key)
                    )
                ],
                "meaning": "Both characters have dialogue lines in at least one same authored flow state; this does not assert a personal relationship.",
            }
        )
    co_speaker_cards = []
    for speaker_node_id, co_states in sorted(co_present_speakers.items()):
        mapped_characters = await session.scalars(
            select(SpeakerEntityLink.entity_node_id).where(
                SpeakerEntityLink.speaker_node_id == speaker_node_id
            )
        )
        co_speaker_cards.append(
            {
                "speaker": await _node_label(session, speaker_node_id, locale),
                "character_entities": [
                    await _node_label(session, entity_node_id, locale)
                    for entity_node_id in mapped_characters
                ],
                "basis": "shared_flow_state_dialogue",
                "flow_states": [
                    state.state_key
                    for state in await session.scalars(
                        select(QuestState)
                        .where(QuestState.node_id.in_(co_states))
                        .order_by(QuestState.state_key)
                    )
                ],
            }
        )

    locale_row = await session.scalar(select(Locale).where(Locale.code == locale))
    aliases = []
    if locale_row:
        aliases = list(
            await session.scalars(
                select(EntityAlias.alias)
                .where(EntityAlias.node_id == node.id, EntityAlias.locale_id == locale_row.id)
                .order_by(EntityAlias.alias)
            )
        )
        search_rows = await session.execute(
            select(SearchDocument.title, SearchDocument.aliases)
            .where(
                SearchDocument.target_node_id == node.id, SearchDocument.locale_id == locale_row.id
            )
            .order_by(SearchDocument.id)
        )
        for title, extra_aliases in search_rows:
            if title:
                aliases.append(title)
            if isinstance(extra_aliases, list):
                aliases.extend(value for value in extra_aliases if isinstance(value, str) and value)

    quest_ids: set[int] = set()
    if state_ids:
        owners = (
            select(Edge.from_node_id)
            .join(RelationType, Edge.relation_type_id == RelationType.id)
            .where(
                Edge.to_node_id.in_(state_ids),
                RelationType.key == "references_flow_state",
                Edge.layer == "source",
            )
        )
        quest_ids.update(
            await session.scalars(select(Quest.node_id).where(Quest.node_id.in_(owners)))
        )
        quest_ids.update(
            await session.scalars(
                select(Quest.node_id)
                .join(QuestNode, QuestNode.game_quest_id == Quest.game_quest_id)
                .where(QuestNode.node_id.in_(owners))
            )
        )
    quests = []
    if quest_ids:
        quest_rows = await session.scalars(
            select(Quest).where(Quest.node_id.in_(quest_ids)).order_by(Quest.game_quest_id)
        )
        quests = [await _quest_info(session, quest, locale, game_version) for quest in quest_rows]

    # Keep real entity links readable; raw config-row references are summarized below.
    edge_rows = await session.execute(
        select(Edge, RelationType, Node, NodeType.key)
        .join(RelationType, RelationType.id == Edge.relation_type_id)
        .join(
            Node,
            Node.id
            == case(
                (Edge.from_node_id == node.id, Edge.to_node_id),
                else_=Edge.from_node_id,
            ),
        )
        .join(NodeType, NodeType.id == Node.type_id)
        .where(
            or_(Edge.from_node_id == node.id, Edge.to_node_id == node.id),
            Edge.layer.in_(["source", "canonical"]),
            NodeType.key != "source_reference",
        )
        .order_by(RelationType.key, Edge.id)
        .limit(300)
    )
    links = []
    for edge, relation, target, _target_type in edge_rows:
        links.append(
            {
                "relation": relation.key,
                "target": await _node_label(session, target.id, locale),
                "direction": "outgoing" if edge.from_node_id == node.id else "incoming",
                "basis": edge.basis,
                "provenance": edge.metadata_json,
            }
        )
    evidence_files = await session.scalars(
        select(Node.metadata_json["source_file"].as_string())
        .select_from(Edge)
        .join(
            Node,
            Node.id
            == case(
                (Edge.from_node_id == node.id, Edge.to_node_id),
                else_=Edge.from_node_id,
            ),
        )
        .join(NodeType, NodeType.id == Node.type_id)
        .where(
            or_(Edge.from_node_id == node.id, Edge.to_node_id == node.id),
            Edge.layer.in_(["source", "canonical"]),
            NodeType.key == "source_reference",
        )
    )
    evidence_counts = Counter(evidence_files)
    source_evidence = [
        {
            "label": source_family_label(source_file),
            "source_file": source_file,
            "record_count": count,
        }
        for source_file, count in sorted(
            evidence_counts.items(), key=lambda item: (-item[1], item[0] or "")
        )
    ]
    has_speaker_mapping = bool(speakers)
    character_source = await _latest_source_record(session, node.id)
    return {
        "character": {
            "canonical_key": node.canonical_key,
            "canonical_name": character.canonical_name,
            "aliases": list(dict.fromkeys(aliases)),
            "description": character.description,
            "playable": character.playable,
            "game_version": game_version,
        },
        "media": {
            "status": "asset_not_extracted",
            "asset_references": _logical_image_references(
                (character_source or {}).get("raw_record", {})
            ),
            "note": "Unreal asset identifiers are available; image bytes or browser URLs are not currently served.",
        },
        "source": {
            key: character_source[key]
            for key in ("source_file", "source_row", "version", "raw_path")
        }
        if character_source
        else None,
        "speakers": speakers,
        "quests_with_dialogue": quests,
        "co_present_characters": co_present_characters,
        "co_present_speakers": co_speaker_cards,
        "explicit_links": links,
        "source_evidence": source_evidence,
        "connection_summary": {
            "confirmed_speakers": len(speakers),
            "quests_with_dialogue": len(quests),
            "co_present_characters": len(co_present_characters),
            "explicit_entity_links": len(links),
            "configuration_records": sum(item["record_count"] for item in source_evidence),
            "narrative_status": (
                "speaker_mapped"
                if has_speaker_mapping
                else "no_confirmed_speaker_crosswalk"
            ),
            "explanation": (
                "Quest dialogue links are shown only when a deterministic speaker-to-character "
                "mapping exists. Name mentions and configuration records do not prove that the "
                "character spoke in a quest."
            ),
        },
        "limitations": [
            "Quest participation is based on explicit speaker-to-dialogue-to-flow-state-to-quest references.",
            "The presence of authored dialogue does not imply that every player traversal includes it.",
            "Only explicit graph links are shown; possible name matches are excluded.",
        ],
    }


@router.get("/dialogue/search")
async def search_dialogue(
    q: str = Query(min_length=1, max_length=512),
    character: str | None = None,
    quest_id: int | None = None,
    locale: str = "en",
    game_version: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    release_id = await _release_id(session, game_version)
    locale_row = await session.scalar(select(Locale).where(Locale.code == locale))
    if locale_row is None:
        raise HTTPException(status_code=400, detail=f"unknown locale: {locale}")
    statement = (
        select(DialogueLine, QuestAction, QuestState, Node, SourceRecord, SourceFile)
        .join(Node, Node.id == DialogueLine.node_id)
        .outerjoin(QuestAction, QuestAction.node_id == DialogueLine.action_node_id)
        .outerjoin(QuestState, QuestState.node_id == QuestAction.quest_state_node_id)
        .outerjoin(SourceRecord, SourceRecord.id == DialogueLine.source_record_id)
        .outerjoin(SourceFile, SourceFile.id == SourceRecord.source_file_id)
        .outerjoin(LocalizationKey, LocalizationKey.id == DialogueLine.localization_key_id)
        .outerjoin(
            LocalizationValue,
            and_(
                LocalizationValue.key_id == DialogueLine.localization_key_id,
                LocalizationValue.locale_id == locale_row.id,
                LocalizationValue.release_id == release_id,
            ),
        )
        .where(
            or_(LocalizationValue.content.ilike(f"%{q}%"), DialogueLine.inline_text.ilike(f"%{q}%"))
        )
    )
    if character:
        character_node = await session.scalar(select(Node).where(Node.canonical_key == character))
        if character_node is None:
            raise HTTPException(status_code=404, detail="character not found")
        speaker_ids = select(SpeakerEntityLink.speaker_node_id).where(
            SpeakerEntityLink.entity_node_id == character_node.id
        )
        statement = statement.where(DialogueLine.speaker_node_id.in_(speaker_ids))
    if quest_id is not None:
        quest = await session.scalar(select(Quest).where(Quest.game_quest_id == quest_id))
        if quest is None:
            raise HTTPException(status_code=404, detail="quest not found")
        states = await _quest_state_ids(session, quest.node_id)
        statement = statement.where(QuestAction.quest_state_node_id.in_(states))
    statement = (
        statement.order_by(
            QuestState.state_key,
            QuestAction.action_index,
            DialogueLine.source_index,
            DialogueLine.node_id,
        )
        .offset(offset)
        .limit(limit)
    )
    rows = list((await session.execute(statement)).all())
    return {
        "query": q,
        "locale": locale,
        "game_version": game_version,
        "total_returned": len(rows),
        "offset": offset,
        "limit": limit,
        "results": [
            await _dialogue_payload(session, row, locale_code=locale, release_id=release_id)
            for row in rows
        ],
    }


@router.get("/quests/{game_quest_id}/transcript")
async def quest_transcript(
    game_quest_id: int,
    character: str | None = None,
    q: str | None = Query(default=None, max_length=512),
    locale: str = "en",
    game_version: str | None = None,
    limit: int = Query(500, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    quest = await session.scalar(select(Quest).where(Quest.game_quest_id == game_quest_id))
    if quest is None:
        raise HTTPException(status_code=404, detail="quest not found")
    states = await _quest_state_ids(session, quest.node_id)
    release_id = await _release_id(session, game_version)
    statement = (
        select(DialogueLine, QuestAction, QuestState, Node, SourceRecord, SourceFile)
        .join(QuestAction, QuestAction.node_id == DialogueLine.action_node_id)
        .join(QuestState, QuestState.node_id == QuestAction.quest_state_node_id)
        .join(Node, Node.id == DialogueLine.node_id)
        .outerjoin(SourceRecord, SourceRecord.id == DialogueLine.source_record_id)
        .outerjoin(SourceFile, SourceFile.id == SourceRecord.source_file_id)
        .where(QuestAction.quest_state_node_id.in_(states))
    )
    if character:
        character_node = await session.scalar(select(Node).where(Node.canonical_key == character))
        if character_node is None:
            raise HTTPException(status_code=404, detail="character not found")
        speaker_ids = select(SpeakerEntityLink.speaker_node_id).where(
            SpeakerEntityLink.entity_node_id == character_node.id
        )
        statement = statement.where(DialogueLine.speaker_node_id.in_(speaker_ids))
    if q:
        locale_row = await session.scalar(select(Locale).where(Locale.code == locale))
        value = (
            select(LocalizationValue.key_id)
            .join(LocalizationKey, LocalizationKey.id == LocalizationValue.key_id)
            .where(
                LocalizationValue.release_id == release_id,
                LocalizationValue.locale_id == locale_row.id if locale_row else False,
                LocalizationValue.content.ilike(f"%{q}%"),
            )
        )
        statement = statement.where(
            or_(
                DialogueLine.localization_key_id.in_(value),
                DialogueLine.inline_text.ilike(f"%{q}%"),
            )
        )
    statement = (
        statement.order_by(
            QuestState.state_key,
            QuestAction.action_index,
            DialogueLine.source_index,
            DialogueLine.node_id,
        )
        .offset(offset)
        .limit(limit)
    )
    rows = list((await session.execute(statement)).all())
    plot_steps = (
        select(Edge.to_node_id)
        .join(RelationType, RelationType.id == Edge.relation_type_id)
        .where(Edge.from_node_id == quest.node_id, RelationType.key == "has_plot_step")
    )
    scene_ids = (
        select(Edge.to_node_id)
        .join(RelationType, RelationType.id == Edge.relation_type_id)
        .where(Edge.from_node_id.in_(plot_steps), RelationType.key == "presents_scene")
    )
    scene_rows = await session.scalars(
        select(Scene)
        .where(Scene.node_id.in_(scene_ids))
        .order_by(Scene.authored_order, Scene.node_id)
    )
    return {
        "quest": await _quest_info(session, quest, locale, game_version),
        "ordering_semantics": "Authored state/action/talk ordering; branches and runtime conditions are not flattened into a guaranteed playthrough.",
        "scenes": [
            {
                "canonical_key": (await session.get(Node, scene.node_id)).canonical_key,
                "title": scene.title,
                "authored_order": scene.authored_order,
                "ordering_basis": scene.source_basis,
            }
            for scene in scene_rows
        ],
        "filters": {"character": character, "text": q, "locale": locale},
        "offset": offset,
        "limit": limit,
        "lines_returned": len(rows),
        "lines": [
            await _dialogue_payload(session, row, locale_code=locale, release_id=release_id)
            for row in rows
        ],
    }

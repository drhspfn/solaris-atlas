"""Entity and quest profile endpoints."""

from collections import Counter
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.api.item_acquisition import ITEM_QUEST_REWARD_LIMIT, item_map_sources
from wuwa_story.api.routes.story.shared import (
    _character_progression_materials,
    _dialogue_payload,
    _explicit_entity_links,
    _latest_source_record,
    _localized,
    _logical_image_references,
    _node_label,
    _quest_info,
    _quest_references_from_links,
    _quest_state_ids,
    _release_id,
)
from wuwa_story.api.source_labels import source_family_label
from wuwa_story.db.models.core import (
    Character,
    DialogueLine,
    Item,
    Location,
    Quest,
    QuestAction,
    QuestNode,
    QuestState,
    Speaker,
    SpeakerEntityLink,
)
from wuwa_story.db.models.graph import Edge, Node, NodeType
from wuwa_story.db.models.i18n import Locale, LocalizationKey
from wuwa_story.db.models.ontology import RelationType
from wuwa_story.db.models.raw import SourceFile, SourceRecord
from wuwa_story.db.models.search import EntityAlias, SearchDocument
from wuwa_story.db.models.story import Scene
from wuwa_story.db.session import get_session
from wuwa_story.storage.entity_media import entity_image_urls

router = APIRouter(tags=["story browsing"])

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
    quest_references = await _quest_references_from_links(session, links, locale, game_version)
    access_ids = raw.get("ItemAccess", [])
    access_rows = await session.execute(
        select(SourceRecord, SourceFile)
        .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
        .where(
            SourceFile.logical_source_path == "BinData/accesspath/accesspath.json",
            SourceRecord.release_id == release_id,
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
    # EntityVoxelInfo only resolves these spawn entities to a world MapId. Join
    # that map to its authored map name, then to the unique same-key area record.
    for world_map in world_map_payloads:
        map_rows = await session.execute(
            select(SourceRecord, SourceFile)
            .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
            .where(
                SourceFile.logical_source_path == "BinData/map/akimap.json",
                SourceRecord.data["MapId"].as_integer() == world_map["map_id"],
            )
            .order_by(SourceRecord.row_index)
            .limit(2)
        )
        map_records = list(map_rows)
        map_record, map_file = map_records[0] if map_records else (None, None)
        map_title_key = map_record.data.get("MapName") if map_record else None
        map_title = (
            await session.scalar(
                select(LocalizationKey).where(LocalizationKey.key == map_title_key)
            )
            if map_title_key
            else None
        )
        named_area_rows = (
            await session.execute(
                select(SourceRecord, SourceFile)
                .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
                .where(
                    SourceFile.logical_source_path == "BinData/area/area.json",
                    SourceRecord.data.contains(
                        {"DungeonId": world_map["map_id"], "Title": map_title_key}
                    ),
                )
                .order_by(SourceRecord.row_index)
                .limit(2)
            )
            if map_title_key
            else []
        )
        named_area_records = list(named_area_rows)
        named_area = named_area_records[0][0].data if len(named_area_records) == 1 else None
        world_map["named_region"] = {
            "canonical_key": f"area:{named_area['AreaId']}" if named_area else None,
            "name": await _localized(
                session, map_title.id if map_title else None, locale, release_id
            ),
            "source": {
                "map_file": map_file.logical_source_path if map_file else None,
                "map_row": map_record.row_index if map_record else None,
                "area_file": named_area_records[0][1].logical_source_path
                if len(named_area_records) == 1
                else None,
                "area_row": named_area_records[0][0].row_index
                if len(named_area_records) == 1
                else None,
                "basis": "EntityVoxelInfo.EntityId -> MapId -> AkiMap.MapName -> unique area Title/DungeonId",
            },
            "resolution": "unique_map_region" if named_area else "unresolved_map_name",
            "precision": "world_map_only; spawn coordinates and named sub-area are not present in these joins",
        }

    # Exact item use in Resonator progression. Keep source records and the
    # separate evidence that maps each progression group back to a RoleInfo.
    progression_uses: list[dict[str, Any]] = []
    breach_rows = await session.execute(
        select(SourceRecord, SourceFile)
        .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
        .where(
            SourceFile.logical_source_path == "BinData/role_level/rolebreach.json",
            SourceRecord.data["BreachConsume"].contains([{"Key": item.game_item_id}]),
        )
        .order_by(SourceRecord.row_index)
    )
    breach_records = list(breach_rows)
    breach_group_ids = {
        record.data.get("BreachGroupId")
        for record, _source_file in breach_records
        if isinstance(record.data.get("BreachGroupId"), int)
    }
    role_records_by_breach: dict[int, list[tuple[SourceRecord, SourceFile]]] = {}
    for group_id in sorted(breach_group_ids):
        role_rows = await session.execute(
            select(SourceRecord, SourceFile)
            .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
            .where(
                SourceFile.logical_source_path == "BinData/role/roleinfo.json",
                SourceRecord.data["BreachId"].as_integer() == group_id,
            )
            .order_by(SourceRecord.row_index)
        )
        matches = list(role_rows)
        if matches:
            # A breach group can be shared by multiple RoleInfo records (for example
            # alternate Rover forms). The exact BreachId join proves every mapping;
            # treating a multi-match as unresolved discarded valid progression uses.
            role_records_by_breach[group_id] = matches
    for record, source_file in breach_records:
        raw = record.data
        group_id = raw.get("BreachGroupId")
        role_matches = role_records_by_breach.get(group_id, [])
        material = next(
            (
                (index, entry)
                for index, entry in enumerate(raw.get("BreachConsume", []))
                if entry.get("Key") == item.game_item_id
            ),
            None,
        )
        if material is None:
            continue
        material_index, material_entry = material
        next_breach_rows = await session.execute(
            select(SourceRecord, SourceFile)
            .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
            .where(
                SourceFile.logical_source_path == "BinData/role_level/rolebreach.json",
                SourceRecord.data["BreachGroupId"].as_integer() == group_id,
                SourceRecord.data["MaxLevel"].as_integer() > raw.get("MaxLevel", 0),
            )
            .order_by(
                SourceRecord.data["MaxLevel"].as_integer(),
                SourceRecord.row_index,
            )
            .limit(2)
        )
        next_breach_matches = list(next_breach_rows)
        next_breach = None
        if len(next_breach_matches) == 1:
            next_record, next_file = next_breach_matches[0]
            next_requirements = []
            for next_index, next_entry in enumerate(next_record.data.get("BreachConsume", [])):
                next_item_id = next_entry.get("Key")
                next_item_node = (
                    await session.scalar(
                        select(Node).where(Node.canonical_key == f"item:{next_item_id}")
                    )
                    if isinstance(next_item_id, int)
                    else None
                )
                next_requirements.append(
                    {
                        "item_id": next_item_id,
                        "item": await _node_label(session, next_item_node.id, locale)
                        if next_item_node
                        else None,
                        "count": next_entry.get("Value"),
                        "source_index": next_index,
                    }
                )
            next_breach = {
                "level_cap": next_record.data.get("MaxLevel"),
                "requirements": next_requirements,
                "source": {
                    "file": next_file.logical_source_path,
                    "row": next_record.row_index,
                    "raw_path": f"$[{next_record.row_index}].BreachConsume",
                    "basis": "same RoleInfo.BreachId group; next higher MaxLevel in authored role breach table",
                },
            }
        # Keep the material evidence even when no RoleInfo points to its group.
        # When several RoleInfo rows share the exact group, emit all proven uses.
        for role_match in role_matches or [None]:
            role_id = role_match[0].data.get("Id") if role_match else None
            character_node = (
                await session.scalar(
                    select(Node).where(Node.canonical_key == f"character:{role_id}")
                )
                if isinstance(role_id, int)
                else None
            )
            progression_uses.append(
                {
                    "kind": "character_ascension",
                    "character": await _node_label(session, character_node.id, locale)
                    if character_node
                    else None,
                    "role_id": role_id,
                    "required_count": material_entry.get("Value"),
                    "breach_level": raw.get("BreachLevel"),
                    "level_cap": raw.get("MaxLevel"),
                    "next_breach": next_breach,
                    "source": {
                        "file": source_file.logical_source_path,
                        "row": record.row_index,
                        "raw_path": f"$[{record.row_index}].BreachConsume[{material_index}]",
                        "basis": "exact BreachConsume[].Key == ItemInfo.Id; exact BreachGroupId == RoleInfo.BreachId",
                        "role_source_file": role_match[1].logical_source_path
                        if role_match
                        else None,
                        "role_source_row": role_match[0].row_index if role_match else None,
                    },
                }
            )

    pros_rows = await session.execute(
        select(SourceRecord, SourceFile)
        .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
        .where(
            SourceFile.logical_source_path == "BinData/RoleDev/roledevprosroleitem.json",
            SourceRecord.data["ItemGroup"].contains([{"Item1": item.game_item_id}]),
        )
        .order_by(SourceRecord.row_index)
    )
    for record, source_file in pros_rows:
        group_id = record.data.get("ItemGroupId")
        role_rows = await session.execute(
            select(SourceRecord, SourceFile)
            .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
            .where(
                SourceFile.logical_source_path == "BinData/role/roleinfo.json",
                SourceRecord.data["SkillBranchIds"].contains([group_id]),
            )
            .order_by(SourceRecord.row_index)
            .limit(2)
        )
        role_matches = list(role_rows)
        role_record, role_file = role_matches[0] if len(role_matches) == 1 else (None, None)
        progression_kind = (
            "skill_tree_material" if role_record else "unresolved_progression_material"
        )
        project_record = None
        project_file = None
        project_role_field = None
        if role_record is None:
            project_rows = await session.execute(
                select(SourceRecord, SourceFile)
                .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
                .where(
                    SourceFile.logical_source_path == "BinData/RoleDev/roledevprosproject.json",
                    or_(
                        SourceRecord.data["RoleItemGroup"].contains([group_id]),
                        SourceRecord.data["SkillItemGroup"].contains([group_id]),
                        SourceRecord.data["WeaponBreachItemGroup"].contains([group_id]),
                    ),
                )
                .order_by(SourceRecord.row_index)
                .limit(2)
            )
            project_matches = list(project_rows)
            if len(project_matches) == 1:
                project_record, project_file = project_matches[0]
                project_raw = project_record.data
                project_role_fields = [
                    ("RoleItemGroup", "resonator_level_material"),
                    ("SkillItemGroup", "resonator_skill_material"),
                    ("WeaponBreachItemGroup", "weapon_breach_material"),
                ]
                matched_fields = [
                    (field, kind)
                    for field, kind in project_role_fields
                    if group_id in project_raw.get(field, [])
                ]
                role_id_from_project = project_raw.get("Id")
                matching_role_rows = await session.execute(
                    select(SourceRecord, SourceFile)
                    .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
                    .where(
                        SourceFile.logical_source_path == "BinData/role/roleinfo.json",
                        SourceRecord.data["Id"].as_integer() == role_id_from_project,
                    )
                    .order_by(SourceRecord.row_index)
                    .limit(2)
                )
                exact_roles = list(matching_role_rows)
                if len(matched_fields) == 1 and len(exact_roles) == 1:
                    project_role_field, progression_kind = matched_fields[0]
                    role_record, role_file = exact_roles[0]
        role_id = role_record.data.get("Id") if role_record else None
        character_node = (
            await session.scalar(select(Node).where(Node.canonical_key == f"character:{role_id}"))
            if isinstance(role_id, int)
            else None
        )
        for material_index, entry in enumerate(record.data.get("ItemGroup", [])):
            if entry.get("Item1") != item.game_item_id:
                continue
            progression_uses.append(
                {
                    "kind": progression_kind,
                    "character": await _node_label(session, character_node.id, locale)
                    if character_node
                    else None,
                    "required_count": entry.get("Item2"),
                    "item_group_id": group_id,
                    "project_role_field": project_role_field,
                    "source": {
                        "file": source_file.logical_source_path,
                        "row": record.row_index,
                        "raw_path": f"$[{record.row_index}].ItemGroup[{material_index}]",
                        "basis": (
                            "exact ItemGroup[].Item1 == ItemInfo.Id; ItemGroupId is present in RoleInfo.SkillBranchIds"
                            if project_record is None
                            else "exact ItemGroup[].Item1 == ItemInfo.Id; ItemGroupId is present in RoleDevProsProject role group; project Id equals RoleInfo.Id"
                        ),
                        "role_source_file": role_file.logical_source_path if role_file else None,
                        "role_source_row": role_record.row_index if role_record else None,
                        "project_source_file": project_file.logical_source_path
                        if project_file
                        else None,
                        "project_source_row": project_record.row_index if project_record else None,
                    },
                }
            )

    quest_uses: list[dict[str, Any]] = []
    quest_node_rows = await session.execute(
        select(SourceRecord, SourceFile)
        .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
        .where(
            SourceFile.logical_source_path == "BinData/QuestNodeData/questnodedata.json",
            SourceRecord.data.contains(
                {
                    "Data": {
                        "Condition": {
                            "HandInItems": {"GroupConfig": {"ItemIds": [item.game_item_id]}}
                        }
                    }
                }
            ),
        )
        .order_by(SourceRecord.row_index)
    )
    for record, source_file in quest_node_rows:
        raw = record.data
        key = raw.get("Key", "")
        quest_id = int(key.split("_", 1)[0]) if key.split("_", 1)[0].isdigit() else None
        quest = (
            await session.scalar(select(Quest).where(Quest.game_quest_id == quest_id))
            if quest_id is not None
            else None
        )
        hand_in = raw.get("Data", {}).get("Condition", {}).get("HandInItems", {})
        group_config = hand_in.get("GroupConfig", {})
        quest_uses.append(
            {
                "kind": "quest_hand_in_requirement",
                "quest": await _quest_info(session, quest, locale, game_version)
                if quest
                else {"game_quest_id": quest_id, "canonical_key": f"quest:{quest_id}"},
                "quest_node_key": key,
                "required_count": group_config.get("Count"),
                "repeat_items": hand_in.get("RepeatItems"),
                "source": {
                    "file": source_file.logical_source_path,
                    "row": record.row_index,
                    "raw_path": f"$[{record.row_index}].Data.Condition.HandInItems.GroupConfig",
                    "basis": "exact HandInItems.GroupConfig.ItemIds[] == ItemInfo.Id",
                },
            }
        )

    shop_offers = []
    shop_rows = await session.execute(
        select(SourceRecord, SourceFile)
        .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
        .where(
            SourceFile.logical_source_path == "BinData/shop/shopfixed.json",
            SourceRecord.release_id == release_id,
            SourceRecord.data["ItemId"].as_integer() == item.game_item_id,
        )
        .order_by(SourceRecord.row_index)
    )
    for record, source_file in shop_rows:
        raw = record.data
        shop_rows_by_id = await session.execute(
            select(SourceRecord, SourceFile)
            .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
            .where(
                SourceFile.logical_source_path == "BinData/shop/shopinfo.json",
                SourceRecord.release_id == release_id,
                SourceRecord.data["Id"].as_integer() == raw.get("ShopId", -1),
            )
            .order_by(SourceRecord.row_index)
            .limit(2)
        )
        shop_matches = list(shop_rows_by_id)
        shop_record, shop_file = shop_matches[0] if len(shop_matches) == 1 else (None, None)
        shop_name_key = await session.scalar(
            select(LocalizationKey).where(
                LocalizationKey.key == (shop_record.data.get("ShopName") if shop_record else "")
            )
        )
        prices = []
        for price in raw.get("Price", []):
            currency_id = price.get("Key")
            currency_node = (
                await session.scalar(
                    select(Node).where(Node.canonical_key == f"item:{currency_id}")
                )
                if isinstance(currency_id, int)
                else None
            )
            prices.append(
                {
                    "item_id": currency_id,
                    "item": await _node_label(session, currency_node.id, locale)
                    if currency_node
                    else None,
                    "amount": price.get("Value"),
                }
            )
        shop_offers.append(
            {
                "offer_id": raw.get("Id"),
                "shop_id": raw.get("ShopId"),
                "shop_name": await _localized(
                    session, shop_name_key.id if shop_name_key else None, locale, release_id
                ),
                "item_count": raw.get("ItemNum"),
                "purchase_limit": raw.get("LimitNum"),
                "visible": raw.get("Show"),
                "prices": prices,
                "source": {
                    "file": source_file.logical_source_path,
                    "row": record.row_index,
                    "raw_path": f"$[{record.row_index}]",
                    "basis": "exact ShopFixed.ItemId == ItemInfo.Id",
                    "shop_file": shop_file.logical_source_path if shop_file else None,
                    "shop_row": shop_record.row_index if shop_record else None,
                },
            }
        )
    reward_ids = list(await session.scalars(
        select(SourceRecord.data["Id"].as_integer())
        .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
        .where(SourceFile.logical_source_path == "BinData/drop/droppackage.json",
               SourceRecord.release_id == release_id,
               SourceRecord.data["DropPreview"].contains([{"Key": item.game_item_id}]))
    ))
    quest_rewards = []
    reward_rows = await session.execute(
        select(SourceRecord, Quest)
        .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
        .join(Quest, Quest.game_quest_id == SourceRecord.data["QuestId"].as_integer())
        .where(SourceFile.logical_source_path == "BinData/QuestData/questdata.json",
               SourceRecord.release_id == release_id,
               SourceRecord.data["Data"]["RewardId"].as_integer().in_(reward_ids or [-1]))
        .order_by(Quest.game_quest_id).limit(ITEM_QUEST_REWARD_LIMIT + 1)
    )
    for record, quest in reward_rows:
        quest_rewards.append({"quest": await _quest_info(session, quest, locale, game_version),
                              "source": {"basis": "QuestData.Data.RewardId -> DropPackage.DropPreview.Key",
                                         "row": record.row_index},
                              "kind": "possible_quest_reward"})
    image_url = (await entity_image_urls(session, [node.id])).get(node.id)
    return {
        "item": {
            "canonical_key": node.canonical_key,
            "canonical_name": item.canonical_name,
            "name": await _localized(session, item.name_key_id, locale, release_id),
            "description": await _localized(session, item.description_key_id, locale, release_id),
            "game_item_id": item.game_item_id,
            "status": node.status,
        },
        "media": {
            "status": "ready" if image_url else "asset_not_extracted",
            "image_url": image_url,
            "icon_asset_path": raw.get("Icon"),
            "small_icon_asset_path": raw.get("IconSmall"),
            "medium_icon_asset_path": raw.get("IconMiddle"),
            "note": "Artwork comes from published client assets; the recording version is separate from the story snapshot.",
        },
        "gameplay_metadata": {
            "item_type": raw.get("ItemType"),
            "main_type_id": raw.get("MainTypeId"),
            "quality_id": raw.get("QualityId"),
            "show_types": raw.get("ShowTypes", []),
            "item_access_ids": raw.get("ItemAccess", []),
        },
        "map_sources": await item_map_sources(session, item.game_item_id),
        "quest_rewards": quest_rewards[:ITEM_QUEST_REWARD_LIMIT],
        "quest_rewards_has_more": len(quest_rewards) > ITEM_QUEST_REWARD_LIMIT,
        "acquisition_paths": access_paths,
        "harvest_sources": enrichment_sources,
        "harvest_world_maps": world_map_payloads,
        "progression_uses": progression_uses,
        "quest_uses": quest_uses,
        "shop_offers": shop_offers,
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
            "canonical_name": location.canonical_name,
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
            "asset_references": _logical_image_references((raw_source or {}).get("raw_record", {})),
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
        parent_key = (
            nodes_by_game_id.get(str(parent_id)) if parent_id not in (None, 0, "0") else None
        )
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
            select(QuestAction)
            .where(QuestAction.quest_state_node_id == state.node_id)
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
        select(Scene)
        .where(Scene.node_id.in_(scene_ids))
        .order_by(Scene.authored_order, Scene.node_id)
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
    release_id = await _release_id(session, game_version)

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

    # Some snapshots do not ship precomputed speaker/character links. A unique,
    # exact match between the character's FormationRoleCard and Speaker's
    # RolePileIconAsset is source evidence; shared icon paths are intentionally
    # left unresolved rather than expanded into multiple character matches.
    role_id_text = node.canonical_key.partition(":")[2]
    role_id = int(role_id_text) if role_id_text.isdigit() else None
    if release_id is not None and role_id is not None and not speakers:
        role_source_rows = await session.scalars(
            select(SourceRecord)
            .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
            .where(
                SourceRecord.release_id == release_id,
                SourceFile.logical_source_path == "BinData/role/roleinfo.json",
                SourceRecord.data["Id"].as_integer() == role_id,
            )
            .order_by(SourceRecord.row_index)
            .limit(2)
        )
        role_sources = list(role_source_rows)
        if len(role_sources) == 1:
            role_source = role_sources[0]
            role_asset = role_source.data.get("FormationRoleCard")
            if isinstance(role_asset, str) and role_asset:
                role_asset_count = await session.scalar(
                    select(func.count(SourceRecord.id))
                    .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
                    .where(
                        SourceRecord.release_id == release_id,
                        SourceFile.logical_source_path == "BinData/role/roleinfo.json",
                        SourceRecord.data["FormationRoleCard"].as_string() == role_asset,
                    )
                )
                speaker_source_rows = await session.scalars(
                    select(SourceRecord)
                    .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
                    .where(
                        SourceRecord.release_id == release_id,
                        SourceFile.logical_source_path == "BinData/speaker/speaker.json",
                        SourceRecord.data["RolePileIconAsset"].as_string() == role_asset,
                    )
                    .order_by(SourceRecord.row_index)
                    .limit(2)
                )
                speaker_sources = list(speaker_source_rows)
                if role_asset_count == 1 and len(speaker_sources) == 1:
                    speaker_source = speaker_sources[0]
                    speaker_id = speaker_source.data.get("Id")
                    matched_speaker = await session.scalar(
                        select(Speaker).where(Speaker.game_speaker_id == speaker_id)
                    )
                    if matched_speaker is not None:
                        speaker_node = await session.get(Node, matched_speaker.node_id)
                        if speaker_node is not None:
                            own_speaker_ids.add(matched_speaker.node_id)
                            speakers.append(
                                {
                                    "canonical_key": speaker_node.canonical_key,
                                    "game_speaker_id": matched_speaker.game_speaker_id,
                                    "resolution": "unique_exact_asset_crosswalk",
                                    "evidence_source_record_id": speaker_source.id,
                                    "evidence": {
                                        "role_file": "BinData/role/roleinfo.json",
                                        "role_row": role_source.row_index,
                                        "role_field": "FormationRoleCard",
                                        "speaker_file": "BinData/speaker/speaker.json",
                                        "speaker_row": speaker_source.row_index,
                                        "speaker_field": "RolePileIconAsset",
                                        "asset_path": role_asset,
                                        "uniqueness": "exact asset path occurs once in each table for this release",
                                    },
                                }
                            )
                            speaker_states = (
                                select(QuestAction.quest_state_node_id)
                                .join(DialogueLine, DialogueLine.action_node_id == QuestAction.node_id)
                                .where(DialogueLine.speaker_node_id == matched_speaker.node_id)
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
        state_rows = list(
            await session.scalars(
                select(QuestState)
                .where(QuestState.node_id.in_(co_states))
                .order_by(QuestState.state_key)
            )
        )
        shared_scenes = []
        # Expose source-authored context for the relationship card. A shared
        # flow state is evidence of co-presence only, so return its transcript
        # with the state and quest references rather than asserting a relation.
        for shared_state in state_rows[:8]:
            dialogue_statement = (
                select(DialogueLine, QuestAction, QuestState, Node, SourceRecord, SourceFile)
                .join(Node, Node.id == DialogueLine.node_id)
                .join(QuestAction, QuestAction.node_id == DialogueLine.action_node_id)
                .join(QuestState, QuestState.node_id == QuestAction.quest_state_node_id)
                .outerjoin(SourceRecord, SourceRecord.id == DialogueLine.source_record_id)
                .outerjoin(SourceFile, SourceFile.id == SourceRecord.source_file_id)
                .where(QuestState.node_id == shared_state.node_id)
                .order_by(QuestAction.action_index, DialogueLine.source_index, DialogueLine.node_id)
                .limit(80)
            )
            transcript_rows = list((await session.execute(dialogue_statement)).all())
            transcript = [
                await _dialogue_payload(
                    session, row, locale_code=locale, release_id=release_id
                )
                for row in transcript_rows
            ]
            owner_nodes = select(Edge.from_node_id).join(
                RelationType, Edge.relation_type_id == RelationType.id
            ).where(
                Edge.to_node_id == shared_state.node_id,
                RelationType.key == "references_flow_state",
                Edge.layer == "source",
            )
            quest_rows = await session.scalars(
                select(Quest)
                .where(
                    or_(
                        Quest.node_id.in_(owner_nodes),
                        Quest.game_quest_id.in_(
                            select(QuestNode.game_quest_id).where(
                                QuestNode.node_id.in_(owner_nodes),
                                QuestNode.game_quest_id.is_not(None),
                            )
                        ),
                    )
                )
                .order_by(Quest.game_quest_id)
                .limit(5)
            )
            state_source = (
                await session.execute(
                    select(SourceRecord, SourceFile)
                    .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
                    .where(SourceRecord.id == shared_state.source_record_id)
                )
            ).first()
            shared_scenes.append(
                {
                    "flow_state": shared_state.state_key,
                    "quest_refs": [
                        await _quest_info(session, quest, locale, game_version)
                        for quest in quest_rows
                    ],
                    "dialogue": transcript,
                    "dialogue_truncated": len(transcript_rows) == 80,
                    "basis": "full authored dialogue rows in the same QuestState; shared state does not imply direct conversation",
                    "source": {
                        "file": state_source[1].logical_source_path if state_source else None,
                        "row": state_source[0].row_index if state_source else None,
                        "record_id": shared_state.source_record_id,
                    },
                }
            )
        co_present_characters.append(
            {
                "character": await _node_label(session, entity_node_id, locale),
                "basis": "shared_flow_state_dialogue",
                "flow_states": [state.state_key for state in state_rows],
                "shared_scenes": shared_scenes,
                "shared_scenes_total": len(state_rows),
                "shared_scenes_truncated": len(state_rows) > len(shared_scenes),
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
    raw_role_id = node.canonical_key.partition(":")[2]
    progression_materials = await _character_progression_materials(
        session,
        int(raw_role_id) if raw_role_id.isdigit() else -1,
        locale,
        release_id,
    )
    return {
        "character": {
            "canonical_key": node.canonical_key,
            "canonical_name": character.canonical_name,
            "name": await _localized(session, character.name_key_id, locale, release_id),
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
        "progression_materials": progression_materials,
        "explicit_links": links,
        "source_evidence": source_evidence,
        "connection_summary": {
            "confirmed_speakers": len(speakers),
            "quests_with_dialogue": len(quests),
            "co_present_characters": len(co_present_characters),
            "explicit_entity_links": len(links),
            "configuration_records": sum(item["record_count"] for item in source_evidence),
            "narrative_status": (
                "speaker_mapped" if has_speaker_mapping else "no_confirmed_speaker_crosswalk"
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

"""Shared source-backed response builders for story browse endpoints."""

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.api.quest_classification import QUEST_NODES, item_requirement
from wuwa_story.db.models.core import (
    Quest,
    QuestNode,
)
from wuwa_story.db.models.graph import Node
from wuwa_story.db.models.raw import SourceFile, SourceRecord
from wuwa_story.db.repositories.quest_scope import quest_state_links
from wuwa_story.story.domain import choice_branch as _choice_branch
from wuwa_story.story.domain import version_key as _version_key
from wuwa_story.story.service import (
    _dialogue_payload,
    _explicit_entity_links,
    _latest_source_record,
    _localized,
    _node_label,
    _provenance,
    _quest_info,
    _quest_state_ids,
    _release_id,
)

__all__ = ['_choice_branch', '_version_key', '_dialogue_payload', '_explicit_entity_links', '_latest_source_record', '_localized', '_node_label', '_provenance', '_quest_info', '_quest_state_ids', '_release_id']

def _display_label(value: str | None) -> str | None:
    """Reject upstream locale placeholders that are error messages, not names."""
    if not value or not value.strip():
        return None
    value = value.strip()
    if "please contact our customer service" in value.casefold():
        return None
    return value


async def _character_progression_materials(
    session: AsyncSession, role_id: int, locale_code: str, release_id: int | None
) -> list[dict[str, Any]]:
    """Resolve a character's exact ascension and configured skill material groups."""
    if release_id is None:
        return []
    role_rows = await session.execute(
        select(SourceRecord, SourceFile)
        .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
        .where(
            SourceRecord.release_id == release_id,
            SourceFile.logical_source_path == "BinData/role/roleinfo.json",
            SourceRecord.data["Id"].as_integer() == role_id,
        )
        .order_by(SourceRecord.row_index)
        .limit(2)
    )
    role_matches = list(role_rows)
    if len(role_matches) != 1:
        return []
    role_record, role_file = role_matches[0]
    raw_role = role_record.data
    group_kinds: dict[int, set[str]] = {}

    breach_id = raw_role.get("BreachId")
    if isinstance(breach_id, int):
        group_kinds.setdefault(breach_id, set()).add("character_ascension")

    for group_id in raw_role.get("SkillBranchIds") or []:
        if isinstance(group_id, int):
            group_kinds.setdefault(group_id, set()).add("skill_tree_material")

    project_rows = await session.scalars(
        select(SourceRecord)
        .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
        .where(
            SourceRecord.release_id == release_id,
            SourceFile.logical_source_path == "BinData/RoleDev/roledevprosproject.json",
            SourceRecord.data["Id"].as_integer() == role_id,
        )
        .order_by(SourceRecord.row_index)
        .limit(2)
    )
    project_matches = list(project_rows)
    if len(project_matches) == 1:
        project = project_matches[0].data
        for field, kind in (
            ("RoleItemGroup", "resonator_level_material"),
            ("SkillItemGroup", "resonator_skill_material"),
        ):
            for group_id in project.get(field) or []:
                if isinstance(group_id, int):
                    group_kinds.setdefault(group_id, set()).add(kind)

    if not group_kinds:
        return []
    item_rows = await session.execute(
        select(SourceRecord, SourceFile)
        .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
        .where(
            SourceRecord.release_id == release_id,
            SourceFile.logical_source_path == "BinData/RoleDev/roledevprosroleitem.json",
            SourceRecord.data["ItemGroupId"].as_integer().in_(group_kinds),
        )
        .order_by(SourceRecord.row_index)
    )
    rows = list(item_rows)
    breach_rows = await session.execute(
        select(SourceRecord, SourceFile)
        .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
        .where(
            SourceRecord.release_id == release_id,
            SourceFile.logical_source_path == "BinData/role_level/rolebreach.json",
            SourceRecord.data["BreachGroupId"].as_integer() == breach_id,
        )
        .order_by(SourceRecord.data["MaxLevel"].as_integer(), SourceRecord.row_index)
    )
    materials: list[dict[str, Any]] = []

    async def append_material(
        item_id: Any,
        count: Any,
        kind: str,
        source_file: SourceFile,
        source_record: SourceRecord,
        raw_path: str,
        *,
        level_cap: int | None = None,
        breach_level: int | None = None,
        group_id: int | None = None,
        basis: str,
    ) -> None:
        if not isinstance(item_id, int):
            return
        item_node = await session.scalar(
            select(Node).where(Node.canonical_key == f"item:{item_id}")
        )
        materials.append(
            {
                "kind": kind,
                "item_id": item_id,
                "item": await _node_label(session, item_node.id, locale_code)
                if item_node
                else None,
                "required_count": count,
                "level_cap": level_cap,
                "breach_level": breach_level,
                "group_id": group_id,
                "source": {
                    "file": source_file.logical_source_path,
                    "row": source_record.row_index,
                    "raw_path": raw_path,
                    "basis": basis,
                    "role_file": role_file.logical_source_path,
                    "role_row": role_record.row_index,
                },
            }
        )

    for record, source_file in breach_rows:
        for index, entry in enumerate(record.data.get("BreachConsume") or []):
            await append_material(
                entry.get("Key"),
                entry.get("Value"),
                "character_ascension",
                source_file,
                record,
                f"$[{record.row_index}].BreachConsume[{index}]",
                level_cap=record.data.get("MaxLevel"),
                breach_level=record.data.get("BreachLevel"),
                group_id=breach_id,
                basis="exact RoleInfo.BreachId == RoleBreach.BreachGroupId; exact BreachConsume[].Key == ItemInfo.Id",
            )

    for record, source_file in rows:
        group_id = record.data.get("ItemGroupId")
        for index, entry in enumerate(record.data.get("ItemGroup") or []):
            for kind in sorted(group_kinds.get(group_id, ())):
                await append_material(
                    entry.get("Item1"),
                    entry.get("Item2"),
                    kind,
                    source_file,
                    record,
                    f"$[{record.row_index}].ItemGroup[{index}]",
                    group_id=group_id,
                    basis="exact RoleInfo/RoleDevProsProject material group ID == RoleDevProsRoleItem.ItemGroupId; exact ItemGroup[].Item1 == ItemInfo.Id",
                )
    return materials


def _quest_state_links():
    """The same explicit ownership paths for transcript and reverse navigation."""
    return quest_state_links()


async def _quest_references_from_links(
    session: AsyncSession,
    links: list[dict[str, Any]],
    locale_code: str,
    game_version: str | None = None,
    entity_key: str | None = None,
) -> list[dict[str, Any]]:
    quest_node_ids = {link["node"]["id"] for link in links if link["node"]["type"] == "quest_node"}
    if not quest_node_ids:
        return []
    quest_nodes = list(
        await session.scalars(select(QuestNode).where(QuestNode.node_id.in_(quest_node_ids)))
    )
    item_id = int(entity_key[5:]) if entity_key and re.fullmatch(r"item:[0-9]+", entity_key) else None
    condition_rows = {}
    if item_id is not None:
        release_id = await _release_id(session, game_version)
        keys = {f"{row.game_quest_id}_{row.game_node_id}" for row in quest_nodes}
        condition_rows = {row.data["Key"]: row for row in await session.scalars(
            select(SourceRecord).join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
            .where(SourceRecord.release_id == release_id, SourceFile.logical_source_path == QUEST_NODES,
                   SourceRecord.data["Key"].astext.in_(keys)))}
    game_quest_ids = {row.game_quest_id for row in quest_nodes if row.game_quest_id is not None}
    quests = {
        quest.game_quest_id: quest
        for quest in await session.scalars(
            select(Quest).where(Quest.game_quest_id.in_(game_quest_ids or {-1}))
        )
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
        source = condition_rows.get(f"{quest_node.game_quest_id}_{quest_node.game_node_id}")
        interpretation = item_requirement(source.data.get("Data") or {}, item_id) if source else None
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
                "interpretation": {**interpretation, "source": {"file": QUEST_NODES, "row": source.row_index}}
                if interpretation else None,
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

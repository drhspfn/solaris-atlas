"""Shared source-backed response builders for story browse endpoints."""

import re
from typing import Any

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.db.models.core import (
    Character,
    Item,
    Location,
    PlayerChoice,
    Quest,
    QuestNode,
    Speaker,
)
from wuwa_story.db.models.graph import Edge, Node, NodeRevision, NodeType
from wuwa_story.db.models.i18n import Locale, LocalizationKey, LocalizationValue
from wuwa_story.db.models.ontology import RelationType
from wuwa_story.db.models.ops import GameRelease
from wuwa_story.db.models.raw import SourceFile, SourceRecord
from wuwa_story.db.models.search import EntityAlias, SearchDocument


def _display_label(value: str | None) -> str | None:
    """Reject upstream locale placeholders that are error messages, not names."""
    if not value or not value.strip():
        return None
    value = value.strip()
    if "please contact our customer service" in value.casefold():
        return None
    return value

async def _release_id(session: AsyncSession, game_version: str | None) -> int | None:
    if game_version:
        return await session.scalar(
            select(GameRelease.id)
            .where(GameRelease.game_version == game_version)
            .order_by(GameRelease.sequence.desc())
            .limit(1)
        )
    releases = list(await session.scalars(select(GameRelease)))
    if not releases:
        return None
    # Import order is operational metadata, not game chronology. A late import of
    # 3.1 must not make 3.1 the default view when a 3.6 snapshot already exists.
    return max(
        releases, key=lambda release: (_version_key(release.game_version), release.sequence)
    ).id

def _version_key(value: str | None) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", value or ""))

async def _localized(
    session: AsyncSession,
    key_id: int | None,
    locale_code: str,
    release_id: int | None,
) -> dict[str, Any] | None:
    cache_key = (key_id, locale_code, release_id)
    cache = session.info.setdefault("wuwa_localized_cache", {})
    if cache_key in cache:
        return cache[cache_key]
    if key_id is None:
        return None
    key = await session.get(LocalizationKey, key_id)
    locale = await session.scalar(select(Locale).where(Locale.code == locale_code))
    if key is None or locale is None or release_id is None:
        result = {
            "key": key.key if key else None,
            "locale": locale_code,
            "content": None,
            "resolution": "unavailable",
        }
        cache[cache_key] = result
        return result
    requested_release = await session.get(GameRelease, release_id)
    values = list(
        (
            await session.execute(
                select(LocalizationValue, GameRelease)
                .join(GameRelease, GameRelease.id == LocalizationValue.release_id)
                .where(
                    LocalizationValue.key_id == key_id,
                    LocalizationValue.locale_id == locale.id,
                )
            )
        ).all()
    )
    exact = next((value for value, release in values if release.id == release_id), None)
    chosen = exact
    target_version = _version_key(requested_release.game_version if requested_release else None)
    if exact is None or exact.status != "resolved_nonempty" or not exact.content:
        previous_nonempty = [
            (value, release)
            for value, release in values
            if value.status == "resolved_nonempty"
            and value.content
            and _version_key(release.game_version) <= target_version
        ]
        if previous_nonempty:
            chosen, source_release = max(
                previous_nonempty,
                key=lambda item: (_version_key(item[1].game_version), item[1].sequence),
            )
        else:
            source_release = requested_release
    else:
        source_release = requested_release
    if chosen is None:
        source_release = requested_release
    result = {
        "key": key.key,
        "locale": locale_code,
        "content": chosen.content if chosen else None,
        "resolution": chosen.status if chosen else "missing_key",
        "requested_resolution": exact.status if exact else "missing_key",
        "fallback": bool(chosen and chosen.release_id != release_id),
        "source_game_version": source_release.game_version if source_release else None,
        "source_record_id": chosen.source_record_id if chosen else None,
    }
    cache[cache_key] = result
    return result

async def _node_label(session: AsyncSession, node_id: int, locale_code: str) -> dict[str, Any]:
    node = await session.get(Node, node_id)
    if node is None:
        return {"id": node_id, "canonical_key": None, "type": None, "label": None}
    node_type = await session.get(NodeType, node.type_id)
    locale = await session.scalar(select(Locale).where(Locale.code == locale_code))
    name_key_id = None
    canonical_name = None
    name_row = await session.execute(
        select(
            func.coalesce(
                Character.name_key_id,
                Item.name_key_id,
                Location.name_key_id,
                Quest.name_key_id,
                Speaker.name_key_id,
            ),
            func.coalesce(Character.canonical_name, Item.canonical_name, Location.canonical_name),
        )
        .select_from(Node)
        .outerjoin(Character, Character.node_id == Node.id)
        .outerjoin(Item, Item.node_id == Node.id)
        .outerjoin(Location, Location.node_id == Node.id)
        .outerjoin(Quest, Quest.node_id == Node.id)
        .outerjoin(Speaker, Speaker.node_id == Node.id)
        .where(Node.id == node_id)
    )
    if row := name_row.one_or_none():
        name_key_id, canonical_name = row
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
    alias = _display_label(alias)
    canonical_name = _display_label(canonical_name)
    if not alias and name_key_id is not None:
        release_id = await _release_id(session, None)
        localized = await _localized(session, name_key_id, locale_code, release_id)
        alias = _display_label(localized.get("content")) if localized else None
    fallback_label = (
        f"Area · {node.canonical_key.partition(':')[2]}"
        if node_type and node_type.key == "area"
        else node.slug or node.canonical_key
    )
    return {
        "id": node.id,
        "canonical_key": node.canonical_key,
        "type": node_type.key if node_type else None,
        "label": alias or canonical_name or fallback_label,
        "status": node.status,
    }

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

def _choice_branch(
    params: dict[str, Any], state_key: str, action_index: int, target_line_id: str | None
) -> dict[str, Any] | None:
    """Read one source-authored talk sequence without inferring runtime paths."""
    prefix = f"talk_item:{state_key}:{action_index}:"
    if not target_line_id or not target_line_id.startswith(prefix):
        return None
    items = params.get("TalkItems")
    sequences = params.get("TalkSequence")
    if not isinstance(items, list) or not isinstance(sequences, list):
        return None
    try:
        target_item = items[int(target_line_id.removeprefix(prefix))]
        target_talk_id = target_item["Id"]
    except (ValueError, IndexError, TypeError, KeyError):
        return None
    matches = [
        (index, sequence.index(target_talk_id))
        for index, sequence in enumerate(sequences)
        if isinstance(sequence, list) and target_talk_id in sequence
    ]
    if len(matches) != 1:
        return None
    sequence_index, start = matches[0]
    local_ids: dict[Any, list[str]] = {}
    for index, item in enumerate(items):
        if isinstance(item, dict) and item.get("Id") is not None:
            local_ids.setdefault(item["Id"], []).append(f"{prefix}{index}")

    def unique_line_id(talk_id: Any) -> str | None:
        candidates = local_ids.get(talk_id, [])
        return candidates[0] if len(candidates) == 1 else None

    line_ids = [unique_line_id(talk_id) for talk_id in sequences[sequence_index][start:]]
    if not line_ids or any(line_id is None for line_id in line_ids):
        return None
    continuation_line_id = None
    transitions = params.get("SequenceTransitions") or {}
    if isinstance(transitions, dict):
        outgoing = transitions.get(str(sequence_index), [])
        if isinstance(outgoing, list) and len(outgoing) == 1 and isinstance(outgoing[0], dict):
            transition = outgoing[0]
            next_index = transition.get("NextSequenceIndex")
            if not transition.get("OptionTextKey") and isinstance(next_index, int) and 0 <= next_index < len(sequences):
                next_sequence = sequences[next_index]
                if isinstance(next_sequence, list) and next_sequence:
                    continuation_line_id = unique_line_id(next_sequence[0])
    return {"line_ids": line_ids, "continuation_line_id": continuation_line_id}

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
    choices = sorted(
        choices,
        key=lambda choice: (
            (choice.metadata_json or {}).get("choice_index")
            if (choice.metadata_json or {}).get("choice_index") is not None
            else 999,
            choice.node_id,
        ),
    )
    choice_ids = [choice.node_id for choice in choices]
    target_keys: dict[int, set[str]] = {choice_id: set() for choice_id in choice_ids}
    if choice_ids:
        direct_targets = await session.execute(
            select(Edge.from_node_id, Node.canonical_key)
            .join(RelationType, RelationType.id == Edge.relation_type_id)
            .join(Node, Node.id == Edge.to_node_id)
            .where(Edge.from_node_id.in_(choice_ids), RelationType.key == "choice_branch")
        )
        for choice_id, target_key in direct_targets:
            target_keys[choice_id].add(target_key)
        unresolved = [choice_id for choice_id in choice_ids if not target_keys[choice_id]]
        if unresolved:
            nested_actions = await session.execute(
                select(Edge.from_node_id, Edge.to_node_id)
                .join(RelationType, RelationType.id == Edge.relation_type_id)
                .where(Edge.from_node_id.in_(unresolved), RelationType.key == "contains_nested_action")
            )
            nested_owners: dict[int, int] = {
                nested_id: choice_id for choice_id, nested_id in nested_actions
            }
            if nested_owners:
                jumps = await session.execute(
                    select(Edge.from_node_id, Node.canonical_key)
                    .join(RelationType, RelationType.id == Edge.relation_type_id)
                    .join(Node, Node.id == Edge.to_node_id)
                    .where(Edge.from_node_id.in_(nested_owners), RelationType.key == "jumps_to_talk")
                )
                for nested_id, target_key in jumps:
                    target_keys[nested_owners[nested_id]].add(target_key)
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
                "target_line_id": next(iter(target_keys[choice.node_id]))
                if len(target_keys[choice.node_id]) == 1 else None,
                "branch": _choice_branch(
                    action.params, state.state_key, action.action_index,
                    next(iter(target_keys[choice.node_id])),
                ) if action and state and len(target_keys[choice.node_id]) == 1 else None,
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

def _quest_state_links():
    """The same explicit ownership paths for transcript and reverse navigation."""
    def source_edges(relation):
        return select(Edge.from_node_id.label("parent"), Edge.to_node_id.label("child")).join(
            RelationType, RelationType.id == Edge.relation_type_id
        ).where(Edge.layer == "source", RelationType.key == relation).subquery()

    children = source_edges("has_quest_node")
    steps = source_edges("has_plot_step")
    scenes = source_edges("presents_scene")
    owners = select(Quest.node_id.label("quest_id"), Quest.node_id.label("owner_id")).union(
        select(Quest.node_id, children.c.child).join(children, children.c.parent == Quest.node_id),
        select(Quest.node_id, scenes.c.child)
        .join(steps, steps.c.parent == Quest.node_id)
        .join(scenes, scenes.c.parent == steps.c.child),
    ).subquery()
    references = source_edges("references_flow_state")
    return select(owners.c.quest_id, references.c.child.label("state_id")).join(
        references, references.c.parent == owners.c.owner_id
    ).distinct().subquery()


async def _quest_state_ids(session: AsyncSession, quest_node_id: int) -> list[int]:
    links = _quest_state_links()
    return list(await session.scalars(select(links.c.state_id).where(links.c.quest_id == quest_node_id)))

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
    quest_node_ids = {link["node"]["id"] for link in links if link["node"]["type"] == "quest_node"}
    if not quest_node_ids:
        return []
    quest_nodes = list(
        await session.scalars(select(QuestNode).where(QuestNode.node_id.in_(quest_node_ids)))
    )
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

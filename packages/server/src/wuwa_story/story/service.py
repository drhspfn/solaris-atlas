"""Request-scoped story loading, grouping and response presentation."""

from collections import defaultdict
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.api.quest_classification import (
    QUEST_NODES,
    quest_classification,
    quest_item_requirements,
)
from wuwa_story.db.models.core import (
    Quest,
)
from wuwa_story.db.models.raw import SourceFile, SourceRecord
from wuwa_story.db.repositories import story_browsing as repository
from wuwa_story.db.repositories.nodes import get_node
from wuwa_story.story.domain import choice_branch as _choice_branch
from wuwa_story.story.domain import localization_payload, version_key
from wuwa_story.urls import node_address


async def releases(session):
    if "story_releases" not in session.info:
        session.info["story_releases"] = {
            row.id: row for row in await repository.fetch_releases(session)
        }
    return session.info["story_releases"]


async def prefetch_localizations(session, key_ids, locale_code, release_id):
    cache = session.info.setdefault("wuwa_localized_cache", {})
    needed = sorted(
        {key for key in key_ids if key is not None and (key, locale_code, release_id) not in cache}
    )
    if not needed:
        return
    locales = session.info.setdefault("story_locales", {})
    if locale_code not in locales:
        locales[locale_code] = await repository.locale_id(session, locale_code)
    locale_id = locales[locale_code]
    versions = await releases(session)
    requested = versions.get(release_id)
    for offset in range(0, len(needed), 500):
        batch = needed[offset : offset + 500]
        keys = await repository.localization_keys(session, batch)
        grouped = defaultdict(list)
        if locale_id is not None and requested:
            rows = await repository.localization_values(session, batch, locale_id)
            for row in rows:
                grouped[row.key_id].append(row)
        for identity in batch:
            cache[(identity, locale_code, release_id)] = localization_payload(
                keys.get(identity),
                locale_code,
                requested,
                grouped[identity],
                versions,
                available=locale_id is not None,
            )


async def prefetch_node_labels(session, node_ids, locale_code):
    cache = session.info.setdefault("story_node_labels", {})
    needed = sorted(
        {
            identity
            for identity in node_ids
            if identity is not None and (identity, locale_code) not in cache
        }
    )
    if not needed:
        return
    locales = session.info.setdefault("story_locales", {})
    if locale_code not in locales:
        locales[locale_code] = await repository.locale_id(session, locale_code)
    locale_id = locales[locale_code]
    versions = await releases(session)
    latest = max(
        versions.values(),
        key=lambda row: (version_key(row.game_version), row.sequence),
        default=None,
    )

    def display(value):
        if (
            not value
            or not value.strip()
            or "please contact our customer service" in value.casefold()
        ):
            return None
        return value.strip()

    for offset in range(0, len(needed), 500):
        batch = needed[offset : offset + 500]
        rows, aliases = await repository.node_label_rows(session, batch, locale_id)
        await prefetch_localizations(
            session,
            [key for node, _, key, _ in rows if not display(aliases.get(node.id))],
            locale_code,
            latest.id if latest else None,
        )
        for identity in batch:
            cache[(identity, locale_code)] = {
                "id": identity,
                "canonical_key": None,
                "type": None,
                "label": None,
            }
        texts = session.info.get("wuwa_localized_cache", {})
        for node, kind, key, canonical in rows:
            localized = texts.get((key, locale_code, latest.id if latest else None)) or {}
            cache[(node.id, locale_code)] = {
                "id": node.id,
                "canonical_key": node.canonical_key,
                **node_address(node, kind),
                "type": kind,
                "label": display(aliases.get(node.id))
                or display(localized.get("content"))
                or display(canonical)
                or (
                    f"Area · {node.canonical_key.partition(':')[2]}"
                    if kind == "area"
                    else node.canonical_key
                ),
                "status": node.status,
            }


async def prefetch_dialogue(session, rows, locale_code, release_id, *, include_raw=True):
    if not rows:
        return
    cache = session.info.setdefault("story_choices", {})
    line_ids = sorted({row[0].node_id for row in rows if row[0].node_id not in cache})
    for offset in range(0, len(line_ids), 500):
        batch = line_ids[offset : offset + 500]
        for identity in batch:
            cache[identity] = []
        choices = await repository.dialogue_choices(session, batch)
        targets = defaultdict(set)
        choice_ids = {choice.node_id for _, choice, _, _, _ in choices}
        if choice_ids:
            for owner, key in await repository.choice_targets(session, choice_ids):
                targets[owner].add(key)
            unresolved = choice_ids - targets.keys()
            if unresolved:
                owners = defaultdict(set)
                for owner, nested in await repository.nested_actions(session, unresolved):
                    owners[nested].add(owner)
                if owners:
                    for nested, key in await repository.nested_targets(session, owners):
                        for owner in owners[nested]:
                            targets[owner].add(key)
        seen = set()
        for owner, choice, key, record, file in choices:
            if (owner, choice.node_id) not in seen:
                cache[owner].append((choice, key, record, file, targets[choice.node_id]))
                seen.add((owner, choice.node_id))
        for identity in batch:
            cache[identity].sort(
                key=lambda row: (
                    (row[0].metadata_json or {}).get("choice_index")
                    if (row[0].metadata_json or {}).get("choice_index") is not None
                    else 999,
                    row[0].node_id,
                )
            )
    await prefetch_node_labels(session, [row[0].speaker_node_id for row in rows], locale_code)
    keys = {row[0].localization_key_id for row in rows}
    keys.update(choice.localization_key_id for row in rows for choice, *_ in cache[row[0].node_id])
    await prefetch_localizations(session, keys, locale_code, release_id)
    action_ids = {
        row[1].node_id for row in rows if row[1] and (include_raw or cache[row[0].node_id])
    }
    loaded = session.info.setdefault("story_action_params", set())
    missing = sorted(action_ids - loaded)
    for offset in range(0, len(missing), 500):
        # JSON belongs to the action, not each dialogue line. Loading once avoids
        # decoding the same large TalkItems array hundreds of times in a join.
        await repository.load_action_params(session, missing[offset : offset + 500])
    loaded.update(missing)
    if include_raw:
        source_ids = {row[4].id for row in rows if row[4]}
        loaded = session.info.setdefault("story_raw_records", set())
        missing = sorted(source_ids - loaded)
        for offset in range(0, len(missing), 500):
            await repository.load_raw_records(session, missing[offset : offset + 500])
        loaded.update(missing)


async def _release_id(session: AsyncSession, game_version: str | None) -> int | None:
    available = list((await releases(session)).values())
    if game_version:
        return (
            max(
                (row for row in available if row.game_version == game_version),
                key=lambda row: row.sequence,
                default=None,
            ).id
            if any(row.game_version == game_version for row in available)
            else None
        )
    latest = max(
        available, key=lambda row: (version_key(row.game_version), row.sequence), default=None
    )
    return latest.id if latest else None


async def _localized(
    session: AsyncSession, key_id: int | None, locale_code: str, release_id: int | None
) -> dict[str, Any] | None:
    if key_id is None:
        return None
    await prefetch_localizations(session, [key_id], locale_code, release_id)
    return session.info["wuwa_localized_cache"][(key_id, locale_code, release_id)]


async def _node_label(session: AsyncSession, node_id: int, locale_code: str) -> dict[str, Any]:
    await prefetch_node_labels(session, [node_id], locale_code)
    return session.info["story_node_labels"][(node_id, locale_code)]


async def _explicit_entity_links(
    session: AsyncSession, node_id: int, locale_code: str, limit: int = 200
) -> list[dict[str, Any]]:
    rows = await repository.entity_links(session, node_id, limit)
    await prefetch_node_labels(session, [neighbor.id for _, _, neighbor, _ in rows], locale_code)
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
    record: SourceRecord | None, source_file: SourceFile | None, *, include_raw: bool = True
) -> dict[str, Any] | None:
    if record is None:
        return None
    return {
        "source_file": source_file.logical_source_path if source_file else None,
        "source_row": record.row_index,
        "source_key": record.source_key,
        **({"raw_record": record.data} if include_raw else {}),
    }


async def _dialogue_payload(
    session: AsyncSession,
    row: tuple[Any, ...],
    *,
    locale_code: str,
    release_id: int | None,
    include_raw: bool = True,
) -> dict[str, Any]:
    line, action, state, node, record, source_file = row
    speaker = (
        await _node_label(session, line.speaker_node_id, locale_code)
        if line.speaker_node_id
        else None
    )
    text = await _localized(session, line.localization_key_id, locale_code, release_id)
    await prefetch_dialogue(session, [row], locale_code, release_id, include_raw=include_raw)
    choice_payloads = []
    for choice, key, choice_source, choice_file, targets in session.info["story_choices"][
        line.node_id
    ]:
        target = next(iter(targets)) if len(targets) == 1 else None
        choice_payloads.append(
            {
                "id": key,
                "text": await _localized(
                    session, choice.localization_key_id, locale_code, release_id
                ),
                "target_line_id": target,
                "branch": _choice_branch(
                    action.params, state.state_key, action.action_index, target
                )
                if action and state and target
                else None,
                "provenance": _provenance(choice_source, choice_file, include_raw=include_raw),
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
            "params": action.params if include_raw else None,
        }
        if action
        else None,
        "flow_state": state.state_key if state else None,
        "ordering": {
            "basis": "action_index_then_talk_source_index",
            "scope": "authored order; runtime branch traversal is not implied",
        },
        "provenance": _provenance(record, source_file, include_raw=include_raw),
    }


async def _quest_state_ids(session: AsyncSession, quest_node_id: int, release_id: int | None = None) -> list[int]:
    return await repository.quest_state_ids(session, quest_node_id, release_id)


async def _quest_info(
    session: AsyncSession,
    quest: Quest,
    locale_code: str,
    game_version: str | None = None,
) -> dict[str, Any]:
    node = await repository.node(session, quest.node_id)
    release_id = await _release_id(session, game_version)
    name = await _localized(session, quest.name_key_id, locale_code, release_id)
    classification = await quest_classification(session, quest.game_quest_id, release_id)
    area_rows = await repository.quest_area_rows(session, quest.node_id)
    return {
        "game_quest_id": quest.game_quest_id,
        "canonical_key": node.canonical_key if node else f"quest:{quest.game_quest_id}",
        **(node_address(node, "quest") if node else {}),
        "name": name,
        "quest_type": str(classification["source_type"])
        if classification["source_type"] is not None
        else quest.quest_type,
        "classification": classification,
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
    row = await repository.latest_source_row(session, node_id)
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


async def quest_profile_payload(session, quest, locale="en", game_version=None) -> dict[str, Any]:
    game_quest_id = quest.game_quest_id
    release_id = await _release_id(session, game_version)
    qnode_rows = await repository.quest_node_rows(session, game_quest_id)
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
    hierarchy_rows = await repository.quest_hierarchy_rows(session, qnode_ids)
    states = await repository.quest_states(session, await _quest_state_ids(session, quest.node_id, release_id))
    actions_by_state = {}
    for action, key in await repository.quest_actions(session, [state.node_id for state in states]):
        actions_by_state.setdefault(action.quest_state_node_id, []).append((action, key))
    state_payloads = []
    for state in states:
        state_payloads.append(
            {
                "state_key": state.state_key,
                "flow_id": state.flow_id,
                "state_id": state.state_id,
                "actions": [
                    {
                        "canonical_key": action_key,
                        "action_id": action.action_id,
                        "action_guid": action.action_guid,
                        "name": action.action_name,
                        "action_index": action.action_index,
                        "params": action.params,
                        "source_record_id": action.source_record_id,
                    }
                    for action, action_key in actions_by_state.get(state.node_id, [])
                ],
            }
        )
    scenes = await repository.quest_scenes(session, quest.node_id)
    presented_scenes = scene_payloads(scenes)
    quest_links = await _explicit_entity_links(session, quest.node_id, locale)
    requirement_rows = await repository.quest_requirement_records(
        session,
        release_id,
        [f"{game_quest_id}_{row[0].game_node_id}" for row in qnode_rows],
        QUEST_NODES,
    )
    requirements = []
    for record in requirement_rows:
        for requirement in quest_item_requirements(record.data.get("Data") or {}):
            item_node = await get_node(session, f"item:{requirement['item_id']}", ("item",))
            requirements.append(
                {
                    **requirement,
                    "item": await _node_label(session, item_node.id, locale) if item_node else None,
                    "source": {
                        "file": QUEST_NODES,
                        "row": record.row_index,
                        "release_id": release_id,
                    },
                }
            )
    nodes_by_id = {node.id: node for _, node, _, _ in qnode_rows}
    return {
        "quest": await _quest_info(session, quest, locale, game_version),
        "item_requirements": requirements,
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
            if (source := nodes_by_id.get(edge.from_node_id)) is not None
        ],
        "raw_parent_links": raw_parent_links,
        "flow_states": state_payloads,
        "scenes": presented_scenes,
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


def scene_payloads(rows):
    return [
        {
            "canonical_key": key,
            "title": scene.title,
            "authored_order": scene.authored_order,
            "ordering_basis": scene.source_basis,
        }
        for scene, key in rows
    ]


async def dialogue_lines(
    session, rows, locale_code, release_id, media_by_line, *, include_raw=True
):
    return [
        {
            **await _dialogue_payload(
                session,
                row,
                locale_code=locale_code,
                release_id=release_id,
                include_raw=include_raw,
            ),
            "media": media_by_line.get(
                row[0].node_id, {"voice_references": [], "audio_event_paths": []}
            ),
        }
        for row in rows
    ]

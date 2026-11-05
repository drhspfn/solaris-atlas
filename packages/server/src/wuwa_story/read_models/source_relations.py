"""Exact snapshot source relationships and search filters; no personal inference."""

from fastapi.encoders import jsonable_encoder
from sqlalchemy import case, or_, select

from wuwa_story.api.routes.story import (
    shared,
)
from wuwa_story.db.models.core import (
    Character,
    Item,
    Quest,
)
from wuwa_story.db.models.game_events import GameEvent, GameEventOccurrence
from wuwa_story.db.models.graph import Edge, EdgeEvidence, Node, NodeRevision, NodeType
from wuwa_story.db.models.notices import GameNotice
from wuwa_story.db.models.ontology import RelationType
from wuwa_story.db.repositories.character_identity import dialogue_participation, speaker_crosswalk
from wuwa_story.db.repositories.quest_scope import quest_state_links
from wuwa_story.db.repositories.read_models import ENTITY_READ_MODELS
from wuwa_story.ingestion.game_events import CLIENT_SOURCE
from wuwa_story.read_models.context import source_build
from wuwa_story.read_models.source_sections import _event_version, _public_json, _request_caches
from wuwa_story.story import service

_NODE_KINDS = {
    "character": {"character"},
    "item": {"item"},
    "location": {"area", "location"},
    "quest": {"quest"},
    "speaker": {"speaker"},
    "dialogue": {"dialogue_line", "talk_item", "narration", "player_choice", "phone_message"},
    "cutscene": {"cutscene"},
}


async def _dialogue_filters(session, entity_id, release_id):
    result = {"character_keys": [], "quest_ids": []}
    participation = dialogue_participation(release_id, line_ids=[entity_id])
    row = (
        await session.execute(
            select(participation.c.speaker_id, participation.c.state_id)
            .where(participation.c.line_id == entity_id)
        )
    ).one_or_none()
    if row is None:
        return result
    speaker_id, state_id = row
    if speaker_id is not None:
        crosswalk = _speaker_crosswalk(release_id)
        characters = await session.execute(
            select(Node.canonical_key, Node.slug, Node.id)
            .select_from(crosswalk)
            .join(Node, Node.id == crosswalk.c.character_id)
            .join(Character, Character.node_id == Node.id)
            .where(crosswalk.c.speaker_id == speaker_id)
            .order_by(Node.id)
        )
        result["character_keys"] = sorted(
            {key for key, _slug, _identity in characters if key})
    if state_id is not None:
        links = quest_state_links(release_id, state_ids=[state_id])
        result["quest_ids"] = sorted(
            set(
                await session.scalars(
                    select(Quest.game_quest_id)
                    .join(links, links.c.quest_id == Quest.node_id)
                    .where(links.c.state_id == state_id)
                )
            )
        )
    return result


async def build_relations(session, kind, entity_id, snapshot_key="current"):
    """Build publication-ready source facts, independently of localized page payloads.

    Direct incoming edges retain their original relation name and an explicit
    direction. Composed character dialogue/quest facts require an exact, evidenced
    crosswalk in this release. Co-presence is never promoted to a personal relation.
    The caller atomically publishes this complete list, including an empty list
    after deletion. No public response limit bounds this background computation.
    """
    kind = {"event": "game_event", "notice": "game_notice"}.get(kind, kind)
    if kind not in ENTITY_READ_MODELS:
        raise ValueError(f"Unknown read-model kind: {kind}")
    if not isinstance(snapshot_key, str) or not snapshot_key or len(snapshot_key) > 64:
        raise ValueError(
            "A snapshot key must be current or an explicit version")
    grouped = {}

    def add(related_kind, related_id, relation_type, fact):
        key = (related_kind, related_id, relation_type)
        row = grouped.setdefault(
            key,
            {
                "related_kind": related_kind,
                "related_id": related_id,
                "relation_type": relation_type,
                "evidence": {"source_facts": []},
            },
        )
        if fact not in row["evidence"]["source_facts"]:
            row["evidence"]["source_facts"].append(fact)

    with source_build(), _request_caches(session):
        if kind == "story_map":
            return []
        if kind == "game_notice":
            if snapshot_key != "current":
                raise ValueError(
                    "game_notice has no public historical snapshot selector")
            notice = await session.get(GameNotice, entity_id)
            if notice is None:
                return []
            # These are stored import associations, not newly inferred matches.
            for target in await session.scalars(
                select(GameEvent.id).where(
                    GameEvent.id.in_(notice.related_event_ids),
                )
            ):
                add(
                    "game_event",
                    target,
                    "notice_event_association",
                    {
                        "evidence_type": "stored_import_association",
                        "notice_id": entity_id,
                        "notice_revision": notice.revision,
                        "field": "related_event_ids",
                    },
                )
        elif kind == "game_event":
            event = await session.get(GameEvent, entity_id)
            if event is None or event.source != CLIENT_SOURCE:
                return []
            statement = select(GameEventOccurrence).where(
                GameEventOccurrence.event_id == entity_id)
            if snapshot_key != "current":
                selected_version = _event_version(snapshot_key)
                if selected_version is None:
                    raise ValueError(
                        "Event snapshots require a numeric game version")
                patch = ".".join(map(str, selected_version))
                version = GameEventOccurrence.game_version
                statement = statement.where(
                    or_(version == patch, version.like(patch + ".%")),
                    version.op("~")(r"^\d+\.\d+(\.\d+)*$"),
                )
            runs = list(await session.scalars(statement.order_by(GameEventOccurrence.id)))
            rewards = (
                [(None, reward) for reward in event.rewards or []]
                if not runs and snapshot_key == "current"
                else []
            )
            rewards.extend((run.id, reward)
                           for run in runs for reward in run.rewards or [])
            item_ids = {reward.get("id") for _, reward in rewards if type(
                reward.get("id")) is int}
            items = (
                {
                    game_item_id: node_id
                    for game_item_id, node_id in await session.execute(
                        select(Item.game_item_id, Item.node_id).where(
                            Item.game_item_id.in_(item_ids)
                        )
                    )
                }
                if item_ids
                else {}
            )
            for occurrence_id, reward in rewards:
                if reward.get("id") in items:
                    add(
                        "item",
                        items[reward["id"]],
                        "event_reward",
                        {
                            "evidence_type": "explicit_item_id",
                            "event_id": entity_id,
                            "occurrence_id": occurrence_id,
                            "field": "rewards",
                            "game_item_id": reward["id"],
                            "reward": reward,
                        },
                    )
        else:
            node = await session.get(Node, entity_id)
            node_type = await session.get(NodeType, node.type_id) if node else None
            if node is None or node_type is None or node_type.key not in _NODE_KINDS[kind]:
                return []
            release_id = await service._release_id(
                session, None if snapshot_key == "current" else snapshot_key
            )
            if release_id is None:
                raise ValueError(f"Snapshot not imported: {snapshot_key}")
            neighbor = case(
                (Edge.from_node_id == entity_id, Edge.to_node_id), else_=Edge.from_node_id
            )
            rows = await session.execute(
                select(Edge, Node, NodeType.key,
                       RelationType.key, EdgeEvidence)
                .select_from(Edge)
                .join(Node, Node.id == neighbor)
                .join(NodeType, NodeType.id == Node.type_id)
                .join(RelationType, RelationType.id == Edge.relation_type_id)
                .join(EdgeEvidence, EdgeEvidence.edge_id == Edge.id)
                .where(
                    or_(Edge.from_node_id == entity_id,
                        Edge.to_node_id == entity_id),
                    neighbor != entity_id,
                    Edge.layer.in_(["source", "canonical"]),
                    Edge.basis.in_(
                        [
                            "explicit_reference",
                            "exact_join",
                            "authored_order",
                            "source_array_adjacency",
                        ]
                    ),
                    EdgeEvidence.release_id == release_id,
                )
                .order_by(Edge.id, EdgeEvidence.id)
            )
            for edge, target, target_type, relation, evidence in rows:
                target_kind = next(
                    (name for name, types in _NODE_KINDS.items()
                     if target_type in types), None
                )
                if target_kind is None:
                    continue
                add(
                    target_kind,
                    target.id,
                    relation,
                    {
                        "evidence_type": "source_edge",
                        "edge_id": edge.id,
                        "direction": "outgoing" if edge.from_node_id == entity_id else "incoming",
                        "from_node_id": edge.from_node_id,
                        "to_node_id": edge.to_node_id,
                        "related_canonical_key": target.canonical_key,
                        "basis": edge.basis,
                        "release_id": evidence.release_id,
                        "source_record_id": evidence.source_record_id,
                        "source_file": evidence.source_file_path,
                        "raw_path": evidence.source_raw_path,
                        "explanation": evidence.explanation,
                    },
                )
            if kind == "character":
                await _character_relations(session, node, release_id, add)
    return _public_json(jsonable_encoder([grouped[key] for key in sorted(grouped)]))


def _speaker_crosswalk(release_id, character_id=None):
    return speaker_crosswalk(release_id, [character_id] if character_id is not None else None)


async def _character_relations(session, node, release_id, add):
    # Family members are source roles of one public entity, not co-present peers.
    member_ids = {node.id}
    character = await session.get(Character, node.id)
    identity = ((character.metadata_json or {}).get(
        "public_identity") or {}) if character else {}
    if identity.get("family"):
        member_ids.update(await session.scalars(select(Character.node_id).where(
            Character.metadata_json["public_identity"]["family"].as_string(
            ) == identity["family"],
            select(NodeRevision.id).where(NodeRevision.node_id == Character.node_id,
                                          NodeRevision.release_id == release_id).exists(),
        )))
    crosswalk = speaker_crosswalk(release_id, member_ids)
    speaker_edges = {}
    for speaker_id, edge_id in await session.execute(
        select(crosswalk.c.speaker_id, crosswalk.c.edge_id)
        .order_by(crosswalk.c.speaker_id, crosswalk.c.edge_id)
    ):
        speaker_edges.setdefault(speaker_id, set()).add(edge_id)
    lines = []
    if speaker_edges:
        participation = dialogue_participation(release_id, speaker_ids=sorted(speaker_edges))
        lines = list(await session.execute(
            select(participation.c.line_id, participation.c.speaker_id, participation.c.state_id,
                   participation.c.source_record_id)
            .order_by(participation.c.line_id)
        ))
    state_lines = {}
    for line_id, speaker_id, state_id, record_id in lines:
        for edge_id in sorted(speaker_edges[speaker_id]):
            add(
                "dialogue",
                line_id,
                "has_dialogue",
                {
                    "evidence_type": "exact_crosswalk_dialogue",
                    "release_id": release_id,
                    "crosswalk_edge_id": edge_id,
                    "speaker_node_id": speaker_id,
                    "state_node_id": state_id,
                    "source_record_id": record_id,
                },
            )
        if state_id is not None:
            state_lines.setdefault(state_id, set()).add(line_id)
    if state_lines:
        links = quest_state_links(release_id, state_ids=state_lines)
        state_quests = {}
        for quest_id, state_id in await session.execute(
            select(links.c.quest_id, links.c.state_id)
            .where(links.c.state_id.in_(state_lines))
            .order_by(links.c.quest_id, links.c.state_id)
        ):
            state_quests.setdefault(state_id, set()).add(quest_id)
            add(
                "quest",
                quest_id,
                "has_dialogue_in_quest",
                {
                    "evidence_type": "release_evidenced_quest_state_path",
                    "release_id": release_id,
                    "state_node_id": state_id,
                    "dialogue_node_ids": sorted(state_lines[state_id]),
                },
            )
        other_crosswalk = _speaker_crosswalk(release_id)
        shared_facts = {}
        other_participation = dialogue_participation(
            release_id, state_ids=state_lines)
        for character_id, state_id, line_id, source_id, edge_id in await session.execute(
            select(other_crosswalk.c.character_id, other_participation.c.state_id,
                   other_participation.c.line_id, other_participation.c.source_record_id,
                   other_crosswalk.c.edge_id)
            .join(other_participation,
                  other_participation.c.speaker_id == other_crosswalk.c.speaker_id)
            .where(other_crosswalk.c.character_id.not_in(member_ids))
            .order_by(other_crosswalk.c.character_id, other_participation.c.state_id,
                      other_participation.c.line_id)
        ):
            fact = shared_facts.setdefault(
                (character_id, state_id),
                {
                    "evidence_type": "co_presence_only",
                    "release_id": release_id,
                    "state_node_id": state_id,
                    "quest_refs": [{"id": quest_id} for quest_id in sorted(state_quests.get(state_id, ()))],
                    "owner_dialogue_node_ids": sorted(state_lines[state_id]),
                    "other_dialogue_node_ids": [],
                    "other_source_record_ids": [],
                    "other_crosswalk_edge_ids": [],
                    "meaning": "Dialogue in one authored flow state; no personal relationship is asserted",
                },
            )
            for field, value in (
                ("other_dialogue_node_ids", line_id),
                ("other_source_record_ids", source_id),
                ("other_crosswalk_edge_ids", edge_id),
            ):
                if value not in fact[field]:
                    fact[field].append(value)
        for (character_id, _), fact in sorted(shared_facts.items()):
            add("character", character_id, "co_present_in_flow_state", fact)
    role_id = node.canonical_key.partition(":")[2]
    if role_id.isdecimal():
        for use in await shared._character_progression_materials(
            session, int(role_id), "en", release_id
        ):
            item = use.get("item")
            if item and type(item.get("id")) is int:
                add(
                    "item",
                    item["id"],
                    "uses_progression_item",
                    {
                        "evidence_type": "exact_material_group_join",
                        "release_id": release_id,
                        "usage": use["kind"],
                        "game_item_id": use["item_id"],
                        "required_count": use.get("required_count"),
                        "level_cap": use.get("level_cap"),
                        "group_id": use.get("group_id"),
                        "source": use["source"],
                    },
                )

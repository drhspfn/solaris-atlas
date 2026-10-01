import hashlib
import itertools
import json
import re
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.db.models import (
    NPC,
    AudioEvent,
    Character,
    Cutscene,
    DialogueLine,
    Edge,
    EdgeEvidence,
    Item,
    Location,
    Narration,
    Node,
    NodeRevision,
    NodeType,
    PhoneMessage,
    PlayerChoice,
    Quest,
    QuestAction,
    QuestNode,
    QuestState,
    RelationType,
    Scene,
    Speaker,
    SpeakerEntityLink,
    VoiceReference,
)
from wuwa_story.db.models.i18n import LocalizationKey
from wuwa_story.db.models.raw import SourceFile, SourceRecord
from wuwa_story.db.seeds import NODE_TYPES
from wuwa_story.ingestion.canonical import CanonicalRecordAdapter

EDGE_BASIS = {
    "explicit_reference",
    "exact_join",
    "authored_order",
    "source_array_adjacency",
    "semantic_extraction",
    "inference",
    "manual",
}
SOURCE_BASIS_MAP = {
    "explicit_reference": "explicit_reference",
    "exact_join": "exact_join",
    "runtime_condition": "explicit_reference",
    "authored_order": "authored_order",
    "source_array_adjacency": "source_array_adjacency",
}


def _row_index(source: dict[str, Any] | None) -> int | None:
    if not isinstance(source, dict):
        return None
    row = source.get("row", source.get("array_index"))
    return row if isinstance(row, int) else None


def _nested_key(record: dict[str, Any], field: str) -> str | None:
    value = record.get(field)
    key = value.get("key") if isinstance(value, dict) else None
    return key if isinstance(key, str) else None


def _source_identity(source: dict[str, Any] | None) -> tuple[str, int] | None:
    if not isinstance(source, dict) or not isinstance(source.get("file"), str):
        return None
    row = _row_index(source)
    return (source["file"], row) if row is not None else None


async def _source_record_ids(
    session: AsyncSession, release_id: int, refs: Iterable[tuple[str, int]]
) -> dict[tuple[str, int], int]:
    pairs = sorted(set(refs))
    if not pairs:
        return {}
    paths = sorted({path for path, _ in pairs})
    file_rows = await session.execute(
        select(SourceFile.logical_source_path, SourceFile.id).where(
            SourceFile.release_id == release_id,
            SourceFile.logical_source_path.in_(paths),
        )
    )
    file_ids = dict(file_rows.all())
    positions_by_path: dict[str, list[int]] = {}
    for path, row in pairs:
        positions_by_path.setdefault(path, []).append(row)
    resolved: dict[tuple[str, int], int] = {}
    for path, positions in positions_by_path.items():
        file_id = file_ids.get(path)
        if file_id is None:
            continue
        for offset in range(0, len(positions), 1000):
            result = await session.execute(
                select(SourceRecord.row_index, SourceRecord.id).where(
                    SourceRecord.release_id == release_id,
                    SourceRecord.source_file_id == file_id,
                    SourceRecord.row_index.in_(positions[offset : offset + 1000]),
                )
            )
            resolved.update(((path, row), record_id) for row, record_id in result)
    return resolved


def _text_key(record: dict[str, Any], *fields: str) -> str | None:
    for field in fields:
        value = record.get(field)
        if isinstance(value, dict) and isinstance(value.get("key"), str):
            return value["key"]
    for value in record.get("text_keys", []):
        if isinstance(value, dict) and isinstance(value.get("key"), str):
            path = value.get("raw_path", "")
            if any(path.endswith(name) for name in fields):
                return value["key"]
    return None


async def import_canonical_entities(
    adapter: CanonicalRecordAdapter,
    session: AsyncSession,
    release_id: int,
    revision: int,
    batch_size: int = 1000,
) -> int:
    """Import compiler entities as stable graph identities and typed source records."""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    type_ids = {row.key: row.id for row in await session.scalars(select(NodeType))}
    total = 0
    for path in adapter.entity_files():
        kind = path.stem
        if kind not in NODE_TYPES or kind not in type_ids:
            raise ValueError(
                f"Canonical entity type is not in the controlled node vocabulary: {kind}"
            )
        records = adapter.iter_entities(kind)
        while batch := list(itertools.islice(records, batch_size)):
            refs = [_source_identity(row.get("source")) for row in batch]
            source_ids = await _source_record_ids(
                session, release_id, (ref for ref in refs if ref is not None)
            )
            node_values = []
            for record in batch:
                source = record.get("source") or {}
                node_values.append(
                    {
                        "type_id": type_ids[kind],
                        "canonical_key": record["id"],
                        "created_release_id": release_id,
                        "metadata": {
                            "source_file": source.get("file"),
                            "source_row": _row_index(source),
                            "source_raw_path": source.get("raw_path"),
                            "source_repository": record.get("source_repository"),
                            "source_commit": record.get("source_commit"),
                            "game_version": record.get("version"),
                        },
                    }
                )
            await session.execute(
                insert(Node)
                .values(node_values)
                .on_conflict_do_nothing(index_elements=[Node.canonical_key])
            )
            node_rows = await session.execute(
                select(Node.id, Node.canonical_key, Node.type_id).where(
                    Node.canonical_key.in_([row["canonical_key"] for row in node_values])
                )
            )
            node_map = {key: (node_id, node_type) for node_id, key, node_type in node_rows}
            if any(node_map[row["id"]][1] != type_ids[kind] for row in batch):
                raise ValueError(f"Canonical key changed graph type in entity file {kind}")
            digests = [
                hashlib.sha256(
                    json.dumps(
                        row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
                    ).encode()
                ).digest()
                for row in batch
            ]
            revision_values = []
            for record, digest in zip(batch, digests, strict=True):
                source = record.get("source") or {}
                source_ref = _source_identity(source)
                revision_values.append(
                    {
                        "node_id": node_map[record["id"]][0],
                        "release_id": release_id,
                        "revision": revision,
                        "content_hash": digest,
                        "source_record_id": source_ids.get(source_ref) if source_ref else None,
                        "metadata": {
                            "source_file": source.get("file"),
                            "source_row": _row_index(source),
                            "source_raw_path": source.get("raw_path"),
                            "source_repository": record.get("source_repository"),
                            "source_commit": record.get("source_commit"),
                        },
                    }
                )
            await session.execute(
                insert(NodeRevision)
                .values(revision_values)
                .on_conflict_do_nothing(
                    index_elements=[NodeRevision.node_id, NodeRevision.revision]
                )
            )
            await session.commit()
            total += len(batch)
    typed_order = (
        "speaker",
        "character",
        "npc",
        "quest",
        "quest_node",
        "flow_state",
        "action",
        "item",
        "area",
        "scene",
        "cutscene",
        "voice_reference",
        "audio_event",
        "talk_item",
        "player_choice",
        "narration",
        "phone_message",
    )
    for kind in typed_order:
        if not (adapter.root / "entities" / f"{kind}.jsonl").is_file():
            continue
        for batch in _batches(adapter.iter_entities(kind), batch_size):
            keys = [record["id"] for record in batch]
            node_rows = await session.execute(
                select(Node.id, Node.canonical_key, Node.type_id).where(
                    Node.canonical_key.in_(keys)
                )
            )
            node_map = {key: (node_id, type_id) for node_id, key, type_id in node_rows}
            refs = [_source_identity(row.get("source")) for row in batch]
            source_ids = await _source_record_ids(
                session, release_id, (ref for ref in refs if ref is not None)
            )
            await _insert_typed_entities(session, kind, batch, node_map, source_ids)
            await session.commit()
    return total


async def _insert_typed_entities(
    session: AsyncSession,
    kind: str,
    records: list[dict[str, Any]],
    node_map: dict[str, tuple[int, int]],
    source_ids: dict[tuple[str, int], int],
) -> None:
    keys = {_text_key(record, "TidName", "Name", "Title", "CaptionText") for record in records}
    keys |= {_nested_key(record, "localization") for record in records}
    keys |= {_nested_key(record, "display_name") for record in records}
    keys |= {_nested_key(record, "name") for record in records}
    keys |= {_nested_key(record, "nickname") for record in records}
    keys |= {_nested_key(record, "bgdescription") for record in records}
    keys |= {_nested_key(record, "title") for record in records}
    keys.discard(None)
    key_rows = await session.execute(
        select(LocalizationKey.key, LocalizationKey.id).where(LocalizationKey.key.in_(keys or {""}))
    )
    localization_ids = dict(key_rows.all())
    related_keys: set[str] = set()
    for record in records:
        raw = record.get("raw") if isinstance(record.get("raw"), dict) else {}
        state_key = record.get("state_key")
        if isinstance(state_key, str):
            related_keys.add(f"flow_state:{state_key}")
            if isinstance(record.get("action_index"), int):
                related_keys.add(f"action:{state_key}:{record['action_index']}")
        speaker_id = record.get("speaker_id")
        if speaker_id is not None:
            related_keys.add(f"speaker:{speaker_id}")
        if kind == "area":
            parent_id = raw.get("Father")
            if isinstance(parent_id, int) and parent_id > 0:
                related_keys.add(f"area:{parent_id}")
    related_rows = await session.execute(
        select(Node.id, Node.canonical_key).where(Node.canonical_key.in_(related_keys or {""}))
    )
    related_node_ids = {
        canonical_key: node_id for node_id, canonical_key in related_rows
    }
    typed: list[tuple[type, list[dict[str, Any]]]] = []
    for record in records:
        node_id = node_map[record["id"]][0]
        source = record.get("source") or {}
        source_ref = _source_identity(source)
        source_id = source_ids.get(source_ref) if source_ref else None
        raw = record.get("raw") if isinstance(record.get("raw"), dict) else {}
        localization_key = _text_key(record, "TidName", "Name", "Title", "CaptionText")
        localization = record.get("localization")
        if localization_key is None and isinstance(localization, dict):
            localization_key = localization.get("key")
        if localization_key is None:
            for field in ("display_name", "name", "title"):
                value = record.get(field)
                if isinstance(value, dict) and isinstance(value.get("key"), str):
                    localization_key = value["key"]
                    break
        if kind == "quest":
            data = record.get("data") or {}
            typed.append(
                (
                    Quest,
                    [
                        {
                            "node_id": node_id,
                            "game_quest_id": record["quest_id"],
                            "name_key_id": localization_ids.get(data.get("TidName")),
                            "description_key_id": localization_ids.get(data.get("TidDesc")),
                            "quest_type": str(data.get("Type"))
                            if data.get("Type") is not None
                            else None,
                            "region_id": str(data.get("RegionId"))
                            if data.get("RegionId") is not None
                            else None,
                            "metadata": {"raw_quest_id": record["quest_id"]},
                        }
                    ],
                )
            )
        elif kind == "quest_node":
            typed.append(
                (
                    QuestNode,
                    [
                        {
                            "node_id": node_id,
                            "game_node_id": str(record.get("node_id", "")),
                            "game_quest_id": record.get("quest_id"),
                            "node_type": record.get("node_type"),
                            "source_record_id": source_id,
                            "metadata": {"data": record.get("data", {})},
                        }
                    ],
                )
            )
        elif kind == "flow_state":
            typed.append(
                (
                    QuestState,
                    [
                        {
                            "node_id": node_id,
                            "state_key": record["state_key"],
                            "source_record_id": source_id,
                            "metadata": {"game_state_key": record["state_key"]},
                        }
                    ],
                )
            )
        elif kind == "action":
            state_key = record.get("state_key", "")
            action_index = record.get("action_index", 0)
            target_state = related_node_ids.get(f"flow_state:{state_key}")
            if target_state is not None:
                params = raw.get("Params", {})
                if isinstance(params, str):
                    try:
                        params = json.loads(params)
                    except json.JSONDecodeError:
                        params = {"raw": params}
                typed.append(
                    (
                        QuestAction,
                        [
                            {
                                "node_id": node_id,
                                "quest_state_node_id": target_state,
                                "action_id": str(record["action_id"])
                                if record.get("action_id") is not None
                                else None,
                                "action_guid": record.get("action_guid"),
                                "action_name": record.get("name", "unknown"),
                                "action_index": action_index,
                                "params": params if isinstance(params, dict) else {"raw": params},
                                "source_record_id": source_id,
                                "metadata": {"state_key": state_key},
                            }
                        ],
                    )
                )
        elif kind == "talk_item":
            state_key = record.get("state_key", "")
            action_index = record.get("action_index", 0)
            action_node_id = related_node_ids.get(f"action:{state_key}:{action_index}")
            speaker_id = record.get("speaker_id")
            speaker_node_id = (
                related_node_ids.get(f"speaker:{speaker_id}") if speaker_id is not None else None
            )
            typed.append(
                (
                    DialogueLine,
                    [
                        {
                            "node_id": node_id,
                            "action_node_id": action_node_id,
                            "speaker_node_id": speaker_node_id,
                            "localization_key_id": localization_ids.get(localization_key),
                            "game_talk_item_id": str(record["talk_id"])
                            if record.get("talk_id") is not None
                            else None,
                            "game_text_id": str(record["text_id"])
                            if record.get("text_id") is not None
                            else None,
                            "source_type": record.get("source_type"),
                            "source_index": record.get("talk_index"),
                            "source_record_id": source_id,
                            "metadata": {
                                "state_key": state_key,
                                "action_index": action_index,
                                "resolution": localization.get("resolution")
                                if isinstance(localization, dict)
                                else None,
                            },
                        }
                    ],
                )
            )
        elif kind == "player_choice":
            typed.append(
                (
                    PlayerChoice,
                    [
                        {
                            "node_id": node_id,
                            "localization_key_id": localization_ids.get(localization_key),
                            "source_record_id": source_id,
                            "metadata": {
                                "state_key": record.get("state_key"),
                                "choice_index": record.get("choice_index"),
                                "resolution": localization.get("resolution")
                                if isinstance(localization, dict)
                                else None,
                            },
                        }
                    ],
                )
            )
        elif kind == "narration":
            typed.append(
                (
                    Narration,
                    [
                        {
                            "node_id": node_id,
                            "localization_key_id": localization_ids.get(localization_key),
                            "narration_type": record.get("source_type"),
                            "source_record_id": source_id,
                            "metadata": {"state_key": record.get("state_key")},
                        }
                    ],
                )
            )
        elif kind == "speaker":
            typed.append(
                (
                    Speaker,
                    [
                        {
                            "node_id": node_id,
                            "game_speaker_id": record["game_id"],
                            "name_key_id": localization_ids.get(_nested_key(record, "display_name")),
                            "metadata": {"resolution_method": record.get("resolution_method")},
                        }
                    ],
                )
            )
        elif kind == "character":
            nickname_key = _nested_key(record, "nickname")
            name_value = record.get("name")
            typed.append(
                (
                    Character,
                    [
                        {
                            "node_id": node_id,
                            "canonical_name": name_value.get("en") if isinstance(name_value, dict) else None,
                            "name_key_id": localization_ids.get(_nested_key(record, "name")),
                            "nickname_key_id": localization_ids.get(nickname_key),
                            "metadata": {
                                "game_character_id": record.get("game_id"),
                                "name_key": _nested_key(record, "name"),
                                "role_type": raw.get("RoleType"),
                                "is_trial": raw.get("IsTrial"),
                                "is_show": raw.get("IsShow"),
                            },
                        }
                    ],
                )
            )
        elif kind == "npc":
            typed.append(
                (
                    NPC,
                    [
                        {
                            "node_id": node_id,
                            "game_npc_id": record["game_id"],
                            "source_record_id": source_id,
                            "metadata": {"resolution_method": record.get("resolution_method")},
                        }
                    ],
                )
            )
        elif kind == "item":
            description_key = _nested_key(record, "bgdescription")
            name_value = record.get("name")
            typed.append(
                (
                    Item,
                    [
                        {
                            "node_id": node_id,
                            "canonical_name": name_value.get("en")
                            if isinstance(name_value, dict)
                            else None,
                            "game_item_id": record.get("game_id"),
                            "name_key_id": localization_ids.get(_nested_key(record, "name")),
                            "description_key_id": localization_ids.get(description_key),
                            "metadata": {"name_key": _nested_key(record, "name")},
                        }
                    ],
                )
            )
        elif kind == "area":
            title = record.get("title")
            parent_id = raw.get("Father")
            parent_node_id = related_node_ids.get(f"area:{parent_id}") if parent_id else None
            typed.append(
                (
                    Location,
                    [
                        {
                            "node_id": node_id,
                            "game_location_id": str(record.get("game_id", "")),
                            "canonical_name": title.get("en") if isinstance(title, dict) else None,
                            "name_key_id": localization_ids.get(_nested_key(record, "title")),
                            "parent_location_node_id": parent_node_id,
                            "metadata": {"title_key": _nested_key(record, "title")},
                        }
                    ],
                )
            )
        elif kind == "scene":
            typed.append(
                (
                    Scene,
                    [
                        {
                            "node_id": node_id,
                            "authored_order": record.get("step_index"),
                            "source_basis": "authored_order",
                            "metadata": {
                                "game_quest_id": record.get("quest_id"),
                                "flow": record.get("flow"),
                            },
                        }
                    ],
                )
            )
        elif kind == "cutscene":
            typed.append(
                (
                    Cutscene,
                    [
                        {
                            "node_id": node_id,
                            "cg_name": record.get("cg_name"),
                            "metadata": {
                                "resolution": record.get("resolution"),
                                "resolution_basis": record.get("resolution_basis"),
                            },
                        }
                    ],
                )
            )
        elif kind == "voice_reference":
            typed.append(
                (
                    VoiceReference,
                    [
                        {
                            "node_id": node_id,
                            "plot_audio_id": record.get("plot_audio_id"),
                            "file_name": record.get("file_name"),
                        }
                    ],
                )
            )
        elif kind == "audio_event":
            typed.append(
                (
                    AudioEvent,
                    [
                        {
                            "node_id": node_id,
                            "event_path": record.get("event_path"),
                            "source_record_id": source_id,
                            "metadata": {"cg_name": record.get("cg_name")},
                        }
                    ],
                )
            )
        elif kind == "phone_message":
            speaker_id = record.get("speaker_id")
            speaker_node_id = (
                related_node_ids.get(f"speaker:{speaker_id}") if speaker_id is not None else None
            )
            typed.append(
                (
                    PhoneMessage,
                    [
                        {
                            "node_id": node_id,
                            "speaker_node_id": speaker_node_id,
                            "localization_key_id": localization_ids.get(localization_key),
                            "source_record_id": source_id,
                            "metadata": {"game_talk_id": record.get("talk_id")},
                        }
                    ],
                )
            )
    grouped: dict[Any, list[dict[str, Any]]] = {}
    for model, values in typed:
        grouped.setdefault(model, []).extend(values)
    for model, values in grouped.items():
        statement = insert(model).values(values)
        updates_by_model = {
            Character: ("canonical_name", "name_key_id", "nickname_key_id", "metadata"),
            Item: (
                "canonical_name",
                "game_item_id",
                "name_key_id",
                "description_key_id",
                "metadata",
            ),
            Location: (
                "canonical_name",
                "name_key_id",
                "game_location_id",
                "parent_location_node_id",
                "metadata",
            ),
        }
        update_columns = updates_by_model.get(model)
        if update_columns:
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[model.node_id],
                    set_={column: getattr(statement.excluded, column) for column in update_columns},
                )
            )
        else:
            await session.execute(
                statement.on_conflict_do_nothing(index_elements=[model.node_id])
            )


async def import_canonical_edges(
    adapter: CanonicalRecordAdapter,
    session: AsyncSession,
    release_id: int,
    batch_size: int = 1000,
    relation_filter: set[str] | None = None,
    type_filter: set[str] | None = None,
) -> int:
    relation_ids = {row.key: row.id for row in await session.scalars(select(RelationType))}
    count = 0
    records = adapter.iter_edges()
    if relation_filter is not None:
        records = (record for record in records if record.get("relation") in relation_filter)
    if type_filter is not None:
        records = (record for record in records if record.get("type") in type_filter)
    for batch in _batches(records, batch_size):
        node_keys = {record["from"] for record in batch} | {record["to"] for record in batch}
        node_ids: dict[str, int] = {}
        ordered_node_keys = sorted(node_keys)
        for offset in range(0, len(ordered_node_keys), 250):
            node_rows = await session.execute(
                select(Node.id, Node.canonical_key).where(
                    Node.canonical_key.in_(ordered_node_keys[offset : offset + 250])
                )
            )
            node_ids.update(
                (canonical_key, node_id) for node_id, canonical_key in node_rows
            )
        relation_keys = {record["type"] for record in batch}
        unknown = relation_keys - relation_ids.keys()
        if unknown:
            raise ValueError(f"Canonical source relations are not seeded: {sorted(unknown)}")
        missing = node_keys - node_ids.keys()
        if missing:
            raise ValueError(
                f"Canonical graph edge has unresolved endpoints: {sorted(missing)[:10]}"
            )
        edge_values = []
        for record in batch:
            source_relation = record.get("relation")
            basis = SOURCE_BASIS_MAP.get(source_relation)
            if basis not in EDGE_BASIS:
                raise ValueError(
                    f"Unknown canonical source edge relation/basis: {source_relation!r}"
                )
            edge_values.append(
                {
                    "from_node_id": node_ids[record["from"]],
                    "to_node_id": node_ids[record["to"]],
                    "relation_type_id": relation_ids[record["type"]],
                    "layer": "source",
                    "basis": basis,
                    "created_release_id": release_id,
                    "valid_from_release_id": release_id,
                    "metadata": {
                        "source_basis": record["basis"],
                        "source_relation": source_relation,
                        "source_file": record["source"],
                        "source_raw_path": record["raw_path"],
                        "source_repository": record.get("source_repository"),
                        "source_commit": record.get("source_commit"),
                        "source_version": record["version"],
                    },
                }
            )
        await session.execute(
            insert(Edge)
            .values(edge_values)
            .on_conflict_do_nothing(
                index_elements=[
                    Edge.from_node_id,
                    Edge.relation_type_id,
                    Edge.to_node_id,
                    Edge.layer,
                    Edge.basis,
                ]
            )
        )
        edge_keys = [
            (
                row["from_node_id"],
                row["relation_type_id"],
                row["to_node_id"],
                row["layer"],
                row["basis"],
            )
            for row in edge_values
        ]
        found = await session.execute(
            select(
                Edge.id,
                Edge.from_node_id,
                Edge.relation_type_id,
                Edge.to_node_id,
                Edge.layer,
                Edge.basis,
            ).where(
                tuple_(
                    Edge.from_node_id,
                    Edge.relation_type_id,
                    Edge.to_node_id,
                    Edge.layer,
                    Edge.basis,
                ).in_(edge_keys)
            )
        )
        edge_ids = {
            (from_id, relation_id, to_id, layer, basis): edge_id
            for edge_id, from_id, relation_id, to_id, layer, basis in found
        }
        source_refs = []
        for record in batch:
            match = re.match(r"^\$\[(\d+)\]", record["raw_path"])
            if match and isinstance(record.get("source"), str):
                source_refs.append((record["source"], int(match.group(1))))
        source_ids = await _source_record_ids(session, release_id, source_refs)
        speaker_links = []
        for record in batch:
            if record.get("type") != "references_character" or record.get("relation") != "exact_join":
                continue
            if not record["from"].startswith("speaker:") or not record["to"].startswith("character:"):
                continue
            row_match = re.match(r"^\$\[(\d+)\]", record["raw_path"])
            source_ref = (record["source"], int(row_match.group(1))) if row_match else None
            speaker_links.append(
                {
                    "speaker_node_id": node_ids[record["from"]],
                    "entity_node_id": node_ids[record["to"]],
                    "resolution_type": "unique_external_reference",
                    "confidence": 1.0,
                    "source_record_id": source_ids.get(source_ref) if source_ref else None,
                    "metadata": {
                        "basis": "exact_join",
                        "evidence": record["basis"],
                        "source_file": record["source"],
                        "source_raw_path": record["raw_path"],
                        "version": record["version"],
                    },
                }
            )
        if speaker_links:
            await session.execute(
                insert(SpeakerEntityLink)
                .values(speaker_links)
                .on_conflict_do_nothing(
                    index_elements=[SpeakerEntityLink.speaker_node_id, SpeakerEntityLink.entity_node_id]
                )
            )
        evidences = []
        for record, values in zip(batch, edge_values, strict=True):
            key = (
                values["from_node_id"],
                values["relation_type_id"],
                values["to_node_id"],
                values["layer"],
                values["basis"],
            )
            row_match = re.match(r"^\$\[(\d+)\]", record["raw_path"])
            source_ref = (record["source"], int(row_match.group(1))) if row_match else None
            evidences.append(
                {
                    "edge_id": edge_ids[key],
                    "release_id": release_id,
                    "source_record_id": source_ids.get(source_ref) if source_ref else None,
                    "source_file_path": record["source"],
                    "source_raw_path": record["raw_path"],
                    "explanation": record["basis"],
                }
            )
        if evidences:
            await session.execute(
                insert(EdgeEvidence)
                .values(evidences)
                .on_conflict_do_nothing(
                    index_elements=[
                        EdgeEvidence.edge_id,
                        EdgeEvidence.release_id,
                        EdgeEvidence.source_file_path,
                        EdgeEvidence.source_raw_path,
                    ]
                )
            )
        await session.commit()
        count += len(batch)
    return count


def _batches(iterable, size: int):
    if size < 1:
        raise ValueError("batch_size must be positive")
    iterator = iter(iterable)
    while batch := list(itertools.islice(iterator, size)):
        yield batch

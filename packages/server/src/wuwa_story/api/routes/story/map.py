"""Source-backed QuestTree browse map for the story reader."""

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.api import read_projections
from wuwa_story.api.public_addresses import public_addresses
from wuwa_story.api.routes.story.shared import _display_label, _version_key
from wuwa_story.db.models.core import Quest
from wuwa_story.db.models.graph import NodeRevision
from wuwa_story.db.models.i18n import (
    Locale,
    LocalizationContent,
    LocalizationKey,
    LocalizationValue,
)
from wuwa_story.db.models.ops import GameRelease, ImportRun
from wuwa_story.db.models.raw import SourceFile, SourceRecord
from wuwa_story.db.session import get_session
from wuwa_story.story.domain import authored_order as _authored_order

router = APIRouter(tags=["story browsing"])

TREE_NODE_PATH = "BinData/QuestTree/questtreenode.json"
TREE_CHAPTER_PATH = "BinData/QuestTree/questtreechapter.json"
QUEST_PATH = "BinData/QuestData/questdata.json"
QUEST_CHAPTER_PATH = "BinData/quest_chapter/questchapter.json"


@dataclass
class _SnapshotBatch:
    records: dict[tuple[int, str], list[SourceRecord]]
    first_seen: dict[int, str]
    titles: dict[str, list[tuple[tuple[int, ...], int, str]]]

    def titles_for(self, release: GameRelease) -> dict[str, str]:
        target = _version_key(release.game_version)
        return {
            key: max(candidates, key=lambda row: row[:2])[2]
            for key, history in self.titles.items()
            if (candidates := [row for row in history if row[0] <= target])
        }

    def first_for(self, release: GameRelease) -> dict[int, str]:
        target = _version_key(release.game_version)
        return {key: version for key, version in self.first_seen.items()
                if _version_key(version) <= target}


async def _records(session: AsyncSession, release_id: int, path: str) -> list[SourceRecord]:
    return list(await session.scalars(
        select(SourceRecord)
        .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
        .where(SourceFile.release_id == release_id, SourceFile.logical_source_path == path)
        .order_by(SourceRecord.row_index)
    ))


async def _localized_titles(
    session: AsyncSession, release: GameRelease, locale: str, keys: set[str]
) -> dict[str, str]:
    if not keys:
        return {}
    rows = (await session.execute(
        select(LocalizationKey.key, LocalizationContent.content, GameRelease.game_version,
               GameRelease.sequence)
        .join(LocalizationValue, LocalizationValue.key_id == LocalizationKey.id)
        .outerjoin(LocalizationContent, LocalizationContent.id == LocalizationValue.content_id)
        .join(Locale, Locale.id == LocalizationValue.locale_id)
        .join(GameRelease, GameRelease.id == LocalizationValue.release_id)
        .where(LocalizationKey.key.in_(keys), Locale.code == locale,
               LocalizationValue.status == "resolved_nonempty")
    )).all()
    target = _version_key(release.game_version)
    chosen: dict[str, tuple[tuple[int, ...], int, str]] = {}
    for key, content, version, sequence in rows:
        title = _display_label(content)
        version_key = _version_key(version)
        if title and version_key <= target:
            candidate = (version_key, sequence, title)
            if key not in chosen or candidate[:2] > chosen[key][:2]:
                chosen[key] = candidate
    return {key: value[2] for key, value in chosen.items()}


async def _first_observed(
    session: AsyncSession, release: GameRelease, quest_ids: set[int]
) -> dict[int, str]:
    if not quest_ids:
        return {}
    rows = (await session.execute(
        select(Quest.game_quest_id, GameRelease.game_version, GameRelease.sequence)
        .join(NodeRevision, NodeRevision.node_id == Quest.node_id)
        .join(GameRelease, GameRelease.id == NodeRevision.release_id)
        .where(Quest.game_quest_id.in_(quest_ids), GameRelease.id.in_(
            select(ImportRun.release_id).where(ImportRun.status == "succeeded")
        ))
    )).all()
    first_seen: dict[int, str] = {}
    for quest_id, version, _sequence in sorted(
        rows, key=lambda row: (_version_key(row.game_version), row.sequence)
    ):
        if _version_key(version) <= _version_key(release.game_version):
            first_seen.setdefault(quest_id, version)
    return first_seen


async def _snapshot_batch(
    session: AsyncSession, releases: list[GameRelease], locale: str,
) -> _SnapshotBatch:
    """Load shared source, localization and first-observation data once for all patches."""
    paths = (TREE_NODE_PATH, TREE_CHAPTER_PATH, QUEST_PATH, QUEST_CHAPTER_PATH)
    rows = await session.execute(
        select(SourceFile.release_id, SourceFile.logical_source_path, SourceRecord)
        .join(SourceRecord, SourceFile.id == SourceRecord.source_file_id)
        .where(SourceFile.release_id.in_([release.id for release in releases]),
               SourceFile.logical_source_path.in_(paths))
        .order_by(SourceRecord.row_index)
    )
    records: dict[tuple[int, str], list[SourceRecord]] = defaultdict(list)
    quest_ids: set[int] = set()
    keys: set[str] = set()
    for release_id, path, record in rows:
        records[(release_id, path)].append(record)
        data = record.data
        for field in ("Name", "QuestChapterName", "ChapterName", "ActName", "ChapterNum", "SectionNum"):
            if isinstance(data.get(field), str) and data[field]:
                keys.add(data[field])
        quest = data.get("QuestId")
        if isinstance(quest, int):
            quest_ids.add(quest)
        quest_array = data.get("QuestArray")
        if isinstance(quest_array, list):
            quest_ids.update(value for value in quest_array if isinstance(value, int))
        details = _quest_data(record)
        if isinstance(details.get("TidName"), str) and details["TidName"]:
            keys.add(details["TidName"])
    first_seen = await _first_observed(session, releases[-1], quest_ids)
    titles: dict[str, list[tuple[tuple[int, ...], int, str]]] = defaultdict(list)
    if keys:
        title_rows = await session.execute(
            select(LocalizationKey.key, LocalizationContent.content, GameRelease.game_version,
                   GameRelease.sequence)
            .join(LocalizationValue, LocalizationValue.key_id == LocalizationKey.id)
            .outerjoin(LocalizationContent, LocalizationContent.id == LocalizationValue.content_id)
            .join(Locale, Locale.id == LocalizationValue.locale_id)
            .join(GameRelease, GameRelease.id == LocalizationValue.release_id)
            .where(LocalizationKey.key.in_(keys), Locale.code == locale,
                   LocalizationValue.status == "resolved_nonempty")
        )
        for key, content, version, sequence in title_rows:
            title = _display_label(content)
            if title:
                titles[key].append((_version_key(version), sequence, title))
    return _SnapshotBatch(records, first_seen, titles)


def _quest_data(record: SourceRecord) -> dict[str, Any]:
    data = record.data.get("Data", {})
    return data if isinstance(data, dict) else {}


async def _prerequisite_map(
    session: AsyncSession, release: GameRelease, locale: str,
    quest_type_id: int, versions: list[str], quest_records: list[SourceRecord],
    batch: _SnapshotBatch | None = None,
) -> dict[str, Any]:
    """For early patches without QuestTree, show QuestData and its explicit links."""
    selected = [record for record in quest_records
                if isinstance(record.data.get("QuestId"), int)
                and (quest_type_id == 0 or _quest_data(record).get("Type") == quest_type_id)]
    chapter_records = (batch.records.get((release.id, QUEST_CHAPTER_PATH), []) if batch
                       else await _records(session, release.id, QUEST_CHAPTER_PATH))
    chapter_by_id = {record.data.get("Id"): record for record in chapter_records}
    quest_ids = {record.data["QuestId"] for record in selected}
    first_seen = batch.first_for(release) if batch else await _first_observed(session, release, quest_ids)
    keys = {value for record in selected for value in (_quest_data(record).get("TidName"),)
            if isinstance(value, str) and value}
    keys.update(value for record in chapter_records
                for value in (record.data.get("ChapterName"), record.data.get("ActName"),
                              record.data.get("ChapterNum"), record.data.get("SectionNum"))
                if isinstance(value, str) and value)
    titles = batch.titles_for(release) if batch else await _localized_titles(session, release, locale, keys)
    grouped: dict[int, list[SourceRecord]] = defaultdict(list)
    for record in selected:
        chapter_id = _quest_data(record).get("ChapterId")
        grouped[chapter_id if isinstance(chapter_id, int) and chapter_id > 0 else 0].append(record)
    chapters = []
    for chapter_id in sorted(grouped, key=lambda value: (value == 0, value)):
        chapter = chapter_by_id.get(chapter_id)
        authored = []
        records_by_id = {record.data["QuestId"]: record for record in grouped[chapter_id]}
        for record in grouped[chapter_id]:
            data = _quest_data(record)
            provide = data.get("ProvideType")
            conditions = provide.get("Conditions", []) if isinstance(provide, dict) else []
            prerequisites = [condition.get("PreQuest") for condition in conditions
                             if isinstance(condition, dict) and condition.get("Type") == "PreQuest"
                             and isinstance(condition.get("PreQuest"), int)]
            authored.append({"Id": record.data["QuestId"], "PreNode": prerequisites,
                             "NextNode": None})
        nodes = []
        for ordered in _authored_order(authored):
            record = records_by_id[ordered["Id"]]
            data = _quest_data(record)
            title = titles.get(data.get("TidName")) or f"Quest {ordered['Id']}"
            nodes.append({
                "id": ordered["Id"], "title": title,
                "chapter_label": None,
                "quest_type_id": data.get("Type"), "node_type_id": None,
                "previous_node_ids": ordered["PreNode"], "next_node_id": None,
                "source_kind": "quest_prerequisite",
                "quests": [{"game_quest_id": ordered["Id"], "title": title,
                            "first_observed_game_version": first_seen.get(ordered["Id"]),
                            "source": {"file": QUEST_PATH, "raw_path": f"$[{record.row_index}]"}}],
                "source": {"file": QUEST_PATH, "raw_path": f"$[{record.row_index}]"},
            })
        chapters.append({
            "id": chapter_id,
            "title": titles.get(chapter.data.get("ChapterName")) if chapter else (
                "Quests without a chapter" if chapter_id == 0 else None
            ),
            "chapter_number": titles.get(chapter.data.get("ChapterNum")) if chapter else None,
            "act_title": titles.get(chapter.data.get("ActName")) if chapter else None,
            "act_number": titles.get(chapter.data.get("SectionNum")) if chapter else None,
            "nodes": nodes,
        })
    return {
        "selected_game_version": release.game_version,
        "imported_game_versions": versions,
        "quest_type_id": quest_type_id,
        "tree_available": False,
        "chapters": chapters,
        "ordering_basis": "QuestData.Data.ChapterId groups quests when present; unchaptered quests are grouped separately. ProvideType.Conditions.PreQuest is a prerequisite, not an immediate next event",
        "version_basis": "Earliest imported snapshot containing each quest, not a proven debut patch",
    }


async def _snapshot_map(
    session: AsyncSession, release: GameRelease, locale: str,
    quest_type_id: int, versions: list[str], batch: _SnapshotBatch | None = None,
) -> dict[str, Any]:
    """Build one source-backed snapshot without applying a patch filter."""
    release_id = release.id
    if batch:
        tree_records = batch.records.get((release_id, TREE_NODE_PATH), [])
        chapter_records = batch.records.get((release_id, TREE_CHAPTER_PATH), [])
        quest_records = batch.records.get((release_id, QUEST_PATH), [])
    else:
        tree_records = await _records(session, release_id, TREE_NODE_PATH)
        chapter_records = await _records(session, release_id, TREE_CHAPTER_PATH)
        quest_records = await _records(session, release_id, QUEST_PATH)
    if not tree_records:
        return await _prerequisite_map(session, release, locale, quest_type_id, versions, quest_records, batch)
    tree = [record for record in tree_records if isinstance(record.data.get("Id"), int)
            and (quest_type_id == 0 or record.data.get("QuestType") == quest_type_id)]
    quest_rows = {row.data.get("QuestId"): row for row in quest_records}
    quest_ids = {identifier for row in tree for identifier in row.data.get("QuestArray", [])
                 if isinstance(identifier, int)}
    first_seen = batch.first_for(release) if batch else await _first_observed(session, release, quest_ids)
    keys = {key for record in [*tree_records, *chapter_records]
            for key in (record.data.get("Name"), record.data.get("QuestChapterName"))
            if isinstance(key, str) and key}
    for quest_id in quest_ids:
        record = quest_rows.get(quest_id)
        data = record.data.get("Data", {}) if record else {}
        if isinstance(data, dict) and isinstance(data.get("TidName"), str):
            keys.add(data["TidName"])
    titles = batch.titles_for(release) if batch else await _localized_titles(session, release, locale, keys)
    chapters_by_id = {row.data.get("Id"): row for row in chapter_records}
    grouped: dict[int, list[SourceRecord]] = defaultdict(list)
    for record in tree:
        grouped[record.data.get("ChapterId", 0)].append(record)
    chapters = []
    for chapter_id in sorted(grouped):
        chapter = chapters_by_id.get(chapter_id)
        nodes = []
        by_id = {record.data["Id"]: record for record in grouped[chapter_id]}
        for raw in _authored_order([record.data for record in grouped[chapter_id]]):
            record = by_id[raw["Id"]]
            items = []
            for quest_id in raw.get("QuestArray", []):
                quest_record = quest_rows.get(quest_id)
                quest_data = quest_record.data.get("Data", {}) if quest_record else {}
                if not isinstance(quest_data, dict):
                    quest_data = {}
                items.append({
                    "game_quest_id": quest_id,
                    "title": titles.get(quest_data.get("TidName")) or f"Quest {quest_id}",
                    "first_observed_game_version": first_seen.get(quest_id),
                    "source": {"file": QUEST_PATH, "raw_path": f"$[{quest_record.row_index}]"}
                    if quest_record else None,
                })
            previous = raw.get("PreNode") or []
            nodes.append({
                "id": raw["Id"],
                "title": titles.get(raw.get("Name")) or (items[0]["title"] if items else f"Tree node {raw['Id']}"),
                "chapter_label": titles.get(raw.get("QuestChapterName")),
                "quest_type_id": raw.get("QuestType"),
                "node_type_id": raw.get("NodeType"),
                "source_kind": "quest_tree",
                "previous_node_ids": previous if isinstance(previous, list) else [previous],
                "next_node_id": raw.get("NextNode") or None,
                "quests": items,
                "source": {"file": TREE_NODE_PATH, "raw_path": f"$[{record.row_index}]"},
            })
        chapters.append({
            "id": chapter_id,
            "title": titles.get(chapter.data.get("Name")) if chapter else None,
            "source": {"file": TREE_CHAPTER_PATH, "raw_path": f"$[{chapter.row_index}]"}
            if chapter else None,
            "nodes": nodes,
        })
    return {
        "selected_game_version": release.game_version,
        "imported_game_versions": versions,
        "quest_type_id": quest_type_id,
        "tree_available": True,
        "chapters": chapters,
        "ordering_basis": "QuestTree.PreNode and QuestTree.NextNode; authored navigation, not a guaranteed playthrough",
        "version_basis": "Earliest imported snapshot containing each quest, not a proven debut patch",
    }


def _first_seen_chapters(snapshot: dict[str, Any], version: str) -> list[dict[str, Any]]:
    """Keep only quests first observed in this imported snapshot."""
    chapters = []
    for chapter in snapshot["chapters"]:
        nodes = []
        for node in chapter["nodes"]:
            quests = [quest for quest in node["quests"]
                      if quest["first_observed_game_version"] == version]
            if quests:
                nodes.append({**node, "quests": quests})
        if nodes:
            chapters.append({**chapter, "nodes": nodes,
                             "snapshot_game_version": version,
                             "tree_available": snapshot["tree_available"]})
    return chapters


@router.get("/story-map")
@public_addresses
async def story_map(
    locale: str = "en",
    game_version: str | None = None,
    quest_type_id: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Browse patch additions, in one selected patch or across all patches in order."""
    if read_projections.should_route_to_projection("story_map", game_version, locale=locale):
        return await read_projections.story_map_page(session, locale, game_version, quest_type_id)
    if await session.scalar(select(Locale.id).where(Locale.code == locale)) is None:
        raise HTTPException(status_code=400, detail=f"unknown locale: {locale}")
    releases = list(await session.scalars(
        select(GameRelease)
        .where(GameRelease.id.in_(
            select(ImportRun.release_id).where(ImportRun.status == "succeeded")
        ))
    ))
    latest_by_version = {release.game_version: release
                         for release in sorted(releases, key=lambda item: item.sequence)}
    ordered = sorted(latest_by_version.values(),
                     key=lambda item: (_version_key(item.game_version), item.sequence))
    versions = [release.game_version for release in ordered]
    if not ordered:
        raise HTTPException(status_code=404, detail="no imported game versions")
    if game_version:
        release = latest_by_version.get(game_version)
        if release is None:
            raise HTTPException(status_code=404, detail="game version not imported")
        snapshot = await _snapshot_map(session, release, locale, quest_type_id, versions)
        return {**snapshot, "chapters": _first_seen_chapters(snapshot, game_version)}

    chapters = []
    batch = await _snapshot_batch(session, ordered, locale)
    for release in ordered:
        snapshot = await _snapshot_map(session, release, locale, quest_type_id, versions, batch)
        chapters.extend(_first_seen_chapters(snapshot, release.game_version))
    return {
        "selected_game_version": None,
        "imported_game_versions": versions,
        "quest_type_id": quest_type_id,
        "tree_available": all(chapter["tree_available"] for chapter in chapters),
        "chapters": chapters,
        "ordering_basis": "Imported snapshots in version order; each quest appears in its earliest observed snapshot. Within each snapshot, source chapters and explicit prerequisites determine grouping.",
        "version_basis": "Earliest imported snapshot containing each quest, not a proven debut patch",
    }

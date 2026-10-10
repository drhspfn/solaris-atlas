"""Bounded source tools; every source retains its imported snapshot identity."""

import hashlib
import json
from typing import Any

from sqlalchemy import Select, or_, select
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.agents.contracts import (
    SOURCE_LOCALE_PRIORITY,
    AnalysisResult,
    NoteRequest,
    citation_groups,
    validate_citations,
)
from wuwa_story.agents.cutscene_vision import validate_description, visual_reference
from wuwa_story.agents.providers import ToolCall
from wuwa_story.agents.settings import AgentSettings
from wuwa_story.db.models.agents import AgentNote
from wuwa_story.db.models.core import (
    Character,
    DialogueLine,
    Item,
    Location,
    PlayerChoice,
    Quest,
    QuestAction,
    QuestState,
    Speaker,
)
from wuwa_story.db.models.graph import Edge, EdgeEvidence, Node, NodeRevision, NodeType
from wuwa_story.db.models.i18n import Locale, LocalizationValue
from wuwa_story.db.models.ontology import RelationType
from wuwa_story.db.models.ops import GameRelease
from wuwa_story.db.models.raw import SourceFile, SourceRecord
from wuwa_story.db.repositories.quest_scope import quest_state_links
from wuwa_story.db.repositories.search import lexical_search

ORDERING = "Authored state/action/talk order, including alternatives. This is not a single guaranteed playthrough; inspect graph links for choices and conditions."


def hash_value(value: Any) -> bytes:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).digest()


def source_edges(release_id: int) -> Select[int]:
    return (
        select(Edge.id)
        .join(EdgeEvidence, EdgeEvidence.edge_id == Edge.id)
        .where(Edge.layer == "source", EdgeEvidence.release_id == release_id)
    )


def observed_nodes(release_id: int) -> Select[int]:
    return select(NodeRevision.node_id).where(NodeRevision.release_id == release_id)


async def imported_snapshot_ids(session: AsyncSession) -> list[int]:
    return list(
        await session.scalars(
            select(GameRelease.id)
            .where(
                select(NodeRevision.id).where(NodeRevision.release_id == GameRelease.id).exists()
            )
            .order_by(GameRelease.sequence)
        )
    )


async def imported_source_revision(session: AsyncSession) -> str:
    # Reuse transactional revision metadata; generated notes/edges must not change job identity.
    revision = await session.scalar(
        sql_text("""
        SELECT md5(string_agg(table_name || ':' || transaction_id::text, ',' ORDER BY table_name))
        FROM ops.public_cache_revision
        WHERE table_name LIKE 'raw.%' OR table_name LIKE 'i18n.%' OR table_name LIKE 'core.%'
           OR table_name IN ('graph.node_revision', 'ops.game_release')
    """)
    )
    if not revision:
        raise ValueError("Imported source revision metadata is unavailable")
    return str(revision)


async def quest_states(session: AsyncSession, quest_id: int, release_id: int) -> list[int]:
    links = quest_state_links(release_id)
    return list(await session.scalars(select(links.c.state_id).where(links.c.quest_id == quest_id)))


async def scope_for_request(
    session: AsyncSession, quest_id: int, game_version: str | None, locale: str
) -> tuple[Quest, GameRelease, Locale]:
    language = await session.scalar(select(Locale).where(Locale.code == locale))
    quest = await session.scalar(select(Quest).where(Quest.game_quest_id == quest_id))
    if quest is None or language is None:
        raise ValueError("Quest or locale not found")
    statement = select(GameRelease).order_by(GameRelease.sequence.desc()).limit(1)
    if game_version is not None:
        statement = statement.where(GameRelease.game_version == game_version)
    else:
        statement = statement.where(
            select(NodeRevision.id)
            .where(NodeRevision.node_id == quest.node_id, NodeRevision.release_id == GameRelease.id)
            .exists()
        )
    release = await session.scalar(statement)
    if release is None or language is None or quest is None:
        raise ValueError("Quest, imported version or locale not found")
    if not await session.scalar(
        select(NodeRevision.id)
        .where(NodeRevision.node_id == quest.node_id, NodeRevision.release_id == release.id)
        .limit(1)
    ):
        raise ValueError("Quest is not observed in this imported snapshot")
    return quest, release, language


async def quest_cutscene_assets(session: AsyncSession, quest_id: int, release_id: int):
    states = await quest_states(session, quest_id, release_id)
    actions = select(QuestAction.node_id).where(QuestAction.quest_state_node_id.in_(states))
    cutscenes = (
        select(Edge.to_node_id)
        .join(RelationType)
        .where(
            Edge.from_node_id.in_(actions),
            RelationType.key == "plays_cutscene",
            Edge.id.in_(source_edges(release_id)),
        )
    )
    variants = (
        select(Edge.to_node_id)
        .join(RelationType)
        .where(
            Edge.from_node_id.in_(cutscenes),
            RelationType.key == "has_variant",
            Edge.id.in_(source_edges(release_id)),
        )
    )
    return await session.execute(
        select(Edge.from_node_id, Node.id, Node.canonical_key)
        .join(RelationType)
        .join(Node, Node.id == Edge.to_node_id)
        .where(
            Edge.from_node_id.in_(variants),
            RelationType.key == "references_asset",
            Edge.id.in_(source_edges(release_id)),
        )
        .order_by(Node.id)
    )


async def quest_fingerprint(
    session: AsyncSession,
    quest: Quest,
    release_id: int,
    locale_id: int | None = None,
    *,
    include_visual: bool = False,
) -> bytes:
    states = await quest_states(session, quest.node_id, release_id)
    action_ids = select(QuestAction.node_id).where(QuestAction.quest_state_node_id.in_(states))
    line_ids = (
        select(DialogueLine.node_id)
        .join(QuestAction, QuestAction.node_id == DialogueLine.action_node_id)
        .where(QuestAction.quest_state_node_id.in_(states))
    )
    revisions = list(
        (
            await session.execute(
                select(NodeRevision.node_id, NodeRevision.content_hash)
                .where(
                    NodeRevision.release_id == release_id,
                    or_(
                        NodeRevision.node_id == quest.node_id,
                        NodeRevision.node_id.in_(states),
                        NodeRevision.node_id.in_(line_ids),
                        NodeRevision.node_id.in_(action_ids),
                    ),
                )
                .order_by(NodeRevision.node_id, NodeRevision.revision)
            )
        ).all()
    )
    keys = select(DialogueLine.localization_key_id).where(DialogueLine.node_id.in_(line_ids))
    text_hashes = list(
        (
            await session.execute(
                select(
                    LocalizationValue.key_id,
                    *([LocalizationValue.locale_id] if locale_id is None else []),
                    LocalizationValue.content_hash,
                    LocalizationValue.status,
                )
                .where(
                    LocalizationValue.release_id == release_id,
                    *([LocalizationValue.locale_id == locale_id] if locale_id is not None else []),
                    or_(
                        LocalizationValue.key_id.in_(keys),
                        LocalizationValue.key_id.in_([quest.name_key_id, quest.description_key_id]),
                    ),
                )
                .order_by(LocalizationValue.key_id, LocalizationValue.locale_id)
            )
        ).all()
    )
    edges = list(
        (
            await session.execute(
                select(
                    Edge.from_node_id, Edge.relation_type_id, Edge.to_node_id, Edge.metadata_json
                )
                .where(
                    Edge.id.in_(source_edges(release_id)),
                    or_(
                        Edge.from_node_id == quest.node_id,
                        Edge.from_node_id.in_(states),
                        Edge.from_node_id.in_(line_ids),
                        Edge.from_node_id.in_(action_ids),
                    ),
                )
                .order_by(Edge.id)
            )
        ).all()
    )
    line_fields = (
        await session.execute(
            select(
                DialogueLine.node_id,
                DialogueLine.inline_text,
                DialogueLine.localization_key_id,
                DialogueLine.speaker_node_id,
                DialogueLine.source_index,
            )
            .where(DialogueLine.node_id.in_(line_ids))
            .order_by(DialogueLine.node_id)
        )
    ).all()
    payload = {
        "revisions": [tuple(row) for row in revisions],
        "text": [tuple(row) for row in text_hashes],
        "edges": [tuple(row) for row in edges],
        "line_fields": [tuple(row) for row in line_fields],
    }
    if include_visual:
        visual = []
        for _, asset_id, _ in await quest_cutscene_assets(session, quest.node_id, release_id):
            report = await visual_reference(session, asset_id)
            if report:
                visual.append(
                    (asset_id, report.id, hash_value(report.metadata_json).hex())
                )
        if visual:
            payload["visual"] = visual
    return hash_value(payload)


class EvidenceTools:
    def __init__(
        self,
        session: AsyncSession,
        *,
        run_id: int,
        quest: Quest,
        release_id: int,
        locale: Locale,
        settings: AgentSettings,
        evidence: dict[int, str] | None = None,
        known_nodes: dict[int, str] | None = None,
        source_locale: str | None = None,
        source_release_ids: list[int] | None = None,
        source_evidence: dict[str, str] | None = None,
        source_nodes: dict[str, str] | None = None,
        visual_evidence: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.session, self.run_id, self.quest = session, run_id, quest
        self.release_id, self.locale, self.settings = release_id, locale, settings
        self.evidence = evidence if evidence is not None else {}
        self.known_nodes = known_nodes if known_nodes is not None else {}
        self.coverage: dict[int, int] = {}
        self.total_lines: int | None = None
        self._target_positions: dict[int, int] | None = None
        self.text_locales: dict[int, str] = {}
        self.source_locale = source_locale
        self.source_release_ids = source_release_ids or [release_id]
        self.cross_snapshot = source_release_ids is not None
        self.source_evidence = source_evidence if source_evidence is not None else {}
        self.source_nodes = source_nodes if source_nodes is not None else {}
        self.validated_sources: list[dict[str, Any]] = []
        self.visual_evidence = visual_evidence if visual_evidence is not None else {}

    async def context(self, snapshot_id: int) -> "EvidenceTools":
        if snapshot_id not in self.source_release_ids:
            raise ValueError("Snapshot is outside this run's pinned imported sources")
        if snapshot_id == self.release_id:
            return self
        return EvidenceTools(
            self.session,
            run_id=self.run_id,
            quest=self.quest,
            release_id=snapshot_id,
            locale=self.locale,
            settings=self.settings,
            evidence=self.evidence,
            known_nodes=self.known_nodes,
            source_locale=self.source_locale,
            source_release_ids=[snapshot_id],
            source_evidence=self.source_evidence,
            source_nodes=self.source_nodes,
            visual_evidence=self.visual_evidence,
        )

    async def cutscene_inventory(self, quest: Quest) -> list[dict[str, Any]]:
        rows = await quest_cutscene_assets(self.session, quest.node_id, self.release_id)
        result = []
        for variant, asset_id, key in rows:
            report = await visual_reference(self.session, asset_id)
            result.append(
                {
                    "variant_node_id": variant,
                    "asset_node_id": asset_id,
                    "canonical_key": key,
                    "visual_reference_id": report.id if report else None,
                    "recording_version": report.metadata_json["asset_version"] if report else None,
                    "visual_status": "available" if report else "not_analyzed",
                    "observation_count": len(report.metadata_json.get("events", [])) if report else 0,
                }
            )
        return result

    async def authored_quest_type(self, quest: Quest | None = None) -> str | None:
        quest = quest or self.quest
        raw = await self.session.scalar(select(SourceRecord.data)
            .join(NodeRevision, NodeRevision.source_record_id == SourceRecord.id)
            .where(NodeRevision.node_id == quest.node_id, NodeRevision.release_id == self.release_id)
            .order_by(NodeRevision.revision.desc()).limit(1))
        if raw is None:
            return quest.quest_type
        if not isinstance(raw, dict):
            return None
        data = raw.get("Data", {})
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except ValueError:
                return None
        return str(data["Type"]) if isinstance(data, dict) and type(data.get("Type")) is int else None

    async def read_cutscene_visual(
        self, asset_node_id: int, offset: int = 0, limit: int = 12
    ) -> dict:
        if offset < 0 or not 1 <= limit <= 20:
            raise ValueError("Invalid visual pagination: offset >= 0, limit 1..20 (default 12)")
        await self.remember_node(asset_node_id)
        # Restrict to an authored cutscene asset linked to the target quest.
        if asset_node_id not in {
            item["asset_node_id"] for item in await self.cutscene_inventory(self.quest)
        }:
            raise ValueError("Asset is not a cutscene variant in this quest")
        report = await visual_reference(self.session, asset_node_id)
        if report is None:
            return {"asset_node_id": asset_node_id, "status": "not_analyzed"}
        metadata = report.metadata_json
        events = metadata["events"][offset : offset + limit]
        receipt = self.visual_evidence.get(
            str(report.id), {"hash": hash_value(metadata).hex(), "indices": []}
        )
        receipt["indices"] = sorted(
            set(receipt["indices"]) | set(range(offset, offset + len(events)))
        )
        receipt["snapshot_id"] = self.release_id
        self.visual_evidence[str(report.id)] = receipt
        return {
            "asset_node_id": asset_node_id,
            "visual_reference_id": report.id,
            "snapshot_id": self.release_id,
            "asset_version": metadata["asset_version"],
            "evidence_type": metadata["evidence_type"],
            "duration": metadata["duration"],
            "limitations": "AI observations of sampled frames in the stated recording version, not proof of historical visuals. Variant-specific, not game-authored dialogue; gaps do not prove absence.",
            "events": [{"index": offset + index, **event} for index, event in enumerate(events)],
            "next_offset": offset + len(events)
            if offset + len(events) < len(metadata["events"])
            else None,
        }

    async def snapshot_for_node(self, node_id: int) -> int:
        releases = set(
            await self.session.scalars(
                select(NodeRevision.release_id).where(
                    NodeRevision.node_id == node_id,
                    NodeRevision.release_id.in_(self.source_release_ids),
                )
            )
        )
        if self.release_id in releases:
            return self.release_id
        for snapshot_id in reversed(self.source_release_ids):
            if snapshot_id in releases:
                return snapshot_id
        raise ValueError("Node is not observed in this run's imported sources")

    async def snapshots(self) -> dict[str, Any]:
        rows = await self.session.scalars(
            select(GameRelease)
            .where(GameRelease.id.in_(self.source_release_ids))
            .order_by(GameRelease.sequence)
        )
        return {
            "target_snapshot_id": self.release_id,
            "snapshots": [
                {"snapshot_id": row.id, "game_version": row.game_version} for row in rows
            ],
            "meaning": "Imported snapshots pinned when this run was queued. Patch/version and import order are NOT world or quest chronology.",
        }

    async def text_values(
        self, keys: list[int | None], locale: str | None = None
    ) -> dict[int, str]:
        locale = locale or self.source_locale
        rows = await self.session.execute(
            select(LocalizationValue.key_id, LocalizationValue.content, Locale.code)
            .join(Locale, Locale.id == LocalizationValue.locale_id)
            .where(
                LocalizationValue.release_id == self.release_id,
                *([Locale.code == locale] if locale is not None else []),
                LocalizationValue.key_id.in_([key for key in keys if key is not None]),
                LocalizationValue.status == "resolved_nonempty",
            )
        )
        priority = {code: index for index, code in enumerate(SOURCE_LOCALE_PRIORITY)}
        values: dict[int, str] = {}
        for key, text, code in sorted(rows, key=lambda row: (priority.get(row[2], 99), row[2])):
            if text and key not in values:
                values[key] = text
                self.text_locales[key] = code
        return values

    def remember_text(self, node_id: int, text: str) -> None:
        previous = self.evidence.get(node_id, "")
        if text and text not in previous:
            self.evidence[node_id] = previous + "\n\n" + text if previous else text
        key = f"{self.release_id}:{node_id}"
        previous_source = self.source_evidence.get(key, "")
        if text and text not in previous_source:
            self.source_evidence[key] = previous_source + "\n\n" + text if previous_source else text

    async def remember_node(self, node_id: int) -> Node:
        node = await self.session.scalar(
            select(Node).where(Node.id == node_id, Node.id.in_(observed_nodes(self.release_id)))
        )
        if node is None:
            raise ValueError("Node is not observed in the selected snapshot")
        self.known_nodes[node.id] = node.canonical_key
        self.source_nodes[f"{self.release_id}:{node.id}"] = node.canonical_key
        return node

    async def read_quest(
        self,
        quest_id: int | None = None,
        offset: int = 0,
        limit: int = 30,
        locale: str | None = None,
    ) -> dict[str, Any]:
        if not 0 <= offset <= 100000 or not 1 <= limit <= 50:
            raise ValueError("Invalid transcript pagination: offset 0..100000, limit 1..50 (default 30); use the returned next_offset")
        quest = (
            self.quest
            if quest_id is None
            else await self.session.scalar(select(Quest).where(Quest.game_quest_id == quest_id))
        )
        if quest is None:
            raise ValueError("Unknown quest")
        await self.remember_node(quest.node_id)
        states = await quest_states(self.session, quest.node_id, self.release_id)
        rows = list(
            (
                await self.session.execute(
                    select(DialogueLine, QuestAction, QuestState, Node)
                    .join(QuestAction, QuestAction.node_id == DialogueLine.action_node_id)
                    .join(QuestState, QuestState.node_id == QuestAction.quest_state_node_id)
                    .join(Node, Node.id == DialogueLine.node_id)
                    .where(
                        QuestAction.quest_state_node_id.in_(states),
                        Node.id.in_(observed_nodes(self.release_id)),
                    )
                    .order_by(
                        QuestState.state_key,
                        QuestAction.action_index,
                        DialogueLine.source_index,
                        Node.id,
                    )
                    .offset(offset)
                    .limit(limit + 1)
                )
            ).all()
        )
        values = await self.text_values(
            [
                quest.name_key_id,
                quest.description_key_id,
                *[row[0].localization_key_id for row in rows],
            ],
            locale,
        )
        lines: list[dict[str, Any]] = []
        payload = {
            "snapshot_id": self.release_id,
            "quest_node_id": quest.node_id,
            "quest_id": quest.game_quest_id,
            "name": values.get(quest.name_key_id or -1),
            "description": (values.get(quest.description_key_id or -1) or "")[:1000],
            "ordering": ORDERING,
            "quest_type": await self.authored_quest_type(quest),
            "cutscenes": await self.cutscene_inventory(quest) if offset == 0 else [],
            "offset": offset,
            "lines": lines,
            "next_offset": None,
        }
        # Inventory size varies with the quest; reserve its actual serialized size.
        size = len(json.dumps(payload, ensure_ascii=False)) + 32
        for line, action, state, node in rows[:limit]:
            full_text = (
                values.get(line.localization_key_id or -1)
                or (line.inline_text if locale is None else "")
                or ""
            )
            item: dict[str, Any] = {
                "snapshot_id": self.release_id,
                "encounter_order": offset + len(lines),
                "node_id": node.id,
                "canonical_key": node.canonical_key,
                "text": full_text[:2500],
                "locale": self.text_locales.get(line.localization_key_id or -1)
                if line.localization_key_id in values
                else None,
                "missing_text": not bool(full_text),
                "text_truncated": len(full_text) > 2500,
                "speaker_node_id": line.speaker_node_id,
                "state": state.state_key,
                "action_index": action.action_index,
                "source_index": line.source_index,
            }
            count = len(json.dumps(item, ensure_ascii=False)) + 2
            if lines and size + count > self.settings.tool_result_chars:
                break
            lines.append(item)
            size += count
            self.known_nodes[node.id] = node.canonical_key
            self.source_nodes[f"{self.release_id}:{node.id}"] = node.canonical_key
            if full_text:
                self.remember_text(node.id, item["text"])
        if quest.node_id == self.quest.node_id:
            self.coverage[offset] = max(self.coverage.get(offset, 0), len(lines))
            if len(rows) == len(lines) and (lines or offset == 0):
                self.total_lines = offset + len(lines)
        payload["next_offset"] = offset + len(lines) if len(rows) > len(lines) else None
        return payload

    async def target_positions(self) -> dict[int, int]:
        if self._target_positions is None:
            states = await quest_states(self.session, self.quest.node_id, self.release_id)
            anchors = await self.session.scalars(
                select(DialogueLine.node_id)
                .join(QuestAction, QuestAction.node_id == DialogueLine.action_node_id)
                .join(QuestState, QuestState.node_id == QuestAction.quest_state_node_id)
                .where(QuestState.node_id.in_(states), DialogueLine.node_id.in_(observed_nodes(self.release_id)))
                .order_by(QuestState.state_key, QuestAction.action_index, DialogueLine.source_index, DialogueLine.node_id)
            )
            self._target_positions = {node_id: index for index, node_id in enumerate(anchors)}
        return self._target_positions

    async def read_node(self, node_id: int, locale: str | None = None) -> dict[str, Any]:
        node = await self.remember_node(node_id)
        source_truncated = False
        kind = await self.session.scalar(select(NodeType.key).where(NodeType.id == node.type_id))
        line = await self.session.get(DialogueLine, node_id)
        choice = await self.session.get(PlayerChoice, node_id)
        speaker = await self.session.get(Speaker, node_id)
        if line or choice or speaker:
            key = (
                line.localization_key_id
                if line
                else choice.localization_key_id
                if choice
                else speaker.name_key_id
                if speaker
                else None
            )
            values = await self.text_values([key], locale)
            text = (
                values.get(key or -1) or (line.inline_text if line and locale is None else "") or ""
            )
            source_locale = self.text_locales.get(key or -1) if key in values else None
            available_locales = list(
                await self.session.scalars(
                    select(Locale.code)
                    .join(LocalizationValue, LocalizationValue.locale_id == Locale.id)
                    .where(
                        LocalizationValue.release_id == self.release_id,
                        LocalizationValue.key_id == key,
                        LocalizationValue.status == "resolved_nonempty",
                    )
                    .order_by(Locale.code)
                )
            )
        else:
            source_locale, available_locales = None, []
            row = (
                await self.session.execute(
                    select(SourceRecord.data, SourceFile.logical_source_path)
                    .join(NodeRevision, NodeRevision.source_record_id == SourceRecord.id)
                    .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
                    .where(
                        NodeRevision.node_id == node_id, NodeRevision.release_id == self.release_id
                    )
                    .order_by(NodeRevision.revision.desc())
                    .limit(1)
                )
            ).first()
            raw_limit = 6000 if self.source_locale is not None else 3000
            raw_text = json.dumps(row[0], ensure_ascii=False) if row and locale is None else ""
            source_truncated = len(raw_text) > raw_limit
            text = raw_text[:raw_limit]
            model = {
                "quest": Quest,
                "character": Character,
                "item": Item,
                "location": Location,
            }.get(kind or "")
            record = await self.session.get(model, node_id) if model else None
            keys = {
                field: getattr(record, field + "_key_id", None)
                for field in ("name", "description", "nickname")
            }
            values = await self.text_values(list(keys.values()), locale)
            localized = [
                {"field": field, "text": values[key][:900], "locale": self.text_locales[key]}
                for field, key in keys.items()
                if key in values
            ]
            if localized:
                source_truncated = source_truncated or any(
                    len(value) > 900 for value in values.values()
                )
                text += "\n" + "\n".join(
                    value["field"] + ": " + value["text"] for value in localized
                )
                languages = {value["locale"] for value in localized}
                source_locale = (
                    next(iter(languages)) if len(languages) == 1 and locale is not None else None
                )
            available_locales = list(
                await self.session.scalars(
                    select(Locale.code)
                    .join(LocalizationValue, LocalizationValue.locale_id == Locale.id)
                    .where(
                        LocalizationValue.release_id == self.release_id,
                        LocalizationValue.key_id.in_(
                            [key for key in keys.values() if key is not None]
                        ),
                        LocalizationValue.status == "resolved_nonempty",
                    )
                    .distinct()
                    .order_by(Locale.code)
                )
            )
        returned = text[:6000]
        if returned:
            self.remember_text(node_id, returned)
        return {
            "snapshot_id": self.release_id,
            "node_id": node.id,
            "canonical_key": node.canonical_key,
            "type": kind,
            "encounter_order": (await self.target_positions()).get(node.id) if line else None,
            "text": returned,
            "locale": source_locale,
            "available_locales": available_locales,
            "localized_fields": localized if not (line or choice or speaker) else [],
            "truncated": source_truncated or len(text) > 6000,
            "meaning": "Imported source data; IDs and authored order are not inferred chronology.",
        }

    async def graph(
        self, node_id: int, offset: int = 0, snapshot_id: int | None = None
    ) -> dict[str, Any]:
        release_ids = [snapshot_id] if snapshot_id is not None else self.source_release_ids
        context = await self.context(
            snapshot_id if snapshot_id is not None else await self.snapshot_for_node(node_id)
        )
        await context.remember_node(node_id)
        if not 0 <= offset <= 10000:
            raise ValueError("Invalid graph pagination")
        rows = list(
            (
                await self.session.execute(
                    select(Edge, RelationType.key)
                    .join(RelationType, RelationType.id == Edge.relation_type_id)
                    .where(
                        Edge.id.in_(
                            select(EdgeEvidence.edge_id).where(
                                EdgeEvidence.release_id.in_(release_ids),
                                Edge.layer == "source",
                            )
                        ),
                        or_(Edge.from_node_id == node_id, Edge.to_node_id == node_id),
                    )
                    .order_by(Edge.id)
                    .offset(offset)
                    .limit(31)
                )
            ).all()
        )
        nodes = list(
            await self.session.scalars(
                select(Node).where(
                    Node.id.in_(
                        {
                            value
                            for edge, _ in rows
                            for value in (edge.from_node_id, edge.to_node_id)
                        }
                    ),
                    Node.id.in_(
                        select(NodeRevision.node_id).where(NodeRevision.release_id.in_(release_ids))
                    ),
                )
            )
        )
        self.known_nodes.update({node.id: node.canonical_key for node in nodes})
        versions = await self.session.execute(
            select(NodeRevision.node_id, NodeRevision.release_id).where(
                NodeRevision.node_id.in_([node.id for node in nodes]),
                NodeRevision.release_id.in_(release_ids),
            )
        )
        for node_id_in_graph, source_snapshot_id in versions:
            self.source_nodes[f"{source_snapshot_id}:{node_id_in_graph}"] = self.known_nodes[
                node_id_in_graph
            ]
        edge_versions: dict[int, set[int]] = {}
        for edge_id, source_snapshot_id in await self.session.execute(
            select(EdgeEvidence.edge_id, EdgeEvidence.release_id).where(
                EdgeEvidence.edge_id.in_([edge.id for edge, _ in rows[:30]]),
                EdgeEvidence.release_id.in_(release_ids),
            )
        ):
            if source_snapshot_id is not None:
                edge_versions.setdefault(edge_id, set()).add(source_snapshot_id)
        return {
            "nodes": [{"node_id": node.id, "canonical_key": node.canonical_key} for node in nodes],
            "edges": [
                {
                    "id": edge.id,
                    "snapshot_ids": sorted(edge_versions.get(edge.id, set())),
                    "from": edge.from_node_id,
                    "to": edge.to_node_id,
                    "relation": relation,
                    "basis": edge.basis,
                    "metadata": json.dumps(edge.metadata_json, ensure_ascii=False)[:200],
                }
                for edge, relation in rows[:30]
            ],
            "next_offset": offset + 30 if len(rows) > 30 else None,
        }

    async def search(self, query: str, locale: str | None = None) -> dict[str, Any]:
        if not 1 <= len(query) <= 256:
            raise ValueError("Invalid search query")
        language = await self.session.scalar(select(Locale).where(Locale.code == (locale or "en")))
        if language is None:
            raise ValueError("Unknown source locale")
        rows = await lexical_search(self.session, query, locale_id=language.id, limit=12)
        versions: dict[int, set[int]] = {}
        for node_id, snapshot_id in await self.session.execute(
            select(NodeRevision.node_id, NodeRevision.release_id).where(
                NodeRevision.release_id.in_(self.source_release_ids),
                NodeRevision.node_id.in_([row["id"] for row in rows]),
            )
        ):
            if snapshot_id is not None:
                versions.setdefault(node_id, set()).add(snapshot_id)
        for row in rows:
            for snapshot_id in versions.get(row["id"], set()):
                self.known_nodes[row["id"]] = row["canonical_key"]
                self.source_nodes[f"{snapshot_id}:{row['id']}"] = row["canonical_key"]
        return {
            "results": [
                {**row, "snapshot_ids": sorted(versions[row["id"]])}
                for row in rows
                if row["id"] in versions
            ],
            "meaning": "Search index provides candidates across imported snapshots, not version-specific evidence. Read an explicit snapshot before citing; absent matches do not prove absent lore.",
        }

    async def memory(self, query: str = "") -> dict[str, Any]:
        if len(query) > 256:
            raise ValueError("Memory query too long")
        statement = select(AgentNote).where(AgentNote.release_id.in_(self.source_release_ids))
        if query:
            statement = statement.where(
                AgentNote.text.ilike("%" + query.replace("%", "\\%").replace("_", "\\_") + "%")
            )
        else:
            statement = statement.where(AgentNote.node_id == self.quest.node_id)
        notes = list(await self.session.scalars(statement.order_by(AgentNote.id.desc()).limit(8)))
        return {
            "notes": [
                {
                    "id": note.id,
                    "snapshot_id": note.release_id,
                    "node_id": note.node_id,
                    "kind": note.kind,
                    "text": note.text[:1000],
                    "citations": note.citations,
                }
                for note in notes
            ],
            "meaning": "Generated working memory, NOT primary evidence. Re-read cited source nodes.",
        }

    async def note(self, request: NoteRequest) -> dict[str, Any]:
        validate_citations(request, self.evidence)
        if self.cross_snapshot:
            self.validate_source_identity(request)
        note = AgentNote(
            run_id=self.run_id,
            node_id=self.quest.node_id,
            release_id=self.release_id,
            locale_id=self.locale.id,
            text=request.text,
            kind=request.kind,
            citations=[citation.model_dump() for citation in request.citations],
        )
        self.session.add(note)
        await self.session.flush()
        return {"saved_note_id": note.id}

    async def call(self, call: ToolCall) -> dict[str, Any]:
        args = dict(call.arguments)
        if call.name == "list_snapshots":
            return await self.snapshots()
        if call.name in ("read_quest", "read_node", "graph_neighbors", "read_cutscene_visual"):
            snapshot_id = args.pop("snapshot_id", None)
            if call.name == "graph_neighbors":
                return await self.graph(**args, snapshot_id=snapshot_id)
            if snapshot_id is None and call.name != "graph_neighbors":
                node_id = args.get("node_id", args.get("asset_node_id", self.quest.node_id))
                if call.name == "read_quest" and args.get("quest_id") is not None:
                    node_id = await self.session.scalar(
                        select(Quest.node_id).where(Quest.game_quest_id == args["quest_id"])
                    )
                    if node_id is None:
                        raise ValueError("Unknown quest")
                snapshot_id = await self.snapshot_for_node(node_id)
            target = await self.context(snapshot_id) if snapshot_id is not None else self
            if call.name == "read_quest":
                return await target.read_quest(**args)
            if call.name == "read_node":
                return await target.read_node(**args)
            if call.name == "read_cutscene_visual":
                return await target.read_cutscene_visual(**args)
        if call.name == "search_entities":
            return await self.search(**args)
        if call.name == "search_lore":
            query = args.get("query", "")
            from wuwa_story.search.lore import LoreSearchService
            from wuwa_story.search.embedding import generate_query_embedding
            try:
                query_embedding = await generate_query_embedding(self.session, query)
                service = LoreSearchService(self.session)
                results = await service.hybrid_search(query, query_embedding, limit=5)
                output = ""
                for i, r in enumerate(results, 1):
                    output += f"[{i}] Quest: {r.quest_id} | Type: {r.chunk_type}\n{r.content}\n\n"
                return {"results": output if output else "No results found"}
            except Exception as e:
                return {"error": str(e)}
        if call.name == "read_memory":
            return await self.memory(**args)
        if call.name == "save_note":
            return await self.note(NoteRequest.model_validate(args))
        raise ValueError("Unknown agent tool")

    def validate_source_identity(self, result: AnalysisResult | NoteRequest) -> None:
        for group in citation_groups(result):
            for citation in group.citations:
                key = f"{citation.snapshot_id}:{citation.node_id}"
                if (
                    citation.snapshot_id not in self.source_release_ids
                    or citation.quote not in self.source_evidence.get(key, "")
                ):
                    raise ValueError("Citation must quote a read source in its explicit snapshot")

    async def validate_result(
        self,
        result: AnalysisResult,
        *,
        require_full_quest: bool = True,
        require_visual: bool = False,
    ) -> None:
        from wuwa_story.agents.narrative import validate_narrative_links

        validate_narrative_links(result)
        if require_visual:
            available = {
                item["asset_node_id"]
                for item in await self.cutscene_inventory(self.quest)
                if item["visual_status"] == "available"
            }
            described = {item.asset_node_id for item in result.cutscene_descriptions}
            if not available <= described:
                raise ValueError(
                    "Read and describe every available cutscene variant before publishing"
                )
            if len(described) != len(result.cutscene_descriptions):
                raise ValueError("Duplicate cutscene variant descriptions")
        for description in result.cutscene_descriptions:
            receipt = self.visual_evidence.get(str(description.visual_reference_id))
            if receipt is None:
                raise ValueError("Read visual evidence before describing a cutscene")
            context = await self.context(receipt["snapshot_id"])
            report = await visual_reference(self.session, description.asset_node_id)
            if (
                report is None
                or report.id != description.visual_reference_id
                or hash_value(report.metadata_json).hex() != receipt["hash"]
            ):
                raise ValueError("Visual source changed before publication")
            if description.asset_node_id not in {
                item["asset_node_id"] for item in await context.cutscene_inventory(context.quest)
            }:
                raise ValueError("Cutscene description is outside this quest")
            validate_description(description, report.metadata_json, receipt["indices"])
        validate_citations(result, self.evidence)
        if self.cross_snapshot:
            self.validate_source_identity(result)
        # Recheck exact source text before publication, including sources outside this quest.
        groups = citation_groups(result)
        citations = [citation for group in groups for citation in group.citations]
        fresh_sources: dict[tuple[int, int, str | None], dict[str, Any]] = {}
        for citation in citations:
            snapshot_id = citation.snapshot_id or self.release_id
            identity = (snapshot_id, citation.node_id, citation.locale)
            if identity not in fresh_sources:
                context = await self.context(snapshot_id)
                fresh_sources[identity] = await context.read_node(citation.node_id, citation.locale)
            fresh = fresh_sources[identity]
            if citation.quote not in fresh["text"]:
                raise ValueError("Cited source changed or no longer contains the quoted passage")
            citation.locale = fresh["locale"]
        self.validated_sources = [
            {
                "snapshot_id": snapshot_id,
                "node_id": node_id,
                "locale": value["locale"],
                "text_hash": hash_value(value["text"]).hex(),
            }
            for (snapshot_id, node_id, _), value in fresh_sources.items()
        ]
        end = 0
        for offset, count in sorted(self.coverage.items()):
            if offset > end:
                break
            end = max(end, offset + count)
        if require_full_quest and (self.total_lines is None or end < self.total_lines):
            raise ValueError(f"Read every page of the target quest before publishing: next missing read_quest offset={end}, limit=50; follow next_offset until null, do not reread covered pages")
        if any(block.assertions for block in result.blocks):
            positions = await self.target_positions()
            for block in result.blocks:
                for assertion in block.assertions:
                    chronology = assertion.chronology_in_quest
                    if chronology.anchor_node_id not in positions:
                        raise ValueError(f"Chronology anchor {chronology.anchor_node_id} is not a dialogue passage in the target quest")
                    # This ordinal is imported metadata, not a model interpretation.
                    chronology.order = positions[chronology.anchor_node_id]
                    if self.cross_snapshot and not any(
                        c.node_id == chronology.anchor_node_id and c.snapshot_id == self.release_id
                        for c in assertion.citations
                    ):
                        raise ValueError(
                            "Quest encounter must cite its anchor in the target snapshot"
                        )
                    for resolution in assertion.later_resolution:
                        position = positions.get(resolution.revealed_in_node_id)
                        in_target = any(
                            c.node_id == resolution.revealed_in_node_id
                            and (c.snapshot_id or self.release_id) == self.release_id
                            for c in resolution.citations
                        )
                        if in_target and position is not None and position <= chronology.order:
                            raise ValueError("A later revelation must follow the initial encounter")
        ids = {node_id for block in result.blocks for node_id in block.related_node_ids}
        ids.update(record.node_id for block in result.blocks for record in block.related_records)
        for block in result.blocks:
            for assertion in block.assertions:
                ids.add(assertion.chronology_in_quest.anchor_node_id)
                ids.update(item.revealed_in_node_id for item in assertion.later_resolution)
                if assertion.chronology_in_quest.anchor_node_id not in {
                    c.node_id for c in assertion.citations
                }:
                    raise ValueError("Quest chronology must be anchored to a cited passage")
                for resolution in assertion.later_resolution:
                    if resolution.revealed_in_node_id not in {
                        c.node_id for c in resolution.citations
                    }:
                        raise ValueError("Later resolution must cite its revelation source")
        ids.update(value for link in result.links for value in (link.from_node_id, link.to_node_id))
        ids.update(value for event in result.events for value in event.participant_node_ids)
        if ids - self.known_nodes.keys():
            raise ValueError("Links may only refer to nodes discovered in this run")
        known = set(
            await self.session.scalars(
                select(NodeRevision.node_id).where(
                    NodeRevision.release_id.in_(self.source_release_ids),
                    NodeRevision.node_id.in_(ids),
                )
            )
        )
        if ids - known:
            raise ValueError("Linked nodes are not observed in this snapshot")
        relations = set(
            await self.session.scalars(
                select(RelationType.key).where(
                    RelationType.key.in_([link.relation for link in result.links])
                )
            )
        )
        if {link.relation for link in result.links} - relations:
            raise ValueError("Use an existing graph relation; report missing ontology as a note")


def definitions() -> list[dict[str, Any]]:
    def tool(
        name: str, description: str, properties: dict[str, Any], required: list[str] | None = None
    ) -> dict[str, Any]:
        return {
            "type": "function",
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
                "additionalProperties": False,
            },
            "strict": False,
        }

    integer = {"type": "integer"}
    snapshot = {
        "type": "integer",
        "description": "Exact imported snapshot ID from list_snapshots/search/graph results. Omit to prefer the target snapshot for reading, or all imported snapshots for graph neighbors.",
    }
    locale = {
        "type": "string",
        "description": "Optional source language (en, zh-Hans, ja, or another imported locale). Omit for priority fallback.",
    }
    return [
        tool(
            "list_snapshots",
            "List pinned imported snapshots available for research. Patch versions do not establish story chronology.",
            {},
        ),
        tool(
            "read_quest",
            "Read paginated exact-snapshot dialogue. Follow next_offset until null; alternatives are not a single playthrough.",
            {
                "quest_id": {"type": "integer", "description": "Game quest ID, not a graph node ID. Omit for the target quest."},
                "offset": {"type": "integer", "minimum": 0, "maximum": 100000},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 30},
                "locale": locale,
                "snapshot_id": snapshot,
            },
        ),
        tool(
            "read_node",
            "Read a source node. available_locales lists translations; call again with locale to compare Chinese/Japanese. Cite returned text with its locale.",
            {"node_id": integer, "locale": locale, "snapshot_id": snapshot},
            ["node_id"],
        ),
        tool(
            "read_cutscene_visual",
            "Read paginated AI visual observations for an authored cutscene variant. These are sampled visual evidence, NOT exact dialogue. Follow next_offset; preserve variant identity.",
            {
                "asset_node_id": integer,
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 12},
                "snapshot_id": snapshot,
            },
            ["asset_node_id"],
        ),
        tool(
            "graph_neighbors",
            "Read source graph neighbors; paginate using next_offset to inspect choices, scene links and conditions.",
            {"node_id": integer, "offset": integer, "snapshot_id": snapshot},
            ["node_id"],
        ),
        tool(
            "search_entities",
            "Resolve characters, factions, quests and concepts. Read source nodes before citing.",
            {"query": {"type": "string"}, "locale": locale},
            ["query"],
        ),
        tool(
            "search_lore",
            "Semantic search across previously generated Wuthering Waves storyline lore (cutscenes, quests, characters). Use this to recall past lore facts.",
            {"query": {"type": "string"}},
            ["query"],
        ),
        tool(
            "read_memory",
            "Read prior generated notes. These are not evidence; verify their sources.",
            {"query": {"type": "string"}},
        ),
        {
            "type": "function",
            "name": "save_note",
            "description": "Save scoped working memory or report missing_data, contradiction or bug.",
            "parameters": NoteRequest.model_json_schema(),
            "strict": False,
        },
        tool(
            "finish_analysis",
            "Publish only after reading the full target quest. result_json must match the AnalysisResult schema in the system instruction.",
            {"result_json": {"type": "string"}},
            ["result_json"],
        ),
    ]

"""Bounded source tools; exact snapshot text is kept distinct from generated memory."""

import hashlib
import json
from typing import Any

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.agents.contracts import (
    SOURCE_LOCALE_PRIORITY,
    AnalysisResult,
    ExplanationBlock,
    InferredEvent,
    InferredLink,
    NoteRequest,
    validate_citations,
)
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


async def quest_states(session: AsyncSession, quest_id: int, release_id: int) -> list[int]:
    children = (
        select(Edge.to_node_id)
        .join(RelationType, RelationType.id == Edge.relation_type_id)
        .where(
            Edge.from_node_id == quest_id,
            RelationType.key == "has_quest_node",
            Edge.id.in_(source_edges(release_id)),
        )
    )
    states = (
        select(Edge.to_node_id)
        .join(RelationType, RelationType.id == Edge.relation_type_id)
        .where(
            or_(Edge.from_node_id == quest_id, Edge.from_node_id.in_(children)),
            RelationType.key == "references_flow_state",
            Edge.id.in_(source_edges(release_id)),
        )
    )
    return list(await session.scalars(states.distinct()))


async def scope_for_request(
    session: AsyncSession, quest_id: int, game_version: str, locale: str
) -> tuple[Quest, GameRelease, Locale]:
    release = await session.scalar(
        select(GameRelease)
        .where(GameRelease.game_version == game_version)
        .order_by(GameRelease.sequence.desc())
        .limit(1)
    )
    language = await session.scalar(select(Locale).where(Locale.code == locale))
    quest = await session.scalar(select(Quest).where(Quest.game_quest_id == quest_id))
    if release is None or language is None or quest is None:
        raise ValueError("Quest, imported version or locale not found")
    if not await session.scalar(
        select(NodeRevision.id)
        .where(NodeRevision.node_id == quest.node_id, NodeRevision.release_id == release.id)
        .limit(1)
    ):
        raise ValueError("Quest is not observed in this imported snapshot")
    return quest, release, language


async def quest_fingerprint(
    session: AsyncSession, quest: Quest, release_id: int, locale_id: int | None = None
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
    return hash_value(
        {
            "revisions": [tuple(row) for row in revisions],
            "text": [tuple(row) for row in text_hashes],
            "edges": [tuple(row) for row in edges],
            "line_fields": [tuple(row) for row in line_fields],
        }
    )


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
    ) -> None:
        self.session, self.run_id, self.quest = session, run_id, quest
        self.release_id, self.locale, self.settings = release_id, locale, settings
        self.evidence = evidence or {}
        self.known_nodes = known_nodes or {}
        self.coverage: dict[int, int] = {}
        self.total_lines: int | None = None
        self.text_locales: dict[int, str] = {}
        self.source_locale = source_locale

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

    async def remember_node(self, node_id: int) -> Node:
        node = await self.session.scalar(
            select(Node).where(Node.id == node_id, Node.id.in_(observed_nodes(self.release_id)))
        )
        if node is None:
            raise ValueError("Node is not observed in the selected snapshot")
        self.known_nodes[node.id] = node.canonical_key
        return node

    async def read_quest(
        self,
        quest_id: int | None = None,
        offset: int = 0,
        limit: int = 30,
        locale: str | None = None,
    ) -> dict[str, Any]:
        if not 0 <= offset <= 100000 or not 1 <= limit <= 50:
            raise ValueError("Invalid transcript pagination")
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
        size = 0
        for line, action, state, node in rows[:limit]:
            full_text = (
                values.get(line.localization_key_id or -1)
                or (line.inline_text if locale is None else "")
                or ""
            )
            item: dict[str, Any] = {
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
            count = len(json.dumps(item, ensure_ascii=False))
            if lines and size + count > self.settings.tool_result_chars - 1500:
                break
            lines.append(item)
            size += count
            self.known_nodes[node.id] = node.canonical_key
            if full_text:
                self.remember_text(node.id, item["text"])
        if quest.node_id == self.quest.node_id:
            self.coverage[offset] = len(lines)
            if len(rows) == len(lines):
                self.total_lines = offset + len(lines)
        return {
            "quest_node_id": quest.node_id,
            "quest_id": quest.game_quest_id,
            "name": values.get(quest.name_key_id or -1),
            "description": (values.get(quest.description_key_id or -1) or "")[:1000],
            "ordering": ORDERING,
            "offset": offset,
            "lines": lines,
            "next_offset": offset + len(lines) if len(rows) > len(lines) else None,
        }

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
            "node_id": node.id,
            "canonical_key": node.canonical_key,
            "type": kind,
            "text": returned,
            "locale": source_locale,
            "available_locales": available_locales,
            "localized_fields": localized if not (line or choice or speaker) else [],
            "truncated": source_truncated or len(text) > 6000,
            "meaning": "Imported source data; IDs and authored order are not inferred chronology.",
        }

    async def graph(self, node_id: int, offset: int = 0) -> dict[str, Any]:
        await self.remember_node(node_id)
        if not 0 <= offset <= 10000:
            raise ValueError("Invalid graph pagination")
        rows = list(
            (
                await self.session.execute(
                    select(Edge, RelationType.key)
                    .join(RelationType, RelationType.id == Edge.relation_type_id)
                    .where(
                        Edge.id.in_(source_edges(self.release_id)),
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
                    Node.id.in_(observed_nodes(self.release_id)),
                )
            )
        )
        self.known_nodes.update({node.id: node.canonical_key for node in nodes})
        return {
            "nodes": [{"node_id": node.id, "canonical_key": node.canonical_key} for node in nodes],
            "edges": [
                {
                    "id": edge.id,
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
        allowed = set(
            await self.session.scalars(
                select(NodeRevision.node_id).where(
                    NodeRevision.release_id == self.release_id,
                    NodeRevision.node_id.in_([row["id"] for row in rows]),
                )
            )
        )
        return {
            "results": [row for row in rows if row["id"] in allowed],
            "meaning": "Entity index uses the current import; results are filtered to observed snapshot IDs. Read source nodes before citing.",
        }

    async def memory(self, query: str = "") -> dict[str, Any]:
        if len(query) > 256:
            raise ValueError("Memory query too long")
        statement = select(AgentNote).where(AgentNote.release_id == self.release_id)
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
        args = call.arguments
        if call.name == "read_quest":
            return await self.read_quest(**args)
        if call.name == "read_node":
            return await self.read_node(**args)
        if call.name == "graph_neighbors":
            return await self.graph(**args)
        if call.name == "search_entities":
            return await self.search(**args)
        if call.name == "read_memory":
            return await self.memory(**args)
        if call.name == "save_note":
            return await self.note(NoteRequest.model_validate(args))
        raise ValueError("Unknown agent tool")

    async def validate_result(self, result: AnalysisResult) -> None:
        validate_citations(result, self.evidence)
        # Recheck exact source text before publication, including sources outside this quest.
        groups: list[ExplanationBlock | InferredLink | InferredEvent] = [
            *result.blocks,
            *result.links,
            *result.events,
        ]
        citations = [citation for group in groups for citation in group.citations]
        fresh_sources: dict[tuple[int, str | None], dict[str, Any]] = {}
        for citation in citations:
            identity = (citation.node_id, citation.locale)
            if identity not in fresh_sources:
                fresh_sources[identity] = await self.read_node(*identity)
            fresh = fresh_sources[identity]
            if citation.quote not in fresh["text"]:
                raise ValueError("Cited source changed or no longer contains the quoted passage")
            citation.locale = fresh["locale"]
        end = 0
        for offset, count in sorted(self.coverage.items()):
            if offset > end:
                break
            end = max(end, offset + count)
        if self.total_lines is None or end < self.total_lines:
            raise ValueError("Read every page of the target quest before publishing")
        ids = {node_id for block in result.blocks for node_id in block.related_node_ids}
        ids.update(value for link in result.links for value in (link.from_node_id, link.to_node_id))
        ids.update(value for event in result.events for value in event.participant_node_ids)
        if ids - self.known_nodes.keys():
            raise ValueError("Links may only refer to nodes discovered in this run")
        known = set(
            await self.session.scalars(
                select(NodeRevision.node_id).where(
                    NodeRevision.release_id == self.release_id, NodeRevision.node_id.in_(ids)
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
    locale = {
        "type": "string",
        "description": "Optional source language (en, zh-Hans, ja, or another imported locale). Omit for priority fallback.",
    }
    return [
        tool(
            "read_quest",
            "Read paginated exact-snapshot dialogue. Follow next_offset until null; alternatives are not a single playthrough.",
            {"quest_id": integer, "offset": integer, "limit": integer, "locale": locale},
        ),
        tool(
            "read_node",
            "Read a source node. available_locales lists translations; call again with locale to compare Chinese/Japanese. Cite returned text with its locale.",
            {"node_id": integer, "locale": locale},
            ["node_id"],
        ),
        tool(
            "graph_neighbors",
            "Read source graph neighbors; paginate using next_offset to inspect choices, scene links and conditions.",
            {"node_id": integer, "offset": integer},
            ["node_id"],
        ),
        tool(
            "search_entities",
            "Resolve characters, factions, quests and concepts. Read source nodes before citing.",
            {"query": {"type": "string"}, "locale": locale},
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

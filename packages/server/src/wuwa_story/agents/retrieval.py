"""Readable explanation retrieval; paid query vectors are separately guarded and cached."""

import json
from collections.abc import Awaitable
from typing import Any, cast
from urllib.parse import quote, urlencode

import httpx
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.agents.budget import BudgetExceeded, reserve, settle
from wuwa_story.agents.contracts import SOURCE_LOCALE_PRIORITY
from wuwa_story.agents.evidence import (
    EvidenceTools,
    hash_value,
    imported_snapshot_ids,
    quest_fingerprint,
    source_edges,
)
from wuwa_story.agents.providers import Provider, vector_values
from wuwa_story.agents.publication import document_type
from wuwa_story.agents.settings import AgentSettings
from wuwa_story.db.models.agents import ExplanationEmbedding
from wuwa_story.db.models.content import Document, DocumentHead, DocumentReference
from wuwa_story.db.models.core import (
    Character,
    DialogueLine,
    Item,
    Location,
    Quest,
    QuestAction,
    QuestNode,
)
from wuwa_story.db.models.graph import Edge, Node, NodeType
from wuwa_story.db.models.i18n import Locale, LocalizationValue
from wuwa_story.db.models.ops import GameRelease
from wuwa_story.db.models.search import EmbeddingModel


async def source_link(
    session: AsyncSession, node: Node, kind: str, release: GameRelease, language: Locale
) -> str:
    params = {"game_version": release.game_version, "locale": language.code}
    if kind == "dialogue_line":
        state_id = await session.scalar(
            select(QuestAction.quest_state_node_id)
            .join(DialogueLine, DialogueLine.action_node_id == QuestAction.node_id)
            .where(DialogueLine.node_id == node.id)
        )
        owners = select(Edge.from_node_id).where(
            Edge.to_node_id == state_id, Edge.id.in_(source_edges(release.id))
        )
        quest_id = await session.scalar(
            select(Quest.game_quest_id)
            .where(
                or_(
                    Quest.node_id.in_(owners),
                    Quest.game_quest_id.in_(
                        select(QuestNode.game_quest_id).where(QuestNode.node_id.in_(owners))
                    ),
                )
            )
            .order_by(Quest.game_quest_id)
            .limit(1)
        )
        if quest_id:
            return f"/quests/{quest_id}?" + urlencode({**params, "line": node.canonical_key})
    if kind == "quest":
        quest_id = await session.scalar(select(Quest.game_quest_id).where(Quest.node_id == node.id))
        if quest_id:
            return f"/quests/{quest_id}?" + urlencode(params)
    path = {"character": "characters", "location": "locations", "item": "items"}.get(kind, "nodes")
    return f"/{path}/{quote(node.canonical_key, safe='')}?" + urlencode(params)


async def scope(
    session: AsyncSession, game_version: str | None, locale: str
) -> tuple[GameRelease, Locale]:
    stmt = select(GameRelease).order_by(GameRelease.sequence.desc()).limit(1)
    if game_version:
        stmt = stmt.where(GameRelease.game_version == game_version)
    release = await session.scalar(stmt)
    language = await session.scalar(select(Locale).where(Locale.code == locale))
    if release is None or language is None:
        raise ValueError("Imported version or locale not found")
    return release, language


async def public_document(
    session: AsyncSession, document: Document, quest: Quest, release: GameRelease, locale: Locale
) -> dict[str, Any] | None:
    output_locale = await session.get(Locale, document.locale_id)
    assert output_locale is not None
    fingerprint_locale = (
        None if document.metadata_json.get("source_scope") == "all_locales" else output_locale.id
    )
    if document.source_hash != await quest_fingerprint(
        session, quest, release.id, fingerprint_locale
    ):
        return None
    corpus_changed = False
    if document.metadata_json.get("schema_version") in ("story-v4", "story-v5"):
        corpus_changed = document.metadata_json.get(
            "source_release_ids"
        ) != await imported_snapshot_ids(session)
        if corpus_changed and document.metadata_json.get("schema_version") == "story-v4":
            return None
        tools = EvidenceTools(
            session,
            run_id=document.processor_run_id or 0,
            quest=quest,
            release_id=release.id,
            locale=output_locale,
            settings=AgentSettings(_env_file=None),
            source_release_ids=document.metadata_json["source_release_ids"],
        )
        for receipt in document.metadata_json.get("source_receipts", []):
            try:
                context = await tools.context(receipt["snapshot_id"])
                current = await context.read_node(receipt["node_id"], receipt["locale"])
            except ValueError:
                return None
            if hash_value(current["text"]).hex() != receipt["text_hash"]:
                return None
    events = (
        await session.execute(
            select(DocumentReference.target_node_id, DocumentReference.label).where(
                DocumentReference.document_id == document.id,
                DocumentReference.reference_type == "event",
            )
        )
    ).all()
    ids = {c["node_id"] for block in document.body_ast for c in block.get("citations", [])}
    for hook in document.metadata_json.get("hooks", []):
        ids.update(c["node_id"] for c in hook["citations"])
    ids.update(value for block in document.body_ast for value in block.get("related_node_ids", []))
    for block in document.body_ast:
        ids.update(record["node_id"] for record in block.get("related_records", []))
        for assertion in block.get("assertions", []):
            ids.add(assertion["chronology_in_quest"]["anchor_node_id"])
            ids.update(c["node_id"] for c in assertion["citations"])
            for resolution in assertion["later_resolution"]:
                ids.add(resolution["revealed_in_node_id"])
                ids.update(c["node_id"] for c in resolution["citations"])
    ids.update(
        value
        for item in document.metadata_json.get("links", [])
        for value in (item["from_node_id"], item["to_node_id"])
    )
    nodes = []
    source_nodes = document.metadata_json.get("source_nodes", {})
    node_snapshots: dict[int, int] = {}
    for key in source_nodes:
        snapshot_id, node_id = map(int, key.split(":"))
        if node_id not in node_snapshots or snapshot_id == release.id:
            node_snapshots[node_id] = snapshot_id
    for block in document.body_ast:
        for citation in block.get("citations", []):
            node_snapshots.setdefault(
                citation["node_id"], citation.get("snapshot_id") or release.id
            )
    snapshot_releases = {
        value.id: value
        for value in await session.scalars(
            select(GameRelease).where(GameRelease.id.in_([release.id, *node_snapshots.values()]))
        )
    }
    for node, kind, label, inline_text in (
        await session.execute(
            select(Node, NodeType.key, LocalizationValue.content, DialogueLine.inline_text)
            .join(NodeType, NodeType.id == Node.type_id)
            .outerjoin(Character, Character.node_id == Node.id)
            .outerjoin(Item, Item.node_id == Node.id)
            .outerjoin(Location, Location.node_id == Node.id)
            .outerjoin(Quest, Quest.node_id == Node.id)
            .outerjoin(DialogueLine, DialogueLine.node_id == Node.id)
            .outerjoin(
                LocalizationValue,
                and_(
                    LocalizationValue.key_id
                    == func.coalesce(
                        Character.name_key_id,
                        Item.name_key_id,
                        Location.name_key_id,
                        Quest.name_key_id,
                        DialogueLine.localization_key_id,
                    ),
                    LocalizationValue.release_id
                    == case(node_snapshots, value=Node.id, else_=release.id)
                    if node_snapshots
                    else LocalizationValue.release_id == release.id,
                    LocalizationValue.locale_id == locale.id,
                ),
            )
            .where(Node.id.in_(ids))
        )
    ).all():
        nodes.append(
            {
                "id": node.id,
                "canonical_key": node.canonical_key,
                "kind": kind,
                "label": ("Passage: " + (label or inline_text or "")[:180])
                if kind == "dialogue_line" and (label or inline_text)
                else label
                or (
                    "Dialogue passage"
                    if kind == "dialogue_line"
                    else node.slug or node.canonical_key
                ),
                "href": await source_link(
                    session,
                    node,
                    kind,
                    snapshot_releases[node_snapshots.get(node.id, release.id)],
                    locale,
                ),
            }
        )

    async def with_citations(group: dict[str, Any]) -> dict[str, Any]:
        citations = []
        for citation in group.get("citations", []):
            source_locale = (
                await session.scalar(select(Locale).where(Locale.code == citation.get("locale")))
                if citation.get("locale")
                else output_locale
            )
            citation_node = await session.get(Node, citation["node_id"])
            citation_kind = (
                await session.scalar(
                    select(NodeType.key).where(NodeType.id == citation_node.type_id)
                )
                if citation_node
                else None
            )
            citation_release = await session.get(
                GameRelease, citation.get("snapshot_id") or release.id
            )
            citations.append(
                {
                    **citation,
                    "game_version": citation_release.game_version if citation_release else None,
                    "href": await source_link(
                        session, citation_node, citation_kind, citation_release, source_locale
                    )
                    if citation_node and citation_kind and source_locale and citation_release
                    else None,
                }
            )
        return {**group, "citations": citations}

    blocks = []
    for block in document.body_ast:
        enriched = await with_citations(block)
        assertions = []
        for assertion in block.get("assertions", []):
            item = await with_citations(assertion)
            item["later_resolution"] = [
                await with_citations(value) for value in assertion["later_resolution"]
            ]
            assertions.append(item)
        enriched["assertions"] = assertions
        blocks.append(enriched)
    return {
        "id": document.id,
        "quest_id": quest.game_quest_id,
        "game_version": release.game_version,
        "research_scope": "all_imported_snapshots"
        if document.metadata_json.get("schema_version") in ("story-v4", "story-v5")
        else "target_snapshot",
        "locale": output_locale.code,
        "requested_locale": locale.code,
        "title": document.title,
        "assessment": document.metadata_json.get("assessment"),
        "narrative_function": document.metadata_json.get("narrative_function"),
        "knowledge_boundary": document.metadata_json.get("knowledge_boundary"),
        "hooks": [await with_citations(hook) for hook in document.metadata_json.get("hooks", [])],
        "corpus_changed": corpus_changed,
        "loaded_versions": list(
            await session.scalars(
                select(GameRelease.game_version)
                .where(
                    GameRelease.id.in_(
                        document.metadata_json.get("source_release_ids", [release.id])
                    )
                )
                .order_by(GameRelease.sequence)
            )
        ),
        "blocks": blocks,
        "generated": True,
        "unresolved_questions": document.metadata_json.get("unresolved_questions", []),
        "events": [{"node_id": node_id, "title": title} for node_id, title in events],
        "links": [await with_citations(item) for item in document.metadata_json.get("links", [])],
        "nodes": nodes,
    }


async def get_explanation(
    session: AsyncSession, quest_id: int, game_version: str | None, locale: str
) -> dict[str, Any]:
    release, language = await scope(session, game_version, locale)
    quest = await session.scalar(select(Quest).where(Quest.game_quest_id == quest_id))
    if quest is None:
        raise ValueError("Quest not found")
    documents = await session.scalars(
        select(Document)
        .join(DocumentHead, DocumentHead.document_id == Document.id)
        .where(
            Document.node_id == quest.node_id,
            Document.document_type == document_type(release.id),
        )
        .order_by(language_priority(language.code))
    )
    payload = None
    for document in documents:
        payload = await public_document(session, document, quest, release, language)
        if payload:
            break
    return {
        "status": "available" if payload else "pending",
        "explanation": payload,
        "game_version": release.game_version,
    }


def language_priority(requested: str) -> Any:
    codes = list(dict.fromkeys((requested, *SOURCE_LOCALE_PRIORITY)))
    return case(
        {code: index for index, code in enumerate(codes)},
        value=select(Locale.code).where(Locale.id == Document.locale_id).scalar_subquery(),
        else_=len(codes),
    )


async def query_vector(
    session: AsyncSession, query: str, settings: AgentSettings, redis: Redis | None, actor: str
) -> list[float] | None:
    if not settings.public_query_embeddings or not settings.embedding_model or redis is None:
        return None
    identity = hash_value(
        {
            "q": query,
            "provider": settings.provider,
            "url": settings.base_url,
            "model": settings.embedding_model,
            "dimensions": settings.embedding_dimensions,
        }
    ).hex()
    key = "agent:query-vector:" + identity
    try:
        cached = await redis.get(key)
        if cached:
            return vector_values(json.loads(cached), settings.embedding_dimensions)
        limiter = "agent:query-limit:" + hash_value(actor).hex()
        count = await cast(
            Awaitable[Any],
            redis.eval(
                "local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],3600) end; return n",
                1,
                limiter,
            ),
        )
        if int(count) > settings.query_requests_per_hour:
            return None
        locked = await redis.set(
            key + ":lock", "1", nx=True, ex=int(settings.http_timeout_seconds) + 30
        )
        if not locked:
            return None
    except (RedisError, ValueError, TypeError):
        return None  # Fail closed to paid work when cache/rate limiter is unavailable.
    try:
        call = await reserve(
            session,
            settings,
            run_id=None,
            step=0,
            input_bound=len(query.encode()) + 1024,
            output_bound=0,
            kind="embedding",
        )
        await session.commit()
    except (BudgetExceeded, ValueError):
        await session.rollback()
        return None
    try:
        async with httpx.AsyncClient() as client:
            vectors, raw = await Provider(settings, client).embed([query])
        tokens = raw.get("usage", {}).get("prompt_tokens", len(query.encode()) + 1024)
        if type(tokens) is not int:
            raise ValueError("Invalid usage")
        bounded = await settle(session, call.id, settings, tokens, 0, raw)
        await session.commit()
        if not bounded:
            return None
        try:
            await redis.set(key, json.dumps(vectors[0]), ex=86400)
        except RedisError:
            pass
        return vectors[0]
    except (ValueError, KeyError, TypeError, IndexError):
        call.status = "uncertain"
        await session.commit()
        return None


async def search_explanations(
    session: AsyncSession,
    query: str,
    game_version: str | None,
    locale: str,
    limit: int,
    vector: list[float] | None = None,
    settings: AgentSettings | None = None,
) -> dict[str, Any]:
    release, language = await scope(session, game_version, locale)
    # Restrict to current published heads; older generated revisions are never search results.
    base = (
        select(Document, Quest)
        .join(DocumentHead, DocumentHead.document_id == Document.id)
        .join(Quest, Quest.node_id == Document.node_id)
        .where(Document.document_type == document_type(release.id))
    )
    score = func.ts_rank_cd(
        func.to_tsvector("simple", Document.plain_text), func.plainto_tsquery("simple", query)
    )
    mode = "text"
    if vector is not None and settings is not None:
        model_id = await session.scalar(
            select(EmbeddingModel.id)
            .where(
                EmbeddingModel.provider == settings.provider,
                EmbeddingModel.model_name == settings.embedding_model,
                EmbeddingModel.dimensions == settings.embedding_dimensions,
                EmbeddingModel.config["base_url"].astext == settings.base_url,
            )
            .limit(1)
        )
        if model_id:
            distance = ExplanationEmbedding.embedding.cosine_distance(vector)
            statement = (
                base.add_columns(ExplanationEmbedding.ordinal, (1 - distance).label("score"))
                .join(ExplanationEmbedding, ExplanationEmbedding.document_id == Document.id)
                .where(ExplanationEmbedding.model_id == model_id)
                .order_by(distance, language_priority(language.code))
                .limit(limit * 2)
            )
            mode = "vector"
    if mode == "text":
        # pg_trgm supplies a useful fallback for short questions and inflected names.
        fuzzy = func.similarity(Document.plain_text, query)
        statement = (
            base.add_columns(score.label("score"), fuzzy.label("fuzzy"))
            .where((score > 0) | (fuzzy > 0.04))
            .order_by((score + fuzzy).desc(), language_priority(language.code))
            .limit(limit * 2)
        )
    rows = (await session.execute(statement)).all()
    results = []
    seen = set()
    for row in rows:
        document, quest = cast(Document, row[0]), cast(Quest, row[1])
        if quest.node_id in seen:
            continue
        ordinal_or_score, score_or_fuzzy = cast(float, row[2]), cast(float, row[3])
        payload = await public_document(session, document, quest, release, language)
        if payload is None:
            continue
        if mode == "vector":
            blocks = [payload["blocks"][int(ordinal_or_score)]]
            rank = float(score_or_fuzzy)
        else:
            words = query.casefold().split()
            blocks = sorted(
                payload["blocks"],
                key=lambda block: sum(word in block["text"].casefold() for word in words),
                reverse=True,
            )[:2]
            rank = float(ordinal_or_score) + float(score_or_fuzzy)
        results.append({**payload, "blocks": blocks, "score": rank})
        seen.add(quest.node_id)
        if len(results) >= limit:
            break
    return {
        "results": results,
        "mode": mode,
        "game_version": release.game_version,
        "locale": language.code,
    }

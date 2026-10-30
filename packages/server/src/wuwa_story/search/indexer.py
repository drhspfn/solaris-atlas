"""Build deterministic lexical search documents from imported typed text references."""

import hashlib
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import func, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.db.models import (
    Character,
    DialogueLine,
    Item,
    Location,
    Narration,
    PhoneMessage,
    PlayerChoice,
    Quest,
    Speaker,
)
from wuwa_story.db.models.graph import NodeRevision
from wuwa_story.db.models.i18n import LocalizationContent, LocalizationValue
from wuwa_story.db.models.ops import GameRelease, ImportRun
from wuwa_story.db.models.search import SearchDocument
from wuwa_story.ingestion.releases import release_order

TEXT_SOURCES = (
    (DialogueLine, "localization_key_id", "dialogue"),
    (Narration, "localization_key_id", "narration"),
    (PlayerChoice, "localization_key_id", "choice"),
    (PhoneMessage, "localization_key_id", "phone_message"),
    (Quest, "name_key_id", "quest"),
    (Quest, "description_key_id", "quest_description"),
    (Character, "name_key_id", "character"),
    (Character, "nickname_key_id", "character_nickname"),
    (Speaker, "name_key_id", "speaker"),
    (Item, "name_key_id", "item"),
    (Item, "description_key_id", "item_description"),
    (Location, "name_key_id", "area"),
)


def _document(node_id, locale_id, content, category):
    body = str(content)
    title = body[:512]
    return {
        "target_node_id": node_id,
        "category": category,
        "locale_id": locale_id,
        "title": title,
        "aliases": [],
        "body": body,
        "search_vector": func.to_tsvector("simple", f"{title} {body}"),
        "content_hash": hashlib.sha256(f"{category}\0{locale_id}\0{title}\0{body}".encode()).digest(),
    }


async def _source_rows(
    session: AsyncSession,
    model: type[Any],
    key_field: str,
    category: str,
    release_id: int,
    batch_size: int,
) -> AsyncIterator[dict[str, Any]]:
    key_id = getattr(model, key_field)
    statement = (
        select(model.node_id, LocalizationValue.locale_id, LocalizationContent.content)
        .join(LocalizationValue, LocalizationValue.key_id == key_id)
        .join(LocalizationContent, LocalizationContent.id == LocalizationValue.content_id)
        .where(
            LocalizationValue.release_id == release_id,
            LocalizationValue.status == "resolved_nonempty",
            LocalizationContent.content != "",
        )
    )
    last: tuple[int, int] | None = None
    while True:
        page = statement
        if last is not None:
            page = page.where(tuple_(model.node_id, LocalizationValue.locale_id) > last)
        rows = (
            await session.execute(
                page.order_by(model.node_id, LocalizationValue.locale_id).limit(batch_size)
            )
        ).all()
        if not rows:
            return
        for node_id, locale_id, content in rows:
            yield _document(node_id, locale_id, content, category)
        last = (rows[-1][0], rows[-1][1])


async def _historical_source_rows(session, model, key_field, category, release_id, releases, batch_size):
    """Index nodes in this snapshot with the newest available text per key and locale."""
    key_id = getattr(model, key_field)
    rank = {release.id: index for index, release in enumerate(sorted(releases, key=release_order))}
    last = 0
    while True:
        rows = list(await session.execute(
            select(model.node_id, key_id)
            .join(NodeRevision, NodeRevision.node_id == model.node_id)
            .where(NodeRevision.release_id == release_id, model.node_id > last, key_id.is_not(None))
            .distinct().order_by(model.node_id).limit(batch_size)
        ))
        if not rows:
            return
        values = await session.execute(
            select(LocalizationValue.key_id, LocalizationValue.locale_id,
                   LocalizationValue.release_id, LocalizationContent.content)
            .join(LocalizationContent, LocalizationContent.id == LocalizationValue.content_id)
            .where(LocalizationValue.key_id.in_({key for _, key in rows}),
                   LocalizationValue.release_id.in_(rank),
                   LocalizationValue.status == "resolved_nonempty", LocalizationContent.content != "")
        )
        best = {}
        for key, locale, identity, content in values:
            pair = (key, locale)
            if pair not in best or rank[identity] > best[pair][0]:
                best[pair] = (rank[identity], content)
        by_key = {}
        for (key, locale), (_, content) in best.items():
            by_key.setdefault(key, []).append((locale, content))
        for node_id, key in rows:
            for locale, content in by_key.get(key, []):
                yield _document(node_id, locale, content, category)
        last = rows[-1][0]


async def build_lexical_index(
    session: AsyncSession,
    game_version: str,
    batch_size: int = 1000,
    categories: set[str] | None = None,
    *,
    release_id: int | None = None,
) -> int:
    """Upsert all searchable localized text for one imported release."""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    statement = select(GameRelease).where(GameRelease.game_version == game_version)
    if release_id is not None:
        statement = statement.where(GameRelease.id == release_id)
    release = await session.scalar(statement.order_by(GameRelease.sequence.desc()).limit(1))
    if release is None:
        raise ValueError(f"Game release {game_version!r} is not imported")

    all_releases = list(await session.scalars(select(GameRelease)))
    newer_release_ids = [other.id for other in all_releases if release_order(other) > release_order(release)]
    releases = list(await session.scalars(select(GameRelease).where(
        (GameRelease.id == release.id) | GameRelease.id.in_(
            select(ImportRun.release_id).where(ImportRun.status == "succeeded")
        )
    )))
    historical = bool(newer_release_ids)
    indexed = 0
    for model, key_field, category in TEXT_SOURCES:
        if categories is not None and category not in categories:
            continue
        batch: list[dict[str, Any]] = []
        rows = (_historical_source_rows(session, model, key_field, category, release.id, releases, batch_size)
                if historical else _source_rows(session, model, key_field, category, release.id, batch_size))
        async for row in rows:
            batch.append(row)
            if len(batch) >= batch_size:
                indexed += await _upsert_batch(session, batch, newer_release_ids)
                batch.clear()
        if batch:
            indexed += await _upsert_batch(session, batch, newer_release_ids)
    return indexed


async def _upsert_batch(session: AsyncSession, batch: list[dict[str, Any]], newer_release_ids=None) -> int:
    statement = insert(SearchDocument).values(batch)
    protected = set(await session.scalars(select(NodeRevision.node_id).where(
        NodeRevision.node_id.in_([value["target_node_id"] for value in batch]),
        NodeRevision.release_id.in_(newer_release_ids),
    ))) if newer_release_ids else set()
    result = await session.execute(
        statement.on_conflict_do_update(
            constraint="uq_search_document",
            set_={
                "title": statement.excluded.title,
                "aliases": statement.excluded.aliases,
                "body": statement.excluded.body,
                "search_vector": statement.excluded.search_vector,
                "content_hash": statement.excluded.content_hash,
                "updated_at": func.now(),
            },
            where=statement.excluded.target_node_id.not_in(protected) if protected else None,
        )
    )
    await session.commit()
    return max(result.rowcount or 0, 0)

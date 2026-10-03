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
from wuwa_story.db.models.i18n import LocalizationContent, LocalizationValue
from wuwa_story.db.models.ops import GameRelease
from wuwa_story.db.models.search import SearchDocument

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
            body = str(content)
            title = body[:512]
            content_hash = hashlib.sha256(
                f"{category}\0{locale_id}\0{title}\0{body}".encode()
            ).digest()
            yield {
                "target_node_id": node_id,
                "category": category,
                "locale_id": locale_id,
                "title": title,
                "aliases": [],
                "body": body,
                "search_vector": func.to_tsvector("simple", f"{title} {body}"),
                "content_hash": content_hash,
            }
        last = (rows[-1][0], rows[-1][1])


async def build_lexical_index(
    session: AsyncSession,
    game_version: str,
    batch_size: int = 1000,
    categories: set[str] | None = None,
) -> int:
    """Upsert all searchable localized text for one imported release."""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    release = await session.scalar(
        select(GameRelease).where(GameRelease.game_version == game_version)
    )
    if release is None:
        raise ValueError(f"Game release {game_version!r} is not imported")

    indexed = 0
    for model, key_field, category in TEXT_SOURCES:
        if categories is not None and category not in categories:
            continue
        batch: list[dict[str, Any]] = []
        async for row in _source_rows(
            session, model, key_field, category, release.id, batch_size
        ):
            batch.append(row)
            if len(batch) >= batch_size:
                indexed += await _upsert_batch(session, batch)
                batch.clear()
        if batch:
            indexed += await _upsert_batch(session, batch)
    return indexed


async def _upsert_batch(session: AsyncSession, batch: list[dict[str, Any]]) -> int:
    statement = insert(SearchDocument).values(batch)
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
        )
    )
    await session.commit()
    return max(result.rowcount or 0, 0)

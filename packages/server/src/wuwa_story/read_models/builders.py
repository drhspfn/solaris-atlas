"""Background-only projection entry points; no writes or transaction ownership.

The refresh worker supplies generation and atomically publishes entries/relations.
Raw source bodies are stored once in source tables, never copied into projections.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.db.models.i18n import Locale
from wuwa_story.db.repositories.read_models import ENTITY_READ_MODELS
from wuwa_story.read_models import source_payloads
from wuwa_story.read_models import source_sections as sections
from wuwa_story.read_models.context import source_build
from wuwa_story.read_models.source_relations import build_relations as build_relations


async def build_entity(
    session: AsyncSession,
    entity_kind: str,
    entity_id: int,
    locale_id: int | str,
    snapshot_key: str = "current",
) -> dict[str, Any]:
    """Build one card and full page for refresh-time publication, without writes.

    ``entity_id`` is graph.node.id except for game_event/game_notice, whose own
    table IDs are used. ``locale_id`` also accepts a locale code. Event versions
    match only explicitly dated runs; unversioned runs stay separate. Notices
    and source page APIs without version selection reject historical snapshots.
    The caller stamps generation; it must not publish a failed/partial build.
    """
    kind = {"event": "game_event", "notice": "game_notice"}.get(entity_kind, entity_kind)
    if kind not in ENTITY_READ_MODELS:
        raise ValueError(f"Unknown read-model kind: {entity_kind}")
    if not isinstance(snapshot_key, str) or not snapshot_key or len(snapshot_key) > 64:
        raise ValueError("A snapshot key must be current or an explicit version")
    locale = (
        await session.get(Locale, locale_id)
        if isinstance(locale_id, int)
        else await session.scalar(select(Locale).where(Locale.code == locale_id))
    )
    if locale is None:
        raise ValueError(f"Unknown locale: {locale_id}")
    if snapshot_key != "current" and kind in {"game_notice", "location", "speaker"}:
        raise ValueError(f"{kind} has no public historical snapshot selector")
    with source_build(), sections._request_caches(session):
        return await source_payloads._build_entity(session, kind, entity_id, locale, snapshot_key)

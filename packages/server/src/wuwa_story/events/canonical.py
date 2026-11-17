"""Explicit canonical bindings retain source identities, periods and snapshots."""

from sqlalchemy import select, text

from wuwa_story.db.models.game_events import GameEvent


async def bind_event_alias(session, alias_id: int, canonical_id: int, evidence: dict) -> None:
    """Bind existing records; callers must supply their source or manual evidence."""
    if alias_id == canonical_id or not isinstance(evidence, dict):
        raise ValueError("A distinct alias and documented evidence are required")
    if evidence.get("basis") not in {"game_source", "manual"} or not evidence.get("reference"):
        raise ValueError("A game source or manual evidence reference is required")
    # The importer uses the same lock, so alias bindings cannot race catalog writes.
    await session.execute(text("SELECT pg_advisory_xact_lock(705, 1)"))
    rows = list(await session.scalars(select(GameEvent).where(
        GameEvent.id.in_([alias_id, canonical_id])).order_by(GameEvent.id).with_for_update()))
    by_id = {row.id: row for row in rows}
    alias, canonical = by_id.get(alias_id), by_id.get(canonical_id)
    if not alias or not canonical or canonical.canonical_event_id is not None:
        raise ValueError("Both events must exist and the canonical event must be a root")
    if alias.canonical_event_id is not None:
        if alias.canonical_event_id == canonical_id and alias.canonical_evidence == evidence:
            return
        raise ValueError("An existing binding cannot be changed implicitly")
    children = await session.scalar(select(GameEvent.id).where(GameEvent.canonical_event_id == alias_id).limit(1))
    if children is not None:
        raise ValueError("An event with aliases cannot itself become an alias")
    alias.canonical_event_id = canonical_id
    alias.canonical_evidence = dict(evidence)
    await session.flush()


async def event_family_ids(session, event_id: int) -> tuple[int, list[int]]:
    """Resolve either an original ID or its root without consulting schedule dates."""
    event = await session.get(GameEvent, event_id)
    if event is None:
        raise ValueError("Event does not exist")
    root = event.canonical_event_id or event.id
    aliases = list(await session.scalars(select(GameEvent.id).where(
        GameEvent.canonical_event_id == root).order_by(GameEvent.id)))
    return root, [root, *aliases]

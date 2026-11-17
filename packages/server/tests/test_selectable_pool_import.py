from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.ingestion.client_events import (
    _bind_selectable_pools,
    _same_authored_capture,
    _selectable_pool,
    store_client_events,
)
from wuwa_story.ingestion.game_events import CLIENT_SOURCE


def fixture(kind=13, size=4):
    ids = [31 + index * 11 for index in range(size)]
    events = [SimpleNamespace(id=100 + index, source_id=f"gacha:{identifier}",
                              canonical_event_id=None, canonical_evidence=None)
              for index, identifier in enumerate(ids)]
    captures = [{"table": "GachaViewInfo", "gacha_type": kind, "source_checksum": "a" * 64,
                 "compiler_checksum": "b" * 64, "gacha_pool_proof": {
                     "gacha_id": 100096, "view_id": identifier, "members": ids,
                     "pool_checksum": f"{index:064x}", "gacha_checksum": "c" * 64,
                     "group_checksum": "d" * 64}}
                for index, identifier in enumerate(ids)]
    members = [SimpleNamespace(event_id=event.id, client_capture=capture)
               for event, capture in zip(events, captures, strict=True)]
    return events, captures, members


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,size", [(5, 10), (6, 5), (7, 5), (9, 8), (13, 4), (8, 5), (10, 8), (14, 4)])
async def test_complete_exact_pools_bind_all_options_with_stable_existing_root(kind, size):
    events, captures, members = fixture(kind, size)
    session = SimpleNamespace(scalars=AsyncMock(return_value=members))
    await _bind_selectable_pools(session, events, captures, events)
    assert events[0].canonical_event_id is None
    assert all(row.canonical_event_id == events[0].id for row in events[1:])
    assert all(row.canonical_evidence["reference"] == "GachaPool:100096" for row in events[1:])
    assert all(row.canonical_evidence["gacha_type"] == kind for row in events[1:])
    await _bind_selectable_pools(session, events, captures, events)
    assert all(row.canonical_event_id == events[0].id for row in events[1:])


@pytest.mark.asyncio
async def test_incomplete_or_conflicting_pool_never_binds():
    events, captures, members = fixture()
    session = SimpleNamespace(scalars=AsyncMock(return_value=members))
    await _bind_selectable_pools(session, events[:-1], captures[:-1], events)
    session.scalars.assert_not_awaited()
    members[0].client_capture = {**captures[0], "gacha_pool_proof": None}
    await _bind_selectable_pools(session, events, captures, events)
    assert all(row.canonical_event_id is None for row in events)
    members[0].client_capture = captures[0]
    events[1].canonical_event_id = events[0].id
    events[1].canonical_evidence = {"basis": "manual", "reference": "explicit"}
    await _bind_selectable_pools(session, events, captures, events)
    assert all(row.canonical_event_id is None for row in events[2:])


def test_pool_proof_is_derived_but_conflicts_and_other_gacha_types_are_rejected():
    events, captures, _ = fixture()
    original = captures[0]
    corrected = {**original, "gacha_pool_proof": {**original["gacha_pool_proof"], "group_checksum": "e" * 64}}
    assert _same_authored_capture(original, corrected)
    assert _selectable_pool(events[0].source_id, {**original, "gacha_type": 2}) is None
    assert _selectable_pool("gacha:9999", original) is None
    assert _selectable_pool(events[0].source_id, {**original, "gacha_pool_proof": {
        **original["gacha_pool_proof"], "members": [31, 31]}}) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_index", [0, 1])
async def test_changed_pool_cannot_silently_invalidate_existing_binding(changed_index):
    events, captures, members = fixture()
    bind_session = SimpleNamespace(scalars=AsyncMock(return_value=members))
    await _bind_selectable_pools(bind_session, events, captures, events)
    for event in events:
        event.source = CLIENT_SOURCE
    changed = {**captures[changed_index], "locales": {"en": "Title"},
               "gacha_pool_proof": {**captures[changed_index]["gacha_pool_proof"], "gacha_id": 999}}
    session = SimpleNamespace(execute=AsyncMock(), get=AsyncMock(return_value=None),
                              scalars=AsyncMock(return_value=events))
    with pytest.raises(ValueError, match="existing selectable pool binding"):
        await store_client_events(session, {events[changed_index].source_id: changed}, "3.8", "download")


@pytest.mark.asyncio
async def test_root_with_unrelated_children_is_not_reused():
    events, captures, members = fixture()
    other = SimpleNamespace(id=999, canonical_event_id=events[0].id)
    session = SimpleNamespace(scalars=AsyncMock(return_value=members))
    await _bind_selectable_pools(session, events, captures, [*events, other])
    assert all(event.canonical_event_id is None for event in events)

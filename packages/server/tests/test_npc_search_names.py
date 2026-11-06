from unittest.mock import AsyncMock

import pytest

from wuwa_story.search.indexer import _npc_display_rows, npc_display_keys


def instance(key, override):
    return {"name": {"key": key}, "components": {
        "BaseInfoComponent": {"TidName": key} if override else {}}}


@pytest.mark.asyncio
async def test_search_uses_unique_localized_instance_override_with_variants():
    iris = {"instances": [instance("iris", False), instance("empty", True),
                          instance("iris_alt", True)]}
    zahira = {"instances": [instance("iris", False), instance("zahira", True)]}
    assert npc_display_keys(zahira) == ({"zahira"}, {"iris", "zahira"})
    session = type("Session", (), {})()
    session.execute = AsyncMock(side_effect=[
        [(1, {"npc": iris}), (2, {"npc": zahira})],
        [("iris", 1, "I.R.I.S.", "resolved_nonempty"),
         ("iris_alt", 1, "I.R.I.S", "resolved_nonempty"),
         ("zahira", 1, "Zahira", "resolved_nonempty"), ("empty", 1, "", "resolved_empty")],
        [],
    ])
    documents = [row async for row in _npc_display_rows(session, 3, 100)]
    assert [(row["target_node_id"], row["title"], row["aliases"]) for row in documents] == [
        (1, "I.R.I.S", ["I.R.I.S."]), (2, "Zahira", ["I.R.I.S."])]


@pytest.mark.asyncio
async def test_multiple_localized_override_keys_do_not_choose_a_person():
    npc = {"instances": [instance("one", True), instance("two", True)]}
    session = type("Session", (), {})()
    session.execute = AsyncMock(side_effect=[[(1, {"npc": npc})],
        [("one", 1, "One", "resolved_nonempty"), ("two", 1, "Two", "resolved_nonempty")], []])
    assert [row async for row in _npc_display_rows(session, 3, 100)] == []


@pytest.mark.asyncio
async def test_unresolved_override_is_not_treated_as_empty():
    npc = {"instances": [instance("one", True), instance("unknown", True)]}
    session = type("Session", (), {})()
    session.execute = AsyncMock(side_effect=[[(1, {"npc": npc})],
        [("one", 1, "One", "resolved_nonempty")], []])
    assert [row async for row in _npc_display_rows(session, 3, 100)] == []

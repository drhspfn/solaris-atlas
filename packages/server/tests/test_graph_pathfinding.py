"""Source graph path search keeps authored edge direction and bounded outcomes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.graph.pathfinding import find_source_path


def edge(identifier: int, source: int, target: int) -> SimpleNamespace:
    return SimpleNamespace(id=identifier, from_node_id=source, to_node_id=target)


@pytest.mark.asyncio
async def test_path_can_traverse_a_source_edge_in_reverse_without_reversing_its_meaning():
    first = edge(1, 2, 1)
    second = edge(2, 2, 3)
    session = SimpleNamespace(execute=AsyncMock(side_effect=[
        SimpleNamespace(all=lambda: [(first, "quest_tree_contains_quest")]),
        SimpleNamespace(all=lambda: [(first, "quest_tree_contains_quest"),
                                     (second, "quest_tree_next")]),
    ]))
    path, truncated = await find_source_path(
        session, 1, 3, release_id=10,
        relation_keys=frozenset({"quest_tree_contains_quest", "quest_tree_next"}),
    )
    assert not truncated
    assert [(row[0].id, row[2], row[3]) for row in path] == [(1, 1, 2), (2, 2, 3)]
    assert first.from_node_id == 2  # first hop was reverse, source edge stays authored


@pytest.mark.asyncio
async def test_path_reports_bound_instead_of_claiming_nodes_are_unconnected():
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [
        (edge(1, 1, 2), "requires_quest"),
        (edge(2, 1, 3), "requires_quest"),
    ])))
    path, truncated = await find_source_path(
        session, 1, 9, release_id=None,
        relation_keys=frozenset({"requires_quest"}), max_nodes=2,
    )
    assert path is None
    assert truncated

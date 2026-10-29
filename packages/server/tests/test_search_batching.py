"""Search cards should reuse the existing batched label loader."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.api.routes import search as search_route
from wuwa_story.db.repositories import story_browsing as repository


@pytest.mark.parametrize("count,locale", [(0, "en"), (1, "en"), (50, "en"), (60, "ru"), (100, "en")])
async def test_entity_search_batches_labels_and_preserves_cards(monkeypatch, count, locale):
    nodes = {
        i: SimpleNamespace(
            id=i, canonical_key=f"item:{i}", slug=f"item-{i}", status="active"
        )
        for i in range(1, count + 1)
    }
    matches = [
        {
            "id": i,
            "canonical_key": f"item:{i}",
            "slug": f"item-{i}",
            "node_type": "item",
            "alias": f"matching description {i}",
            "score": 0.5,
        }
        for i in nodes
    ]
    session = SimpleNamespace(
        info={}, scalar=AsyncMock(return_value=None), scalars=AsyncMock(return_value=["item"])
    )
    monkeypatch.setattr(search_route, "lexical_search", AsyncMock(return_value=matches))
    monkeypatch.setattr(search_route, "entity_image_urls", AsyncMock(return_value={1: "image"}))
    monkeypatch.setattr(repository, "locale_id", AsyncMock(return_value=7))
    monkeypatch.setattr(repository, "fetch_releases", AsyncMock(return_value=[]))
    label_rows = AsyncMock(
        side_effect=lambda _session, ids, _locale: (
            [(nodes[i], "item", None, f"canonical {i}") for i in ids],
            {i: f"localized {i}" for i in ids if i != 2},
        )
    )
    monkeypatch.setattr(repository, "node_label_rows", label_rows)

    response = await search_route.search(
        q="core", category=["item"], locale=locale, locale_id=7, limit=max(60, count), session=session
    )

    assert label_rows.await_count == int(count > 0)
    if count:
        assert label_rows.await_args.args[1] == list(nodes)
        repository.locale_id.assert_awaited_once_with(session, locale)
        first = response["results"][0]
        assert (first["alias"], first["title"], first["slug"], first["href"], first["image_url"]) == (
            "matching description 1", "localized 1", "item-1", "/items/item-1", "image"
        )
    if count > 1:
        assert response["results"][1]["title"] == "canonical 2"
    assert response["has_more"] is (count >= 60)

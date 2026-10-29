"""Category validation should query types only when a filter is supplied."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from wuwa_story.api.routes import search as search_route


@pytest.mark.parametrize("categories", [None, []])
async def test_unfiltered_search_skips_type_lookup(monkeypatch, categories):
    session = SimpleNamespace(info={}, scalar=AsyncMock(return_value=None), scalars=AsyncMock())
    lexical = AsyncMock(return_value=[])
    monkeypatch.setattr(search_route, "lexical_search", lexical)
    monkeypatch.setattr(search_route, "entity_image_urls", AsyncMock(return_value={}))

    response = await search_route.search(
        q="core", category=categories, locale_id=7, session=session
    )

    session.scalars.assert_not_awaited()
    assert lexical.await_args.kwargs["categories"] == []
    assert response["filters"]["categories"] == []
    assert response["results"] == []


@pytest.mark.parametrize("categories", [["item"], ["location"], ["item", "location"]])
async def test_filtered_search_validates_expanded_categories(monkeypatch, categories):
    session = SimpleNamespace(
        info={}, scalar=AsyncMock(return_value=None),
        scalars=AsyncMock(return_value=["item", "area", "location"]),
    )
    lexical = AsyncMock(return_value=[])
    monkeypatch.setattr(search_route, "lexical_search", lexical)
    monkeypatch.setattr(search_route, "entity_image_urls", AsyncMock(return_value={}))

    response = await search_route.search(
        q="core", category=categories, locale_id=7, session=session
    )

    session.scalars.assert_awaited_once()
    expected = [kind for category in categories for kind in (
        ["area", "location"] if category == "location" else [category]
    )]
    assert lexical.await_args.kwargs["categories"] == expected
    assert response["filters"]["categories"] == categories


async def test_unknown_category_remains_an_error(monkeypatch):
    session = SimpleNamespace(
        info={}, scalar=AsyncMock(return_value=None), scalars=AsyncMock(return_value=["item"])
    )
    lexical = AsyncMock()
    monkeypatch.setattr(search_route, "lexical_search", lexical)

    with pytest.raises(HTTPException) as error:
        await search_route.search(q="core", category=["invalid"], locale_id=7, session=session)

    assert error.value.status_code == 422
    assert error.value.detail == "Unknown categories: ['invalid']"
    session.scalars.assert_awaited_once()
    lexical.assert_not_awaited()

"""Explicit rollout selection precedes any source or projected database read."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError

import test_projection_routes
from wuwa_story.api import read_projections as reader
from wuwa_story.config.settings import Settings
from wuwa_story.db.models.read_refresh import READ_PROJECTION_KINDS
from wuwa_story.db.session import get_session
from wuwa_story.read_models import context

app = test_projection_routes.app
ROUTES = test_projection_routes.ROUTES

FIRST_KINDS = ("character", "item", "location", "story_map")


def settings(kinds=FIRST_KINDS, snapshots=("current",), enabled=True, locales=()):
    return SimpleNamespace(
        read_projections_enabled=enabled,
        read_projection_kinds=kinds,
        read_projection_snapshots=snapshots,
        read_projection_locales=locales,
    )


def test_csv_environment_selectors_are_parsed_validated_and_empty_is_unrestricted(monkeypatch):
    monkeypatch.setenv("READ_PROJECTION_KINDS", " character, item,location,story_map,character ")
    monkeypatch.setenv("READ_PROJECTION_SNAPSHOTS", "current,3.3.0,current")
    monkeypatch.setenv("READ_PROJECTION_LOCALES", " en,ja,en ")
    configured = Settings(_env_file=None)
    assert configured.read_projection_kinds == FIRST_KINDS
    assert configured.read_projection_snapshots == ("current", "3.3.0")
    assert configured.read_projection_locales == ("en", "ja")
    assert Settings(_env_file=None, read_projection_locales="").read_projection_locales == ()
    assert (
        Settings(
            _env_file=None, read_projection_kinds="", read_projection_snapshots=""
        ).read_projection_kinds
        == ()
    )
    for field, value in (
        ("read_projection_kinds", "characters"),
        ("read_projection_snapshots", "latest"),
        ("read_projection_snapshots", "3.3"),
    ):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, **{field: value})


@pytest.mark.parametrize(
    "kind,snapshot,expected",
    [
        ("character", None, True),
        ("item", "current", True),
        ("location", None, True),
        ("story_map", None, True),
        ("quest", None, False),
        ("character", "3.3.0", False),
        (("character", "item"), None, True),
        (("character", "quest"), None, False),
        (None, None, False),
    ],
)
def test_explicit_selection_requires_every_kind_and_exact_snapshot(kind, snapshot, expected):
    assert context.should_route_to_projection(kind, snapshot, settings=settings()) is expected
    assert context.should_route_to_projection(
        kind, snapshot, settings=settings(kinds=(), snapshots=())
    )
    assert not context.should_route_to_projection(kind, snapshot, settings=settings(enabled=False))
    with context.source_build():
        assert not context.should_route_to_projection(kind, snapshot, settings=settings())


def test_search_categories_aliases_dependencies_and_counts_are_atomic_selections():
    chosen = settings()
    assert reader.should_route_search_to_projection(["character", "item", "area"], settings=chosen)
    assert not reader.should_route_search_to_projection(["character", "quest"], settings=chosen)
    assert not reader.should_route_search_to_projection(settings=chosen)
    assert not reader.should_route_search_to_projection(
        ["character"], snapshot="3.3.0", settings=chosen
    )
    assert not context.should_route_to_projection(reader.ENTITY_PROJECTION_KINDS, settings=chosen)
    assert not reader.should_route_search_to_projection(scope="dialogue", settings=chosen)
    assert reader.should_route_search_to_projection(
        scope="dialogue", settings=settings(kinds=("dialogue",))
    )
    assert not reader.should_route_search_to_projection(
        scope="dialogue", character="7", settings=settings(kinds=("dialogue",))
    )
    historical = settings(
        kinds=("character", "item", "quest", "game_event", "cutscene"), snapshots=("3.3.0",)
    )
    assert reader.should_route_search_to_projection(snapshot="3.3.0", settings=historical)
    assert not reader.should_route_search_to_projection(
        ["location"], snapshot="3.3.0", settings=historical
    )
    assert reader.should_route_node_to_projection("area:7", settings=chosen)
    assert not reader.should_route_node_to_projection("quest:7", settings=chosen)
    assert not reader.should_route_node_to_projection("character:7", "3.3.0", settings=chosen)
    assert not reader.should_route_node_to_projection("action:7", settings=settings(kinds=()))
    assert context.should_route_to_projection(
        "character", "3.3.0", settings=settings(snapshots=("current", "3.3.0"))
    )
    assert not context.should_route_to_projection(
        "character", settings=settings(snapshots=("3.3.0",))
    )
    assert context.should_route_to_projection(
        reader.ENTITY_PROJECTION_KINDS,
        settings=settings(kinds=reader.ENTITY_PROJECTION_KINDS),
    )


class SourceSelected(RuntimeError):
    pass


@pytest.mark.parametrize(
    "path,query,kind,expected",
    [
        ("/catalog", "category=character", "character", True),
        ("/catalog", "category=character&game_version=3.3.0", "character", True),
        ("/characters/character:7/profile", "game_version=3.3.0", "character", False),
        ("/dialogue/search", "q=Name", "dialogue", True),
        ("/dialogue/search", "q=Name&character=character:7", "dialogue", False),
        ("/dialogue/search", "q=Name&game_version=3.3.0", "dialogue", False),
        ("/search", "q=Name&category=character&category=item", "character", False),
        ("/categories", "", "character", False),
        ("/story-map", "", "story_map", True),
    ],
)
def test_cache_revision_predicate_matches_actual_route_selection(path, query, kind, expected):
    from wuwa_story.api.cache import projection_request

    scope = {"path": path, "query_string": query.encode()}
    assert projection_request(scope, settings(kinds=(kind,))) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("path,helper", ROUTES)
async def test_first_rollout_routes_selected_slices_and_keeps_other_source_readers(
    app, monkeypatch, path, helper
):
    monkeypatch.setattr(context, "get_settings", lambda: settings())
    source = SimpleNamespace(
        info={},
        **{
            name: AsyncMock(side_effect=SourceSelected())
            for name in ("scalar", "scalars", "execute", "get")
        },
    )
    app.dependency_overrides[get_session] = lambda: source
    projected = AsyncMock(side_effect=reader._unready())
    monkeypatch.setattr(reader, helper, projected)
    selected = (
        path.startswith(("/characters/", "/items/", "/locations/"))
        or path.startswith("/catalog?category=character")
        or path == "/story-map"
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        if selected:
            response = await client.get(path)
            assert response.status_code == 503
            projected.assert_awaited_once()
        else:
            with pytest.raises(SourceSelected):
                await client.get(path)
            projected.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,helper",
    [
        ("/characters/character:7/profile", "profile_page"),
        ("/items/item:7/profile", "profile_page"),
        ("/characters/character:7/archive", "archive_page"),
        ("/quests/7/profile", "profile_page"),
        ("/quests/7/transcript", "transcript_page"),
        ("/quests/7/continuity", "quest_section"),
        ("/quests/7/media", "quest_section"),
        ("/story-map", "story_map_page"),
        ("/cutscenes", "cutscene_catalog"),
        ("/cutscenes/C0022", "cutscene_detail"),
        ("/dialogue/search?q=Name", "dialogue_search_page"),
        ("/search?q=Name&category=character", "search_page"),
        ("/nodes/talk_item:7/narrative-context", "node_section"),
        ("/events?server=asia", "events_page"),
    ],
)
async def test_current_only_rollout_keeps_explicit_history_on_source(
    app, monkeypatch, path, helper
):
    monkeypatch.setattr(context, "get_settings", lambda: settings(kinds=READ_PROJECTION_KINDS))
    source = SimpleNamespace(
        info={},
        **{
            name: AsyncMock(side_effect=SourceSelected())
            for name in ("scalar", "scalars", "execute", "get")
        },
    )
    app.dependency_overrides[get_session] = lambda: source
    projected = AsyncMock(side_effect=AssertionError("Unselected historical projection lookup"))
    monkeypatch.setattr(reader, helper, projected)
    version_param = "version" if path.startswith("/events") else "game_version"
    path += (
        ("&" if "?" in path else "?")
        + version_param
        + ("=3.3" if version_param == "version" else "=3.3.0")
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        with pytest.raises(SourceSelected):
            await client.get(path)
    projected.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "categories,selected", [("character&category=item", True), ("character&category=quest", False)]
)
async def test_search_route_never_combines_projection_and_source_categories(
    app, monkeypatch, categories, selected
):
    monkeypatch.setattr(context, "get_settings", lambda: settings())
    app.dependency_overrides[get_session] = lambda: SimpleNamespace(
        scalar=AsyncMock(side_effect=SourceSelected())
    )
    projected = AsyncMock(side_effect=reader._unready())
    monkeypatch.setattr(reader, "search_page", projected)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        path = "/search?q=Name&category=" + categories
        if selected:
            assert (await client.get(path)).status_code == 503
            projected.assert_awaited_once()
        else:
            with pytest.raises(SourceSelected):
                await client.get(path)
            projected.assert_not_awaited()


def test_locale_selector_and_search_id_override_are_configuration_only():
    chosen = settings(kinds=READ_PROJECTION_KINDS, locales=("en",))
    assert context.should_route_to_projection("character", settings=chosen)
    assert not context.should_route_to_projection("character", locale="ja", settings=chosen)
    assert reader.should_route_node_to_projection("character:7", locale="en", settings=chosen)
    assert not reader.should_route_node_to_projection("character:7", locale="ja", settings=chosen)
    assert reader.should_route_search_to_projection(["character"], locale="en", settings=chosen)
    assert not reader.should_route_search_to_projection(["character"], locale="ja", settings=chosen)
    for locale_id in (0, 1, 2):
        assert not reader.should_route_search_to_projection(
            ["character"], locale="en", locale_id=locale_id, settings=chosen
        )
        assert reader.should_route_search_to_projection(
            ["character"],
            locale="en",
            locale_id=locale_id,
            settings=settings(kinds=READ_PROJECTION_KINDS),
        )
    with context.source_build():
        assert not context.should_route_to_projection("character", locale="en", settings=chosen)


LOCALIZED_ROUTES = [
    (path, helper)
    for path, helper in ROUTES
    if path
    not in {
        "/categories",
        "/notices",
        "/notices/7",
        "/nodes/speaker:7",
        "/nodes/speaker:7/edges",
        "/nodes/speaker:7/source-record",
    }
]


@pytest.mark.asyncio
@pytest.mark.parametrize("path,helper", LOCALIZED_ROUTES)
@pytest.mark.parametrize("locale", ["en", "ja"])
async def test_locale_rollout_selected_misses_stay_503_unselected_uses_source(
    app, monkeypatch, path, helper, locale
):
    monkeypatch.setattr(
        context, "get_settings", lambda: settings(kinds=READ_PROJECTION_KINDS, locales=("en",))
    )
    source = SimpleNamespace(
        info={},
        **{
            name: AsyncMock(side_effect=SourceSelected())
            for name in ("scalar", "scalars", "execute", "get")
        },
    )
    app.dependency_overrides[get_session] = lambda: source
    projected = AsyncMock(side_effect=reader._unready())
    monkeypatch.setattr(reader, helper, projected)
    path += ("&" if "?" in path else "?") + "locale=" + locale
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        if locale == "en":
            response = await client.get(path)
            assert response.status_code == 503
            projected.assert_awaited_once()
            assert not any(
                getattr(source, name).await_count
                for name in ("scalar", "scalars", "execute", "get")
            )
        else:
            with pytest.raises(SourceSelected):
                await client.get(path)
            projected.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/search?q=Name&category=character&locale=en&locale_id=1",
        "/search?q=Name&scope=dialogue&locale_id=2",
    ],
)
async def test_search_locale_id_override_preserves_source_reader_under_locale_allowlist(
    app, monkeypatch, path
):
    monkeypatch.setattr(
        context, "get_settings", lambda: settings(kinds=READ_PROJECTION_KINDS, locales=("en",))
    )
    app.dependency_overrides[get_session] = lambda: SimpleNamespace(
        scalar=AsyncMock(side_effect=SourceSelected())
    )
    projected = AsyncMock(side_effect=AssertionError("Cannot infer locale from numeric ID"))
    monkeypatch.setattr(reader, "search_page", projected)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        with pytest.raises(SourceSelected):
            await client.get(path)
    projected.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,helper",
    [
        ("/categories", "category_counts"),
        ("/notices", "notices_page"),
        ("/notices/7", "notice_detail_payload"),
        ("/nodes/speaker:7", "node_section"),
        ("/nodes/speaker:7/edges", "node_section"),
        ("/nodes/speaker:7/source-record", "node_section"),
    ],
)
async def test_nonlocalized_routes_use_fixed_english_even_if_query_says_japanese(
    app, monkeypatch, path, helper
):
    monkeypatch.setattr(
        context, "get_settings", lambda: settings(kinds=READ_PROJECTION_KINDS, locales=("ja",))
    )
    source = SimpleNamespace(
        info={},
        **{
            name: AsyncMock(side_effect=SourceSelected())
            for name in ("scalar", "scalars", "execute", "get")
        },
    )
    app.dependency_overrides[get_session] = lambda: source
    projected = AsyncMock(side_effect=AssertionError("This route reads English only"))
    monkeypatch.setattr(reader, helper, projected)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        with pytest.raises(SourceSelected):
            await client.get(path + "?locale=ja")
    projected.assert_not_awaited()


@pytest.mark.asyncio
async def test_dialogue_locale_id_source_selection_survives_delegation(app, monkeypatch):
    from wuwa_story.api.routes.story import transcripts

    monkeypatch.setattr(
        context, "get_settings", lambda: settings(kinds=READ_PROJECTION_KINDS, locales=("en",))
    )
    app.dependency_overrides[get_session] = lambda: SimpleNamespace(
        scalar=AsyncMock(return_value=None)
    )
    source = AsyncMock(side_effect=SourceSelected())
    monkeypatch.setattr(transcripts.usecases, "search_dialogue", source)
    projected = AsyncMock(side_effect=AssertionError("Delegation must retain source selection"))
    monkeypatch.setattr(reader, "dialogue_search_page", projected)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        with pytest.raises(SourceSelected):
            await client.get("/search?q=Name&scope=dialogue&locale=en&locale_id=1")
    source.assert_awaited_once()
    projected.assert_not_awaited()


@pytest.mark.parametrize(
    "path,query,expected",
    [
        ("/catalog", "category=character&locale=en", True),
        ("/catalog", "category=character&locale=ja", False),
        ("/characters/character:7/profile", "locale=ja", False),
        ("/story-map", "locale=ja", False),
        ("/events", "locale=ja", False),
        ("/dialogue/search", "q=Name&locale=ja", False),
        ("/search", "q=Name&category=character&locale=en", True),
        ("/search", "q=Name&category=character&locale=en&locale_id=1", False),
        ("/search", "q=Name&category=character&locale=ja", False),
        ("/nodes/character:7/related", "locale=ja", False),
        ("/nodes/character:7/narrative-context", "locale=ja", False),
        ("/nodes/character:7", "locale=ja", True),
        ("/nodes/character:7/edges", "locale=ja", True),
        ("/nodes/character:7/source-record", "locale=ja", True),
        ("/notices", "locale=ja", True),
        ("/categories", "locale=ja", True),
    ],
)
def test_locale_cache_selection_matches_effective_route_locale(path, query, expected):
    from wuwa_story.api.cache import projection_request

    configured = settings(kinds=READ_PROJECTION_KINDS, locales=("en",))
    assert (
        projection_request({"path": path, "query_string": query.encode()}, configured) is expected
    )

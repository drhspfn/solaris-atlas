"""Public notice contracts shared by source reads and projection publication."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.dialects import postgresql

from wuwa_story.api.routes import notices
from wuwa_story.db.models.game_events import GameEvent
from wuwa_story.db.models.notices import GameNotice, GameNoticeRevision
from wuwa_story.db.repositories.notices import SqlAlchemyNoticeRepository

CURRENT = "a" * 64
PREVIOUS = "b" * 64
OBSERVED = datetime(2026, 10, 30, tzinfo=UTC)


def notice_record(**overrides):
    return GameNotice(
        **{
            "id": 7,
            "slug": "official-update",
            "source_id": "10",
            "locale": "en",
            "category": "game",
            "title": "Current title",
            "revision": CURRENT,
            "source_url": "https://official.example/7",
            "published_at": None,
            "observed_at": OBSERVED,
            "related_event_ids": [11],
            "content": {"title": "Current title", "blocks": [{"type": "text", "text": "Body"}]},
            **overrides,
        }
    )


@pytest.fixture
def source_client(monkeypatch):
    monkeypatch.setattr(notices.read_projections, "should_route_to_projection", lambda *_: False)
    session = AsyncMock()
    app = FastAPI()
    app.include_router(notices.router)
    app.dependency_overrides[notices.get_session] = lambda: session
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ), session


@pytest.mark.asyncio
async def test_source_page_preserves_summary_and_lookahead(source_client):
    client, session = source_client
    session.scalars.return_value = [notice_record(), notice_record(id=8)]
    async with client:
        response = await client.get("/notices?limit=1&offset=3")
    assert response.status_code == 200
    assert response.json() == {
        "notices": [
            {
                "id": 7,
                "slug": "official-update",
                "href": "/notices/official-update",
                "source_id": "10",
                "locale": "en",
                "category": "game",
                "title": "Current title",
                "revision": CURRENT,
                "banner_url": None,
                "source_url": "https://official.example/7",
                "available_from": None,
                "observed_at": OBSERVED.isoformat(),
                "related_event_ids": [11],
            }
        ],
        "has_more": True,
        "offset": 3,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", ["7", "official-update"])
async def test_source_detail_resolves_id_and_slug(source_client, identity):
    client, session = source_client
    session.get.return_value = session.scalar.return_value = notice_record()
    session.scalars.side_effect = [[GameEvent(id=11, title="Event", slug="event")], [CURRENT]]
    async with client:
        response = await client.get(f"/notices/{identity}")
    assert response.status_code == 200
    assert response.json()["events"] == [
        {"id": 11, "title": "Event", "slug": "event", "href": "/events?event=event"}
    ]
    assert response.json()["blocks"] == [{"type": "text", "text": "Body"}]
    assert response.json()["revisions"] == [CURRENT]


@pytest.mark.asyncio
async def test_historical_detail_uses_snapshot_and_preserves_current_identity(source_client):
    client, session = source_client
    notice = notice_record()
    snapshot = GameNoticeRevision(
        notice_id=7,
        revision=PREVIOUS,
        observed_at=datetime(2026, 10, 29, tzinfo=UTC),
        content={
            "title": "Old title",
            "category": "activity",
            "related_event_ids": [],
            "blocks": [{"type": "image", "url": "https://official.example/old.png"}],
        },
    )
    session.get.side_effect = [notice, snapshot]
    session.scalars.return_value = [CURRENT, PREVIOUS]
    async with client:
        response = await client.get(f"/notices/7?revision={PREVIOUS}")
    assert response.status_code == 200
    body = response.json()
    assert (body["title"], body["category"], body["revision"]) == (
        "Old title",
        "activity",
        PREVIOUS,
    )
    assert body["events"] == body["related_event_ids"] == []
    assert body["href"] == "/notices/official-update"
    assert body["observed_at"] == "2026-10-29T00:00:00+00:00"
    assert body["banner_url"] is None
    assert body["blocks"] == snapshot.content["blocks"]
    assert notice.title == "Current title" and notice.related_event_ids == [11]
    session.scalars.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "revision,message",
    [(None, "Announcement not found"), (PREVIOUS, "Announcement revision not found")],
)
async def test_source_missing_resources_keep_distinct_404_messages(
    source_client, revision, message
):
    client, session = source_client
    session.get.side_effect = [notice_record(), None] if revision else [None]
    async with client:
        response = await client.get("/notices/7", params={"revision": revision} if revision else {})
    assert response.status_code == 404 and response.json() == {"detail": message}
    session.scalars.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_current_revision_requires_its_archived_snapshot(source_client):
    client, session = source_client
    session.get.side_effect = [notice_record(), None]
    async with client:
        response = await client.get(f"/notices/7?revision={CURRENT}")
    assert response.status_code == 404
    assert response.json() == {"detail": "Announcement revision not found"}


@pytest.mark.asyncio
async def test_repository_filters_literal_search_and_bounds_page_in_postgres():
    session = AsyncMock()
    session.scalars.return_value = []
    await SqlAlchemyNoticeRepository(session).page(
        category="game",
        q="  100%_/  ",
        offset=3,
        limit=25,
        event_id=11,
    )
    statement = session.scalars.await_args.args[0].compile(dialect=postgresql.dialect())
    assert statement.params == {
        "locale_1": "en",
        "category_1": "game",
        "title_1": "100/%/_//",
        "related_event_ids_1": [11],
        "param_1": 25,
        "param_2": 3,
    }
    sql = str(statement)
    assert "ESCAPE '/'" in sql and "@>" in sql
    assert "ORDER BY core.game_notice.published_at DESC NULLS LAST, core.game_notice.id DESC" in sql


@pytest.mark.asyncio
async def test_http_service_can_be_overridden_without_database_access(monkeypatch):
    monkeypatch.setattr(notices.read_projections, "should_route_to_projection", lambda *_: False)
    service = AsyncMock()
    service.page.return_value = {"notices": [], "has_more": False, "offset": 0}
    app = FastAPI()
    app.include_router(notices.router)
    app.dependency_overrides[notices.get_notice_service] = lambda: service
    app.dependency_overrides[notices.get_session] = lambda: object()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/notices")
    assert response.status_code == 200 and response.json() == service.page.return_value
    service.page.assert_awaited_once_with(category=None, q="", offset=0, limit=24, event_id=None)


@pytest.mark.asyncio
async def test_projection_publisher_reuses_service_without_http_handlers(monkeypatch):
    from wuwa_story.read_models.source_payloads import _notice_payload

    handler = AsyncMock(side_effect=AssertionError("Publisher must not invoke an HTTP handler"))
    monkeypatch.setattr(notices, "notice_detail", handler)
    session = AsyncMock()
    current = notice_record(related_event_ids=[])
    snapshot = GameNoticeRevision(
        notice_id=7,
        revision=PREVIOUS,
        content={"title": "Old title"},
        observed_at=datetime(2026, 10, 29, tzinfo=UTC),
    )
    session.get.side_effect = [current, current, snapshot]
    session.scalars.side_effect = [[CURRENT, PREVIOUS], [CURRENT, PREVIOUS]]
    payload = await _notice_payload(session, 7)
    assert payload["title"] == "Current title"
    assert payload["revision_snapshot_format"] == "overrides-v1"
    assert payload["revision_snapshots"][PREVIOUS]["title"] == "Old title"
    assert payload["revision_snapshots"][PREVIOUS]["revision"] == PREVIOUS
    handler.assert_not_awaited()

"""Public notice contracts shared by source reads and projection publication."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from wuwa_story.api.routes import notices
from wuwa_story.db.models.game_events import GameEvent
from wuwa_story.db.models.notices import GameNotice, GameNoticeRevision

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

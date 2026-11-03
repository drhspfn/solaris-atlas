"""Revision policy and use cases work without HTTP, SQLAlchemy or configuration."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from wuwa_story.notices.domain import (
    Notice,
    NoticeNotFound,
    NoticeRevision,
    NoticeRevisionNotFound,
)
from wuwa_story.notices.repository import NoticeRepository
from wuwa_story.notices.service import NoticeService


@pytest.fixture
def notice():
    return Notice(
        id=7,
        slug="update",
        source_id="10",
        locale="en",
        category="game",
        title="Current",
        revision="current",
        content={"banner_url": "current.png"},
        source_url="https://official/7",
        published_at=None,
        observed_at=datetime(2026, 10, 30, tzinfo=UTC),
        related_event_ids=[11],
    )


def test_old_snapshot_metadata_fallback_keeps_missing_artwork_missing(notice):
    snapshot = NoticeRevision("old", {}, datetime(2026, 10, 29, tzinfo=UTC))
    selected = notice.for_revision(snapshot)
    assert (selected.id, selected.slug, selected.source_url, selected.published_at) == (
        notice.id,
        notice.slug,
        notice.source_url,
        notice.published_at,
    )
    assert (selected.title, selected.category, selected.related_event_ids) == (
        "Current",
        "game",
        [11],
    )
    assert selected.revision == "old" and selected.observed_at == snapshot.observed_at
    assert selected.content == {}
    assert notice.revision == "current" and notice.content == {"banner_url": "current.png"}


@pytest.mark.asyncio
async def test_service_uses_repository_lookahead_without_sql(notice):
    repository = AsyncMock(spec=NoticeRepository)
    repository.page.return_value = [notice, notice]
    service = NoticeService(repository, lambda url: url)
    page = await service.page(category="game", q=" query ", limit=1, offset=4, event_id=11)
    repository.page.assert_awaited_once_with(
        category="game",
        q=" query ",
        limit=2,
        offset=4,
        event_id=11,
    )
    assert len(page["notices"]) == 1 and page["has_more"] and page["offset"] == 4


@pytest.mark.asyncio
async def test_service_renders_snapshot_images_without_mutating_source(notice):
    repository = AsyncMock(spec=NoticeRepository)
    repository.find.return_value = notice
    content = {
        "title": "Old",
        "related_event_ids": [],
        "banner_url": "old.png",
        "blocks": [{"type": "image", "url": "body.png", "source_url": "official.png"}],
        "links": ["https://official/link"],
    }
    repository.revision.return_value = NoticeRevision("old", content, notice.observed_at)
    repository.events.return_value = []
    repository.revisions.return_value = ["current", "old"]
    service = NoticeService(repository, lambda url: f"cdn/{url}" if url else None)
    payload = await service.detail("update", "old")
    repository.revision.assert_awaited_once_with(7, "old")
    repository.events.assert_awaited_once_with([])
    assert payload["title"] == "Old" and payload["banner_url"] == "cdn/old.png"
    assert payload["blocks"] == [
        {"type": "image", "url": "cdn/body.png", "source_url": "official.png"}
    ]
    assert content["blocks"][0]["url"] == "body.png"
    assert payload["links"] == content["links"] and notice.related_event_ids == [11]


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_notice", [True, False])
async def test_service_raises_domain_errors_and_stops_before_related_reads(notice, missing_notice):
    repository = AsyncMock(spec=NoticeRepository)
    repository.find.return_value = None if missing_notice else notice
    repository.revision.return_value = None
    service = NoticeService(repository, lambda url: url)
    error = NoticeNotFound if missing_notice else NoticeRevisionNotFound
    with pytest.raises(error):
        await service.detail("7", "current")
    repository.events.assert_not_awaited()
    repository.revisions.assert_not_awaited()
    if missing_notice:
        repository.revision.assert_not_awaited()

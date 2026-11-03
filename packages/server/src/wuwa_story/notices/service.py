"""Notice list/detail use cases and public response assembly."""

from collections.abc import Callable
from typing import Any

from wuwa_story.notices.domain import Notice, NoticeNotFound, NoticeRevisionNotFound
from wuwa_story.notices.repository import NoticeRepository
from wuwa_story.urls import record_url


class NoticeService:
    def __init__(
        self, repository: NoticeRepository, image_url: Callable[[str | None], str | None]
    ) -> None:
        self.repository = repository
        self.image_url = image_url

    def summary(self, notice: Notice) -> dict[str, Any]:
        return {
            "id": notice.id,
            "slug": notice.slug,
            "href": record_url(notice, "notices"),
            "source_id": notice.source_id,
            "locale": notice.locale,
            "category": notice.category,
            "title": notice.title,
            "revision": notice.revision,
            "banner_url": self.image_url(notice.content.get("banner_url")),
            "source_url": notice.source_url,
            "available_from": notice.published_at,
            "observed_at": notice.observed_at,
            "related_event_ids": notice.related_event_ids,
        }

    async def page(
        self,
        *,
        category: str | None = None,
        q: str = "",
        offset: int = 0,
        limit: int = 24,
        event_id: int | None = None,
    ) -> dict[str, Any]:
        rows = await self.repository.page(
            category=category,
            q=q,
            offset=offset,
            limit=limit + 1,
            event_id=event_id,
        )
        return {
            "notices": [self.summary(notice) for notice in rows[:limit]],
            "has_more": len(rows) > limit,
            "offset": offset,
        }

    async def detail(self, identity: str, revision: str | None = None) -> dict[str, Any]:
        notice = await self.repository.find(identity)
        if notice is None:
            raise NoticeNotFound()
        snapshot = None
        if revision:
            snapshot = await self.repository.revision(notice.id, revision)
            if snapshot is None:
                raise NoticeRevisionNotFound()
        selected = notice.for_revision(snapshot)
        events = await self.repository.events(selected.related_event_ids)
        revisions = await self.repository.revisions(notice.id)
        return {
            **self.summary(selected),
            "blocks": [
                {**block, "url": self.image_url(block.get("url"))}
                if block["type"] == "image"
                else block
                for block in selected.content.get("blocks", [])
            ],
            "links": selected.content.get("links", []),
            "revisions": revisions,
            "events": [
                {
                    "id": event.id,
                    "title": event.title,
                    "slug": event.slug,
                    "href": record_url(event, "events"),
                }
                for event in events
            ],
        }

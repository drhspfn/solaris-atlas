"""Notice read models independent of HTTP and persistence."""

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any


class NoticeNotFound(LookupError):
    def __init__(self) -> None:
        super().__init__("Announcement not found")


class NoticeRevisionNotFound(LookupError):
    def __init__(self) -> None:
        super().__init__("Announcement revision not found")


@dataclass(frozen=True)
class NoticeRevision:
    revision: str
    content: dict[str, Any]
    observed_at: datetime


@dataclass(frozen=True)
class Notice:
    id: int
    slug: str | None
    source_id: str
    locale: str
    category: str
    title: str
    revision: str
    content: dict[str, Any]
    source_url: str
    published_at: datetime | None
    observed_at: datetime
    related_event_ids: list[int]

    def for_revision(self, snapshot: NoticeRevision | None = None) -> "Notice":
        """Keep the public identity while selecting revision-owned content.

        Older snapshots can omit metadata; retain the existing current-value
        fallback, but an explicitly empty event list must stay empty.
        """
        content = snapshot.content if snapshot else self.content
        return replace(
            self,
            title=content.get("title", self.title),
            category=content.get("category", self.category),
            related_event_ids=content.get("related_event_ids", self.related_event_ids),
            content=content,
            revision=snapshot.revision if snapshot else self.revision,
            observed_at=snapshot.observed_at if snapshot else self.observed_at,
        )


@dataclass(frozen=True)
class NoticeEvent:
    id: int
    title: str
    slug: str | None

"""Persistence port for event calendars and run details."""

from typing import Protocol

from wuwa_story.events.domain import (
    EventAnnouncement,
    EventMedia,
    EventNoticeSnapshot,
    EventRewardNode,
    EventRun,
)

EVENT_RUN_BATCH_SIZE = 16


class EventRepository(Protocol):
    async def spine(self, captures: set[tuple[str, str]]) -> list[EventMedia]: ...

    async def pool_occurrences(
        self, *, server: str, proof: dict, starts_at: object, ends_at: object,
    ) -> list[tuple[int, int]]: ...

    async def runs(
        self,
        *,
        server: str,
        kind: str | None,
        version: str | None,
        locale: str,
        summary: bool,
        event_id: int | None,
        occurrence_id: int | None,
        occurrence_ids: list[int] | None = None,
    ) -> list[EventRun]: ...

    async def announcements(
        self,
        *,
        event_id: int | None,
        notice_ids: set[int],
    ) -> list[EventAnnouncement]: ...

    async def notice_snapshots(self, revisions: set[str]) -> list[EventNoticeSnapshot]: ...

    async def reward_nodes(self, reward_keys: set[str]) -> list[EventRewardNode]: ...

    async def media(
        self,
        paths: set[str],
        *,
        captures: set[tuple[str, str]],
    ) -> list[EventMedia]: ...

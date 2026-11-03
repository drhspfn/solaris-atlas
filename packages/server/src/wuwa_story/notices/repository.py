"""Persistence port for the notice browsing use cases."""

from typing import Protocol

from wuwa_story.notices.domain import Notice, NoticeEvent, NoticeRevision


class NoticeRepository(Protocol):
    async def page(
        self, *, category: str | None, q: str, offset: int, limit: int, event_id: int | None
    ) -> list[Notice]: ...

    async def find(self, identity: str) -> Notice | None: ...

    async def revision(self, notice_id: int, revision: str) -> NoticeRevision | None: ...

    async def events(self, event_ids: list[int]) -> list[NoticeEvent]: ...

    async def revisions(self, notice_id: int) -> list[str]: ...

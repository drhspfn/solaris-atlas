from pathlib import Path
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.ingestion.context import ImportSummary


class Importer(Protocol):
    key: str
    version: str

    def detect(self, source: Path) -> bool: ...

    async def import_release(
        self, source: Path, session: AsyncSession, batch_size: int = 1000
    ) -> ImportSummary: ...

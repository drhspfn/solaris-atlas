from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(slots=True)
class ImportContext:
    session: AsyncSession
    release_id: int
    import_run_id: int
    source_root: Path
    batch_size: int = 1000
    counts: dict[str, int] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ImportSummary:
    release_id: int
    import_run_id: int
    records_seen: int
    records_created: int
    records_failed: int
    status: str

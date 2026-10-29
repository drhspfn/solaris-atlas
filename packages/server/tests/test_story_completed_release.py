from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.story import service


@pytest.mark.asyncio
async def test_default_release_ignores_incomplete_import_but_explicit_version_is_preserved(monkeypatch):
    rows = {1: SimpleNamespace(id=1, game_version="3.3.0", sequence=1),
            2: SimpleNamespace(id=2, game_version="3.4.0", sequence=2),
            3: SimpleNamespace(id=3, game_version="3.5.0", sequence=3)}
    monkeypatch.setattr(service, "releases", AsyncMock(return_value=rows))
    completed = AsyncMock(return_value={1, 2})
    monkeypatch.setattr(service.repository, "completed_release_ids", completed)
    session = SimpleNamespace(info={})
    assert await service._release_id(session, None) == 2
    assert await service._release_id(session, "3.5.0") == 3
    assert await service._release_id(session, None) == 2
    completed.assert_awaited_once()
